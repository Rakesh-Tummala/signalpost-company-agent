#!/usr/bin/env python3
"""Re-run the identity gate over already-published website evidence.

Run after every tightening of assess_website_identity (first the single-token-name
branch, then the multi-word-name branch: "Blue Bay" on an Italian resort site).
Re-uses the already-crawled page content cached in evidence.website /
evidence.website_discovered -- no new network requests, no new API spend.

Applied uniformly regardless of whether the URL originally came from the BRREG
registry field or search-based discovery: the deep-crawl merge already collapsed
both into evidence.website under one source_type, and per the source policy's own
stated preference, a demotion on ambiguous evidence is the safer failure mode
either way.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def was_published(record: dict) -> bool:
    return bool(((record.get("value") or {}).get("identity_assessment") or {}).get("publishable"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    demoted = []
    for profile in profiles:
        evidence = profile.setdefault("evidence", {})
        for module in ("website", "website_discovered"):
            record = evidence.get(module)
            if not record or record.get("status") != "available" or not was_published(record):
                continue
            gated = apply_website_identity_gate(profile, dict(record))
            assessment = gated["assessment"]
            if assessment and assessment["publishable"]:
                continue  # re-assessed, still holds (e.g. it does end in .no after all)
            demoted.append({
                "organisation_number": profile["organisation_number"],
                "module": module,
                "url": record.get("source_url"),
                "old_score": (record.get("value") or {}).get("identity_assessment", {}).get("score"),
                "new_score": assessment["score"] if assessment else None,
            })
            demoted_record = dict(record)
            demoted_record["status"] = "not_found"
            demoted_record["note"] = "Demoted on identity-gate re-assessment: " + "; ".join((assessment or {}).get("reasons") or ["no longer publishable"])
            evidence[module] = demoted_record
            if module == "website":
                profile["website"] = ""
    write_jsonl(Path(args.output), profiles)
    print(json.dumps({"profiles": len(profiles), "demoted": len(demoted)}, indent=2))
    for item in demoted:
        print(json.dumps(item))


if __name__ == "__main__":
    main()
