#!/usr/bin/env python3
"""Extract real hiring facts from an identity-verified company's own site.

A hiring fact is only published when the company's page shows an actual role:
a schema.org `JobPosting`, a role card linking to an individual posting, an item in the
company's own careers feed, or an explicit apply action. A generic "Careers" link or page is not one (the earlier
careers-page detector was retired for exactly that reason). Expired postings
(`validThrough` in the past) are skipped: this is a statement about current hiring.

Works from the page signals stored by the crawl (see norway_company_agent/page_signals.py),
so it needs no new requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.page_signals import CAREERS_PAGE  # noqa: E402

MAX_PER_COMPANY = 10


def observations_for(profile: dict, today: str | None = None) -> list[dict]:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return []
    today = today or date.today().isoformat()
    org = str(profile["organisation_number"])
    proof = [{"type": "website_identity_gate", "score": identity.get("score"), "method": identity.get("method")}]
    found: dict[tuple[str, str], dict] = {}
    pages_with_roles: set[str] = set()

    def build(page: dict, item: dict, title: str) -> dict | None:
        page_url = str(page.get("url") or "")
        digest = str(page.get("content_sha256") or "")
        span = str(item.get("span") or "")
        if not page_url.startswith(("http://", "https://")) or len(digest) != 64 or not span:
            return None
        posting_url = str(item.get("url") or page_url)
        return {
            "id": "company-site-job-" + hashlib.sha256(f"{org}|{page_url}|{posting_url}|{title}".encode()).hexdigest()[:24],
            "organisation_number": org,
            "platform": "company_site",
            "signal_type": "job_posting",
            "source_url": page_url,
            "retrieved_at": page.get("retrieved_at") or website.get("retrieved_at"),
            "content_sha256": digest,
            "snapshot_path": page.get("snapshot_path"),
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_site",
            "evidence_span": span,
            "effective_at": item.get("date"),
            "extraction_method": item.get("evidence_kind"),
            "metrics": {
                "title": title,
                "posting_url": posting_url,
                "date_posted": item.get("date"),
                "valid_through": item.get("valid_through"),
                "employment_type": item.get("employment_type"),
                "evidence_kind": item.get("evidence_kind"),
            },
            "strategy": "company_site_job_posting",
        }

    for page in value.get("pages") or []:
        signals = page.get("signals") or {}
        for item in signals.get("jobs") or []:
            valid_through = item.get("valid_through")
            if valid_through and str(valid_through)[:10] < today:
                continue
            title = str(item.get("title") or "").strip()
            row = build(page, item, title) if title else None
            if row is None:
                continue
            found.setdefault((str(item.get("url") or page.get("url")), title.casefold()), row)
            pages_with_roles.add(str(page.get("url")))
    # A careers RSS/Atom feed (".../jobs/feed") lists real postings: each dated item is a job-feed item.
    for feed in value.get("feeds") or []:
        if not CAREERS_PAGE.search(urlparse(str(feed.get("url") or "")).path):
            continue
        for item in feed.get("items") or []:
            title = str(item.get("title") or "").strip()
            row = build(feed, {**item, "evidence_kind": "job_feed_item"}, title) if title else None
            if row is not None:
                found.setdefault((str(item.get("url")), title.casefold()), row)
    for page in value.get("pages") or []:
        if str(page.get("url")) in pages_with_roles:
            continue
        for item in (page.get("signals") or {}).get("apply_actions") or []:
            title = str(page.get("title") or "Apply action on company careers page").strip()[:200]
            row = build(page, item, title)
            if row is not None:
                found.setdefault((str(item.get("url")), "apply"), row)
    return list(found.values())[:MAX_PER_COMPANY]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract real job postings/role cards/apply actions from verified company sites.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [item for profile in profiles for item in observations_for(profile)]
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["metrics"]["evidence_kind"]] = kinds.get(row["metrics"]["evidence_kind"], 0) + 1
    report = {
        "connector": "exact_company_site_jobs_v1",
        "profiles": len(profiles),
        "companies_with_hiring_facts": len({row["organisation_number"] for row in rows}),
        "observations": len(rows),
        "by_evidence_kind": kinds,
        "claim_boundary": "Only real roles (JobPosting, role card linking to an individual posting, or explicit apply action) on an identity-verified company site; a generic careers page is not a hiring fact.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
