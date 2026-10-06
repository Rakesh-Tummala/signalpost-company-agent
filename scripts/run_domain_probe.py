#!/usr/bin/env python3
"""Key-free website discovery: probe the company's registered e-mail domain and name-derived domains.

For each company with no website yet: take the domain of the e-mail address it registered with Brreg (unless it is a
mail provider) and likely hostnames from its legal name (`norway_company_agent.domain_probe`), fetch the ones that
resolve (robots.txt respected, public addresses only, bounded size, plus up to three contact, about or privacy pages),
and keep a site only if it shows this company's exact organisation number, or the phone number or e-mail address the
company registered (with its name in the hostname or title), or sits on the registered e-mail domain (same name
requirement), or shows the registered postcode and town. A kept site then gets the full independent crawl and the
normal identity gate; it is published only if the gate calls it exact. Needs no API key, so it runs before (and
spares) the paid search stages.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.contacts import identifier_proofs, own_email_domain, registered_contacts  # noqa: E402
from norway_company_agent.domain_probe import candidate_hosts, contact_links, place_on_page  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.identity import _name_in_hostname_or_title, _tokens, apply_website_identity_gate  # noqa: E402
from norway_company_agent.page_signals import _identifiers  # noqa: E402
from norway_company_agent.website import SAFE_OPENER, USER_AGENT, _robots_allowed, assert_public_url, fetch_website  # noqa: E402

MAX_BYTES = 1_000_000


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def has_website(row: dict) -> bool:
    return bool(row.get("website")) or ((row.get("evidence") or {}).get("website") or {}).get("status") == "available"


def search_name(row: dict) -> str | None:
    live = ((row.get("evidence") or {}).get("registry_live") or {}).get("value") or {}
    return row.get("name") or live.get("name")


def registered_place(row: dict) -> tuple[str | None, str | None]:
    address = (((row.get("evidence") or {}).get("registry_live") or {}).get("value") or {}).get("business_address") or {}
    bulk = ((row.get("evidence") or {}).get("registry") or {}).get("value") or {}
    return (address.get("postnummer") or bulk.get("forretningsadresse.postnummer"), address.get("poststed") or bulk.get("forretningsadresse.poststed"))


def _read_page(url: str, timeout: float) -> tuple[BeautifulSoup | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with SAFE_OPENER.open(request, timeout=timeout) as response:
        if "html" not in response.headers.get("content-type", "").lower():
            return None, None
        raw = response.read(MAX_BYTES + 1)
        final_url = response.geturl()
    if len(raw) > MAX_BYTES:
        return None, None
    return BeautifulSoup(raw.decode("utf-8", errors="replace"), "lxml"), final_url


def light_probe(host: str, organisation_number: str, timeout: float, place: tuple[str | None, str | None] = (None, None),
                contacts: dict | None = None, name: str | None = None) -> tuple[str | None, str | None, int]:
    """(url, proof, requests): proof says what ties `host` to this company, or None.

    Proofs, strongest first: "organisation_number" (the exact number on the homepage or a contact, about or privacy
    page), "registered_contact" (the phone number or e-mail address the company registered with Brreg, with its name
    in the hostname or title), "registry_email_domain" (the host is the registered e-mail domain, with the name in
    the hostname or title), "registered_place" (registered postcode and town on the homepage). The identity gate
    still decides afterwards.
    """
    url = f"https://{host}/"
    try:
        assert_public_url(url)  # also fails fast, with no HTTP request, for the many names that do not resolve
    except ValueError:
        return None, None, 0
    contacts = contacts or {"phones": set(), "email": "", "email_domain": ""}
    requests = 0
    try:
        requests += 1
        if not _robots_allowed(url, timeout):
            return None, None, requests
        requests += 1
        soup, final_url = _read_page(url, timeout)
        if soup is None:
            return None, None, requests
        # The page must still be on the candidate's own registered domain after redirects.
        final_host = urllib.parse.urlparse(final_url).hostname
        if final_host and not final_host.endswith(host.removeprefix("www.")):
            return None, None, requests
        text = soup.get_text(" ", strip=True)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        pages = [_identifiers(soup)]
        proofs = identifier_proofs(organisation_number, contacts, pages)
        if "organisation_number" not in proofs:
            links = contact_links(final_url, [(str(a.get("href")), a.get_text(" ", strip=True)) for a in soup.select("a[href]")], final_host or host)
            for link in links:
                try:
                    requests += 1
                    page, _ = _read_page(link, timeout)
                except Exception:  # noqa: BLE001 -- an unreachable secondary page is simply not read
                    continue
                if page is not None:
                    pages.append(_identifiers(page))
            proofs = identifier_proofs(organisation_number, contacts, pages)
        core = _tokens(name)
        named = _name_in_hostname_or_title(core, final_host or host, title)
        if "organisation_number" in proofs:
            return final_url, "organisation_number", requests
        if named and set(proofs) & {"registered_phone", "registered_email"}:
            return final_url, "registered_contact", requests
        registered_domain = own_email_domain(contacts.get("email_domain", ""))
        if named and registered_domain and (host == registered_domain or host.endswith("." + registered_domain)):
            return final_url, "registry_email_domain", requests
        if place_on_page(text, *place):
            return final_url, "registered_place", requests
        return None, None, requests
    except Exception:  # noqa: BLE001 -- an unreachable guess is simply not a hit
        return None, None, requests


def probe_row(row: dict, timeout: float, deadline: float | None) -> tuple[dict, dict, Counter]:
    counts: Counter[str] = Counter()
    org = str(row["organisation_number"])
    name = search_name(row)
    registry_row = ((row.get("evidence") or {}).get("registry") or {}).get("value") or {}
    contacts = registered_contacts(registry_row)
    candidates = candidate_hosts(name, registry_row)
    counts["candidates"] += len(candidates)
    for host in candidates:
        if deadline is not None and time.time() > deadline:
            counts["skipped_time_budget"] += 1
            break
        hit, proof, requests = light_probe(host, org, timeout, registered_place(row), contacts, name)
        counts["probe_requests"] += requests
        if not hit:
            continue
        counts[f"hits_by_{proof}"] += 1
        website, ops = fetch_website(hit, timeout=timeout)
        counts["crawl_requests"] += ops.get("requests", 0)
        profile = {**row, "name": name}
        gated = apply_website_identity_gate(profile, website)
        website, assessment = gated["website"], gated["assessment"]
        website["source_type"] = "domain_probe_company_website"
        publishable = bool(assessment and assessment["publishable"] and website.get("status") == "available")
        row.setdefault("evidence", {})["website_discovery"] = evidence(
            "website_discovery", "available" if publishable else "not_found", "domain_probe_then_independent_crawl", hit,
            value={"method": f"candidate_domain_proven_by_{proof}", "candidate_hosts": candidates, "independent_page_url": hit if publishable else None},
            note="Candidate came from the legal name or the registered e-mail domain; publication depends on the site showing the organisation number or the registered contact details and on the identity gate.",
        )
        row["evidence"]["website_discovered"] = website
        if publishable:
            row["evidence"]["website"] = website
            row["website"] = (website.get("value") or {}).get("final_url") or website.get("source_url")
            row["website_seed_source"] = "domain_probe"
            counts["verified_sites"] += 1
        else:
            counts["quarantined_sites"] += 1
        return row, {"host": host, "publishable": publishable, "proof": proof}, counts
    return row, {}, counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe name-derived domains; keep a site only if the page shows the exact organisation number.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--deadline-epoch", type=float, help="Unix time after which no further candidate is tried")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input))
    todo = [index for index, row in enumerate(rows) if not has_website(row) and search_name(row)]
    if args.limit:
        todo = todo[: args.limit]
    totals: Counter[str] = Counter()
    totals["input_profiles"] = len(rows)
    totals["probed_profiles"] = len(todo)
    started_at = utc_now()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe_row, rows[index], args.timeout, args.deadline_epoch): index for index in todo}
        for future in as_completed(futures):
            row, _, counts = future.result()
            rows[futures[future]] = row
            totals.update(counts)
    write_jsonl(Path(args.output), rows)
    report = {
        "connector": "domain_probe_v1", "started_at": started_at, "completed_at": utc_now(), "counts": dict(totals),
        "claim_boundary": "A site is published only if it shows the exact organisation number, or the phone number or e-mail address the company registered with Brreg (with its name in the hostname or title), or the registered postcode and town, AND the identity gate calls it exact; a name-derived domain alone is never evidence.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
