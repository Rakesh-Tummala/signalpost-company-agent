# Crawlers and connectors

## Wired into `run_agent.py` (the single evaluator command)

| Stage | Script | Source | Budget/throttle |
|---|---|---|---|
| Registry identity + financials + financial_history + roles + locations + group | `scripts/run_competition_batch.py` | Official BRREG bulk snapshot + live per-org API | 8 parallel workers, checkpoints every 25. `financial_history` is rate-limited server-side to ~30 request-starts/minute (`_reserve_history_slot` in `official.py`) |
| Website discovery (fallback 1) | `scripts/run_tavily_discovery.py` | Tavily Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `TAVILY_API_KEY` is set |
| Website discovery (fallback 2) | `scripts/run_exa_discovery.py` | Exa Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `EXA_API_KEY` is set |
| Deep multi-page site crawl | `scripts/run_scrapy_websites.py` | Registry-linked or discovered company website | 8 concurrent requests, 2/domain; best-effort -- skipped cleanly if `scrapy` isn't installed |
| Company-owned activity extraction | `scripts/extract_company_site_activity.py` | Pages already fetched by the deep crawl above (no new requests) | — |
| Company-owned news extraction | `scripts/extract_company_site_news.py` | Pages already fetched by the deep crawl above (no new requests) | — |
| OCR workforce extraction | `scripts/run_annual_report_workforce_connector.py` | Official BRREG annual-report PDF copies | 4 workers; needs `tesseract` (language pack `nor`, not the default `eng`) + `poppler` (`pdftoppm`) on PATH; degrades per-company to an error status (not a pipeline failure) if either binary is missing |
| Claims/evidence conversion | `scripts/build_output_contract.py` | (reshapes existing evidence + observation files, no new fetches) | — |

Discovery connectors follow the same rule: the search query and its raw results
(titles, snippets, ranks) are held in memory only and never written to disk or
published. Only an independently re-fetched and identity-gated page becomes evidence
for a claim. Every stage after the registry batch is best-effort in `run_agent.py` --
a missing API key, a missing `scrapy` install, or missing OCR binaries each skip only
that stage rather than failing the whole run. The agent always produces a complete,
schema-valid submission; what varies is how much external coverage it can add on top
of the official-registry foundation.

## Not wired in (see LIMITATIONS.md for why)

- `scripts/run_linkedin_guest_jobs_connector.py`,
  `scripts/discover_linkedin_company_profiles.py`,
  `scripts/run_linkedin_guest_experiment.py` — unofficial LinkedIn endpoints, self-tagged
  not publishable.
- `scripts/run_google_news_rss_connector.py`,
  `scripts/run_fagfolkguiden_reviews_connector.py`,
  `scripts/run_youtube_search_connector.py`,
  `scripts/normalize_google_maps_results.py` — all self-tagged
  `rights_review_experiment` / `unofficial_api_experiment`.
- `scripts/run_brave_discovery.py` — a third search-discovery fallback, same safe
  pattern as Tavily/Exa; not wired into `run_agent.py` only because it needs a paid
  key we don't currently hold. Would be a straightforward third fallback to add.
- `scripts/run_sentiment_model.py` — local HF model over collected snippets; no gold
  corpus to evaluate it against yet, and no independent public-text sources feed it
  today anyway.

## Fallback order

`run_agent.py` tries Tavily first, then Exa, for any company still missing a website
after the previous stage — each connector's own skip logic checks
`evidence.website.status == "available"`, so a company Tavily already resolved is not
re-queried by Exa. See the `run_tavily_discovery.py` / `run_exa_discovery.py` commit
history for why: an earlier version only checked the registry-provided website field
and would have wastefully re-queried already-discovered sites.
