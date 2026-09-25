# Data schema

## Submission envelope (`out/output-contract-envelopes.jsonl`)

One JSON object per organisation number, produced by `scripts/build_output_contract.py`
from the pipeline's internal profile format. This matches `OUTPUT_CONTRACT.md`:

```json
{
  "organisation_number": "923609016",
  "run": {"run_id": "...", "started_at": "...", "completed_at": "...", "terminal_status": "completed"},
  "claims": [
    {"field": "legal_name", "value": "Example AS", "availability": "available", "confidence": 1.0, "evidence_ids": ["ev-registry_live-1"]}
  ],
  "evidence": [
    {"id": "ev-registry_live-1", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016", "source_class": "official_registry_live", "retrieved_at": "...", "content_sha256": "...", "claim_span": "\"navn\":\"Example AS\"", "snapshot": "snapshots/<sha256>.json", "extraction_method": "official_api_json"}
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
- From the crawl-derived observation files (`--observations`, wired in by
  `run_agent.py`): `site_news.<index>` (one per **dated** company-owned news item --
  value `{headline, published_at, url}` -- from `extract_company_site_news.py`; an
  undated news page is not published), `job_posting.<index>` (one per real role on the
  company's own site -- value `{title, url, date_posted, employment_type, evidence_kind}`
  where `evidence_kind` is `json_ld_jobposting`, `role_card` or `apply_action` -- from
  `extract_company_site_jobs.py`; a generic careers page is not a hiring fact),
  `workforce_value.<year>` (from the OCR
  annual-report connector -- see `CRAWLERS.md`), `annual_accounts.<year>` for a
  *prior* filed year (from `extract_prior_year_financials.py` -- see
  `CRAWLERS.md`; reuses the same claim-field convention as the structured
  `financials` module above since it's the same kind of fact, just recovered from
  the comparative figures printed alongside the current year in the same official
  annual-report copy rather than returned by the API call). These claims each
  carry their own evidence entry (a real, independently-verified source fetch)
  rather than sharing one per module.

## Saved sources and exact excerpts

Every claim gets **its own evidence entry** (`ev-<source>-<n>`), so each can carry the exact
excerpt that supports it:

- `snapshot` -- path (relative to the output directory) of the saved raw body:
  `snapshots/<sha256>.<json|html|xml|pdf>`, named by the SHA-256 of its exact bytes, so
  `content_sha256` always equals the hash of that file.
- `claim_span` -- a *literal slice* of that saved body, never a paraphrase: the JSON
  `"key":value` for official API facts, the `<time>`/`<meta>`/anchor element or the
  JSON-LD pair for company-site facts, the feed `<item>` for feed articles. For annual-report
  facts read from a PDF (OCR or text layer) it is the matched report line; a PDF's bytes cannot
  be searched for that text, so the audit reports those separately.
- `extraction_method` -- how the value was read (`official_api_json`, `company_page_html`,
  `json_ld_jobposting`, `role_card`, `feed_item`, `annual_report_pdf_text_or_ocr`, ...).

Role-holder excerpts cover only the person's *name object*; the surrounding registry JSON carries
a date of birth, which this project never stores or republishes. `accounting_obligation`
is our rule applied to registry facts, so it cites the registry response (the
`sisteInnsendteAarsregnskap` or `organisasjonsform` field it was applied to) and records the rule
version as its `extraction_method`. `python scripts/audit_evidence.py --envelopes <file> --root <output-dir>`
re-checks all of this and exits non-zero on any hash mismatch or non-literal excerpt.

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

## Offline viewer (`out/viewer.html`)

`scripts/build_viewer.py` reads `output-contract-envelopes.jsonl` and produces a
single, self-contained HTML file — no build step, no external dependencies, no
local server required (it opens directly from disk). Each company's claims,
evidence source links, and grounded summary are searchable by organisation number
or name. This exists purely to make the submitted data browsable and verifiable by
a person, not to add or change any fact — it reads the final artifact, it doesn't
produce one.
