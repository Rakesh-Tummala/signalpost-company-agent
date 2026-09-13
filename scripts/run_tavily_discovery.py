#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.discovery import build_company_search_query, choose_search_candidate, parse_tavily_web_results  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def tavily_search(profile: dict, *, api_key: str | None, timeout: float, max_results: int) -> tuple[list[dict], dict]:
    query = build_company_search_query(profile)
    body = json.dumps({
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["X-Tavily-Access-Mode"] = "keyless"
    request = urllib.request.Request(TAVILY_ENDPOINT, data=body, headers=headers, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = json.loads(raw)
        return parse_tavily_web_results(payload, query=query), {
            "status": response.status,
            "latency_ms": elapsed_ms,
            "bytes": len(raw),
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return [], {
            "status": getattr(exc, "code", 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "bytes": 0,
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "error": type(exc).__name__,
        }


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Transient Tavily discovery followed by independent exact-entity site crawling.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--limit", type=int, default=20, help="Maximum missing-website profiles to query")
    parser.add_argument("--max-results", type=int, default=10, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--min-interval", type=float, default=0.5)
    parser.add_argument("--promote-verified", action="store_true", help="Copy exact-entity discovered sites into canonical website evidence")
    parser.add_argument("--api-key-env", default="TAVILY_API_KEY", help="Optional; falls back to keyless mode if unset")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Write partial output every N queried profiles")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be positive")
    api_key = os.environ.get(args.api_key_env, "").strip() or None

    rows = read_jsonl(Path(args.input))
    counts: Counter[str] = Counter()
    provider_latencies: list[int] = []
    started_at = utc_now()
    queried = 0
    for row in rows:
        if queried >= args.limit:
            break
        if row.get("website") or (row.get("evidence", {}).get("website") or {}).get("status") == "available":
            counts["registry_website_present_skipped"] += 1
            continue
        if not row.get("name"):
            counts["skipped_no_name"] += 1
            row.setdefault("evidence", {})["website_discovery"] = evidence(
                "website_discovery",
                "not_applicable",
                "transient_tavily_search",
                TAVILY_ENDPOINT,
                note="No legal name available (absent from both the bulk snapshot and the live registry lookup); nothing to search for.",
            )
            continue
        queried += 1
        results, operation = tavily_search(row, api_key=api_key, timeout=args.timeout, max_results=args.max_results)
        provider_latencies.append(operation["latency_ms"])
        counts["provider_requests"] += 1
        counts["provider_bytes"] += operation["bytes"]
        if operation.get("error"):
            counts["provider_errors"] += 1
        decision = choose_search_candidate(row, results)
        selected = decision.get("selected")
        discovery_summary = {
            "provider": "tavily_search_api",
            "access_mode": "api_key" if api_key else "keyless",
            "query_sha256": operation["query_sha256"],
            "candidate_count": len(results),
            "selected_for_independent_crawl": bool(selected),
            "provider_status": operation["status"],
            "retention_policy": "Search titles, snippets, ranks, query text, and raw response are not persisted.",
        }
        if not selected:
            counts["abstained_before_crawl"] += 1
            row.setdefault("evidence", {})["website_discovery"] = evidence(
                "website_discovery",
                "not_found",
                "transient_tavily_search",
                TAVILY_ENDPOINT,
                value=discovery_summary,
                note="No result passed the deterministic crawl-candidate gate; raw search output was discarded.",
            )
            time.sleep(args.min_interval)
            continue

        website, web_ops = fetch_website(selected["url"], timeout=args.timeout)
        gated = apply_website_identity_gate(row, website)
        website = gated["website"]
        assessment = gated["assessment"]
        publishable = bool(assessment and assessment["publishable"])
        value = website.get("value") or {}
        # Only independently fetched page evidence is retained. Tavily title/snippet/rank/query are discarded.
        website["source_type"] = "search_discovered_company_website"
        website["value"] = value
        row.setdefault("evidence", {})["website_discovery"] = evidence(
            "website_discovery",
            "available" if publishable else "not_found",
            "transient_tavily_search_then_independent_crawl",
            TAVILY_ENDPOINT,
            value={**discovery_summary, "independent_page_url": website.get("source_url") if publishable else None},
            note="Search output was transient. Publication depends only on independently fetched exact-entity page evidence.",
        )
        row["evidence"]["website_discovered"] = website
        counts["independent_crawls"] += 1
        counts["crawl_requests"] += web_ops.get("requests", 0)
        if publishable and website.get("status") == "available":
            counts["verified_sites"] += 1
            if args.promote_verified:
                row["evidence"]["website"] = website
                row["website"] = website.get("value", {}).get("final_url") or website.get("source_url")
                counts["promoted_sites"] += 1
        else:
            counts["quarantined_sites"] += 1
        if queried % args.checkpoint_every == 0:
            write_jsonl(Path(args.output), rows)
        time.sleep(args.min_interval)

    write_jsonl(Path(args.output), rows)
    report = {
        "generated_at": utc_now(),
        "started_at": started_at,
        "provider": "Tavily Search API",
        "provider_endpoint": TAVILY_ENDPOINT,
        "access_mode": "api_key" if api_key else "keyless",
        "input_profiles": len(rows),
        "queried_missing_website_profiles": queried,
        "counts": dict(counts),
        "provider_latency_ms": {"p50": percentile(provider_latencies, 0.5), "p95": percentile(provider_latencies, 0.95)},
        "raw_search_results_persisted": False,
        "promote_verified_enabled": args.promote_verified,
        "qualification": "not_evaluated_on_500_org_external_final_corpus",
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
