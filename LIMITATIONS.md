# Known limitations

Honest account of what this agent does not yet do, as of this submission. None of
these are silently hidden: every gap below shows up as an honest `not_available` /
`not_applicable` claim rather than a fabricated or zero value.

## Real per-field coverage (1,000-company entry batch, from the final claims artifact)

| Field | Companies | % |
|---|---|---|
| Legal identity, legal form, accounting obligation | 1,000 | 100% |
| Bankruptcy/liquidation status, industry, latest filed year, roles | 999 | ~100% |
| Business address, latest annual accounts | 997 | 99.7% |
| Registered locations/subunits | 745 | 74.5% |
| **Workforce size** (OCR'd from official annual reports) | 459 | 45.9% |
| Registered workplaces (subunit detail) | 255 | 25.5% |
| Registry-reported employee count | 144 | 14.4% |
| Verified official website | 139 | 13.9% |
| Company-site activity metrics | 90 | 9.0% |
| Group/ownership structure | 70 | 7.0% |
| Verified social profiles | 53 | 5.3% |
| Dated company-owned news/press items | 13 | 1.3% |

The official-registry fields (top of the table) are near-100% because BRREG's bulk
snapshot and live API are comprehensive and always attempted. Everything below that
line depends on either finding a real external footprint (a company genuinely having
a website, a group structure, dated news) or a rights-cleared connector reaching it —
which is why coverage tapers off rather than reflecting a bug.

## Zero coverage on one required information type

- **Independently-sourced job postings and third-party reviews/sentiment.** The
  workforce *size* now has real coverage (see above, via official annual-report OCR),
  but job *postings* specifically remain uncovered: the only postings connector
  (`scripts/run_linkedin_guest_jobs_connector.py`) hits an unofficial LinkedIn
  endpoint and self-tags its own output `"publishable": false`. Using it for scored
  claims would violate the source policy's rule that unofficial platform access
  "does not make prohibited collection permissible." Independent sentiment
  (`scripts/run_sentiment_model.py`) exists but has never been evaluated against a
  frozen gold corpus (`evaluate_sentiment.py`, which it references, doesn't exist in
  this repo), and nothing currently feeds it real snippets anyway.
- **Third-party reviews and broader public buzz.** Google News RSS, Fagfolkguiden
  reviews, and YouTube discovery connectors exist
  (`scripts/run_google_news_rss_connector.py`,
  `scripts/run_fagfolkguiden_reviews_connector.py`, etc.), all self-tagged
  `rights_review_experiment` or `unofficial_api_experiment` in their own output. None
  are wired into `run_agent.py` for the same reason as the LinkedIn connectors.

## Partial coverage, with real numbers now

- **Website discovery.** 139 of 1,000 have a verified official website: 71 from the
  BRREG registry field, 52 from Tavily/Exa search-based discovery, 21 more confirmed
  by the deeper multi-page crawl (`run_scrapy_websites.py`) after the single-page
  fetch had missed them. The remaining ~861 were queried and abstained — some
  genuinely have no independent web presence (housing co-operatives, holding
  companies confirmed by manual spot-check), others may be findable with more search
  budget (Tavily's free tier was exhausted mid-run; Exa's 20k/month is barely
  touched) or a different provider.
- **Workforce size.** 459/1,000 (45.9%) via official annual-report OCR -- see the
  table above. 856 companies were eligible (BRREG's own registry field was blank for
  them); of those, 395 had no matching Norwegian employee-count phrase found by the
  connector's regex patterns (could be phrasing variants not yet covered, or a
  genuinely OCR-illegible scan) and 2 had conflicting counts across the document and
  correctly abstained rather than guess.
- **Group structure.** Fetched for every company but only 70/1,000 have a non-empty
  result — most Norwegian small businesses in the sample are standalone entities, so
  this is expected, not a bug.

## Not yet run against real data

- **Refresh/update correctness** is implemented and passes its offline fixture test
  (`scripts/run_refresh_replay.py`, precision 1.0 / recall 1.0), but has not yet been
  run as a second pass over the real 1,000-company batch to confirm change detection
  works at that scale.
- **No held-out gold-set evaluation.** `scripts/score_company_completeness.py` was run
  against our data (see `EVAL.md`) but reads a different, older observation pipeline
  than the claims/evidence format actually submitted -- it does not reflect today's
  website/social/activity/news/workforce additions. We have the per-field coverage
  table above, but no independent, evaluator-style estimate of overall score before
  submission.

## Source rights

Every claim published by the strict path (registry, financials, roles, locations,
group, verified website) comes from an official Brønnøysundregistrene endpoint or an
independently crawled and identity-gated company website — see
`docs/signalpost-sources.md` for the full policy this follows. Search-provider
results (Tavily, Exa) are used only to generate crawl candidates; raw search output
(titles, snippets, ranks, query text) is never persisted, and a candidate is only
published after independent re-fetch and exact-entity verification.
