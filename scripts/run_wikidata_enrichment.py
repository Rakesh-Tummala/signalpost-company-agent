#!/usr/bin/env python3
"""Attach Wikidata facts (matched by organisation number) to each profile.

One batched query per 150 companies (so 1 request for a 100-company run). Every profile gets an
explicit `evidence.wikidata` record: `available` (an item exists), `not_found`, or `source_error`.
A website Wikidata attributes to the entity is crawled and run through the normal identity gate for
companies that have no verified site yet, treating Wikidata's listing as an independent anchor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.evidence_spans import module_spans  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.snapshot_store import save_snapshot  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402
from norway_company_agent import wikidata  # noqa: E402

SOURCE_URL = "https://www.wikidata.org/wiki/Special:Search?search=haswbstatement:P2333="


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def record_for(organisation_number: str, bindings: list[dict] | None, error: str | None, retrieved_at: str) -> dict:
    if error is not None:
        return evidence("wikidata", "source_error", "wikidata_sparql", wikidata.ENDPOINT, note=error, retrieved_at=retrieved_at)
    if not bindings:
        return evidence("wikidata", "not_found", "wikidata_sparql", SOURCE_URL + organisation_number, retrieved_at=retrieved_at,
                        note="No Wikidata item carries this organisation number (P2333).")
    summary = wikidata.summarize(bindings)
    body = wikidata.retained_body(organisation_number, bindings)
    return evidence(
        "wikidata", "available", "wikidata_entity", summary["entity_url"], value=summary, retrieved_at=retrieved_at,
        content_sha256=hashlib.sha256(body).hexdigest(), snapshot_path=save_snapshot(body, "json"),
        spans=module_spans("wikidata", body, None), extraction_method="wikidata_sparql_p2333",
        note="Wikidata (CC0), matched exactly by organisation number P2333; the retained response is the rows returned for this company.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach Wikidata facts, matched by organisation number, to each profile.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-site-promotion", action="store_true", help="Record facts only; do not crawl a Wikidata-listed website")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input))
    orgs = [str(row["organisation_number"]) for row in rows]
    retrieved_at = utc_now()
    results: dict[str, tuple[list[dict] | None, str | None]] = {}
    for start in range(0, len(orgs), wikidata.BATCH):
        chunk = orgs[start:start + wikidata.BATCH]
        bindings, error = wikidata.query_batch(chunk)
        grouped = wikidata.group_by_organisation(bindings or [])
        for org in chunk:
            results[org] = (grouped.get(org), error)

    counts = {"profiles": len(rows), "available": 0, "not_found": 0, "source_error": 0, "websites_listed": 0, "sites_promoted": 0, "crawl_requests": 0}
    for row in rows:
        org = str(row["organisation_number"])
        bindings, error = results[org]
        record = record_for(org, bindings, error, retrieved_at)
        row.setdefault("evidence", {})["wikidata"] = record
        counts[record["status"]] += 1
        if record["status"] != "available":
            continue
        sites = record["value"]["websites"]
        counts["websites_listed"] += bool(sites)
        already = (row.get("evidence", {}).get("website") or {})
        if args.no_site_promotion or not sites or (already.get("status") == "available" and (already.get("value") or {}).get("identity_assessment", {}).get("publishable")):
            continue
        website, ops = fetch_website(sites[0])
        counts["crawl_requests"] += ops.get("requests", 0)
        gated = apply_website_identity_gate(row, website)
        website, assessment = gated["website"], gated["assessment"]
        if assessment and assessment["publishable"] and website.get("status") == "available":
            website["source_type"] = "wikidata_listed_company_website"
            row["evidence"]["website"] = website
            row["website"] = (website.get("value") or {}).get("final_url") or website.get("source_url")
            row["website_seed_source"] = "wikidata"
            counts["sites_promoted"] += 1
    write_jsonl(Path(args.output), rows)
    report = {"connector": "wikidata_p2333_v1", "completed_at": utc_now(), "counts": counts,
              "claim_boundary": "Exact entity by organisation number (P2333); a Wikidata-listed site is published only after an independent crawl and the identity gate."}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
