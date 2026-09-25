#!/usr/bin/env python3
"""Extract dated news/press items from an identity-verified company's own site.

Only items with a parseable publication date count: schema.org
`Article`/`NewsArticle`/`BlogPosting` (`datePublished`), `<time datetime>` cards,
article meta tags, and items from the site's own RSS/Atom feed. A page that merely
lives under `/news/` with no date is not a dated article and is not published.

Works from the page signals and feeds stored by the crawl (see
norway_company_agent/page_signals.py), so it needs no new requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.page_signals import CAREERS_PAGE  # noqa: E402

MAX_PER_COMPANY = 10


def observations_for(profile: dict) -> list[dict]:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return []
    org = str(profile["organisation_number"])
    proof = [{"type": "website_identity_gate", "score": identity.get("score"), "method": identity.get("method")}]
    found: dict[tuple[str, str], dict] = {}
    seen_stories: set[tuple[str, str]] = set()

    def add(source: dict, item: dict) -> None:
        source_url = str(source.get("url") or "")
        digest = str(source.get("content_sha256") or "")
        span = str(item.get("span") or "")
        if not source_url.startswith(("http://", "https://")) or len(digest) != 64 or not span:
            return
        article_url = str(item.get("url") or source_url)
        key = (article_url, str(item.get("date")))
        same_story = (str(item.get("title") or "").casefold(), str(item.get("date"))[:10])
        if key in found or same_story in seen_stories:
            return
        seen_stories.add(same_story)
        found[key] = {
            "id": "company-site-news-" + hashlib.sha256(f"{org}|{article_url}|{item.get('date')}".encode()).hexdigest()[:24],
            "organisation_number": org,
            "platform": "company_site",
            "signal_type": "public_post",
            "source_url": source_url,
            "retrieved_at": source.get("retrieved_at") or website.get("retrieved_at"),
            "content_sha256": digest,
            "snapshot_path": source.get("snapshot_path"),
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_site",
            "evidence_span": span,
            "effective_at": item.get("date"),
            "extraction_method": item.get("evidence_kind"),
            "metrics": {
                "headline": item.get("title"),
                "published_at": item.get("date"),
                "url": article_url,
                "evidence_kind": item.get("evidence_kind"),
                "interpretation": "Company-owned dated publication; not independent sentiment.",
            },
            "strategy": "company_site_dated_article",
        }

    for feed in value.get("feeds") or []:
        if CAREERS_PAGE.search(urlparse(str(feed.get("url") or "")).path):
            continue  # a careers feed lists vacancies (see extract_company_site_jobs.py), not news
        for item in feed.get("items") or []:
            add(feed, item)
    for page in value.get("pages") or []:
        for item in (page.get("signals") or {}).get("articles") or []:
            add(page, item)
    ordered = sorted(found.values(), key=lambda row: str(row["metrics"]["published_at"]), reverse=True)
    return ordered[:MAX_PER_COMPANY]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract dated news/press items from verified company sites.")
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
        "connector": "exact_company_site_dated_articles_v1",
        "profiles": len(profiles),
        "companies_with_dated_articles": len({row["organisation_number"] for row in rows}),
        "observations": len(rows),
        "by_evidence_kind": kinds,
        "claim_boundary": "Only items with a parseable publication date on an identity-verified company site or its own RSS/Atom feed; an undated news page is not published.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
