# Identity resolution

## The anchor

Every profile starts from the organisation number, never a name or domain. All
downstream data must trace back to that exact 9-digit key before publication.

## Candidate vs. publishable

A search result (Tavily, Exa) or registry-listed URL is only ever a **candidate**.
`src/norway_company_agent/discovery.py::score_search_candidate` scores it against the
company's legal name, organisation number and municipality:

- exact organisation number appears in the result's title/snippet: +0.75
- all distinctive legal-name tokens appear in the result title: +0.45 (or +0.25 if
  scattered across title+snippet)
- the normalized legal name appears in the candidate's hostname: +0.30
- registry municipality appears in the snippet: +0.10

A candidate only proceeds to independent crawl if its score is ≥0.75 **and** the name
appears in the hostname **and** (the org number matched or the full name matched the
title). Directory/aggregator/social hosts (`proff.no`, `1881.no`, `linkedin.com`, etc.)
are rejected outright regardless of score — see `BLOCKED_DISCOVERY_HOSTS`.

## Publication gate

Passing the candidate score is not enough. `src/norway_company_agent/identity.py::apply_website_identity_gate`
independently fetches the candidate page and runs `assess_website_identity`, which
checks the *fetched page's own content* (title, structured JSON-LD organisation data,
meta description, page text) against the legal name — search snippets are never used
as evidence for a published claim, only as a pointer to what to crawl.

Real example this caught during testing: a search for "Wyssen Norge AS" (a Norwegian
subsidiary) surfaced `wyssenavalanche.com`, which passed candidate scoring. The
crawled page's structured data identified it as "Wyssen Avalanche Control AG" (the
Swiss parent) — the identity gate correctly rejected this as `quarantined` rather
than publish a parent-company page under the subsidiary's profile.

## Single-word company names need a country signal, not just a word match

A short legal name (e.g. "SAGO AS" reduces to the single distinctive token "sago"
once the legal-form suffix is stripped) is common for small Norwegian companies, but
a single common word matching a page's title/description is a real global-collision
risk. `assess_website_identity` requires that case to *also* have the candidate's
hostname end in `.no`, or the organisation number appear on the page (checked by a
separate, higher-priority branch) — a bare single-token match with neither signal
scores 0.5 ("related_or_uncertain", not published) instead of 0.95 ("exact").

Found the hard way, by spot-checking discovered sites before trusting them: "SAGO AS"
had matched `sago.com`, an unrelated US market-research firm with offices worldwide
and none in Norway; other real cases included an Italian resort, a UK domain, and a
`readthedocs.io` page for unrelated open-source software. 25 of 62 published
discovered-website matches (40%) relied on the unguarded version of this branch
before the fix — see `LIMITATIONS.md` for the full accounting and the known
recall cost (a genuine Norwegian company on a non-`.no` domain, like Zivid AS, gets
quarantined by this same rule; a lower-risk fix would additionally accept detected
Norwegian-language page content as corroboration, not implemented yet).

## Multi-word names need a Norway signal too ("Blue Bay" is not enough)

The single-word fix left the same hole one word wider. "BLUE BAY AS" (a Norwegian
company; the registry lists no website) matched `bluebayresidence.it`, an Italian
resort, at 0.95/"exact" because both name words appear on that page. Other real
cases in the submitted batch: "SOFT ONE AS" on a Qatari road-marking site, "GI ENERGY
AS" on an Australian one, "MK FUTURE AS" on a Polish one. Name words that merely
co-occur on a page are not evidence of *this* entity.

`assess_website_identity` now publishes a multi-word name match only when the site
is tied to the Norwegian entity by at least one of:

1. **the registry itself lists that site** (`hjemmeside` / live `website`) -- the
   company told Brreg it is theirs, which outranks any text match;
2. **a `.no` domain**;
3. **the registered place on the page** -- the registered postal code *and* town both
   appear, or the registered town appears together with an explicit mention of
   Norway (`norway`/`norge`/`noreg`). A town that is already part of the company
   name ("This Is Narvik") does not count, since it proves nothing beyond the name;
4. **the organisation number** on the page (a separate, higher-priority branch).

Without any of those the score is 0.5 ("related_or_uncertain"), not published.
Re-running the gate over the already-published batch (`scripts/reassess_published_websites.py`,
cached pages only, no new requests) took published websites from 74 to 59; the 15
demoted include every clear foreign-site match. The cost is real: a few genuine
Norwegian companies on a `.com` (TBG Holding, Axess Technologies, Oslo Analytica,
Lie Nilsen) fall out because their captured text carries none of the four signals.
That is the intended trade -- "it is better to miss some information than publish it
under the wrong company."

## When uncertain

If a candidate fails the gate, or no candidate exists, the profile gets a
`not_available` claim, not an empty or fabricated one. Parent, franchise, brand and
sister-company matches are treated as **not exact** and are never published as the
target company's own website — consistent with the source policy's rule that these
relationships "must be labelled, not collapsed."
