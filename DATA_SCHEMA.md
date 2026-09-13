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
  "changes": [],
  "errors": [],
  "operations": {"requests": 5, "runtime_ms": null, "third_party_cost_usd": 0}
}
```

`availability` is one of `available`, `not_available`, `blocked`, `not_applicable`,
`ambiguous`, `failed` — never silently replaced with a zero or empty value.

Claim fields currently emitted: `legal_name`, `legal_form`, `employees`, `bankrupt`,
`liquidating`, `business_address`, `industry`, `latest_submitted_accounts`,
`accounting_obligation`, `annual_accounts.<year>` (one per filed year),
`role.<index>` (one per active role), `location.<index>` (one per registered
subunit), `group_structure`, `official_website`.

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
