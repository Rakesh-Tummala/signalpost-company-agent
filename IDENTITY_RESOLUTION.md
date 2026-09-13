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

## When uncertain

If a candidate fails the gate, or no candidate exists, the profile gets a
`not_available` claim, not an empty or fabricated one. Parent, franchise, brand and
sister-company matches are treated as **not exact** and are never published as the
target company's own website — consistent with the source policy's rule that these
relationships "must be labelled, not collapsed."
