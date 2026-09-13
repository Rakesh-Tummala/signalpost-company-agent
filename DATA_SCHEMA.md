# Data schema

## Submission envelope (`out/output-contract-envelopes.jsonl`)

One JSON object per organisation number, produced by `scripts/build_output_contract.py`
from the pipeline's internal profile format. This matches `OUTPUT_CONTRACT.md`:

```json
{
  "organisation_number": "923609016",
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
  "claims": [
    {"field": "legal_name", "value": "Example AS", "availability": "available", "confidence": 1.0, "evidence_ids": ["ev-registry_live"]}
  ],
  "evidence": [
    {"id": "ev-registry_live", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016", "source_class": "official_registry_live", "retrieved_at": "...", "content_sha256": "...", "claim_span": null}
  ],
  "summary": {"text": "Example AS is an AS...", "unknown_fields": ["group/ownership structure"], "grounded_in_claims": true},
  "changes": [],
  "errors": [],
  "operations": {"requests": 5, "runtime_ms": null, "third_party_cost_usd": 0}
}
```

`availability` is one of `available`, `not_available`, `blocked`, `not_applicable`,
`ambiguous`, `failed` — never silently replaced with a zero or empty value. `summary`
is additive beyond OUTPUT_CONTRACT.md's minimal example — see AGENT.md for how it's
built (template over already-published claims, never a model's own synthesis).

Claim fields currently emitted:
- From the official registry pipeline: `legal_name`, `legal_form`, `employees`,
  `bankrupt`, `liquidating`, `business_address`, `industry`,
  `latest_submitted_accounts`, `accounting_obligation`, `annual_accounts.<year>`
  (one per filed year the `financials` module actually returned figures for --
  typically just the latest; see `annual_accounts_years_on_file` below for the
  fuller list), `annual_accounts_years_on_file` (every year with a filing on
  record, from `financial_history` -- a list of years, not full figures for each),
  `role.<index>` (one per active role), `location.<index>` (one per registered
  subunit), `group_structure`.
- From website discovery/crawl: `official_website`, `social_profile.<platform>`
  (one per verified social link found on the crawled site).
- From the deep-crawl-derived observation files (`--observations`, wired in by
  `run_agent.py`): `site_activity_metrics` (one per company, from
  `extract_company_site_activity.py`), `site_news.<index>` (one per dated
  company-owned press/news item, from `extract_company_site_news.py`),
  `workforce_value.<year>` (from the OCR annual-report connector -- see
  `CRAWLERS.md`). These claims each carry their own evidence entry (a real,
  independently-verified source fetch) rather than sharing one per module.

## Internal pipeline profile (`out/profiles.jsonl`)

The working format the pipeline (`run_competition_batch.py`, discovery connectors)
reads and writes. One row per company:

```json
{
  "organisation_number": "923609016",
  "name": "...", "legal_form": "...", "employees": 0, "bankrupt": false, "liquidating": false,
  "municipality": "...", "industry_code": "...", "website": "",
  "evidence": {
    "registry": {"field": "registry", "status": "available", "source_type": "official_registry_bulk", "source_url": "...", "retrieved_at": "...", "content_sha256": "...", "value": {...}},
    "registry_live": {...}, "financials": {...}, "roles": {...}, "locations": {...},
    "group": {...}, "website": {...}, "website_discovery": {...}, "accounting_obligation": {...}
  }
}
```

Every `evidence.<module>` entry follows the same `Evidence` shape
(`src/norway_company_agent/evidence.py`): `field`, `status`, `source_type`,
`source_class`, `source_url`, `retrieved_at`, `value`, `as_of`, `note`,
`content_sha256`, `source_row_key`, `effective_at`. This is the format
`build_output_contract.py` decomposes into the flatter claims/evidence shape above.

## Internal terminal envelope (`out/envelopes.jsonl`)

A second, module-state-keyed envelope (`src/norway_company_agent/batch.py::terminal_envelope`)
used by the pipeline's own validation checks (`validate_envelopes`): exact expected
count, unique organisation numbers, all entity/module states terminal, zero silent
drops. This is not the submission format — it's what
`scripts/run_competition_batch.py` uses to self-check a batch before handing off to
`build_output_contract.py`.
