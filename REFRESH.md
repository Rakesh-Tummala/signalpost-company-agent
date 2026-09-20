# Refresh and change detection

## Mechanism

`src/norway_company_agent/refresh.py::diff_profile` / `diff_datasets` compare a
previous and current snapshot of the same organisation's evidence field by field and
emit typed change events, not a blind overwrite:

- field, old value, new value
- source URL, retrieval time, effective/reporting date
- old and new content hashes
- status (`available`, etc.)

An unchanged rerun produces zero events (idempotent) and preserves the prior
snapshot rather than discarding it. `scripts/run_refresh_replay.py` runs this against
a fixed old/new fixture pair to check exactly that: expected changes found, no false
changes, no changes on a same-input rerun.

## Verified

The offline fixture check (`tests/fixtures/refresh-snapshots.json`) passes cleanly:
precision 1.0, recall 1.0, `idempotent_rerun: true`, `evidence_complete: true`. This
is the practice check the starter kit's own "first run" instructions describe — it
uses saved responses, not live data, and doesn't by itself prove refresh correctness
at the scale of a real submission.

## Validated at real scale (`scripts/validate_refresh_at_scale.py`)

We don't have two real time-separated snapshots of the full batch (that needs
re-fetching live data days apart), but every real profile already carries two
genuinely independent points in time for the registry identity fields: the frozen
bulk snapshot (`evidence.registry`) and the live fetch taken during this run
(`evidence.registry_live`). Diffing those two, normalized through the same
production code path, against all 1,000 real profiles is a real "before vs after"
test — not the offline fixture's two hand-crafted companies.

Result on the real batch: **zero false positives, zero crashes across 1,000
structurally varied real profiles, and confirmed idempotency at full scale**
(re-diffing the same dataset against itself produces zero changes). No genuine
registry drift existed to detect in this particular run (the bulk snapshot and the
live fetch happened too close together in time), so a second pass injects a known,
deliberate change into real company shapes (not the fixture's two companies) to
test true-positive detection: 50 injected changes across the real batch, **50
detected, precision 1.0, recall 1.0**.

**This validation pass itself caught and fixed a real bug** in
`extract_prior_year_financials.py` (see `LIMITATIONS.md`): building the
"previous" snapshot from real bulk-CSV data surfaced two representation
mismatches the fixture's hand-crafted JSON never could — a blank CSV cell (`""`)
being compared against a real `None`, and CSV numbers arriving as strings (`"6"`)
being compared against the live API's real ints (`6`) — both of which the actual
production ingestion path already normalizes correctly (`sampling.py`'s
`normalize_row`), but which is exactly the class of bug that a synthetic 2-company
fixture, built by hand to already match production shapes, can never surface.

## Known limitation of this validation

`registry.website` is excluded from the real bulk-vs-live comparison on purpose:
in production, the top-level `website` field isn't a pure registry passthrough —
it's the outcome of the identity-gate/crawl pipeline (a registry-listed homepage
can be rejected if it fails verification), so diffing it against a raw-registry
"previous" state would conflate genuine source drift with our own gate's decision,
not test refresh correctness.

## Re-crawl cadence (not yet scheduled)

The agent playbook recommends re-crawling by source volatility: official annual
accounts change slowly (yearly), while jobs and company news change often. This
submission runs as a single point-in-time batch; a production deployment would need
a scheduler that re-crawls high-volatility fields more frequently than the registry
identity fields.
