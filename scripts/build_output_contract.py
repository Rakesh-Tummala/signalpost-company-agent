#!/usr/bin/env python3
"""Convert our module/evidence profiles into the OUTPUT_CONTRACT.md claims/evidence schema.

The pipeline (run_competition_batch.py, run_tavily_discovery.py, run_exa_discovery.py)
stores evidence keyed by internal module name (registry, financials, roles, ...), each
holding one evidence record with an optional structured value. OUTPUT_CONTRACT.md
describes a different, flatter shape: a top-level `claims` array (one entry per
individual fact, each pointing at evidence by id) plus a top-level `evidence` array
(one entry per distinct source fetch). This script performs that decomposition without
re-fetching anything -- it is a pure reshape of data we already collected.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.evidence_spans import module_spans  # noqa: E402

AVAILABILITY_MAP = {
    "available": "available",
    "not_found": "not_available",
    "not_applicable": "not_applicable",
    "not_fetched": "not_available",
    "source_error": "failed",
    "blocked": "blocked",
}

OFFICIAL_SOURCE_CLASSES = {
    "official_registry_bulk", "official_registry_live", "official_annual_accounts",
    "official_annual_account_copies", "official_roles", "official_group_structure",
    "official_subunits", "official_rule_interpretation",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            # Saved page bodies can contain U+2028/U+2029/U+0085: valid inside a JSON string, but
            # splitlines()-style readers treat them as line breaks. Escape them so one envelope is
            # always exactly one physical line.
            for char in ("\u2028", "\u2029", "\u0085"):
                line = line.replace(char, "\\u%04x" % ord(char))
            handle.write(line + "\n")


def availability_for(status: str | None) -> str:
    return AVAILABILITY_MAP.get(status or "", "failed")


def confidence_for(record: dict[str, Any]) -> float | None:
    if record.get("status") != "available":
        return None
    if record.get("source_class") in OFFICIAL_SOURCE_CLASSES:
        return 1.0
    value = record.get("value") or {}
    assessment = value.get("identity_assessment") or {}
    score = assessment.get("score")
    if isinstance(score, (int, float)):
        return round(float(score), 3)
    return 0.9


INLINE_MAX_BYTES = 1_000_000
ENVELOPE_INLINE_BUDGET = 4_000_000
CONTENT_TYPES = {
    ".json": "application/json", ".html": "text/html", ".xml": "application/xml", ".pdf": "application/pdf", ".txt": "text/plain",
}


class Emitter:
    """Collects claims; every claim gets its own evidence entry so it can carry an exact excerpt.

    Every saved source body a claim rests on is also registered once in the envelope's
    `source_snapshots`, with the body inline when it is text and fits the budget, so the
    result alone is enough to reopen and re-hash the exact response behind each claim.
    """

    def __init__(self, snapshot_root: Path | None = None) -> None:
        self.claims: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self._snapshot_ids: dict[str, str] = {}
        self._inline_used = 0
        self._root = snapshot_root
        self._counts: dict[str, int] = {}

    def _register_snapshot(self, path: str, source: dict[str, Any]) -> str:
        if path in self._snapshot_ids:
            return self._snapshot_ids[path]
        sha = Path(path).stem
        snapshot_id = f"snap-{sha[:16]}"
        suffix = Path(path).suffix.lower()
        entry: dict[str, Any] = {
            "id": snapshot_id, "sha256": sha, "path": path, "content_type": CONTENT_TYPES.get(suffix, "application/octet-stream"),
            "source_url": source.get("source_url"), "retrieved_at": source.get("retrieved_at"),
        }
        file = (self._root / path) if self._root is not None else None
        raw = file.read_bytes() if file is not None and file.exists() else None
        if raw is None:
            entry["body_omitted"] = "file_not_found"
        else:
            entry["bytes"] = len(raw)
            if suffix == ".pdf":
                entry["body_omitted"] = "binary_pdf"
            elif len(raw) > INLINE_MAX_BYTES or self._inline_used + len(raw) > ENVELOPE_INLINE_BUDGET:
                entry["body_omitted"] = "too_large"
            else:
                try:
                    entry["body"], entry["body_encoding"] = raw.decode("utf-8"), "utf-8"
                except UnicodeDecodeError:
                    entry["body"], entry["body_encoding"] = base64.b64encode(raw).decode("ascii"), "base64"
                self._inline_used += len(raw)
        self.snapshots.append(entry)
        self._snapshot_ids[path] = snapshot_id
        return snapshot_id

    def _next_id(self, stem: str) -> str:
        self._counts[stem] = self._counts.get(stem, 0) + 1
        return f"ev-{stem}-{self._counts[stem]}"

    def _add(self, stem: str, source: dict[str, Any], *, span: str | None, method: str | None) -> str:
        eid = self._next_id(stem)
        entry = {
            "id": eid,
            "source_url": source.get("source_url"),
            "source_class": source.get("source_class") or source.get("source_type"),
            "retrieved_at": source.get("retrieved_at"),
            "content_sha256": source.get("content_sha256"),
            "claim_span": span,
        }
        # The same facts under the names Builderr's reviewers use, so no reader has to guess:
        # capture date, content hash and retained-response reference.
        entry["captured_at"] = entry["retrieved_at"]
        entry["capture_date"] = str(entry["retrieved_at"])[:10] if entry["retrieved_at"] else None
        entry["content_hash"] = entry["content_sha256"]
        if source.get("snapshot_path"):
            entry["snapshot"] = source["snapshot_path"]
            entry["snapshot_id"] = self._register_snapshot(source["snapshot_path"], source)
            entry["retained_response_ref"] = entry["snapshot_id"]
        if method:
            entry["extraction_method"] = method
        self.evidence.append(entry)
        return eid

    def observation_claim(self, field: str, value: Any, observation: dict[str, Any]) -> None:
        eid = self._add(
            str(observation["id"]), observation,
            span=observation.get("evidence_span"), method=observation.get("extraction_method") or observation.get("strategy"),
        )
        self.claims.append({
            "field": field,
            "value": value,
            "availability": "available",
            "confidence": 1.0 if observation.get("rights_status") == "approved" and observation.get("exact_entity") else 0.9,
            "evidence_ids": [eid],
        })

    def claim(self, field: str, value: Any, record: dict[str, Any], *, module: str, span: str | None = None, method: str | None = None, availability: str | None = None) -> None:
        eid = self._add(module, record, span=span, method=method or record.get("extraction_method"))
        self.claims.append({
            "field": field,
            "value": value,
            "availability": availability or availability_for(record.get("status")),
            "confidence": confidence_for(record),
            "evidence_ids": [eid],
        })


def emit_registry_claims(emitter: Emitter, evidence: dict[str, Any]) -> None:
    # Prefer registry_live (freshest) when available; fall back to the bulk snapshot.
    module = "registry_live" if (evidence.get("registry_live") or {}).get("status") == "available" else "registry"
    record = evidence.get(module) or {}
    value = record.get("value") or {}
    if record.get("status") != "available":
        emitter.claim("legal_identity", None, record, module=module)
        return
    fields = {
        "legal_name": value.get("name") or value.get("navn"),
        "legal_form": value.get("legal_form") or value.get("organisasjonsform.kode"),
        "employees": value.get("employees") if "employees" in value else value.get("antallAnsatte"),
        "bankrupt": value.get("bankrupt") if "bankrupt" in value else value.get("konkurs"),
        "liquidating": value.get("liquidating") if "liquidating" in value else value.get("underAvvikling"),
        "business_address": value.get("business_address") or value.get("forretningsadresse"),
        "industry": value.get("industry") or value.get("naeringskode1"),
        "latest_submitted_accounts": value.get("latest_submitted_accounts") or value.get("sisteInnsendteAarsregnskap"),
    }
    spans = record.get("spans") or {}
    span_keys = {
        "legal_name": "navn", "legal_form": "organisasjonsform", "employees": "antallAnsatte", "bankrupt": "konkurs",
        "liquidating": "underAvvikling", "business_address": "forretningsadresse", "industry": "naeringskode1",
        "latest_submitted_accounts": "sisteInnsendteAarsregnskap",
    }
    if module == "registry":  # the flat bulk row names legal_form by its code column
        span_keys["legal_form"] = "organisasjonsform.kode"
    for field, claim_value in fields.items():
        if claim_value is None:
            continue
        emitter.claim(field, claim_value, record, module=module, span=spans.get(span_keys[field]))


def emit_accounting_obligation(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("accounting_obligation") or {}
    if not record:
        return
    value = record.get("value") or {}
    method = str(value.get("ruleset_version") or "accounting_obligation_rules")
    # The rule (registry facts -> classification) is ours; the facts it is applied to come from
    # the registry, so a registry response is the cited source: the live response when it carries
    # the fact, else the frozen bulk row.
    filed = value.get("classification") == "filing_observed"
    live = evidence.get("registry_live") or {}
    live_span = (live.get("spans") or {}).get("sisteInnsendteAarsregnskap" if filed else "organisasjonsform")
    if live.get("status") == "available" and live_span:
        emitter.claim("accounting_obligation", value.get("classification"), live, module="accounting_obligation", span=live_span, method=method)
        return
    bulk = evidence.get("registry") or {}
    bulk_value = bulk.get("value") or {}
    key = "sisteInnsendteAarsregnskap" if filed else "organisasjonsform.kode"
    if bulk.get("status") == "available" and bulk_value.get(key):
        span = (bulk.get("spans") or {}).get(key) or json.dumps({key: bulk_value[key]}, ensure_ascii=False, separators=(",", ":"))
        emitter.claim("accounting_obligation", value.get("classification"), bulk, module="accounting_obligation", span=span, method=method)
        return
    emitter.claim("accounting_obligation", value.get("classification"), record, module="accounting_obligation")


def emit_financials(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("financials") or {}
    if record.get("status") != "available":
        if record:
            emitter.claim("annual_accounts", None, record, module="financials")
        return
    records = (record.get("value") or {}).get("records") or []
    if not records:
        emitter.claim("annual_accounts", None, record, module="financials")
        return
    record_spans = (record.get("spans") or {}).get("records") or []
    for index, item in enumerate(records):
        period = item.get("period") or {}
        year = str(period.get("tilDato") or period.get("fraDato") or item.get("record_id"))[:4]
        entry = record_spans[index] if index < len(record_spans) else {}
        emitter.claim(f"annual_accounts.{year}", item, record, module="financials", span=entry.get("period") or entry.get("revenue"))


def emit_financial_history(emitter: Emitter, evidence: dict[str, Any], organisation_number: str | None = None) -> None:
    # The financials module above only returns the most recent filing(s) -- the
    # official API doesn't hand back full historical figures in one call. We do
    # cheaply have the *list* of years with a filing on record (financial_history),
    # which is real "available history" even without every year's full P&L.
    record = evidence.get("financial_history") or {}
    if not record and organisation_number:
        # Not fetched (e.g. dropped to fit a time budget): say so explicitly rather than omit the claim.
        stub = {"status": "not_fetched", "source_url": f"https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{organisation_number}/aar",
                "source_class": "official_annual_account_copies", "retrieved_at": None}
        emitter.claim("annual_accounts_years_on_file", None, stub, module="financial_history")
        return
    if record.get("status") != "available":
        return
    years = (record.get("value") or {}).get("years") or []
    if years:
        emitter.claim("annual_accounts_years_on_file", years, record, module="financial_history", span=(record.get("spans") or {}).get("whole"))


def emit_roles(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("roles") or {}
    if record.get("status") != "available":
        if record:
            emitter.claim("leadership", None, record, module="roles")
        return
    roles = (record.get("value") or {}).get("roles") or []
    if not roles:
        emitter.claim("leadership", None, record, module="roles")
        return
    role_spans = (record.get("spans") or {}).get("roles") or []
    for index, role in enumerate(roles):
        if role.get("inactive"):
            continue
        emitter.claim(f"role.{index}", role, record, module="roles", span=role_spans[index] if index < len(role_spans) else None)


def emit_locations(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("locations") or {}
    if record.get("status") != "available":
        if record:
            emitter.claim("registered_workplaces", None, record, module="locations")
        return
    locations = (record.get("value") or {}).get("locations") or []
    if not locations:
        # Checked, and the registry returned no subunits: an explicit empty list, cited to the
        # response's own totalElements, is different from "not checked".
        emitter.claim("registered_workplaces", [], record, module="locations", span=(record.get("spans") or {}).get("none"))
        return
    location_spans = (record.get("spans") or {}).get("locations") or []
    for index, location in enumerate(locations):
        emitter.claim(f"location.{index}", location, record, module="locations", span=location_spans[index] if index < len(location_spans) else None)


def emit_group(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("group") or {}
    if not record:
        return
    emitter.claim("group_structure", record.get("value"), record, module="group", span=(record.get("spans") or {}).get("whole"))


def emit_website(emitter: Emitter, evidence: dict[str, Any]) -> None:
    # A discovered-and-promoted site lives in evidence.website; otherwise fall back
    # to whatever the registry-listed website module found (may be not_found).
    record = evidence.get("website") or {}
    value = record.get("value") or {}
    assessment = value.get("identity_assessment")
    # A fetched site the identity gate could not tie to this exact entity is not published as
    # the company's website: when the match is uncertain the answer is "ambiguous".
    unverified = record.get("status") == "available" and assessment is not None and not assessment.get("publishable")
    emitter.claim(
        "official_website", value.get("final_url") or value.get("requested_url"), record, module="website",
        span=value.get("title_span"), availability="ambiguous" if unverified else None,
    )


def emit_wikidata(emitter: Emitter, evidence: dict[str, Any]) -> None:
    """Facts from Wikidata, an independent licensed source matched exactly by organisation number."""
    record = evidence.get("wikidata") or {}
    if not record:
        return
    if record.get("status") != "available":
        emitter.claim("wikidata_entity", None, record, module="wikidata")
        return
    value = record.get("value") or {}
    spans = record.get("spans") or {}
    emitter.claim("wikidata_entity", {"qid": value.get("qid"), "url": value.get("entity_url"), "label": value.get("label")}, record, module="wikidata", span=spans.get("i"))
    if value.get("inception"):
        emitter.claim("inception_date", value["inception"], record, module="wikidata", span=spans.get("inc"))
    published = {claim["field"] for claim in emitter.claims}
    keys = {"x": "tw", "facebook": "fb", "linkedin": "li", "instagram": "ig", "youtube": "yt"}
    for platform, url in (value.get("social_links") or {}).items():
        if f"social_profile.{platform}" not in published:
            emitter.claim(f"social_profile.{platform}", url, record, module="wikidata", span=spans.get(keys.get(platform, "")))


def emit_social_links(emitter: Emitter, evidence: dict[str, Any]) -> None:
    # apply_website_identity_gate already independently verified each link against
    # the company's legal name (assess_social_identity, publishable only >= 0.9)
    # before it ever reached evidence.website.value.social_links -- this is not new
    # collection, just claims we were sitting on but never emitted.
    seen: set[str] = set()
    for module in ("website", "website_discovered"):
        record = evidence.get(module) or {}
        pages = {page.get("url"): page for page in (record.get("value") or {}).get("pages") or []}
        links = (record.get("value") or {}).get("social_links") or []
        for link in links:
            url = link.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            platform = link.get("platform") or "unknown"
            # Cite the page the link was actually found on, so its excerpt is a slice of
            # the snapshot the evidence points at (not always the homepage).
            page = pages.get(link.get("found_on"))
            source = record
            if page and link.get("span") and page.get("content_sha256"):
                source = {
                    **record, "source_url": page["url"], "content_sha256": page["content_sha256"],
                    "snapshot_path": page.get("snapshot_path"), "retrieved_at": page.get("retrieved_at") or record.get("retrieved_at"),
                }
            emitter.claim(f"social_profile.{platform}", url, source, module=module, span=link.get("span"))


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return "updated"
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def summarize_profile(evidence: dict[str, Any], observations: list[dict[str, Any]] | None = None, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a grounded, template-based summary -- every sentence traces to claims
    published in the same envelope (`sentences[].fields`, resolved to evidence ids by
    build_envelope). No model invents or infers anything here; this is string formatting
    over facts that already passed the identity/evidence gates.

    `changes` is None when no previous run was supplied; otherwise the material changes
    since that run (possibly empty), which the summary reports.
    """
    registry_module = "registry_live" if (evidence.get("registry_live") or {}).get("status") == "available" else "registry"
    registry_value = (evidence.get(registry_module) or {}).get("value") or {}
    name = registry_value.get("name") or registry_value.get("navn") or "This company"
    legal_form = registry_value.get("legal_form") or registry_value.get("organisasjonsform.kode")
    industry = registry_value.get("industry") or registry_value.get("naeringskode1") or {}
    industry_label = industry.get("beskrivelse") if isinstance(industry, dict) else None
    municipality = None
    address = registry_value.get("business_address") or registry_value.get("forretningsadresse")
    if isinstance(address, dict):
        municipality = address.get("kommune")

    sentences: list[dict[str, Any]] = []

    def say(text: str, *fields: str) -> None:
        sentences.append({"text": text, "fields": list(fields)})

    article = "an" if legal_form and legal_form[0].upper() in "AEIOU" else "a"
    identity_bits = [f"{article} {legal_form}" if legal_form else "a company", "registered in Norway"]
    if industry_label:
        identity_bits.append(f"operating in {industry_label.lower()}")
    if municipality:
        identity_bits.append(f"based in {municipality.title()}")
    say(f"{name} is " + ", ".join(identity_bits) + ".", "legal_name", "legal_form", "industry", "business_address")

    financials_record = evidence.get("financials") or {}
    financials_records = (financials_record.get("value") or {}).get("records") or []
    unknowns: list[str] = []
    if financials_record.get("status") == "available" and financials_records:
        latest = max(financials_records, key=lambda item: (item.get("period") or {}).get("tilDato") or "")
        period = latest.get("period") or {}
        year = str(period.get("tilDato") or "")[:4] or "the latest filed year"
        revenue = latest.get("revenue")
        result = latest.get("annual_result")
        if revenue is not None:
            say(f"Its most recently filed accounts ({year}) report revenue of {revenue:,.0f} NOK" + (f" and a result of {result:,.0f} NOK." if result is not None else "."), f"annual_accounts.{year}")

        prior_year_items = [o for o in (observations or []) if o.get("signal_type") == "prior_year_financials"]
        if prior_year_items:
            prior = max(prior_year_items, key=lambda o: (o.get("metrics") or {}).get("year") or "")
            prior_metrics = prior.get("metrics") or {}
            prior_year = prior_metrics.get("year")
            prior_revenue = prior_metrics.get("revenue")
            if revenue is not None and prior_revenue is not None and prior_year:
                if revenue > prior_revenue:
                    trend = "grew"
                elif revenue < prior_revenue:
                    trend = "fell"
                else:
                    trend = "held steady"
                say(f"Revenue {trend} from {prior_revenue:,.0f} NOK ({prior_year}) to {revenue:,.0f} NOK ({year}).", f"annual_accounts.{year}", f"annual_accounts.{prior_year}")
            elif prior_year:
                say(f"Additional filed figures for {prior_year} are also on file, recovered from the same official annual report.", f"annual_accounts.{prior_year}")
    else:
        unknowns.append("financial results")

    roles_record = evidence.get("roles") or {}
    roles = (roles_record.get("value") or {}).get("roles") or []
    leader = next((r for r in roles if not r.get("inactive") and str(r.get("role_code") or "").upper() == "DAGL"), None)
    if leader and leader.get("name"):
        say(f"{leader['name']} is listed as daglig leder (managing director).", f"role.{roles.index(leader)}")
    elif roles_record.get("status") != "available" or not roles:
        unknowns.append("leadership")

    website_record = evidence.get("website") or {}
    website_value = (website_record.get("value") or {})
    website_url = website_value.get("final_url") or website_value.get("requested_url")
    website_verified = (website_value.get("identity_assessment") or {}).get("publishable", True)
    if website_record.get("status") == "available" and website_url and website_verified:
        say(f"Its verified official website is {website_url}.", "official_website")
    elif website_record.get("status") == "available" and website_url:
        unknowns.append("official website (a site is listed, but nothing on it ties it to this entity)")
    else:
        unknowns.append("official website")

    social_links = (website_value.get("social_links") or []) + ((evidence.get("website_discovered") or {}).get("value") or {}).get("social_links", [])
    if social_links:
        platforms = sorted({link.get("platform", "unknown") for link in social_links})
        say(f"Verified social profiles were found on: {', '.join(platforms)}.", *[f"social_profile.{platform}" for platform in platforms])

    if (evidence.get("group") or {}).get("status") != "available" or not ((evidence.get("group") or {}).get("value") or {}).get("companies"):
        unknowns.append("group/ownership structure")

    news_items = sorted(
        [o for o in (observations or []) if o.get("signal_type") == "public_post"],
        key=lambda o: (o.get("metrics") or {}).get("published_at") or "", reverse=True,
    )
    if news_items:
        latest_news = news_items[0].get("metrics") or {}
        say(f"{len(news_items)} dated news item(s) on its own site; the latest, '{latest_news.get('headline')}', is dated {str(latest_news.get('published_at'))[:10]}.", "site_news.*")
    else:
        unknowns.append("dated news/press")

    workforce_items = [o for o in (observations or []) if o.get("signal_type") == "workforce_snapshot"]
    if workforce_items:
        latest = max(workforce_items, key=lambda o: (o.get("metrics") or {}).get("year") or "")
        metrics = latest.get("metrics") or {}
        measure = "full-time equivalents" if metrics.get("measure") == "full_time_equivalents" else "employees"
        say(f"Its official annual report states {metrics.get('workforce_value')} {measure} ({metrics.get('year')}).", f"workforce_value.{metrics.get('year')}")
    else:
        unknowns.append("workforce size (official annual-report headcount attempted but not found or not applicable for this company)")

    job_items = [o for o in (observations or []) if o.get("signal_type") == "job_posting"]
    if job_items:
        titles = [
            str((o.get("metrics") or {}).get("title"))
            for o in job_items if (o.get("metrics") or {}).get("evidence_kind") != "apply_action"
        ][:3]
        if titles:
            say(f"{len(job_items)} open role(s) shown on its own site, e.g. {', '.join(titles)}.", "job_posting.*")
        else:
            say("Its own site shows an apply action for open roles.", "job_posting.*")
    else:
        unknowns.append("open roles (no job posting, role card or apply action found on its own site)")

    wikidata_value = (evidence.get("wikidata") or {}).get("value") or {}
    if (evidence.get("wikidata") or {}).get("status") == "available" and wikidata_value.get("inception"):
        say(f"Wikidata, matched exactly by organisation number, records it as founded {wikidata_value['inception']}.", "wikidata_entity", "inception_date")

    if changes is not None:
        if changes:
            shown = [f"{item['field']} ({_short(item.get('old_value'))} -> {_short(item.get('new_value'))})" for item in changes[:3]]
            more = f" and {len(changes) - 3} more" if len(changes) > 3 else ""
            say(f"Since the previous run {len(changes)} tracked field(s) changed: {'; '.join(shown)}{more}.")
        else:
            say("No tracked field has changed since the previous run.")

    if unknowns:
        say("Not yet determined: " + "; ".join(unknowns) + ".", "official_website", "leadership", "annual_accounts", "group_structure", "annual_accounts_years_on_file")

    return {
        "text": " ".join(item["text"] for item in sentences),
        "sentences": sentences,
        "unknown_fields": unknowns,
        "grounded_in_claims": True,
    }


def resolve_summary_sources(summary: dict[str, Any], claims: list[dict[str, Any]]) -> None:
    """Attach, to each summary sentence, the evidence ids of the claims it rests on.

    A field ending in ".*" matches every claim with that prefix. Fields with no claim in this
    envelope are dropped, so a sentence never cites something that was not published.
    """
    for sentence in summary["sentences"]:
        evidence_ids: list[str] = []
        cited: list[str] = []
        for field in sentence["fields"]:
            for claim in claims:
                name = str(claim["field"])
                if (name.startswith(field[:-1]) if field.endswith(".*") else name == field):
                    cited.append(name)
                    evidence_ids.extend(item for item in claim["evidence_ids"] if item not in evidence_ids)
        sentence["fields"] = sorted(set(cited))
        sentence["evidence_ids"] = evidence_ids


def emit_external_observations(emitter: Emitter, observations: list[dict[str, Any]]) -> None:
    # Observations come from identity-verified company sites and official annual
    # reports; each already carries its own source, hash, snapshot and exact excerpt.
    news_index = 0
    job_index = 0
    for observation in observations:
        signal_type = observation.get("signal_type")
        metrics = observation.get("metrics") or {}
        if signal_type == "public_post":
            emitter.observation_claim(
                f"site_news.{news_index}",
                {"headline": metrics.get("headline"), "published_at": metrics.get("published_at"), "url": metrics.get("url")},
                observation,
            )
            news_index += 1
        elif signal_type == "job_posting":
            emitter.observation_claim(
                f"job_posting.{job_index}",
                {
                    "title": metrics.get("title"), "url": metrics.get("posting_url"), "date_posted": metrics.get("date_posted"),
                    "employment_type": metrics.get("employment_type"), "evidence_kind": metrics.get("evidence_kind"),
                },
                observation,
            )
            job_index += 1
        elif signal_type == "workforce_snapshot":
            # scripts/run_annual_report_workforce_connector.py: OCR'd (or, when the
            # PDF has a machine-readable text layer, directly extracted) headcount
            # from the company's own official annual-report filing.
            year = metrics.get("year") or observation.get("effective_at") or "unknown"
            emitter.observation_claim(f"workforce_value.{year}", metrics.get("workforce_value"), observation)
        elif signal_type == "prior_year_financials":
            # scripts/extract_prior_year_financials.py: the prior-year comparative
            # figures Norwegian annual reports print beside the current year. Same
            # claim-field convention as emit_financials ("annual_accounts.<year>"):
            # the same kind of fact, for a year the financials API call didn't return.
            year = metrics.get("year") or observation.get("effective_at") or "unknown"
            figures = {key: value for key, value in metrics.items() if key != "year"}
            emitter.observation_claim(f"annual_accounts.{year}", figures, observation)


def build_envelope(profile: dict[str, Any], *, run_id: str, started_at: str, completed_at: str, observations: list[dict[str, Any]] | None = None, snapshot_root: Path | None = None, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    evidence = profile.get("evidence") or {}
    emitter = Emitter(snapshot_root)
    emit_registry_claims(emitter, evidence)
    emit_accounting_obligation(emitter, evidence)
    emit_financials(emitter, evidence)
    emit_financial_history(emitter, evidence, str(profile.get("organisation_number") or ""))
    emit_roles(emitter, evidence)
    emit_locations(emitter, evidence)
    emit_group(emitter, evidence)
    emit_website(emitter, evidence)
    emit_social_links(emitter, evidence)
    emit_wikidata(emitter, evidence)
    emit_external_observations(emitter, observations or [])

    summary = summarize_profile(evidence, observations, changes)
    resolve_summary_sources(summary, emitter.claims)
    metrics = profile.get("run_metrics") or {}
    errors = [
        {"module": module, "note": record.get("note")}
        for module, record in evidence.items()
        if record.get("status") == "source_error"
    ]
    return {
        "organisation_number": profile["organisation_number"],
        "run": {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "terminal_status": "completed",
        },
        "claims": emitter.claims,
        "evidence": emitter.evidence,
        "source_snapshots": emitter.snapshots,
        "summary": summary,
        "changes": changes or [],
        "errors": errors,
        "operations": {
            "requests": metrics.get("requests", 0),
            "runtime_ms": None,
            "third_party_cost_usd": 0,
        },
    }


def derive_spans_from_snapshots(profiles: list[dict[str, Any]], root: Path) -> int:
    """Recompute each official module's excerpts from its saved response body.

    Spans are a pure function of the saved bytes, so this makes profiles produced before
    spans existed (or by an older span rule) consistent with the current code, offline.
    """
    updated = 0
    for profile in profiles:
        for module, record in (profile.get("evidence") or {}).items():
            path = record.get("snapshot_path")
            if not path or not str(path).endswith(".json") or record.get("status") != "available":
                continue
            file = root / path
            if not file.exists():
                continue
            raw = file.read_bytes()
            try:
                spans = module_spans(module, raw, json.loads(raw))
            except ValueError:
                continue
            if spans != record.get("spans"):
                record["spans"] = spans
                updated += 1
    return updated


def build_envelopes_safe(
    profiles: list[dict[str, Any]],
    observations_by_org: dict[str, list[dict[str, Any]]],
    *, run_id: str, started_at: str, completed_at: str, snapshot_root: Path | None = None,
    changes_by_org: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """build_envelope() for every profile, but one bad profile can't drop the batch.

    The hard gate is "exactly N terminal envelopes" for N inputs -- a single
    malformed profile crashing a list comprehension would silently produce zero
    envelopes for everyone else. A conversion failure here gets an honest
    submission_error envelope (empty claims, the exception recorded in errors)
    instead of taking down the whole run.
    """
    envelopes = []
    failures = 0
    for profile in profiles:
        org = str(profile.get("organisation_number") or "")
        try:
            envelopes.append(build_envelope(profile, run_id=run_id, started_at=started_at, completed_at=completed_at, observations=observations_by_org.get(org), snapshot_root=snapshot_root,
                changes=(changes_by_org.get(org, []) if changes_by_org is not None else None)))
        except Exception as exc:  # noqa: BLE001 -- one bad profile must not drop the whole batch
            failures += 1
            envelopes.append({
                "organisation_number": org,
                "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": "submission_error"},
                "claims": [],
                "evidence": [],
                "source_snapshots": [],
                "summary": {"text": "", "unknown_fields": ["all fields -- envelope conversion failed"], "grounded_in_claims": False},
                "changes": [],
                "errors": [{"module": "build_output_contract", "note": f"{type(exc).__name__}: {exc}"}],
                "operations": {"requests": 0, "runtime_ms": None, "third_party_cost_usd": 0},
            })
    return envelopes, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Reshape profiles.jsonl into OUTPUT_CONTRACT.md's claims/evidence envelope.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--observations", action="append", default=[], help="Optional JSONL file(s) of external observations (e.g. dated news, job postings); repeatable")
    parser.add_argument("--changes", help="Optional JSONL of material changes since a previous run (one event per line, with organisation_number); orgs without events then report an empty list")
    parser.add_argument("--snapshot-root", help="Directory the profiles' snapshot paths are relative to (default: the profiles file's directory)")
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    snapshot_root = Path(args.snapshot_root) if args.snapshot_root else Path(args.profiles).parent
    derive_spans_from_snapshots(profiles, snapshot_root)
    observations_by_org: dict[str, list[dict[str, Any]]] = {}
    for observations_path in args.observations:
        for observation in read_jsonl(Path(observations_path)):
            org = str(observation.get("organisation_number"))
            observations_by_org.setdefault(org, []).append(observation)

    changes_by_org: dict[str, list[dict[str, Any]]] | None = None
    if args.changes:
        changes_by_org = {}
        for event in read_jsonl(Path(args.changes)):
            changes_by_org.setdefault(str(event.get("organisation_number")), []).append(event)
    envelopes, conversion_failures = build_envelopes_safe(profiles, observations_by_org, run_id=args.run_id, started_at=args.started_at, completed_at=args.completed_at, snapshot_root=snapshot_root, changes_by_org=changes_by_org)
    write_jsonl(Path(args.output), envelopes)
    total_claims = sum(len(e["claims"]) for e in envelopes)
    print(json.dumps({
        "profiles": len(envelopes),
        "total_claims": total_claims,
        "mean_claims_per_profile": round(total_claims / len(envelopes), 2) if envelopes else 0,
        "conversion_failures": conversion_failures,
    }, indent=2))


if __name__ == "__main__":
    main()
