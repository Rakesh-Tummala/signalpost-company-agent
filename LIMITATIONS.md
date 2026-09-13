# Known limitations

Honest account of what this agent does not yet do, as of this submission. None of
these are silently hidden: every gap below shows up as an honest `not_available` /
`not_applicable` claim rather than a fabricated or zero value.

## Zero coverage on three required information types

- **Jobs / hiring signals.** A LinkedIn guest-jobs connector exists
  (`scripts/run_linkedin_guest_jobs_connector.py`) but is deliberately not wired into
  the agent: it hits an unofficial LinkedIn endpoint, and its own report marks output
  `"publishable": false`. Using it for scored claims would violate the source policy's
  rule that unofficial platform access "does not make prohibited collection
  permissible." An official annual-report OCR path
  (`scripts/run_annual_report_workforce_connector.py`) is rights-clear but requires
  `tesseract` and `poppler` system binaries not installed in this environment — not
  yet run.
- **Public activity (news, reviews, social).** Connectors exist for Google News RSS,
  Fagfolkguiden reviews, and LinkedIn/YouTube discovery
  (`scripts/run_google_news_rss_connector.py`,
  `scripts/run_fagfolkguiden_reviews_connector.py`, etc.), all self-tagged
  `rights_review_experiment` or `unofficial_api_experiment` in their own output. None
  are wired into `run_agent.py`.
- **Independent sentiment.** `scripts/run_sentiment_model.py` exists but its own report
  says it hasn't been evaluated against a frozen gold corpus (`evaluate_sentiment.py`,
  which it references, doesn't exist in this repo).

## Partial coverage

- **Website discovery.** 123 of 1,000 entry-batch companies have a verified official
  website (71 from the BRREG registry field itself, 52 found via search-based
  discovery). The remaining ~877 were queried and abstained — some genuinely have no
  independent web presence (housing co-operatives, holding companies), others may be
  findable with more search budget or a different provider.
- **Deep site crawl.** `scripts/run_scrapy_websites.py` (multi-page crawl of a
  verified company site for activity/news pages) requires the `scrapy` package
  (`uv sync --extra crawler` / `pip install -e .[crawler]`), not installed in this
  environment. Only a single-page fetch of the homepage runs today.
- **Group structure.** Fetched for every company but only 70/1000 have a non-empty
  result — most Norwegian small businesses in the sample are standalone entities, so
  this is expected, not a bug.

## Not yet run against real data

- **Refresh/update correctness** is implemented and passes its offline fixture test
  (`scripts/run_refresh_replay.py`, precision 1.0 / recall 1.0), but has not yet been
  run as a second pass over the real 1,000-company batch to confirm change detection
  works at that scale.
- **No held-out gold-set evaluation.** `scripts/score_company_completeness.py` and
  `scripts/evaluate_external_footprint.py` exist but have not been run against our
  own output, so we do not have an independent estimate of our own score before
  submission.

## Source rights

Every claim published by the strict path (registry, financials, roles, locations,
group, verified website) comes from an official Brønnøysundregistrene endpoint or an
independently crawled and identity-gated company website — see
`docs/signalpost-sources.md` for the full policy this follows. Search-provider
results (Tavily, Exa) are used only to generate crawl candidates; raw search output
(titles, snippets, ranks, query text) is never persisted, and a candidate is only
published after independent re-fetch and exact-entity verification.
