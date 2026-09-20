#!/usr/bin/env python3
"""Validate refresh/change-detection correctness against real production data, at
the scale of the actual 1,000-company submission -- not just the small offline
fixture `run_refresh_replay.py` checks.

We don't have two real time-separated snapshots of the same batch on hand (that
would mean re-fetching live data twice, days apart). But every profile in
`out/profiles.jsonl` already carries two genuinely independent points in time for
the registry identity fields: `evidence.registry` (Brreg's frozen bulk snapshot,
taken when the archive was downloaded) and `evidence.registry_live` (fetched live
during this run). Normalizing the bulk record the same way the live fetch already
is (via `normalize_entity`) and diffing the two through the exact same
`diff_datasets` the production refresh path uses is a real "before vs after" test
on real data at full scale -- not a synthetic fixture.

This also exercises what the fixture test can't: 1,000 real, differently-shaped
profiles running through `diff_profile` without crashing, real change volume and
field distribution, and idempotency on the real dataset rather than a 2-company one.
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.official import normalize_entity  # noqa: E402
from norway_company_agent.refresh import diff_datasets  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Fields genuinely sourced from the registry alone in production (name, legal
# form, employee count, latest filed year) -- these are fair game for a real
# "did the registry's own data change between the bulk snapshot and now" test.
# `website` is deliberately excluded: the real pipeline's top-level `website`
# field is not a registry passthrough -- it's decided by the identity-gate/crawl
# pipeline (a registry-listed homepage can be rejected if it fails verification),
# so diffing it against a raw-registry-only "previous" state would conflate two
# different things (source drift vs. our own gate's decision) rather than testing
# refresh correctness.
REGISTRY_ONLY_FIELDS = ("name", "legal_form", "employees", "latest_submitted_accounts")


def build_previous_snapshot(profiles: list[dict]) -> list[dict]:
    """Reconstruct what each profile looked like before the live registry refresh,
    using only data already on disk (the frozen bulk snapshot), normalized through
    the same function the live path uses so the comparison is apples-to-apples.
    """
    previous = []
    for profile in profiles:
        row = copy.deepcopy(profile)
        evidence = row.get("evidence") or {}
        bulk = evidence.get("registry") or {}
        if bulk.get("status") != "available":
            previous.append(row)  # nothing to roll back for this one; diff will show no registry.* changes
            continue
        normalized = normalize_entity(bulk.get("value"))
        for field in REGISTRY_ONLY_FIELDS:
            value = normalized.get(field)
            # The bulk CSV represents a blank cell as "" and every number as a str
            # (csv.DictReader), while the live JSON API returns real None/int types.
            # The real ingestion path already normalizes this (see
            # sampling.py::normalize_row's `int(x) if x else None`); match that here
            # so the diff isn't polluted by representation artifacts that would
            # never occur in the actual pipeline.
            if value == "":
                value = None
            elif field == "employees" and value is not None:
                value = int(value)
            if value is not None:
                row[field] = value
        evidence.pop("registry_live", None)  # so diff_profile's _evidence_for falls back to the bulk record
        row["evidence"] = evidence
        previous.append(row)
    return previous


def inject_known_changes(profiles: list[dict], *, every_nth: int = 20) -> tuple[list[dict], set[tuple[str, str]]]:
    """The real bulk-vs-live comparison above found zero genuine drift (the bulk
    snapshot and the live fetch happened too close together for BRREG's own data to
    have changed) -- so it proves no false positives, idempotency and no crashes at
    scale, but never actually exercises true-positive detection on this run.

    To test recall/precision on a real, non-synthetic company shape (not just the
    two hand-crafted companies in tests/fixtures/refresh-snapshots.json), take every
    Nth real profile and mutate one already-tracked field to a deliberately
    different, known value -- the same idea the offline fixture uses, but replayed
    across hundreds of real, structurally varied profiles instead of two.
    """
    mutated = copy.deepcopy(profiles)
    expected: set[tuple[str, str]] = set()
    for index, row in enumerate(mutated):
        if index % every_nth != 0:
            continue
        org = row["organisation_number"]
        current_employees = row.get("employees")
        row["employees"] = (current_employees or 0) + 7  # a value guaranteed different from whatever was there
        expected.add((org, "registry.employees"))
    return mutated, expected


def main() -> None:
    profiles = read_jsonl(ROOT / "out" / "profiles.jsonl")
    eligible = [p for p in profiles if (p.get("evidence") or {}).get("registry_live", {}).get("status") == "available"]

    previous = build_previous_snapshot(eligible)
    current = eligible

    started = time.monotonic()
    changes = diff_datasets(previous, current)
    elapsed_s = round(time.monotonic() - started, 3)

    by_field: dict[str, int] = {}
    evidence_incomplete = 0
    for change in changes:
        by_field[change["field"]] = by_field.get(change["field"], 0) + 1
        if not (change.get("source_url") and change.get("retrieved_at")):
            evidence_incomplete += 1

    # Idempotency at real scale: diffing the real current dataset against an exact
    # copy of itself must produce zero changes -- the same check run_refresh_replay.py
    # does on its 2-company fixture, here on the actual 1,000-company batch.
    idempotent_changes = diff_datasets(current, copy.deepcopy(current))

    # No-crash guarantee: run diff_profile-shaped comparisons across every profile,
    # not just the ones with registry_live, to confirm malformed/partial evidence
    # shapes elsewhere in the real batch don't raise.
    crash_count = 0
    for profile in profiles:
        try:
            diff_datasets([profile], [copy.deepcopy(profile)])
        except Exception as exc:  # noqa: BLE001 -- counting failures, not debugging one here
            crash_count += 1

    # True-positive detection pass: real company shapes, known injected changes.
    mutated, expected = inject_known_changes(profiles)
    injected_changes = diff_datasets(profiles, mutated)
    observed = {(c["organisation_number"], c["field"]) for c in injected_changes}
    true_positive = len(expected & observed)
    false_positive = len(observed - expected)
    false_negative = len(expected - observed)
    injection_precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 1.0
    injection_recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 1.0

    report = {
        "validator": "validate_refresh_at_scale_v1",
        "method": "diff real bulk-snapshot-derived state vs real registry_live-refreshed state, both from out/profiles.jsonl, through the production diff_datasets path",
        "total_profiles": len(profiles),
        "eligible_profiles": len(eligible),
        "detected_changes": len(changes),
        "changes_by_field": dict(sorted(by_field.items(), key=lambda kv: -kv[1])),
        "evidence_incomplete_changes": evidence_incomplete,
        "idempotent_rerun_changes": len(idempotent_changes),
        "idempotent": len(idempotent_changes) == 0,
        "single_profile_crash_count": crash_count,
        "runtime_seconds_for_full_diff": elapsed_s,
        "injected_change_test": {
            "description": "Real profile shapes (not the offline fixture's 2 companies) with a known, deliberately injected employees change every 20th company -- tests true-positive detection at scale, since the real bulk-vs-live comparison above found no genuine drift to detect.",
            "injected_count": len(expected),
            "detected_count": len(observed),
            "precision": round(injection_precision, 4),
            "recall": round(injection_recall, 4),
        },
    }
    output_path = ROOT / "out" / "refresh-at-scale-report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
