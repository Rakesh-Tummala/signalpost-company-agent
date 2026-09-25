"""Exact excerpts from raw official (BRREG) JSON bodies.

A claim's `claim_span` must be a literal substring of the saved source, not a
paraphrase, so a reader (or a checker) can find it byte-for-byte. Everything here
slices the original response text; nothing is re-serialised.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_SPAN = 400
_DECODER = json.JSONDecoder()


def _clip(text: str) -> str:
    return text[:MAX_SPAN]


def json_key_span(raw: str, key: str, *, after: int = 0) -> tuple[str, int] | None:
    """Return (`"key":value` exactly as written, end offset) for the first `key` at/after `after`."""
    match = re.compile(r'"' + re.escape(key) + r'"\s*:\s*').search(raw, after)
    if not match:
        return None
    try:
        _, end = _DECODER.raw_decode(raw, match.end())
    except ValueError:
        return None
    return _clip(raw[match.start():end]), end


def window_span(raw: str, needle: str, *, before: int = 40, after: int = 120, start: int = 0) -> tuple[str, int] | None:
    index = raw.find(needle, start)
    if index < 0:
        return None
    return _clip(raw[max(0, index - before): index + len(needle) + after]), index + len(needle)


def entity_spans(raw: str) -> dict[str, str]:
    """Spans for the registry entity fields, keyed by the API's own field names."""
    spans: dict[str, str] = {}
    for key in ("organisasjonsnummer", "navn", "organisasjonsform", "antallAnsatte", "konkurs", "underAvvikling",
                "forretningsadresse", "naeringskode1", "hjemmeside", "sisteInnsendteAarsregnskap"):
        found = json_key_span(raw, key)
        if found:
            spans[key] = found[0]
    return spans


def financials_spans(raw: str, body: Any) -> list[dict[str, str]]:
    """One span map per normalised record (same order as normalize_financials)."""
    out: list[dict[str, str]] = []
    cursor = 0
    for item in (body if isinstance(body, list) else [])[:3]:
        entry: dict[str, str] = {}
        identifier = item.get("id")
        anchor = f'"id":{identifier}' if identifier is not None else None
        position = raw.find(anchor, cursor) if anchor else -1
        if position < 0:
            position = cursor
        period = json_key_span(raw, "regnskapsperiode", after=position)
        if period:
            entry["period"] = period[0]
        revenue = json_key_span(raw, "sumDriftsinntekter", after=position)
        if revenue:
            entry["revenue"] = revenue[0]
        out.append(entry)
        cursor = position + 1
    return out


def roles_spans(raw: str, body: Any) -> list[str | None]:
    """One span per normalised role (same order as normalize_roles).

    The span is only the role holder's name object. The surrounding JSON carries a
    date of birth, which this project never stores or republishes, so a wider
    window around the name would leak it.
    """
    out: list[str | None] = []
    cursor = 0
    for group in body.get("rollegrupper", []) if isinstance(body, dict) else []:
        for item in group.get("roller", []):
            person = item.get("person") or {}
            name = person.get("navn") or {}
            entity = item.get("enhet") or {}
            match = None
            if name.get("etternavn"):
                last = re.escape(json.dumps(name["etternavn"], ensure_ascii=False)[1:-1])
                pattern = re.compile(r'"navn"\s*:\s*\{[^{}]*"etternavn"\s*:\s*"' + last + r'"[^{}]*\}')
                match = pattern.search(raw, cursor) or pattern.search(raw)
            else:
                label = entity.get("navn")
                label = label[0] if isinstance(label, list) and label else label
                if isinstance(label, str) and label:
                    inner = re.escape(json.dumps(label, ensure_ascii=False)[1:-1])
                    pattern = re.compile(r'"navn"\s*:\s*\[\s*"' + inner + r'"[^\]]*\]')
                    match = pattern.search(raw, cursor) or pattern.search(raw)
            if match:
                out.append(_clip(match.group(0)))
                cursor = match.end()
            else:
                out.append(None)
    return out


def locations_spans(raw: str, body: Any) -> list[str | None]:
    """One span per normalised subunit (same order as normalize_locations)."""
    rows = ((body or {}).get("_embedded") or {}).get("underenheter") or [] if isinstance(body, dict) else []
    out: list[str | None] = []
    cursor = 0
    for item in rows:
        number = item.get("organisasjonsnummer")
        found = json_key_span(raw, "organisasjonsnummer", after=cursor) if number else None
        if found and str(number) in found[0]:
            out.append(window_span(raw, found[0], before=0, after=100, start=cursor)[0])
            cursor = found[1]
        else:
            out.append(None)
    return out


def module_spans(module: str, raw_bytes: bytes | None, body: Any) -> Any:
    """Compute the span structure for one official module from its raw response."""
    if not raw_bytes:
        return None
    raw = raw_bytes.decode("utf-8", errors="replace")
    if module == "registry_live":
        return entity_spans(raw)
    if module == "financials":
        return {"records": financials_spans(raw, body)}
    if module == "roles":
        return {"roles": roles_spans(raw, body)}
    if module == "locations":
        total = json_key_span(raw, "totalElements")
        return {"locations": locations_spans(raw, body), "none": total[0] if total else None}
    return {"whole": _clip(raw)}
