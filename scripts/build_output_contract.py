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
import json
from pathlib import Path
from typing import Any

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
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


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


class Emitter:
    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self._evidence_ids: dict[str, str] = {}

    def evidence_id(self, module: str, record: dict[str, Any]) -> str:
        key = module
        if key in self._evidence_ids:
            return self._evidence_ids[key]
        eid = f"ev-{module}"
        self._evidence_ids[key] = eid
        self.evidence.append({
            "id": eid,
            "source_url": record.get("source_url"),
            "source_class": record.get("source_class") or record.get("source_type"),
            "retrieved_at": record.get("retrieved_at"),
            "content_sha256": record.get("content_sha256"),
            "claim_span": None,
        })
        return eid

    def claim(self, field: str, value: Any, record: dict[str, Any], *, module: str) -> None:
        eid = self.evidence_id(module, record)
        self.claims.append({
            "field": field,
            "value": value,
            "availability": availability_for(record.get("status")),
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
    for field, claim_value in fields.items():
        if claim_value is None:
            continue
        emitter.claim(field, claim_value, record, module=module)


def emit_accounting_obligation(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("accounting_obligation") or {}
    if not record:
        return
    value = (record.get("value") or {}).get("classification")
    emitter.claim("accounting_obligation", value, record, module="accounting_obligation")


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
    for item in records:
        period = item.get("period") or {}
        year = str(period.get("tilDato") or period.get("fraDato") or item.get("record_id"))[:4]
        emitter.claim(f"annual_accounts.{year}", item, record, module="financials")


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
    for index, role in enumerate(roles):
        if role.get("inactive"):
            continue
        emitter.claim(f"role.{index}", role, record, module="roles")


def emit_locations(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("locations") or {}
    if record.get("status") != "available":
        if record:
            emitter.claim("registered_workplaces", None, record, module="locations")
        return
    locations = (record.get("value") or {}).get("locations") or []
    if not locations:
        emitter.claim("registered_workplaces", None, record, module="locations")
        return
    for index, location in enumerate(locations):
        emitter.claim(f"location.{index}", location, record, module="locations")


def emit_group(emitter: Emitter, evidence: dict[str, Any]) -> None:
    record = evidence.get("group") or {}
    if not record:
        return
    emitter.claim("group_structure", record.get("value"), record, module="group")


def emit_website(emitter: Emitter, evidence: dict[str, Any]) -> None:
    # A discovered-and-promoted site lives in evidence.website; otherwise fall back
    # to whatever the registry-listed website module found (may be not_found).
    record = evidence.get("website") or {}
    value = record.get("value") or {}
    emitter.claim("official_website", value.get("final_url") or value.get("requested_url"), record, module="website")


def emit_social_links(emitter: Emitter, evidence: dict[str, Any]) -> None:
    # apply_website_identity_gate already independently verified each link against
    # the company's legal name (assess_social_identity, publishable only >= 0.9)
    # before it ever reached evidence.website.value.social_links -- this is not new
    # collection, just claims we were sitting on but never emitted.
    seen: set[str] = set()
    for module in ("website", "website_discovered"):
        record = evidence.get(module) or {}
        links = (record.get("value") or {}).get("social_links") or []
        for link in links:
            url = link.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            platform = link.get("platform") or "unknown"
            emitter.claim(f"social_profile.{platform}", url, record, module=module)


def summarize_profile(evidence: dict[str, Any]) -> dict[str, Any]:
    """Build a grounded, template-based summary -- every sentence traces to a claim
    we already published above. No model invents or infers anything here; this is
    string formatting over facts that already passed the identity/evidence gates.
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

    sentences: list[str] = []
    article = "an" if legal_form and legal_form[0].upper() in "AEIOU" else "a"
    identity_bits = [f"{article} {legal_form}" if legal_form else "a company", "registered in Norway"]
    if industry_label:
        identity_bits.append(f"operating in {industry_label.lower()}")
    if municipality:
        identity_bits.append(f"based in {municipality.title()}")
    sentences.append(f"{name} is " + ", ".join(identity_bits) + ".")

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
            sentences.append(f"Its most recently filed accounts ({year}) report revenue of {revenue:,.0f} NOK" + (f" and a result of {result:,.0f} NOK." if result is not None else "."))
    else:
        unknowns.append("financial results")

    roles_record = evidence.get("roles") or {}
    roles = (roles_record.get("value") or {}).get("roles") or []
    leader = next((r for r in roles if not r.get("inactive") and str(r.get("role_code") or "").upper() == "DAGL"), None)
    if leader and leader.get("name"):
        sentences.append(f"{leader['name']} is listed as daglig leder (managing director).")
    elif roles_record.get("status") != "available" or not roles:
        unknowns.append("leadership")

    website_record = evidence.get("website") or {}
    website_value = (website_record.get("value") or {})
    website_url = website_value.get("final_url") or website_value.get("requested_url")
    if website_record.get("status") == "available" and website_url:
        sentences.append(f"Its verified official website is {website_url}.")
    else:
        unknowns.append("official website")

    social_links = (website_value.get("social_links") or []) + ((evidence.get("website_discovered") or {}).get("value") or {}).get("social_links", [])
    if social_links:
        platforms = ", ".join(sorted({link.get("platform", "unknown") for link in social_links}))
        sentences.append(f"Verified social profiles were found on: {platforms}.")

    if (evidence.get("group") or {}).get("status") != "available" or not ((evidence.get("group") or {}).get("value") or {}).get("companies"):
        unknowns.append("group/ownership structure")
    unknowns.append("hiring activity and dated public activity (no rights-cleared source integrated yet)")

    if unknowns:
        sentences.append("Not yet determined: " + "; ".join(unknowns) + ".")

    return {
        "text": " ".join(sentences),
        "unknown_fields": unknowns,
        "grounded_in_claims": True,
    }


def build_envelope(profile: dict[str, Any], *, run_id: str, started_at: str, completed_at: str) -> dict[str, Any]:
    evidence = profile.get("evidence") or {}
    emitter = Emitter()
    emit_registry_claims(emitter, evidence)
    emit_accounting_obligation(emitter, evidence)
    emit_financials(emitter, evidence)
    emit_roles(emitter, evidence)
    emit_locations(emitter, evidence)
    emit_group(emitter, evidence)
    emit_website(emitter, evidence)
    emit_social_links(emitter, evidence)

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
        "summary": summarize_profile(evidence),
        "changes": [],
        "errors": errors,
        "operations": {
            "requests": metrics.get("requests", 0),
            "runtime_ms": None,
            "third_party_cost_usd": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reshape profiles.jsonl into OUTPUT_CONTRACT.md's claims/evidence envelope.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--completed-at", required=True)
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    envelopes = [build_envelope(profile, run_id=args.run_id, started_at=args.started_at, completed_at=args.completed_at) for profile in profiles]
    write_jsonl(Path(args.output), envelopes)
    total_claims = sum(len(e["claims"]) for e in envelopes)
    print(json.dumps({
        "profiles": len(envelopes),
        "total_claims": total_claims,
        "mean_claims_per_profile": round(total_claims / len(envelopes), 2) if envelopes else 0,
    }, indent=2))


if __name__ == "__main__":
    main()
