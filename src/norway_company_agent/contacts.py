"""Registry-declared contact facts, and the matching identifiers read from a company's own pages.

The Brreg bulk file carries contact details the company itself registered: an e-mail address,
a mobile number and a phone number. They are exact, company-declared facts, so a page that shows
this company's registered phone number or e-mail address (or whose domain is the registered
e-mail domain) is tied to the entity more strongly than a name match. They are used only together
with the legal name appearing in the site's hostname or title, because accountants, housing
associations and property managers often register their *own* number or address for a client.
"""
from __future__ import annotations

import re
from typing import Any

# E-mail providers and ISPs: an address at one of these says nothing about the company's own site.
FREE_MAIL_DOMAINS = frozenset(
    "gmail.com googlemail.com hotmail.com hotmail.no outlook.com outlook.no live.no live.com icloud.com me.com "
    "yahoo.com yahoo.no msn.com online.no getmail.no start.no broadpark.no frisurf.no lyse.net c2i.net tele2.no "
    "telenor.net bbnett.no nordnet.no altibox.no chello.no combo.no mimer.no gmx.com protonmail.com proton.me "
    "webmail.no hotmail.se hotmail.dk live.se online.com mac.com aol.com".split()
)

# Words a company routinely leaves off its own domain or page title ("Inselo Norge AS" is inselo.no).
GENERIC_SUFFIX_WORDS = frozenset(
    {"holding", "holdings", "norge", "norway", "gruppen", "group", "invest", "eiendom", "as", "asa", "da", "ans", "service", "services", "consulting", "partner", "partners"}
)

ORG_LIKE = re.compile(r"(?<!\d)(\d{3})[ . ]?(\d{3})[ . ]?(\d{3})(?!\d)")
# 8-digit Norwegian numbers: 2-2-2-2 ("55 12 34 56"), 3-2-3 ("400 12 345"), or unspaced, with an optional +47.
PHONE_LIKE = re.compile(
    r"(?<![\d.])(?:(?:\+|00)?47[ . -]?)?(?:(\d{2})[ . -]?(\d{2})[ . -]?(\d{2})[ . -]?(\d{2})|(\d{3})[ . -]?(\d{2})[ . -]?(\d{3}))(?![\d])"
)
EMAIL_LIKE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
MAX_IDENTIFIERS = 80


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def registered_contacts(registry_row: dict[str, Any] | None) -> dict[str, Any]:
    """The phones (8 digits), e-mail and e-mail domain the company registered, from a bulk registry row."""
    row = registry_row or {}
    phones = set()
    for key in ("mobil", "telefon"):
        digits = _digits(row.get(key))
        if len(digits) >= 8:
            phones.add(digits[-8:])
    email = str(row.get("epostadresse") or "").strip().casefold()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return {"phones": phones, "email": email if "@" in email else "", "email_domain": domain}


def own_email_domain(domain: str) -> str:
    """The registered e-mail domain if it could be the company's own (not a mail provider or ISP), else ''."""
    domain = (domain or "").casefold().strip()
    return "" if (not domain or "." not in domain or domain in FREE_MAIL_DOMAINS) else domain


def page_identifiers(text: str, hrefs: list[str] | None = None) -> dict[str, list[str]]:
    """Organisation-number-like numbers, phone numbers (8 digits) and e-mail addresses printed on a page.

    `text` is the page's full visible text (footers included) and `hrefs` its mailto:/tel: link targets.
    Only the identifiers are kept, never the surrounding text, so a page's contact details are
    comparable with the registry's without republishing anything beyond the company's own facts.
    """
    haystack = " ".join([text or "", *(hrefs or [])])
    orgs = {"".join(match.groups()) for match in ORG_LIKE.finditer(haystack)}
    phones = set()
    for match in PHONE_LIKE.finditer(haystack):
        groups = [group for group in match.groups() if group]
        digits = "".join(groups)
        if len(digits) == 8:
            phones.add(digits)
    emails = {match.group(0).casefold().rstrip(".") for match in EMAIL_LIKE.finditer(haystack)}
    return {
        "org_numbers": sorted(orgs)[:MAX_IDENTIFIERS],
        "phones": sorted(phones)[:MAX_IDENTIFIERS],
        "emails": sorted(emails)[:MAX_IDENTIFIERS],
    }


def identifier_proofs(profile_org: str, contacts: dict[str, Any], pages_identifiers: list[dict[str, Any]]) -> list[str]:
    """Which of the company's registered identifiers appear on any of the given pages' identifier sets."""
    wanted_org = _digits(profile_org)
    proofs: list[str] = []
    orgs = {value for item in pages_identifiers for value in (item or {}).get("org_numbers", [])}
    phones = {value for item in pages_identifiers for value in (item or {}).get("phones", [])}
    emails = {value for item in pages_identifiers for value in (item or {}).get("emails", [])}
    if wanted_org and wanted_org in orgs:
        proofs.append("organisation_number")
    if contacts.get("phones") and contacts["phones"] & phones:
        proofs.append("registered_phone")
    if contacts.get("email") and contacts["email"] in emails:
        proofs.append("registered_email")
    return proofs
