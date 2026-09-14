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
| Verified official website | 124 | 12.4% |
| Company-site activity metrics | 75 | 7.5% |
| Group/ownership structure | 70 | 7.0% |
| Verified social profiles | 35 | 3.5% |
| Dated company-owned news/press items | 13 | 1.3% |
| Careers/jobs page detected on own site | 4 | 0.4% |

Website (and the activity/news/social/careers claims that depend on a verified site)
dropped from an earlier 139/1,000 after a precision fix caught 24 wrong-company
matches (see "identity-gate precision fix" below), then recovered partway with 9 more
genuine matches from a targeted Tavily retry on companies Exa's discovery pass had
failed to find (Tavily's exact-match hit rate ran roughly 4x higher than Exa's in a
controlled comparison earlier — see the Tavily/Exa connector commits). The numbers
above are what's actually safe to publish, not the raw technically-discovered count.

The official-registry fields (top of the table) are near-100% because BRREG's bulk
snapshot and live API are comprehensive and always attempted. Everything below that
line depends on either finding a real external footprint (a company genuinely having
a website, a group structure, dated news) or a rights-cleared connector reaching it —
which is why coverage tapers off rather than reflecting a bug.

## Zero coverage on one required information type

- **Independently-sourced job postings and third-party reviews/sentiment.** The
  workforce *size* now has real coverage (see above, via official annual-report OCR),
  and a company advertising a careers/jobs page on its own site is now detected
  (4/1,000 — see `extract_company_site_careers.py`), but individual job *postings*
  specifically remain uncovered: the only postings connector
  (`scripts/run_linkedin_guest_jobs_connector.py`) hits an unofficial LinkedIn
  endpoint and self-tags its own output `"publishable": false`. Using it for scored
  claims would violate the source policy's rule that unofficial platform access
  "does not make prohibited collection permissible." Independent sentiment
  (`scripts/run_sentiment_model.py`) exists but has never been evaluated against a
  frozen gold corpus (`evaluate_sentiment.py`, which it references, doesn't exist in
  this repo), and nothing currently feeds it real snippets anyway.

  Investigated NAV's own official job-postings sources specifically, since
  arbeidsplassen.no is Norway's official public employment platform (a government
  body, the same tier as BRREG) — two real official APIs exist, and neither turned
  out practical within reasonable time/request budget:
  - `pam-stilling-feed` (github.com/navikt/pam-stilling-feed) is a genuine official
    open-data API — keyless public token available instantly, no registration — but
    it's a pure forward-only change log starting around 2019, with items so dense
    (1,000 items spanned roughly 10 seconds of original activity in one test fetch)
    that paging from the start to "now" would take an impractical number of pages.
    Its only shortcut, `?last=true`, returns just the single newest event with no
    way to request "the last N days." The job details it does return include the
    employer's exact `orgnr` — genuinely collision-proof — which is what made this
    worth the investigation.
  - `arbeidsplassen.nav.no/stillinger/api/search` is the unauthenticated public
    endpoint arbeidsplassen.no's own site search uses, and does accept free-text
    queries — but returned persistent HTTP 429 for our IP even after 45+ seconds of
    backoff and browser-like headers, which reads as bot detection rather than a
    simple rate limit worth waiting out. Its results also identify the employer by
    name only (sometimes with a department suffix, e.g. "SECURITAS AS AVD BERGEN"),
    not organisation number, so it would have needed the same token-matching
    discipline as the website identity gate regardless.
  Neither is included in `run_agent.py`. A registered API consumer (emailing NAV per
  the feed's own documented process) or a longer soak/backoff test from a different
  network might make one of these viable later, but that's out of scope for this
  submission.
- **Third-party reviews and broader public buzz.** Google News RSS, Fagfolkguiden
  reviews, and YouTube discovery connectors exist
  (`scripts/run_google_news_rss_connector.py`,
  `scripts/run_fagfolkguiden_reviews_connector.py`, etc.), all self-tagged
  `rights_review_experiment` or `unofficial_api_experiment` in their own output. None
  are wired into `run_agent.py` for the same reason as the LinkedIn connectors.

## Partial coverage, with real numbers now

- **Website discovery.** 115 of 1,000 have a verified official website after the
  identity-gate precision fix below (up from an original 71 registry-provided +
  52 search-discovered + 21 deep-crawl-confirmed = 139 before 24 wrong-company
  matches were caught and demoted). The remaining ~885 were queried and abstained —
  some genuinely have no independent web presence (housing co-operatives, holding
  companies confirmed by manual spot-check), others may be findable with more search
  budget (Tavily's free tier was exhausted mid-run; Exa's 20k/month is barely
  touched) or a different provider.

### Identity-gate precision fix (found by manually spot-checking discovered sites)

Before trusting the discovered-website numbers, a manual spot-check of ~8 of the 62
search-discovered matches found several that looked wrong on inspection: "SAGO AS"
matched `sago.com` (an unrelated US market-research firm with a global office list
and zero Norway presence), "BLUE BAY AS" matched an Italian resort site, "SEMBER AS"
matched what looks like a personal website. Tracing the cause: `identity.py`'s
`assess_website_identity` had a branch that scored a match 0.95/"exact" whenever a
company's legal name reduced to a *single* distinctive word (common for short
Norwegian company names) and that word merely appeared on the candidate page — with
no requirement that the page have anything to do with Norway. 25 of the 62 published
discovered-website matches (40%) relied on exactly this branch.

Fixed to also require the candidate's hostname end in `.no`, or the organisation
number appear on the page (already handled by a separate, higher-priority branch).
Re-ran the identity gate against every already-published site using the cached page
content (no new network requests): 42 evidence entries across 24 organisation numbers
demoted, including `brandsuite.com.au`, `procuro.ie` (German-language content),
`ciol.org.uk`, `solaas.it`, and a `readthedocs.io` page for unrelated open-source
software matched to "OVS AS". The 24 activity observations and 3 news observations
that had been extracted from those same wrong pages were filtered out too, since they
would otherwise have described the wrong company.

Known cost of this fix: it will also reject a genuine Norwegian company that happens
to use a `.com`/`.io`/etc. domain instead of `.no` (e.g. Zivid AS, a well-known
Norwegian company, was demoted this way in our data despite likely being a real
match). This trades recall for precision deliberately — the source policy states "a
material wrong-company match... blocks qualification" as the more severe failure
mode, and "it is better to miss some information than publish it under the wrong
company." A stronger fix would corroborate via detected Norwegian-language content
on the page as an alternative to the `.no` domain requirement, which would rescue
cases like Zivid without reopening the collision risk — not implemented here for lack
of time to validate it against real data before this submission.
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
