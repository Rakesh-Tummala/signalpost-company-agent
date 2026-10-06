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

from .contacts import GENERIC_SUFFIX_WORDS, own_email_domain, registered_contacts
from .identity import _tokens

MIN_SLUG = 4
MAX_SLUG = 30
MAX_CANDIDATES = 6
ORG_PATTERN = re.compile(r"(?<!\d)(\d{3})[ . ]?(\d{3})[ . ]?(\d{3})(?!\d)")


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def domain_candidates(name: str | None) -> list[str]:
    """Likely hostnames for a legal name, most probable first, at most MAX_CANDIDATES."""
    raw = str(name or "")
    variants: list[list[str]] = [_tokens(raw)]
    # A trailing generic word (Holding, Norge, Gruppen ...) is often left off the company's own domain.
    trimmed = list(variants[0])
    while len(trimmed) > 1 and trimmed[-1] in GENERIC_SUFFIX_WORDS:
        trimmed = trimmed[:-1]
        variants.append(list(trimmed))
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


CONTACT_LINK = re.compile(r"kontakt|contact|om-oss|omoss|om_oss|about|personvern|privacy|impressum|vilkar|vilk[aå]r|terms|betingelser|handelsbetingelser", re.I)
MAX_CONTACT_PAGES = 3


def candidate_hosts(name: str | None, registry_row: dict | None) -> list[str]:
    """The domain of the e-mail address the company registered (when it is not a mail provider), then name-derived hosts."""
    registered = own_email_domain(registered_contacts(registry_row)["email_domain"])
    hosts = [registered] if registered else []
    for host in domain_candidates(name):
        if host not in hosts:
            hosts.append(host)
    return hosts


def contact_links(base_url: str, hrefs_and_labels: list[tuple[str, str]], host: str) -> list[str]:
    """Same-site links to contact, about or privacy pages, where a company usually prints its organisation number."""
    import urllib.parse

    wanted = host.removeprefix("www.")
    found: list[str] = []
    for href, label in hrefs_and_labels:
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname.removeprefix("www.") != wanted:
            continue
        if CONTACT_LINK.search(parsed.path + " " + label) and url not in found and url.rstrip("/") != base_url.rstrip("/"):
            found.append(url)
    return found[:MAX_CONTACT_PAGES]
