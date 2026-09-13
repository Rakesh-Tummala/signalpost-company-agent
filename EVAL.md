# Evaluation

## What exists

- `scripts/run_refresh_replay.py` — offline fixture check for refresh correctness
  (precision/recall against two known snapshots). Passes: see `REFRESH.md`.
- `scripts/run_competition_batch.py`'s own `validate_envelopes` — structural
  self-check on every batch run: exact expected count, unique organisation numbers,
  all entity/module states terminal, zero silent drops. Passes on the full
  1,000-company run: see `out/run-report.json`.
- `scripts/build_output_contract.py`'s own validation (run manually, not yet
  automated into a test): every claim's `evidence_ids` resolves, every
  `availability` value is one of the six allowed states. Passes on all 1,000
  profiles, 16,228 claims, zero violations.
- `tests/test_poc.py` — 107 unit tests, 5 subtests, covering identity-gate edge
  cases (parent/subsidiary confusion, generic name collisions, parked domains),
  the missing-from-bulk-registry path, and the registry_live backfill.
- `scripts/score_company_completeness.py` and
  `scripts/evaluate_external_footprint.py` — the starter kit's own scoring/audit
  scripts. **Not yet run against our real output** (see below).

## What's missing

No held-out gold corpus of hand-labelled companies exists in this repo yet, so we
have no independent estimate of our own coverage/precision/recall against the
challenge's actual rubric before submission — only the structural and offline
checks above. This is the single biggest evaluation gap: we know our pipeline is
internally consistent and doesn't crash or fabricate, but not what score it would
actually receive.

## Before submitting, worth doing

1. Hand-label a small sample (20-50 companies) from `entry-companies.jsonl` for
   exact-identity correctness on the discovered websites, since that's the one
   field with real false-positive risk (registry-derived fields are inherently
   correct — they're sourced straight from the government record).
2. Run `scripts/score_company_completeness.py` against `out/profiles.jsonl` to get
   a self-reported completeness number, understanding it measures our own claimed
   coverage, not the evaluator's independently-verified collection.
3. Track precision informally: of the 26+26 Tavily/Exa-discovered websites promoted
   into `evidence.website`, spot-check a sample against the actual company to
   confirm the identity gate's decisions hold up to a human check, not just its own
   internal logic.
