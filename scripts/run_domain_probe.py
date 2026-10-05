#!/usr/bin/env python3
"""Key-free website discovery by probing name-derived domains, proven by organisation number.

For each company with no website yet: build likely hostnames from its legal name
(`norway_company_agent.domain_probe`), fetch the ones that resolve (robots.txt respected, public
addresses only, bounded size), and keep a site only if its page shows this company's exact
organisation number as a delimited number. A kept site then gets the full independent crawl and the
normal identity gate; it is published only if the gate calls it exact. Needs no API key, so it runs
before (and spares) the paid search stages.
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

from norway_company_agent.domain_probe import domain_candidates, org_number_on_page, place_on_page  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
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


def light_probe(host: str, organisation_number: str, timeout: float, place: tuple[str | None, str | None] = (None, None)) -> tuple[str | None, str | None, int]:
    """(url, proof, requests): proof is "organisation_number" or "registered_place" when `host` serves a page showing it."""
    url = f"https://{host}/"
    try:
        assert_public_url(url)  # also fails fast, with no HTTP request, for the many names that do not resolve
    except ValueError:
        return None, None, 0
    requests = 0
    try:
        requests += 1
        if not _robots_allowed(url, timeout):
            return None, None, requests
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        requests += 1
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            if "html" not in response.headers.get("content-type", "").lower():
                return None, None, requests
            raw = response.read(MAX_BYTES + 1)
            final_url = response.geturl()
        if len(raw) > MAX_BYTES:
            return None, None, requests
        # The page must still be on the candidate's own registered domain after redirects.
        final_host = urllib.parse.urlparse(final_url).hostname
        if final_host and not final_host.endswith(host.removeprefix("www.")):
            return None, None, requests
        text = BeautifulSoup(raw.decode("utf-8", errors="replace"), "lxml").get_text(" ", strip=True)
        if org_number_on_page(text, organisation_number):
            return final_url, "organisation_number", requests
        if place_on_page(text, *place):
            return final_url, "registered_place", requests
        return None, None, requests
    except Exception:  # noqa: BLE001 -- an unreachable guess is simply not a hit
        return None, None, requests


def probe_row(row: dict, timeout: float, deadline: float | None) -> tuple[dict, dict, Counter]:
    counts: Counter[str] = Counter()
    org = str(row["organisation_number"])
    name = search_name(row)
    candidates = domain_candidates(name)
    counts["candidates"] += len(candidates)
    for host in candidates:
        if deadline is not None and time.time() > deadline:
            counts["skipped_time_budget"] += 1
            break
        hit, proof, requests = light_probe(host, org, timeout, registered_place(row))
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
            value={"method": f"name_derived_domain_proven_by_{proof}", "candidate_hosts": candidates, "independent_page_url": hit if publishable else None},
            note="Candidate came from the legal name only; publication depends on the page showing the exact organisation number and on the identity gate.",
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
        "claim_boundary": "A site is published only if its page shows the exact organisation number (or, for a name-derived domain, the registered postcode and town) AND the identity gate calls it exact; a name-derived domain alone is never evidence.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
