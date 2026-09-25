# Known limitations

Honest account of what this agent does not yet do, as of this submission. None of
these are silently hidden: every gap below shows up as an honest `not_available` /
`not_applicable` claim rather than a fabricated or zero value.

## Real per-field coverage (1,000-company entry batch, from the final claims artifact)

| Field | Companies | % |
|---|---|---|
| Legal identity, legal form, accounting obligation | 1,000 | 100% |
| Bankruptcy/liquidation status, industry, latest filed year, roles | 998 | ~100% |
| Latest annual accounts, business address | 997 / 996 | 99.7% / 99.6% |
| **Workforce size** (OCR'd from official annual reports) | 841 | 84.1% |
| **Prior-year annual accounts** (recovered from the same official annual-report OCR) | 823 | 82.3% |
| Registered locations/subunits | 745 | 74.5% |
| Registry-reported employee count | 142 | 14.2% |
| Group/ownership structure | 70 | 7.0% |
| Verified official website (a further 55 listed sites are published as `ambiguous`) | 58 | 5.8% |
| Verified social profiles | 28 | 2.8% |
| **Dated** company-owned news items (120 items) | 19 | 1.9% |
| **Real job postings** on the company's own site | 1 | 0.1% |

Checked-and-empty results (no registered subunits: 255 companies) are published as an
explicit empty list cited to the response's own `"totalElements":0`, not counted above.

Four rows changed meaning or fell in this revision, on purpose, and the change is
toward precision (see "After the 700-company diagnostic" below): the website row now
counts only sites the identity gate verified (before, 49 sites it had *not* verified
were published as `available` at low confidence); social profiles depend on a verified
site; "dated news" replaced 16 undated `/news/`-path pages with 120 items that each
carry a publication date; and "real job postings" replaced a careers-page detector that
counted a page merely existing. The numbers above are what is safe to publish, not the
raw technically-discovered count.

The official-registry fields (top of the table) are near-100% because BRREG's bulk
snapshot and live API are comprehensive and always attempted. Everything below that
line depends on either finding a real external footprint (a company genuinely having
a website, a group structure, dated news) or a rights-cleared connector reaching it —
which is why coverage tapers off rather than reflecting a bug.

## Zero coverage on one required information type

- **Independently-sourced job postings and third-party reviews/sentiment.** The
  workforce *size* now has real coverage (see above, via official annual-report OCR),
  and real roles on a company's own site are extracted (1/1,000 -- see
  `extract_company_site_jobs.py` and the section below), but third-party job postings
  remain uncovered: the only postings connector
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
- **Workforce size.** 841/1,000 (84.1%) via official annual-report OCR -- see the
  table above. 856 companies were eligible (BRREG's own registry field was blank for
  them). This started at 459 (53.6% of eligible); manually inspecting the cached OCR
  text for "no match" companies (no new downloads or OCR needed -- everything was
  already on disk) found the real cause: the regex patterns required the ASCII
  "regnskapsaret" spelling, but correctly-OCR'd Norwegian text (once the language
  pack was fixed, see the earlier commit) actually reads "regnskapsåret" -- with å.
  Real cached example that was silently missed: "Antall årsverk sysselsatt i
  regnskapsåret: 1,71". Fixing that one spelling gap (plus a Nynorsk phrasing
  variant and adding "borettslag" to the zero-workforce entity list) took accepted
  matches from 459 to 841 -- pure re-processing of already-cached text, no new
  network requests. Verified a sample of the newly-matched values against their
  full surrounding OCR context (financial-note tables linearized by OCR into
  label-then-value line pairs) before trusting the result. Remaining gap: 5 had no
  matching phrase at all, 10 had conflicting counts across the document and
  correctly abstained rather than guess (up from 2 -- the loosened patterns
  surface more candidates, and the safety net still holds them to agreement).
- **Group structure.** Fetched for every company but only 70/1,000 have a non-empty
  result — most Norwegian small businesses in the sample are standalone entities, so
  this is expected, not a bug.

## Follow-up investigation after a real evaluator run

> Historical record of that round. Its careers-page and undated-news numbers are superseded by
> "After the 700-company diagnostic" below, which retires both signals; the website-discovery
> ceiling and the NAV findings here still stand.

An evaluator ran this agent against a held-out 100-company set and reported 66.92/100,
meeting the qualification bar, with specific feedback: "the clearest route to a higher
cumulative score" is broader independently-verified coverage across company sites,
jobs, and dated activity -- exactly the three thinnest areas already documented above.
Two follow-ups from that feedback:

- **Tried Exa's `deep` search type** (multi-query-variant synthesis, not a single
  fast lookup) on a fresh sample of the ~90 remaining "AS with employees" candidates
  neither Tavily nor Exa's faster modes had found a site for. Zero new matches --
  same 5 candidates surfaced and correctly quarantined as with `auto`, at roughly
  4x the cost and 3x the latency. This particular remaining pool looks like a
  genuine ceiling (companies with no real independent web presence), not a
  search-quality problem worth paying more for.
- **Widened the crawl's priority-link limit from 4 to 6** (`_priority_links` in
  `website.py`) -- PRIORITY_TERMS had grown to 13 terms when careers/jobs pages were
  added, so a homepage with contact + news + careers links all present would have
  silently dropped one category under the old cap. Re-crawled all 144
  website-candidate companies: dated news/activity went 13 -> 16, directly on the
  "dated activity" feedback, for a handful more requests per site (concurrency and
  per-domain caps already bound the real cost).
- **Re-measured the NAV `pam-stilling-feed` pagination problem precisely** (see the
  jobs section above): 20 consecutive pages (20,000 events) covered only 58 seconds
  of real calendar time on 2023-06-14. Reaching the present from there would need
  roughly 35 million pages -- not a rough estimate anymore, a confirmed dead end.
- **Fixed a real gap in careers-page detection**: inspecting the actual crawled page
  data directly (not just the connector's own output count) found genuine careers
  pages the extractor was silently missing -- e.g. TBG Holding's `/tbg-careers/`
  page had passed the identity gate (0.95, publishable) and was sitting right there
  in the crawl data, but the old path pattern only matched "careers" immediately
  after a `/`, not hyphenated into a compound segment like "tbg-careers". Loosened
  the boundary; careers-page detection went 4 -> 6. Also checked whether any of
  those pages have specific, extractable job-title content (which would be a
  genuinely stronger "jobs" signal than just "a careers page exists") -- all 6 are
  generic "join our team" marketing copy with no individual role information, so
  there was nothing further to extract from this particular signal right now.
- **Recovered prior-year financial figures from data we already had on disk**
  (`scripts/extract_prior_year_financials.py`), aimed at broader company coverage
  more generally rather than any one of the three named feedback areas. The
  `financials` module only returns the most recently filed year's figures per
  official-API call, but Norwegian annual-report notes conventionally print the
  prior year's comparative figures right next to the current year's on the same
  line -- e.g. `Sum driftsinntekter 658 000 923 400` is (current year) then (prior
  year). We already had the current year's correct value from the official API and
  already had the report's OCR text cached from the workforce stage, so this is a
  pure re-read of existing cached text: no new downloads, no new OCR, no new
  third-party requests. Safety design: OCR'd digit groups separated by spaces are
  ambiguous on their own (is `4 182 614 4 678 118` one four-part number or two
  three/four-part numbers?), so rather than guess a split point, the script anchors
  on the current-year value already known correct from the API, requires it appear
  as an exact prefix of the space-stripped digit sequence on that line, and only
  trusts whatever's left over as the prior year -- if the known value isn't found
  as a clean prefix, it abstains for that field instead of guessing. Ran against
  all 1,000 companies: 854 were eligible (had both a usable `financials` record and
  cached OCR text), and 823 (96.4% of eligible, 82.3% of all 1,000) had at least one
  field recovered this way; field-level yield within that set was uneven (assets
  780, debt 761, operating_result 749, annual_result 618, equity 561, revenue 337 --
  revenue's label, "Sum driftsinntekter", sits lower on the page and more often
  fell outside what OCR captured cleanly). Verified a random sample plus one company
  with all six fields recovered (933787141) against the underlying raw OCR text
  directly, line by line, before trusting the result -- every value matched
  exactly. Feeds `annual_accounts.<year>` claims for the prior year, reusing the
  same claim-field convention the structured `financials` module already uses for
  the current year, since from a consumer's point of view it's the same kind of
  fact just recovered a different way.

  **A follow-up validation pass on this connector caught a real fabricated-value
  bug before submission**, not after: adding the prior-year figure to the
  per-company summary sentence (see "Decision-useful synthesis" below) surfaced an
  absurd ~20-digit "revenue" for organisation 925800023 (a housing cooperative,
  `SAMEIET LENSMANNSTUNET 1`). Root cause: that company's annual report prints
  **four** columns on the revenue line (`Sum driftsinntekter 1 049 490 949 946 1
  045 800 1 107 000` -- this year's actual, this year's budget, last year's
  actual, last year's budget), not the usual two. The original implementation
  found the known current-year value as a prefix of the line's digits and blindly
  trusted "everything left over" as the prior year -- which silently absorbed the
  third and fourth columns into one nonsensical number instead of stopping at the
  first one. Housing-cooperative report templates (`borettslag`/`sameie`) commonly
  add budget columns that ordinary company accounts don't have.

  Fixed by rewriting the parser to work token-by-token on the OCR'd line's own
  whitespace-separated number groups instead of one concatenated digit string:
  after the known current-year value, it consumes exactly one further
  Norwegian-grouped number (a 1-4 digit leading group followed only by exact
  3-digit continuation groups) and **abstains if anything is left over** -- i.e.
  if a third or fourth column is present, since we can no longer be sure which
  one is genuinely "prior year". Re-ran the full batch: the garbage value is gone
  (that company still correctly keeps its other five recovered fields, which
  don't share the ambiguous line), the accepted-company count is unchanged (823),
  and a scan of every recovered figure across all 823 companies for implausible
  magnitudes (>100 billion NOK) found zero more. This is exactly the "it is
  better to miss some information than publish it under the wrong company" (or
  wrong value) principle already applied to the website identity gate, now
  applied here too -- see `tests/test_poc.py::PriorYearFinancialsTests` for the
  regression test built from this exact real case.

## Validated against real data since the last evaluator run

- **Refresh/update correctness** now has a real-scale validation pass, not just the
  offline fixture. See `REFRESH.md` for the full result: zero false positives, zero
  crashes across all 1,000 real profiles, confirmed idempotency at scale, and 50/50
  injected changes correctly detected (precision 1.0, recall 1.0) once real
  company shapes were used instead of only the fixture's two hand-crafted ones.
  Notably, building that validation is what surfaced the fabricated-value bug
  above -- a useful reminder that a bigger, more varied test corpus finds bugs a
  small hand-crafted fixture can't.
- **Offline viewer added** (`scripts/build_viewer.py`, wired into `run_agent.py`).
  Previously there was no way to browse the submitted claims/evidence at all
  except reading raw JSONL -- addresses "is it easy to find, compare and verify
  company information on desktop and mobile" directly. Single self-contained HTML
  file, no external dependencies or server required, searchable by organisation
  number or company name, shows every claim's value, availability, confidence and
  evidence source link.
- **Checked whether Fagfolkguiden's reviews connector could be promoted out of
  `rights_review_experiment`.** `robots.txt` explicitly allows crawling `/bedrift/`
  pages (only `/admin/`, `/api/`, `/logg-inn`, `/tilbud/`, `/portal/` are
  disallowed), and no terms-of-service page prohibiting automated access was found
  (only a privacy policy, which governs their own visitor data collection, not
  third-party scraping of business listings) -- a meaningfully different rights
  posture than LinkedIn's. Left as experimental regardless: "no prohibition found"
  during a short check is not the same as confirmed permission, and promoting a
  connector's acquisition mode to scored/publishable status deserves more
  certainty than that before it touches real claims.

## After the 700-company diagnostic

Builderr later ran a private diagnostic on a stricter ruler (700 companies, scored from the
sources captured in the first run, with the shared reference collection now including any fact
verifiable from *any* participant's captured source). It scored this agent 56.37/100 -- not
comparable to the earlier 66.92, and explicitly not an official result -- and named the main
gap: no validated news or hiring recovery. Its concrete asks were: retain the first-party
responses, extract social links from markup and structured data, attach the exact saved source
to every claim, and add structured dated articles plus role cards / job feeds; "a generic careers
page is not enough".

What changed, with the real numbers from a fresh one-command run over all 1,000 submitted
companies (`out/run-summary.json`; every stage, 3h12m, 6,756 registry requests):

- **Every claim now has its own evidence entry, a saved copy of its source, and an exact
  excerpt.** Raw API responses, crawled pages, feeds and annual-report PDFs are stored under
  `snapshots/<sha256>`; `scripts/audit_evidence.py` re-checks the artifact. Result on the
  regenerated artifact (`out/audit-report.json`): 19,046 claims; 17,169 available claims each
  point at a snapshot whose SHA-256 matches; **15,503 excerpts are literal slices of their
  snapshot; 0 failures.** 1,664 excerpts come from annual-report PDFs (a matched OCR/text-layer
  line) and cannot be searched for in a PDF's compressed bytes, so they are reported as
  `span_from_pdf` rather than passed; 2 claims carry no excerpt. The snapshot store is not
  committed (it holds about a gigabyte of PDFs); regenerate it with `run_agent.py`.
- **Hiring: only real roles count.** The careers-page detector (4 -> 6 companies in the previous
  round) is retired -- Builderr's own words are that it does not count. The replacement reads a
  schema.org `JobPosting`, a role card linking to an individual posting (including links whose
  *file name* carries the vacancy words, like `/ledig-stilling-servicemarkedsleder-...`),
  an item in the company's own careers RSS feed, or an explicit apply action, and skips
  aggregate "Ledige stillinger ..." links and expired postings. **Honest result: 1 real role
  across the whole batch** (Toyota Sulland). Of the 58 verified sites, only 3 had a careers page
  in the crawl: one lists real roles, one is generic marketing copy, and one only links out to
  Jobbnorge (a third-party job board, not fetched). Most small Norwegian companies simply do not
  post roles on their own site, so this will stay small.
- **News: only dated articles count.** Previously any page under a `/news/` path counted (16
  companies, no date on any of them). Now: schema.org `NewsArticle`/`BlogPosting`/`PressRelease`,
  a plain `Article` or `article:published_time` only on a news-style URL, `<time datetime>`
  cards, and the site's own RSS/Atom feed (comment feeds excluded). **19 companies, 120 dated
  items.** Real-data review of the first version found and removed false positives: WordPress
  marks ordinary contact/about pages as `Article` with a publish date, feeds list password-
  protected posts and comments, and the same story appears through several markups.
- **Social links** are also read from JSON-LD `sameAs`, `twitter:site` and `rel="me"`. Because
  fewer sites now pass the identity gate, the published total is 28 companies (was 35).
- **Two precision bugs, found by testing on real data, in the submitted artifact:**
  1. *Wrong-company websites.* "BLUE BAY AS" was published against an Italian resort site because
     both name words appear on it. The identity gate's multi-word branch now needs a Norway tie
     (registry-listed site, `.no`, or the registered place on the page); see
     `IDENTITY_RESOLUTION.md`. Published sites: 74 -> 58.
  2. *Unverified sites published as available.* 49 sites the gate had **not** verified
     (confidence 0.3-0.85) were emitted as `official_website` with availability `available`.
     Builderr's rule is that an uncertain match is `ambiguous`; they are now `ambiguous` (55 in
     this run) and the summary no longer calls them verified.
- **Two crawl-path fixes.** Job/news extraction used to run only when the optional `scrapy` extra
  was installed; both crawlers now record the same page signals, so results do not depend on it.
  And the plain crawler failed on any site whose certificate chain touches an expired root in the
  operating system's trust store (sulland.no, nifu.no here); it now verifies against `certifi`.

**The new code on companies it has never seen.** A separate one-command run over 100
organisation numbers drawn from the full 411,160-company list and *not* in the submitted 1,000
(the evaluator's batch shape): 100 unique terminal envelopes, none failed, 1,890 claims, evidence
audit clean (1,528 exact excerpts, 170 from PDFs, 0 failures); 19m12s wall clock against the 45
minute limit; about 785 outbound requests (658 registry, 38 crawl, roughly 89 annual-report
downloads) against the 2,000 cap; workforce 86 of 89 eligible, prior-year figures 84 of 88.
Site-derived facts are sparse there too (10 dated articles from 1 company, no job postings),
consistent with the 1,000-company rates. No search-API keys were set, so discovery was skipped.

Cost of the stricter gate, stated plainly: a few genuine Norwegian companies on a `.com` (TBG
Holding, Axess Technologies, Oslo Analytica, Lie Nilsen) are no longer published because their
captured text carries none of the four accepted Norway signals. Website coverage on the verified
list fell from 123 to 58 companies; what remains is far less likely to be attributed to the wrong
company.

## Not yet run against real data

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
