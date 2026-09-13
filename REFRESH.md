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

## Not yet done

We have not run a second full pass over our actual 1,000-company batch to confirm
change detection at that scale — see `LIMITATIONS.md`. Doing so before the entry
freezes would mean: snapshot the current `out/profiles.jsonl`, re-run
`run_agent.py` against the same organisation numbers on a later day, and confirm the
diff only reports genuine upstream changes (e.g. an updated annual account) with zero
false positives from re-fetching identical sources.

## Re-crawl cadence (not yet scheduled)

The agent playbook recommends re-crawling by source volatility: official annual
accounts change slowly (yearly), while jobs and company news change often. This
submission runs as a single point-in-time batch; a production deployment would need
a scheduler that re-crawls high-volatility fields more frequently than the registry
identity fields.
