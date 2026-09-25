#!/usr/bin/env python3
"""Audit that every published claim is backed by an exact saved source.

For each claim in a claims/evidence artifact this checks that:
  * each evidence id resolves;
  * the saved snapshot exists and its SHA-256 equals the evidence `content_sha256`;
  * the `claim_span` is a literal substring of the snapshot text.

PDF snapshots hold compressed page streams, so an excerpt read from a PDF (OCR or
text-layer extraction) cannot be matched against its bytes; those are counted
separately as `span_from_pdf` rather than passed or failed.

Exit status is non-zero if any hash mismatches or any excerpt is not a literal
slice of its (text) snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

TEXT_SUFFIXES = {".json", ".html", ".xml", ".txt"}


def audit(envelopes: list[dict], root: Path) -> dict:
    counts: Counter[str] = Counter()
    failures: list[dict] = []
    cache: dict[str, tuple[bytes | None, str | None]] = {}
    by_field: dict[str, Counter[str]] = {}

    def load(path: str) -> tuple[bytes | None, str | None]:
        if path not in cache:
            file = root / path
            raw = file.read_bytes() if file.exists() else None
            text = raw.decode("utf-8", errors="replace") if raw is not None and file.suffix in TEXT_SUFFIXES else None
            cache[path] = (raw, text)
        return cache[path]

    for envelope in envelopes:
        evidence_by_id = {item["id"]: item for item in envelope.get("evidence", [])}
        for claim in envelope.get("claims", []):
            counts["claims"] += 1
            family = str(claim["field"]).split(".")[0]
            tally = by_field.setdefault(family, Counter())
            tally["claims"] += 1
            if claim.get("availability") != "available":
                counts["not_available_claims"] += 1
                continue
            cited = [evidence_by_id.get(eid) for eid in claim.get("evidence_ids", [])]
            if not cited or any(item is None for item in cited):
                counts["orphaned_evidence"] += 1
                failures.append({"org": envelope["organisation_number"], "field": claim["field"], "problem": "evidence id does not resolve"})
                continue
            item = cited[0]
            span, snapshot = item.get("claim_span"), item.get("snapshot")
            if not span:
                counts["no_span"] += 1
                tally["no_span"] += 1
            if not snapshot:
                counts["no_snapshot"] += 1
                tally["no_snapshot"] += 1
                continue
            raw, text = load(snapshot)
            if raw is None:
                counts["snapshot_missing"] += 1
                failures.append({"org": envelope["organisation_number"], "field": claim["field"], "problem": f"snapshot file missing: {snapshot}"})
                continue
            if hashlib.sha256(raw).hexdigest() != item.get("content_sha256"):
                counts["hash_mismatch"] += 1
                failures.append({"org": envelope["organisation_number"], "field": claim["field"], "problem": f"snapshot hash != content_sha256: {snapshot}"})
                continue
            counts["snapshot_hash_ok"] += 1
            tally["snapshot_hash_ok"] += 1
            if not span:
                continue
            if text is None:
                counts["span_from_pdf"] += 1
                tally["span_from_pdf"] += 1
            elif span in text:
                counts["span_exact"] += 1
                tally["span_exact"] += 1
            else:
                counts["span_not_in_snapshot"] += 1
                failures.append({"org": envelope["organisation_number"], "field": claim["field"], "problem": "claim_span is not a literal slice of the snapshot", "span": span[:120]})
    return {"summary": dict(counts), "by_field_family": {key: dict(value) for key, value in sorted(by_field.items())}, "failures": failures[:50], "failure_count": len(failures)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit claims against their saved sources.")
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--root", required=True, help="Output directory the snapshot paths are relative to")
    parser.add_argument("--report")
    args = parser.parse_args()
    envelopes = [json.loads(line) for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = audit(envelopes, Path(args.root))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    sys.exit(1 if report["failure_count"] else 0)


if __name__ == "__main__":
    main()
