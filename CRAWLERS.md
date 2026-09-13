# Crawlers and connectors

## Wired into `run_agent.py` (the single evaluator command)

| Stage | Script | Source | Budget/throttle |
|---|---|---|---|
| Registry identity + financials + roles + locations + group | `scripts/run_competition_batch.py` | Official BRREG bulk snapshot + live per-org API | 8 parallel workers, checkpoints every 25 |
| Website discovery (fallback 1) | `scripts/run_tavily_discovery.py` | Tavily Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `TAVILY_API_KEY` is set |
| Website discovery (fallback 2) | `scripts/run_exa_discovery.py` | Exa Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `EXA_API_KEY` is set |
| Claims/evidence conversion | `scripts/build_output_contract.py` | (reshapes existing evidence, no new fetches) | — |

Both discovery connectors follow the same rule: the search query and its raw results
(titles, snippets, ranks) are held in memory only and never written to disk or
published. Only an independently re-fetched and identity-gated page becomes evidence
for a claim. Neither connector is required — the agent produces a complete,
schema-valid submission with zero website-discovery claims if no API key is set,
just with lower coverage on that field.

## Not wired in (see LIMITATIONS.md for why)

- `scripts/run_scrapy_websites.py` — deeper multi-page company-site crawl; needs the
  `scrapy` package.
- `scripts/run_annual_report_workforce_connector.py` — OCR'd workforce counts from
  official annual-report PDFs; needs `tesseract` + `poppler` binaries.
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
