# Agent research and abstention policy

## What the agent does, given an organisation number

1. Anchor identity in the official BRREG bulk registry snapshot; if the number is
   absent from that snapshot (deregistered since, or registered after it was taken),
   fall back to the live per-org BRREG API rather than failing the whole batch (see
   `backfill_from_registry_live` in `src/norway_company_agent/batch.py`).
2. Fetch official financials, roles, group structure, and registered subunits from
   BRREG's live API.
3. If BRREG has no website on file, generate search candidates (Tavily, then Exa),
   independently crawl the best candidate, and run it through the identity gate
   before publishing anything (`IDENTITY_RESOLUTION.md`).
4. Convert every collected fact into a claim with source, retrieval time and
   confidence (`DATA_SCHEMA.md`).

## Abstention policy

The agent is built to prefer silence over a wrong answer:

- A candidate that fails the identity gate is quarantined, not published under a
  looser threshold.
- A module that errors, times out, or returns nothing gets an explicit
  `not_available` / `blocked` / `failed` claim — never a fabricated value, never a
  silently dropped field, never zero standing in for missing data.
- Parent companies, franchises, and brand relationships are never treated as
  equivalent to the exact legal entity being researched, even when they're the only
  thing findable.

This directly follows the source policy's own framing: "It is better to miss some
information than publish it under the wrong company."

## Confidence

`build_output_contract.py::confidence_for` assigns 1.0 to any claim sourced from an
official BRREG endpoint (registry, registry_live, financials, roles, locations,
group, accounting-obligation rule) — these are authoritative government records, not
inferred. A discovered-website claim's confidence comes from the identity gate's own
match score when available, or a conservative 0.9 default when it passed the gate but
no numeric score was recorded. A claim with `availability != "available"` has no
confidence value at all (`null`) rather than a misleading number.

## What no model decides

No LLM is used to determine exact identity, invent a missing field, or override
deterministic evidence in this pipeline. Every claim in the current submission
traces to a structured API response or an independently fetched and parsed web page,
never to a language model's own synthesis.
