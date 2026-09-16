#!/usr/bin/env python3
"""Recover the prior-year comparative figures Norwegian annual reports print
alongside the current year, from PDFs already downloaded/OCR'd for the workforce
connector (see run_annual_report_workforce_connector.py's --cache directory).

We already have official BRREG figures for the *current* filed year via the
`financials` module. Norwegian annual-report notes conventionally print the prior
year's figure right next to the current year's -- e.g. "Sum driftsinntekter
4 182 614 4 678 118" is (current year) then (prior year). This does not re-fetch
or re-OCR anything: it only re-reads text already sitting in the workforce
connector's cache.

Safety: OCR'd digit groups separated by spaces are ambiguous on their own (is
"4 182 614 4 678 118" one four-part number, or two numbers of three and four
groups?). Rather than guess a split point, we anchor on the current-year value we
already know is correct from the official API, find it as an exact prefix of the
space-stripped digit sequence on that line, and only trust whatever's left over as
the prior-year figure. If the known value isn't found as a clean prefix, we abstain
for that field rather than guess.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from pathlib import Path

FIELD_LABELS = (
    ("revenue", re.compile(r"(?i)sum\s+driftsinntekter\s+([-0-9O ,.]+)")),
    ("operating_result", re.compile(r"(?i)driftsresultat\s+([-0-9O ,.]+)")),
    ("annual_result", re.compile(r"(?i)[aå]rsresultat\s+([-0-9O ,.]+)")),
    ("assets", re.compile(r"(?i)sum\s+eiendeler\s+([-0-9O ,.]+)")),
    ("equity", re.compile(r"(?i)sum\s+egenkapital\s+([-0-9O ,.]+)")),
    ("debt", re.compile(r"(?i)sum\s+gjeld\s+([-0-9O ,.]+)")),
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def known_value_string(value) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == int(number):
        return str(int(number))
    return None  # non-integer current-year figures aren't expected from this API; skip rather than guess a format


def find_prior_year_value(span: str, known_current: str) -> float | None:
    # Keep digits, internal spaces (thousands separators) and minus signs in their
    # original relative order; drop everything else. Then strip spaces only -- a
    # minus sign is real signal, a thousands-separator space is not.
    cleaned = re.sub(r"[^0-9O\-]", "", span.replace(" ", ""))
    cleaned = cleaned.replace("O", "0")
    if not cleaned.startswith(known_current):
        # A leading "-" on the whole span belongs to the current year's figure
        # only if the known value itself is negative; already covered since
        # known_current carries its own sign.
        return None
    remainder = cleaned[len(known_current):]
    if not remainder or remainder in {"-"}:
        return None
    try:
        return float(remainder)
    except ValueError:
        return None


def extract_prior_year(text: str, known_record: dict) -> dict:
    found: dict[str, float] = {}
    for field, pattern in FIELD_LABELS:
        known = known_value_string(known_record.get(field))
        if known is None:
            continue
        match = pattern.search(text)
        if not match:
            continue
        value = find_prior_year_value(match.group(1), known)
        if value is not None:
            found[field] = value
    return found


def prior_year_label(record: dict) -> str | None:
    period = record.get("period") or {}
    year = str(period.get("tilDato") or "")[:4]
    if not year.isdigit():
        return None
    return str(int(year) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover prior-year financial figures from already-cached annual-report OCR text.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--cache", required=True, help="Same --cache dir used by run_annual_report_workforce_connector.py")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    observations = []
    eligible = 0
    accepted = 0
    for profile in profiles:
        financials = (profile.get("evidence") or {}).get("financials") or {}
        records = (financials.get("value") or {}).get("records") or []
        if financials.get("status") != "available" or not records:
            continue
        current_record = max(records, key=lambda item: (item.get("period") or {}).get("tilDato") or "")
        label = prior_year_label(current_record)
        if not label:
            continue
        org = str(profile["organisation_number"])
        cache_hits = sorted(glob.glob(f"{args.cache}/{org}-*-ocr-*.txt")) or sorted(glob.glob(f"{args.cache}/{org}-*.pdf"))
        if not cache_hits:
            continue
        eligible += 1
        text_path = Path(cache_hits[0]) if cache_hits[0].endswith(".txt") else None
        if text_path is None:
            continue  # digital-text-only PDFs weren't cached as .txt; out of scope for this cache-reuse pass
        text = text_path.read_text(encoding="utf-8", errors="replace")
        found = extract_prior_year(text, current_record)
        if not found:
            continue
        accepted += 1
        observations.append({
            "id": "prior-year-financials-" + hashlib.sha256(f"{org}|{label}".encode()).hexdigest()[:24],
            "organisation_number": org,
            "platform": "brreg",
            "signal_type": "prior_year_financials",
            "source_url": financials.get("source_url") or f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}",
            "retrieved_at": financials.get("retrieved_at"),
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "exact_entity": True,
            "identity_proof": [{"type": "official_report_url_organisation_number", "value": org}],
            "acquisition_mode": "official_api",
            "rights_status": "approved",
            "source_class": "official_annual_account_copy",
            "evidence_span": f"Prior-year ({label}) figures cross-checked against the known current-year value on the same line.",
            "effective_at": label,
            "metrics": {**found, "year": label},
            "strategy": "prior_year_financials_cross_check",
        })

    write_jsonl(Path(args.output), observations)
    report = {
        "connector": "prior_year_financials_v1",
        "profiles": len(profiles),
        "eligible": eligible,
        "accepted": accepted,
        "observations": len(observations),
        "claim_boundary": "Only fields where the known current-year value was found as an exact prefix of the OCR'd digit sequence; the remainder is trusted as the prior year. Abstains rather than guesses a split point.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
