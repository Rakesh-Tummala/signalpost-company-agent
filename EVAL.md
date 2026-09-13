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
- `scripts/score_company_completeness.py` — run against our real 1,000-company batch
  with zero external-connector observations (`out/self-score-report.json`). Result:
  foundation (official-registry-derived fields) mean 29.98/30 — essentially maxed,
  since this component only checks that a module reached a terminal state, not that
  it found a positive result. Every external-enrichment dimension (jobs, social
  handles, reviews, sentiment, places) scored 0.0, confirming what LIMITATIONS.md
  already says plainly: those connectors aren't wired in. Important caveat — this
  scorer reads from the `run_company_control`/`external_footprint` observation
  pipeline, a different, older path than the `claims`/`evidence` output-contract
  format `build_output_contract.py` now produces. It does **not** reflect the
  83 social-profile claims added after this run, and it is **not** the same rubric
  Builderr actually scores against (its foundation/enrichment split and weights are
  the starter kit's own internal proxy, not the 35/30/20/10/5 official one). Useful
  as a directional signal that external recall is near-zero; not a preview of the
  real score.
- `scripts/evaluate_external_footprint.py` — audit gate for published external
  observations against evaluator-owned labels. **Not run** — we have no external
  observations to audit yet.

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
2. ~~Run `scripts/score_company_completeness.py`~~ — done, see above.
3. Track precision informally: of the 26+26 Tavily/Exa-discovered websites promoted
   into `evidence.website`, spot-check a sample against the actual company to
   confirm the identity gate's decisions hold up to a human check, not just its own
   internal logic.
