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
