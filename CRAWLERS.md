# Crawlers and connectors

## Wired into `run_agent.py` (the single evaluator command)

| Stage | Script | Source | Budget/throttle |
|---|---|---|---|
| Registry identity + financials + financial_history + roles + locations + group | `scripts/run_competition_batch.py` | Official BRREG bulk snapshot + live per-org API | 8 parallel workers, checkpoints every 25. `financial_history` is rate-limited server-side to ~30 request-starts/minute (`_reserve_history_slot` in `official.py`) |
| Website discovery (fallback 1) | `scripts/run_tavily_discovery.py` | Tavily Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `TAVILY_API_KEY` is set |
| Website discovery (fallback 2) | `scripts/run_exa_discovery.py` | Exa Search API, transient query only | 0.3s min interval, checkpoints every 25, only runs if `EXA_API_KEY` is set |
| Deep multi-page site crawl | `scripts/run_scrapy_websites.py` | Registry-linked or discovered company website | 8 concurrent requests, 2/domain; best-effort -- skipped cleanly if `scrapy` isn't installed |
| Dated company news | `scripts/extract_company_site_news.py` | Page signals and RSS/Atom feeds already fetched by the crawl (no new requests): JSON-LD `NewsArticle`/`BlogPosting`, `<time datetime>` cards, the site's own feed items. Only items with a parseable date | — |
| Real job postings | `scripts/extract_company_site_jobs.py` | Page signals already fetched by the crawl (no new requests): JSON-LD `JobPosting`, role cards linking to an individual posting, explicit apply actions. A generic "Careers" page is **not** a hiring fact | — |
| OCR workforce extraction | `scripts/run_annual_report_workforce_connector.py` | Official BRREG annual-report PDF copies | 4 workers; needs `tesseract` (language pack `nor`, not the default `eng`) + `poppler` (`pdftoppm`) on PATH; degrades per-company to an error status (not a pipeline failure) if either binary is missing |
| Prior-year financials recovery | `scripts/extract_prior_year_financials.py` | The same official annual-report PDF text already OCR'd for the workforce stage above (no new downloads, no new OCR) | Only runs if the workforce OCR cache exists; skipped cleanly otherwise |
| Claims/evidence conversion | `scripts/build_output_contract.py` | (reshapes existing evidence + observation files, no new fetches) | — |
| Offline viewer | `scripts/build_viewer.py` | (reads the finished claims artifact, no new fetches) | Best-effort; never blocks the submission if it fails |

Both site crawlers (the single-pass `urllib` crawl in the registry stage and the deep
scrapy crawl) record the same per-page signals (`src/norway_company_agent/page_signals.py`)
and fetch up to two same-domain RSS/Atom feeds (comment feeds excluded), so the dated-news
and job extraction gives the same result whether or not the optional `scrapy` extra is
installed. The `urllib` crawler verifies TLS against the `certifi` bundle so results do not
depend on the operating system's trust store.

Every stage saves the raw bodies behind its claims (official API responses, crawled pages,
feeds, annual-report PDFs) into a content-addressed store, `<output-dir>/snapshots/<sha256>.<ext>`
(`src/norway_company_agent/snapshot_store.py`, configured by `SIGNALPOST_SNAPSHOT_DIR`, which
`run_agent.py` sets). `scripts/audit_evidence.py` re-checks any claims artifact against that
store: snapshot present, SHA-256 equals the evidence `content_sha256`, and each `claim_span` a
literal slice of the snapshot.

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
