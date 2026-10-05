"""Wikidata as an independent, licensed (CC0) third-party source, matched by organisation number.

Wikidata's `P2333` is the Norwegian organisation number, so an item found through it is the exact
legal entity, with no name matching involved. It carries facts the registry does not: an
independently attributed website, social-media handles, a founding date. Only about 2.5% of
Norwegian companies have an item (mostly larger or notable ones), so this adds a little coverage, but
from a source that is genuinely separate from the company's own site and from Brreg.

The retained response for a company is the rows Wikidata returned for that organisation number,
saved as JSON, so each claim's excerpt is a literal slice of a saved body.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
BATCH = 150
QUERY = """SELECT ?o ?i ?iLabel ?site ?inc ?tw ?fb ?li ?ig ?yt WHERE {
  VALUES ?o { %s }
  ?i wdt:P2333 ?o .
  OPTIONAL { ?i wdt:P856 ?site } OPTIONAL { ?i wdt:P571 ?inc }
  OPTIONAL { ?i wdt:P2002 ?tw } OPTIONAL { ?i wdt:P2013 ?fb } OPTIONAL { ?i wdt:P4264 ?li }
  OPTIONAL { ?i wdt:P2003 ?ig } OPTIONAL { ?i wdt:P2397 ?yt }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nb,no,en". }
}"""


def _tls() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def query_batch(organisation_numbers: list[str], *, timeout: float = 60.0, retries: int = 3) -> tuple[list[dict[str, Any]] | None, str | None]:
    """(bindings, error). Honours Retry-After on HTTP 429 with a short, bounded back-off."""
    values = " ".join(f'"{org}"' for org in organisation_numbers)
    data = urllib.parse.urlencode({"query": QUERY % values, "format": "json"}).encode("utf-8")
    last_error = "request failed"
    for attempt in range(retries + 1):
        request = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_tls()) as response:
                return json.loads(response.read())["results"]["bindings"], None
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 429 and attempt < retries:
                time.sleep(min(float(exc.headers.get("Retry-After") or 10), 45))
                continue
            if exc.code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = type(exc).__name__
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None, last_error


def group_by_organisation(bindings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        grouped.setdefault(binding["o"]["value"], []).append(binding)
    return grouped


def retained_body(organisation_number: str, bindings: list[dict[str, Any]]) -> bytes:
    """The saved response for one company: exactly the rows Wikidata returned for it."""
    return json.dumps({"organisation_number": organisation_number, "bindings": bindings}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _values(bindings: list[dict[str, Any]], key: str) -> list[str]:
    seen: list[str] = []
    for binding in bindings:
        value = (binding.get(key) or {}).get("value")
        if value and value not in seen:
            seen.append(value)
    return seen


def summarize(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    """The facts, normalised. Social handles become profile URLs; everything else is as stated."""
    entity = _values(bindings, "i")[0]
    qid = entity.rsplit("/", 1)[-1]
    social: dict[str, str] = {}
    for key, platform, template in (
        ("tw", "x", "https://x.com/{}"), ("fb", "facebook", "https://facebook.com/{}"), ("li", "linkedin", "https://linkedin.com/company/{}"),
        ("ig", "instagram", "https://instagram.com/{}"), ("yt", "youtube", "https://youtube.com/channel/{}"),
    ):
        for handle in _values(bindings, key)[:1]:
            social[platform] = template.format(handle.strip("/@"))
    inception = (_values(bindings, "inc") or [None])[0]
    return {
        "qid": qid, "entity_url": f"https://www.wikidata.org/wiki/{qid}", "label": (_values(bindings, "iLabel") or [None])[0],
        "websites": _values(bindings, "site"), "inception": inception[:10] if inception else None, "social_links": social,
    }
