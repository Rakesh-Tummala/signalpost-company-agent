#!/usr/bin/env python3
"""Single evaluator entrypoint: registry pipeline -> website discovery -> claims envelope.

Chains the pipeline's existing, independently-tested stages into one command, as
required by the "reproducible setup ... one evaluator command" hard gate. Each stage
is a proven script; this just sequences them and threads output paths between them.

Discovery only runs if TAVILY_API_KEY and/or EXA_API_KEY is set in the environment
(server-side secrets, per the locked evaluator budget's own requirement). Without
either key, the agent still produces a complete, valid submission -- just without
search-based website discovery for companies BRREG has no site on file for.
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


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full Signalpost agent: registry batch, discovery, claims envelope.")
    parser.add_argument("--organisations", required=True, help="JSONL/JSON/text list of organisation numbers")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg bulk entity snapshot (gzip)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--discovery-limit", type=int, default=None, help="Cap discovery queries; default is expected-count")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    discovery_limit = args.discovery_limit or args.expected_count

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

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
    ])

    discovery_stages = []
    if os.environ.get("TAVILY_API_KEY", "").strip():
        discovery_stages.append(("tavily", "run_tavily_discovery.py", "TAVILY_API_KEY"))
    if os.environ.get("EXA_API_KEY", "").strip():
        discovery_stages.append(("exa", "run_exa_discovery.py", "EXA_API_KEY"))

    for name, script, _env_var in discovery_stages:
        discovered_path = output_dir / f"profiles-with-{name}.jsonl"
        run([
            args.python, str(ROOT / script),
            "--input", str(profiles_path),
            "--output", str(discovered_path),
            "--report", str(output_dir / f"{name}-discovery-report.json"),
            "--limit", str(discovery_limit),
            "--promote-verified",
        ])
        profiles_path = discovered_path

    if not discovery_stages:
        print("No TAVILY_API_KEY or EXA_API_KEY set -- skipping website discovery.", file=sys.stderr)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    final_profiles_path = output_dir / "profiles.jsonl"
    if profiles_path != final_profiles_path:
        final_profiles_path.write_text(profiles_path.read_text(encoding="utf-8"), encoding="utf-8")

    envelopes_path = output_dir / "envelopes.jsonl"
    run([
        args.python, str(ROOT / "build_output_contract.py"),
        "--profiles", str(final_profiles_path),
        "--output", str(envelopes_path),
        "--run-id", args.run_id,
        "--started-at", started_at,
        "--completed-at", completed_at,
    ])

    summary = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "discovery_stages_run": [name for name, _, _ in discovery_stages],
        "profiles": str(final_profiles_path),
        "envelopes": str(envelopes_path),
    }
    (output_dir / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
