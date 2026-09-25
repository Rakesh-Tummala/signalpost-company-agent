#!/usr/bin/env python3
"""Single evaluator entrypoint for the full Signalpost agent pipeline.

Chains every stage of the pipeline into one command, as required by the
"reproducible setup ... one evaluator command" hard gate:

  1. registry batch (identity, financials, financial_history, roles, group,
     locations, single-page website fetch) -- required, no optional deps.
  2. website discovery (Tavily, then Exa) for companies still missing a site --
     runs only if TAVILY_API_KEY / EXA_API_KEY is set (server-side secret, per
     the locked evaluator budget's own requirement).
  3. deep multi-page site crawl (scrapy) for every company with a website
     candidate -- best-effort: skipped cleanly if the `scrapy` package isn't
     installed in this environment, rather than failing the whole run.
  4. company-owned dated news and real job postings, read from the page signals
     the crawl stored -- pure-Python, no new requests, and independent of which
     crawler ran (the single-pass crawl in step 1 records the same signals).
  5. official annual-report OCR workforce extraction -- best-effort per
     company already (see run_annual_report_workforce_connector.py's own
     broad except); still guarded here so a totally missing tesseract/poppler
     install degrades to "no workforce data" rather than noisy per-company
     errors for the whole batch.
  6. claims/evidence envelope conversion, folding in every observation file
     collected above.

Every optional stage is best-effort: a failure is logged and the pipeline
moves on rather than losing everything already collected. The registry batch
(step 1) and the final conversion (step 6) are the only required stages --
without them there is no submission at all.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd: list[str], *, optional: bool = False) -> bool:
    print("+ " + " ".join(cmd), file=sys.stderr)
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        if not optional:
            raise
        print(f"  (optional stage failed, continuing: {exc})", file=sys.stderr)
        return False


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def merge_profiles(base: list[dict], updated: list[dict], *, only_if_has: str | None = None) -> int:
    """Merge updated rows into base by organisation_number. Returns count merged."""
    updated_by_org = {row["organisation_number"]: row for row in updated}
    merged = 0
    for index, row in enumerate(base):
        candidate = updated_by_org.get(row["organisation_number"])
        if not candidate:
            continue
        if only_if_has and not (candidate.get("evidence") or {}).get(only_if_has):
            continue
        base[index] = candidate
        merged += 1
    return merged


def backfill_top_level_website(profiles: list[dict]) -> None:
    for row in profiles:
        if row.get("website"):
            continue
        web = row.get("evidence", {}).get("website") or {}
        if web.get("status") == "available":
            value = web.get("value") or {}
            row["website"] = value.get("final_url") or web.get("source_url")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full Signalpost agent end to end.")
    parser.add_argument("--organisations", required=True, help="JSONL/JSON/text list of organisation numbers")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg bulk entity snapshot (gzip)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--discovery-limit", type=int, default=None, help="Cap discovery queries; default is expected-count")
    parser.add_argument("--skip-deep-crawl", action="store_true", help="Skip the scrapy multi-page crawl stage")
    parser.add_argument("--skip-workforce-ocr", action="store_true", help="Skip the annual-report OCR workforce stage")
    parser.add_argument("--previous-profiles", help="profiles.jsonl from an earlier run: websites it found are re-crawled and re-verified (never trusted as-is) for companies the registry lists none for")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    discovery_limit = args.discovery_limit or args.expected_count
    # Every stage saves the raw bodies behind its claims here, content-addressed.
    os.environ["SIGNALPOST_SNAPSHOT_DIR"] = str(output_dir / "snapshots")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stages_run: list[str] = []

    # 1. Registry batch: identity, financials, financial_history, roles, group,
    # locations, single-page website. Required.
    profiles_path = output_dir / "profiles.jsonl"
    run([
        args.python, str(ROOT / "run_competition_batch.py"),
        "--organisations", args.organisations,
        "--bulk", args.bulk,
        "--profiles-output", str(profiles_path),
        "--output", str(output_dir / "registry-envelopes.jsonl"),
        "--report", str(output_dir / "registry-report.json"),
        "--run-id", args.run_id,
        "--expected-count", str(args.expected_count),
        "--modules", "registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website",
    ])
    stages_run.append("registry_batch")

    # 2. Website discovery: Tavily, then Exa, for whatever's still missing.
    for name, script, env_var in (("tavily", "run_tavily_discovery.py", "TAVILY_API_KEY"), ("exa", "run_exa_discovery.py", "EXA_API_KEY")):
        if not os.environ.get(env_var, "").strip():
            print(f"No {env_var} set -- skipping {name} discovery.", file=sys.stderr)
            continue
        discovered_path = output_dir / f"profiles-with-{name}.jsonl"
        ok = run([
            args.python, str(ROOT / script),
            "--input", str(profiles_path),
            "--output", str(discovered_path),
            "--report", str(output_dir / f"{name}-discovery-report.json"),
            "--limit", str(discovery_limit),
            "--promote-verified",
        ], optional=True)
        if ok:
            profiles_path = discovered_path
            stages_run.append(f"{name}_discovery")

    profiles = read_jsonl(profiles_path)
    backfill_top_level_website(profiles)
    if args.previous_profiles:
        previous = {row["organisation_number"]: row for row in read_jsonl(Path(args.previous_profiles))}
        seeded = 0
        for row in profiles:
            site = (previous.get(row["organisation_number"]) or {}).get("website")
            if not row.get("website") and site:
                row["website"] = site
                row["website_seed_source"] = "previous_run"
                seeded += 1
        print(f"Seeded {seeded} website(s) from the previous run; they go through the same crawl and identity gate.", file=sys.stderr)
    write_jsonl(output_dir / "profiles.jsonl", profiles)
    profiles_path = output_dir / "profiles.jsonl"

    # 3. Deep multi-page crawl (scrapy) of every company with a website
    # candidate. Best-effort: scrapy may not be installed everywhere.
    news_obs_path = output_dir / "news-observations.jsonl"
    jobs_obs_path = output_dir / "jobs-observations.jsonl"
    has_scrapy = False
    if not args.skip_deep_crawl:
        try:
            import scrapy  # noqa: F401
            has_scrapy = True
        except ImportError:
            has_scrapy = False

    if has_scrapy:
        crawl_input = output_dir / "crawl-input.jsonl"
        write_jsonl(crawl_input, [row for row in profiles if row.get("website")])
        crawled_path = output_dir / "profiles-crawled.jsonl"
        ok = run([
            args.python, str(ROOT / "run_scrapy_websites.py"),
            "--input", str(crawl_input),
            "--output", str(crawled_path),
            "--events", str(output_dir / "crawl-events.jsonl"),
            "--jobdir", str(output_dir / "crawl-jobdir"),
            "--report", str(output_dir / "crawl-report.json"),
        ], optional=True)
        if ok:
            crawled = read_jsonl(crawled_path)
            merge_profiles(profiles, crawled)
            write_jsonl(profiles_path, profiles)
            stages_run.append("deep_crawl")
    else:
        print("scrapy not installed -- using the single-pass site crawl from the registry/discovery stages.", file=sys.stderr)

    # 4. Dated news and real job postings from the stored page signals. Pure
    # Python over data already collected, so it runs whichever crawler produced it.
    run([args.python, str(ROOT / "extract_company_site_news.py"), "--profiles", str(profiles_path), "--output", str(news_obs_path), "--report", str(output_dir / "news-report.json")], optional=True)
    run([args.python, str(ROOT / "extract_company_site_jobs.py"), "--profiles", str(profiles_path), "--output", str(jobs_obs_path), "--report", str(output_dir / "jobs-report.json")], optional=True)
    if news_obs_path.exists():
        stages_run.append("site_news")
    if jobs_obs_path.exists():
        stages_run.append("site_jobs")

    # 5. Official annual-report OCR workforce extraction. Already degrades
    # per-company internally; skip the whole stage only if asked to.
    workforce_obs_path = output_dir / "workforce-observations.jsonl"
    if not args.skip_workforce_ocr:
        orgs_path = output_dir / "all-orgs.txt"
        orgs_path.write_text("\n".join(row["organisation_number"] for row in profiles), encoding="utf-8")
        ok = run([
            args.python, str(ROOT / "run_annual_report_workforce_connector.py"),
            "--profiles", str(profiles_path),
            "--organisations", str(orgs_path),
            "--output", str(workforce_obs_path),
            "--cache", str(output_dir / "workforce-cache"),
            "--report", str(output_dir / "workforce-report.json"),
        ], optional=True)
        if ok:
            stages_run.append("workforce_ocr")

    # 5b. Prior-year comparative financial figures, recovered from the same
    # annual-report OCR cache the workforce stage just populated -- no new
    # downloads or OCR, so this only runs if that cache exists.
    prior_year_obs_path = output_dir / "prior-year-financials-observations.jsonl"
    workforce_cache_dir = output_dir / "workforce-cache"
    if not args.skip_workforce_ocr and workforce_cache_dir.exists():
        ok = run([
            args.python, str(ROOT / "extract_prior_year_financials.py"),
            "--profiles", str(profiles_path),
            "--cache", str(workforce_cache_dir),
            "--output", str(prior_year_obs_path),
            "--report", str(output_dir / "prior-year-financials-report.json"),
        ], optional=True)
        if ok:
            stages_run.append("prior_year_financials")

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 6. Claims/evidence conversion, folding in every observation file collected.
    envelopes_path = output_dir / "envelopes.jsonl"
    convert_cmd = [
        args.python, str(ROOT / "build_output_contract.py"),
        "--profiles", str(profiles_path),
        "--output", str(envelopes_path),
        "--run-id", args.run_id,
        "--started-at", started_at,
        "--completed-at", completed_at,
    ]
    for obs_path in (news_obs_path, jobs_obs_path, workforce_obs_path, prior_year_obs_path):
        if obs_path.exists():
            convert_cmd += ["--observations", str(obs_path)]
    run(convert_cmd)
    stages_run.append("claims_conversion")

    # 7. Offline HTML viewer -- best-effort, never blocks the submission artifact.
    viewer_path = output_dir / "viewer.html"
    ok = run([
        args.python, str(ROOT / "build_viewer.py"),
        "--envelopes", str(envelopes_path),
        "--profiles", str(profiles_path),
        "--output", str(viewer_path),
    ], optional=True)
    if ok:
        stages_run.append("viewer")

    summary = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "stages_run": stages_run,
        "profiles": str(profiles_path),
        "envelopes": str(envelopes_path),
    }
    (output_dir / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
