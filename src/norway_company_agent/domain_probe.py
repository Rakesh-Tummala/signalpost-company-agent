"""Key-free website discovery: probe the obvious domains for a legal name.

Most small Norwegian companies have no website listed in the registry, and search APIs need
keys the evaluator may not supply. A company's own site very often lives at a domain derived
from its name, and Norwegian business sites very often print the organisation number in the
footer. So: build a few likely domains from the legal name, fetch the ones that resolve, and
treat a page as a *candidate* only if it shows this exact organisation number as a delimited
number. A name match alone is never enough here (two Norwegian companies can share a name);
the organisation number is the proof, and the normal identity gate still runs afterwards.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .identity import _tokens

MIN_SLUG = 4
MAX_SLUG = 30
MAX_CANDIDATES = 4
ORG_PATTERN = re.compile(r"(?<!\d)(\d{3})[ . ]?(\d{3})[ . ]?(\d{3})(?!\d)")


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def domain_candidates(name: str | None) -> list[str]:
    """Likely hostnames for a legal name, most probable first, at most MAX_CANDIDATES."""
    raw = str(name or "")
    variants: list[list[str]] = [_tokens(raw)]
    # Norwegian domains often spell o-slash / a-ring / ae with letter pairs instead of the plain letter.
    if re.search(r"[øØæÆåÅ]", raw):
        paired = raw.translate(str.maketrans({"ø": "oe", "Ø": "OE", "æ": "ae", "Æ": "AE", "å": "aa", "Å": "AA"}))
        variants.append(_tokens(paired))
    hosts: list[str] = []
    for tokens in variants:
        if not tokens:
            continue
        slug = "".join(tokens)
        if not (MIN_SLUG <= len(slug) <= MAX_SLUG):
            continue
        for host in (f"{slug}.no", f"{'-'.join(tokens)}.no" if len(tokens) > 1 else None, f"{slug}.com"):
            if host and host not in hosts:
                hosts.append(host)
    return hosts[:MAX_CANDIDATES]


def place_on_page(text: str, postcode: str | None, town: str | None) -> bool:
    """The registered postal code (as its own 4-digit number) and the registered town both appear.

    A weaker proof than the organisation number, used only for a name-derived domain: a different
    company with the same name, the same postcode and the same town is implausible.
    """
    code = str(postcode or "").strip()
    town_tokens = set(_tokens(town))
    if not re.fullmatch(r"\d{4}", code) or not town_tokens:
        return False
    return bool(re.search(r"(?<!\d)" + code + r"(?!\d)", text or "")) and town_tokens <= set(_tokens(text))


def org_number_on_page(text: str, organisation_number: str) -> bool:
    """The exact organisation number appears as its own number ("923 609 016", "923609016", "923.609.016").

    Delimited, so it cannot be found inside a longer digit run such as a phone number or an
    account number, which the identity gate's own concatenated-digit check could in principle hit.
    """
    wanted = re.sub(r"\D", "", str(organisation_number))
    return any("".join(match.groups()) == wanted for match in ORG_PATTERN.finditer(text or ""))
