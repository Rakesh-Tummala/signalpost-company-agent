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
  profiles, 19,048 claims, zero violations, zero per-profile conversion failures
  (the last one guarded by `build_envelopes_safe`, see its own commit).
- `scripts/validate_refresh_at_scale.py` — real-scale refresh validation (not just
  the offline fixture): zero false positives and zero crashes across all 1,000 real
  profiles, confirmed idempotency at scale, 50/50 injected changes on real company
  shapes correctly detected (precision 1.0, recall 1.0). See `REFRESH.md`.
- `tests/test_poc.py` — 133 unit tests, 5 subtests, covering identity-gate edge
  cases (parent/subsidiary confusion, generic name collisions, parked domains),
  the missing-from-bulk-registry path, the registry_live backfill, the
  claims/evidence conversion (shape, grounding, malformed-profile isolation), the
  workforce-observation-to-claim path, the prior-year-financials recovery logic
  (including the real multi-column housing-cooperative bug found and fixed —
  see `LIMITATIONS.md`), the real-scale refresh validation, and the offline viewer.
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
- **Real per-field coverage from the actual final claims artifact** (not the older
  proxy scorer above) — see the table in `LIMITATIONS.md`. This is the more relevant
  number: computed directly from `out/output-contract-envelopes.jsonl`, the exact
  file that would be submitted, and after the identity-gate precision fix demoted 24
  wrong-company matches (also in `LIMITATIONS.md`). Official-registry fields are
  ~100%; website 12.3%; workforce size 84.1% (via OCR); prior-year annual accounts
  82.3% (also via OCR, recovered from the same cached report text -- no new
  fetches); social profiles 3.5%; company-owned news 1.6%; group structure 7.0%.
  This tells us *our own* coverage, not how it compares to Builderr's
  independently-verified collection or the other entrants' pooled findings, which is
  what the real 35-point coverage score is measured against.

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
3. ~~Track precision informally~~ — done, and it found a real bug: spot-checking ~8
   of the 62 discovered-website matches by hand caught several clear wrong-company
   matches (a US market-research firm, an Italian resort, a personal website). Traced
   to a specific over-permissive branch in `assess_website_identity`, fixed, and
   re-applied retroactively — see `LIMITATIONS.md`'s "identity-gate precision fix"
   section for the full accounting (24 organisation numbers demoted). This is the
   single most valuable check in this list; item 1 below would have caught the same
   issue with a larger, more systematic sample.
4. Item 1 (hand-labelling 20-50 companies) is still worth doing before the actual
   submission, now specifically to check whether the `.no`-domain requirement from
   the fix above is too strict (rejecting real companies on non-`.no` domains) or
   still too loose (any remaining false positives the small ad-hoc spot-check above
   didn't happen to sample).
