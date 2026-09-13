#!/usr/bin/env python3
"""Fetch just evidence.financial_history for an existing profiles.jsonl and merge it in.

run_competition_batch.py's --resume skips a profile only when it already has every
requested module, so asking it to add one new module to an existing batch means
re-fetching everything else too. This does the one missing module only, respecting
the same 2.1s-between-starts rate limit official.py already enforces for this
specific endpoint (_reserve_history_slot / _fetch_history).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.official import fetch_official_modules  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch financial_history for every profile missing it.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    args = parser.parse_args()

    profiles = read_jsonl(Path(args.profiles))
    output_path = Path(args.output)
    pending = [p for p in profiles if (p.get("evidence") or {}).get("financial_history") is None]
    print(f"{len(pending)} of {len(profiles)} profiles missing financial_history", file=sys.stderr)

    for index, profile in enumerate(pending, 1):
        records, _metrics = fetch_official_modules(profile["organisation_number"], {"financial_history"})
        profile.setdefault("evidence", {}).update(records)
        if index % args.checkpoint_every == 0 or index == len(pending):
            write_jsonl(output_path, profiles)
            print(f"{index}/{len(pending)} done", file=sys.stderr)

    write_jsonl(output_path, profiles)
    print("complete", file=sys.stderr)


if __name__ == "__main__":
    main()
