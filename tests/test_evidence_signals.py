"""Tests for saved sources, exact claim excerpts, and real job/news extraction."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.evidence_spans import json_key_span, module_spans, roles_spans  # noqa: E402
from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.page_signals import extract_page_signals, parse_date, parse_feed_items  # noqa: E402
from norway_company_agent.snapshot_store import save_snapshot  # noqa: E402
from norway_company_agent.snapshots import SnapshotFetcher  # noqa: E402
from scripts.build_output_contract import build_envelope, summarize_profile  # noqa: E402
from scripts.extract_company_site_jobs import observations_for as job_observations  # noqa: E402
from scripts.extract_company_site_news import observations_for as news_observations  # noqa: E402

PAGE = (ROOT / "tests" / "fixtures" / "careers-news-page.html").read_text(encoding="utf-8")
PAGE_URL = "https://www.eksempel.no/karriere"
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Eksempel</title>
<item><title>Vi vant anbudet</title><link>https://www.eksempel.no/nyheter/anbud</link><pubDate>Tue, 03 Mar 2026 10:00:00 +0100</pubDate></item>
<item><title><![CDATA[Uten dato]]></title><link>https://www.eksempel.no/nyheter/uten-dato</link></item>
<item><title>Omtale hos andre</title><link>https://andre.example.com/omtale</link><pubDate>Mon, 02 Mar 2026 10:00:00 +0100</pubDate></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Ny ansatt</title><link href="https://www.eksempel.no/nyheter/ny-ansatt"/><updated>2026-01-15T08:30:00Z</updated></entry></feed>"""


class PageSignalsTests(unittest.TestCase):
    def setUp(self):
        self.signals = extract_page_signals(PAGE, PAGE_URL)

    def test_json_ld_job_posting_and_article_are_extracted(self):
        jobs = {item["title"]: item for item in self.signals["jobs"]}
        self.assertEqual(jobs["Sykepleier"]["evidence_kind"], "json_ld_jobposting")
        self.assertEqual(jobs["Sykepleier"]["date"], "2026-04-01")
        articles = {item["title"]: item for item in self.signals["articles"]}
        self.assertEqual(articles["Nytt kontor i Bergen"]["date"], "2026-03-10T08:00:00Z")
        self.assertEqual(articles["Nytt kontor i Bergen"]["url"], "https://www.eksempel.no/nyheter/nytt-kontor")

    def test_time_element_card_carries_headline_date_and_same_site_url_and_future_dates_are_rejected(self):
        card = next(item for item in self.signals["articles"] if item["evidence_kind"] == "time_element")
        self.assertEqual((card["title"], card["date"], card["url"]), ("Ny avtale med kommunen", "2026-02-11", "https://www.eksempel.no/nyheter/ny-avtale"))
        self.assertNotIn("Fremtidig hendelse her", [item["title"] for item in self.signals["articles"]])

    def test_a_card_links_to_its_article_not_to_the_logo_or_home_link_in_the_same_container(self):
        html = (
            '<div class="card"><a href="/"><img alt="logo"></a><time datetime="2026-03-05">5. mars</time>'
            '<h3>Nytt anlegg åpnet i Tromsø</h3><a href="/nyheter/nytt-anlegg">Les mer</a></div>'
        )
        card = extract_page_signals(html, "https://www.eksempel.no/")["articles"][0]
        self.assertEqual((card["title"], card["url"]), ("Nytt anlegg åpnet i Tromsø", "https://www.eksempel.no/nyheter/nytt-anlegg"))

    def test_meta_published_time_counts_as_a_dated_article_only_on_a_news_style_url(self):
        on_news_url = extract_page_signals(PAGE, "https://www.eksempel.no/nyheter/vi-har-faatt-ny-sjef")
        meta = next(item for item in on_news_url["articles"] if item["evidence_kind"] == "meta_published_time")
        self.assertEqual(meta["date"], "2026-05-02T07:00:00Z")
        # The same tags on a careers/contact-style page are a page timestamp, not news.
        self.assertNotIn("meta_published_time", [item["evidence_kind"] for item in self.signals["articles"]])

    def test_a_plain_article_type_on_an_ordinary_page_is_not_news_but_a_post_type_is(self):
        def page(schema_type):
            return (
                '<html><head><meta property="og:type" content="article"><script type="application/ld+json">'
                '{"@type":"' + schema_type + '","headline":"Kontakt oss i Bergen","datePublished":"2025-12-08T10:00:00Z"}</script></head><body></body></html>'
            )
        self.assertEqual(extract_page_signals(page("Article"), "https://www.eksempel.no/kontakt/")["articles"], [])
        self.assertEqual(len(extract_page_signals(page("Article"), "https://www.eksempel.no/nyheter/kontakt-oss")["articles"]), 1)
        self.assertEqual(len(extract_page_signals(page("NewsArticle"), "https://www.eksempel.no/kontakt/")["articles"]), 1)
        self.assertEqual(len(extract_page_signals(page("Article"), "https://www.eksempel.no/2025/12/kontakt-oss/")["articles"]), 1)

    def test_comment_feeds_are_not_discovered(self):
        html = (
            '<html><head><link rel="alternate" type="application/rss+xml" href="/feed/">'
            '<link rel="alternate" type="application/rss+xml" href="/comments/feed/">'
            '<link rel="alternate" type="application/rss+xml" href="/?feed=comments-rss2"></head></html>'
        )
        self.assertEqual(extract_page_signals(html, "https://www.eksempel.no/")["feeds"], ["https://www.eksempel.no/feed/"])

    def test_role_cards_are_real_roles_not_generic_careers_navigation(self):
        titles = {item["title"] for item in self.signals["jobs"] if item["evidence_kind"] == "role_card"}
        self.assertEqual(titles, {"Rørlegger", "Prosjektleder bygg"})  # title read from the card heading when the link says "Les mer"
        everything = json.dumps(self.signals, ensure_ascii=False)
        self.assertNotIn("Se alle stillinger", everything)

    def test_apply_action_is_detected_on_a_careers_page(self):
        self.assertEqual([item["url"] for item in self.signals["apply_actions"]], ["https://example.teamtailor.com/jobs/123-x/apply"])

    def test_role_cards_and_apply_actions_are_only_read_on_careers_like_pages(self):
        signals = extract_page_signals(PAGE, "https://www.eksempel.no/om-oss")
        self.assertEqual([item["evidence_kind"] for item in signals["jobs"]], ["json_ld_jobposting"])
        self.assertEqual(signals["apply_actions"], [])

    def test_vacancy_links_named_in_the_file_name_and_heavily_styled_are_role_cards_but_listings_are_not(self):
        # Shaped like a real careers page (sulland.no/jobb): vacancy words live in the
        # file name, the link wraps a block element (which a lenient parser moves out of
        # the <a>), and the markup around the title is long.
        styling = "tw-group tw-inline-block tw-p-0.5 tw-border-2 tw-ml-1 tw-border-transparent tw-cursor-pointer hover:tw-no-underline " * 3
        card = '<p><template><a class="{c}" href="{href}" target="_blank"><div class="tw-relative tw-inline-flex {c}"><span class="tw-flex-grow">{title}</span></div></a></template></p>'
        html = (
            "<html><body><h2>Ledige stillinger i konsernet</h2>"
            + card.format(c=styling, href="https://www.eksempel.no/stilling-ledig-hos-toyota-i-elverum", title="Ledige stillinger hos Toyota i Elverum")
            + card.format(c=styling, href="https://www.eksempel.no/ledig-stilling-servicemarkedsleder-kongsvinger", title="Ledig stilling Servicemarkedsleder Kongsvinger")
            + '<a href="/jobbmuligheter-innen-bilskadefaget">Jobbmuligheter innen bilskadefaget</a><a href="/jobb">Jobb</a></body></html>'
        )
        jobs = extract_page_signals(html, "https://www.eksempel.no/jobb")["jobs"]
        self.assertEqual([job["title"] for job in jobs], ["Ledig stilling Servicemarkedsleder Kongsvinger"])
        self.assertEqual(jobs[0]["url"], "https://www.eksempel.no/ledig-stilling-servicemarkedsleder-kongsvinger")
        self.assertIn(jobs[0]["span"], html)
        self.assertIn("Servicemarkedsleder", jobs[0]["span"])
        self.assertLessEqual(len(jobs[0]["span"]), 400)

    def test_every_span_is_a_literal_slice_of_the_page(self):
        checked = 0
        for group in ("articles", "jobs", "apply_actions", "social_links"):
            for item in self.signals[group]:
                self.assertIn(item["span"], PAGE, item)
                checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_feed_link_is_discovered(self):
        self.assertEqual(self.signals["feeds"], ["https://www.eksempel.no/feed.xml"])

    def test_social_links_come_from_anchors_json_ld_and_meta_with_their_source(self):
        by_platform = {item["platform"]: item for item in self.signals["social_links"]}
        self.assertEqual(by_platform["facebook"]["source"], "json_ld_sameAs")
        self.assertEqual(by_platform["instagram"]["source"], "anchor")
        self.assertEqual(by_platform["x"]["url"], "https://x.com/eksempelas")
        self.assertEqual(by_platform["x"]["source"], "twitter_meta")
        self.assertEqual(by_platform["linkedin"]["url"], "https://linkedin.com/company/eksempel-as")

    def test_undated_or_external_articles_are_not_articles(self):
        html = (
            '<article><h2>Nyhet uten dato som er lang nok</h2><a href="/nyheter/x">les</a></article>'
            '<article><time datetime="2026-01-05">5. jan</time><h2><a href="https://andre.example.com/omtale">Omtale hos et annet selskap</a></h2></article>'
        )
        self.assertEqual(extract_page_signals(html, "https://www.eksempel.no/")["articles"], [])

    def test_parse_date_accepts_iso_and_rfc822_and_rejects_future_and_garbage(self):
        self.assertEqual(parse_date("2026-03-10"), "2026-03-10")
        self.assertEqual(parse_date("Tue, 03 Mar 2026 10:00:00 +0100"), "2026-03-03T09:00:00Z")
        self.assertIsNone(parse_date("2099-01-01"))
        self.assertIsNone(parse_date("PT8H"))
        self.assertIsNone(parse_date("12. mars 2026"))
        self.assertIsNone(parse_date(""))


class FeedTests(unittest.TestCase):
    def test_rss_items_need_a_date_and_a_same_site_link(self):
        items = parse_feed_items(RSS, "https://www.eksempel.no/feed.xml")
        self.assertEqual([item["title"] for item in items], ["Vi vant anbudet"])
        self.assertEqual(items[0]["date"], "2026-03-03T09:00:00Z")
        self.assertIn(items[0]["span"], RSS)

    def test_password_protected_posts_and_comments_are_not_news(self):
        feed = (
            '<rss><channel><item><title>Protected: For ansatte</title><link>https://www.eksempel.no/for-ansatte/</link><pubDate>Mon, 27 Apr 2026 12:23:22 +0000</pubDate></item>'
            '<item><title>Comment on Hello world! by A WordPress Commenter</title><link>https://www.eksempel.no/hello/#comment-1</link><pubDate>Mon, 27 Apr 2026 12:23:22 +0000</pubDate></item>'
            '<item><title>Kommentar til Lysbøyle av kunde</title><link>https://www.eksempel.no/lysboyle/</link><pubDate>Mon, 27 Apr 2026 12:23:22 +0000</pubDate></item>'
            '<item><title>Ny avdeling åpnet</title><link>https://www.eksempel.no/ny-avdeling/</link><pubDate>Mon, 27 Apr 2026 12:23:22 +0000</pubDate></item></channel></rss>'
        )
        self.assertEqual([item["title"] for item in parse_feed_items(feed, "https://www.eksempel.no/feed/")], ["Ny avdeling åpnet"])

    def test_atom_entries_are_parsed(self):
        items = parse_feed_items(ATOM, "https://www.eksempel.no/feed.xml")
        self.assertEqual([(item["title"], item["url"], item["date"]) for item in items], [("Ny ansatt", "https://www.eksempel.no/nyheter/ny-ansatt", "2026-01-15T08:30:00Z")])


class FeedRecordTests(unittest.TestCase):
    def test_feed_bodies_are_recognised_and_recorded_with_their_hash_and_items(self):
        from norway_company_agent.page_signals import feed_record, looks_like_feed

        self.assertTrue(looks_like_feed(RSS.encode("utf-8")))
        self.assertTrue(looks_like_feed(b"  <FEED xmlns='http://www.w3.org/2005/Atom'></FEED>"))
        self.assertFalse(looks_like_feed(b"<!doctype html><html></html>"))
        record = feed_record("https://www.eksempel.no/feed.xml", RSS.encode("utf-8"), "2026-09-01T00:00:00Z")
        self.assertEqual(record["content_sha256"], hashlib.sha256(RSS.encode("utf-8")).hexdigest())
        self.assertEqual([item["title"] for item in record["items"]], ["Vi vant anbudet"])

    def test_the_spider_feed_event_carries_the_parsed_feed(self):
        from norway_company_agent.crawl_events import extract_feed_event

        event = extract_feed_event(
            organisation_number="923609016", requested_url="https://www.eksempel.no/feed.xml", final_url="https://www.eksempel.no/feed.xml",
            status_code=200, content_type="application/rss+xml", body=RSS.encode("utf-8"),
        )
        self.assertEqual((event["page_kind"], event["status"]), ("feed", "available"))
        self.assertEqual(len(event["feed"]["items"]), 1)
        html = extract_feed_event(
            organisation_number="923609016", requested_url="https://www.eksempel.no/feed/", final_url="https://www.eksempel.no/feed/",
            status_code=200, content_type="text/html", body=b"<!doctype html><html></html>",
        )
        self.assertEqual(html["status"], "source_error")


class EvidenceSpanTests(unittest.TestCase):
    def test_json_key_span_returns_the_key_and_value_exactly_as_written(self):
        raw = '{"a":1,"navn":"Sm\\u00f8rbr\\u00f8d AS","obj":{"x":[1,2]}}'
        self.assertEqual(json_key_span(raw, "navn")[0], '"navn":"Sm\\u00f8rbr\\u00f8d AS"')
        self.assertEqual(json_key_span(raw, "obj")[0], '"obj":{"x":[1,2]}')
        self.assertIsNone(json_key_span(raw, "missing"))

    def test_role_spans_cover_only_the_name_and_never_a_birth_date(self):
        raw = json.dumps({"rollegrupper": [{"type": {"kode": "STYR"}, "roller": [
            {"type": {"kode": "LEDE"}, "person": {"foedselsdato": "1970-05-10", "navn": {"etternavn": "Bakken", "fornavn": "Terje"}}},
            {"type": {"kode": "REVI"}, "enhet": {"organisasjonsnummer": "972412112", "navn": ["SLM REVISJON AS"]}},
        ]}]}, separators=(",", ":"))
        spans = roles_spans(raw, json.loads(raw))
        self.assertEqual(spans, ['"navn":{"etternavn":"Bakken","fornavn":"Terje"}', '"navn":["SLM REVISJON AS"]'])
        self.assertTrue(all(span in raw for span in spans))
        self.assertNotIn("1970", "".join(spans))

    def test_module_spans_for_the_registry_entity(self):
        raw = b'{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS","antallAnsatte":4,"konkurs":false}'
        spans = module_spans("registry_live", raw, json.loads(raw))
        self.assertEqual(spans["navn"], '"navn":"EXAMPLE AS"')
        self.assertEqual(spans["antallAnsatte"], '"antallAnsatte":4')


class SnapshotStoreTests(unittest.TestCase):
    def test_bodies_are_stored_under_their_own_hash_and_only_when_configured(self):
        raw = b"<html>hello</html>"
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": directory}):
                relative = save_snapshot(raw, "html")
                self.assertEqual(relative, f"snapshots/{hashlib.sha256(raw).hexdigest()}.html")
                self.assertEqual((Path(directory) / Path(relative).name).read_bytes(), raw)
                self.assertEqual(save_snapshot(raw, "html"), relative)  # idempotent
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": ""}):
                self.assertIsNone(save_snapshot(raw, "html"))

    def test_official_modules_save_the_raw_response_and_exact_spans(self):
        body = '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS","antallAnsatte":4}'
        fetcher = SnapshotFetcher({"responses": {"https://data.brreg.no/enhetsregisteret/api/enheter/923609016": {"raw_json": body}}, "retrieved_at": "2026-01-01T00:00:00Z"})
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": directory}):
                records, _ = fetch_official_modules("923609016", {"registry_live"}, fetcher=fetcher)
            record = records["registry_live"]
            saved = (Path(directory) / Path(record["snapshot_path"]).name).read_bytes()
            self.assertEqual(hashlib.sha256(saved).hexdigest(), record["content_sha256"])
            self.assertIn(record["spans"]["navn"], saved.decode("utf-8"))


def _profile(pages=None, feeds=None, publishable=True):
    return {
        "organisation_number": "923609016",
        "evidence": {"website": {
            "status": "available", "retrieved_at": "2026-09-01T00:00:00Z",
            "value": {"identity_assessment": {"publishable": publishable, "score": 0.95, "method": "gate"}, "pages": pages or [], "feeds": feeds or []},
        }},
    }


def _page(url=PAGE_URL, html=PAGE, digest="a" * 64):
    return {"url": url, "title": "Karriere", "content_sha256": digest, "retrieved_at": "2026-09-01T00:00:00Z", "snapshot_path": f"snapshots/{digest}.html", "signals": extract_page_signals(html, url)}


class SiteExtractorTests(unittest.TestCase):
    def test_jobs_are_real_roles_with_exact_spans_and_all_publishable(self):
        rows = job_observations(_profile([_page()]), today="2026-09-01")
        self.assertEqual({row["metrics"]["title"] for row in rows}, {"Sykepleier", "Rørlegger", "Prosjektleder bygg"})
        for row in rows:
            self.assertEqual(row["signal_type"], "job_posting")
            self.assertTrue(publishable_observation(row), row)
            self.assertIn(row["evidence_span"], PAGE)
            self.assertEqual(row["snapshot_path"], "snapshots/" + "a" * 64 + ".html")

    def test_a_careers_feed_yields_job_feed_items_and_is_not_read_as_news(self):
        jobs_feed = (
            '<rss><channel><item><title>Elektriker</title><link>https://www.eksempel.no/karriere/elektriker</link><pubDate>Mon, 07 Sep 2026 08:00:00 +0000</pubDate></item></channel></rss>'
        )
        feed = {
            "url": "https://www.eksempel.no/karriere/feed/", "retrieved_at": "2026-09-08T00:00:00Z", "content_sha256": "f" * 64,
            "snapshot_path": "snapshots/" + "f" * 64 + ".xml", "items": parse_feed_items(jobs_feed, "https://www.eksempel.no/karriere/feed/"),
        }
        profile = _profile([], [feed])
        rows = job_observations(profile, today="2026-09-10")
        self.assertEqual([(row["metrics"]["title"], row["metrics"]["evidence_kind"]) for row in rows], [("Elektriker", "job_feed_item")])
        self.assertTrue(publishable_observation(rows[0]))
        self.assertIn("<title>Elektriker</title>", rows[0]["evidence_span"])
        self.assertEqual(news_observations(profile), [])

    def test_a_page_with_only_an_apply_action_still_yields_one_hiring_fact(self):
        html = '<html><head><title>Jobb hos oss</title></head><body><a href="https://x.teamtailor.com/jobs/9/apply">Søk her</a></body></html>'
        rows = job_observations(_profile([_page("https://www.eksempel.no/karriere", html, "b" * 64)]))
        self.assertEqual([row["metrics"]["evidence_kind"] for row in rows], ["apply_action"])

    def test_a_generic_careers_page_is_not_a_hiring_fact(self):
        html = '<html><head><title>Karriere</title></head><body><h1>Karriere</h1><p>Vi er alltid på jakt etter dyktige folk.</p><a href="/kontakt">Kontakt oss</a></body></html>'
        self.assertEqual(job_observations(_profile([_page("https://www.eksempel.no/karriere", html, "c" * 64)])), [])

    def test_expired_postings_are_skipped(self):
        page = _page()
        page["signals"]["jobs"] = [{"kind": "job_posting", "title": "Utløpt stilling", "date": "2026-01-01", "valid_through": "2026-02-01", "url": PAGE_URL, "evidence_kind": "json_ld_jobposting", "span": '"title":"Sykepleier"'}]
        page["signals"]["apply_actions"] = []
        self.assertEqual(job_observations(_profile([page]), today="2026-09-01"), [])

    def test_unverified_sites_publish_nothing(self):
        self.assertEqual(job_observations(_profile([_page()], publishable=False)), [])
        self.assertEqual(news_observations(_profile([_page()], publishable=False)), [])

    def test_news_needs_a_date_and_is_sorted_newest_first_with_feed_items_included(self):
        feed = {"url": "https://www.eksempel.no/feed.xml", "retrieved_at": "2026-09-01T00:00:00Z", "content_sha256": "d" * 64, "snapshot_path": "snapshots/" + "d" * 64 + ".xml",
                "items": parse_feed_items(RSS, "https://www.eksempel.no/feed.xml")}
        rows = news_observations(_profile([_page()], [feed]))
        dates = [row["metrics"]["published_at"] for row in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertIn("Vi vant anbudet", [row["metrics"]["headline"] for row in rows])
        self.assertEqual({row["signal_type"] for row in rows}, {"public_post"})
        for row in rows:
            self.assertTrue(publishable_observation(row), row)
            self.assertTrue(row["metrics"]["published_at"])

    def test_the_same_story_seen_through_a_feed_and_page_markup_is_published_once(self):
        story = {"kind": "article", "title": "Driving the new subsea era", "date": "2023-10-12", "url": "https://www.eksempel.no/nyheter/subsea", "evidence_kind": "json_ld_article", "span": '"headline":"Driving the new subsea era"'}
        page = _page()
        page["signals"] = {"articles": [story, {**story, "url": "https://www.eksempel.no/aktuelt/", "evidence_kind": "time_element", "span": "<time datetime=\"2023-10-12\">"}]}
        self.assertEqual(len(news_observations(_profile([page]))), 1)

    def test_a_news_page_without_any_dated_article_is_not_news(self):
        page = {"url": "https://www.eksempel.no/aktuelt/", "title": "Aktuelt", "content_sha256": "e" * 64, "signals": {}}
        self.assertEqual(news_observations(_profile([page])), [])


class SelfContainedResultTests(unittest.TestCase):
    """The result alone must be enough to reopen and verify the response behind each claim."""

    def _build(self, directory, body: bytes, suffix: str = "json"):
        sha = hashlib.sha256(body).hexdigest()
        (Path(directory) / "snapshots").mkdir(exist_ok=True)
        (Path(directory) / "snapshots" / f"{sha}.{suffix}").write_bytes(body)
        live = evidence(
            "registry_live", "available", "official_registry_live", "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
            value={"name": "EXAMPLE AS"}, content_sha256=sha, snapshot_path=f"snapshots/{sha}.{suffix}",
            spans=module_spans("registry_live", body, json.loads(body)) if suffix == "json" else None,
        )
        envelope = build_envelope({"organisation_number": "923609016", "evidence": {"registry_live": live}}, run_id="r",
                                  started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z", snapshot_root=Path(directory))
        return envelope, sha

    def test_the_text_body_is_carried_inline_and_evidence_points_at_it(self):
        body = '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS"}'.encode()
        with tempfile.TemporaryDirectory() as directory:
            envelope, sha = self._build(directory, body)
        snapshot = envelope["source_snapshots"][0]
        self.assertEqual((snapshot["sha256"], snapshot["body_encoding"], snapshot["content_type"]), (sha, "utf-8", "application/json"))
        self.assertEqual(hashlib.sha256(snapshot["body"].encode()).hexdigest(), sha)
        cited = [item for item in envelope["evidence"] if item.get("snapshot")]
        self.assertGreaterEqual(len(cited), 1)
        self.assertTrue(all(item["snapshot_id"] == snapshot["id"] for item in cited))
        self.assertEqual(snapshot["source_url"], "https://data.brreg.no/enhetsregisteret/api/enheter/923609016")
        self.assertEqual(len(envelope["source_snapshots"]), 1)  # shared by every claim, stored once

    def test_evidence_also_uses_the_reviewers_field_names(self):
        body = '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS"}'.encode()
        with tempfile.TemporaryDirectory() as directory:
            envelope, sha = self._build(directory, body)
        cited = next(item for item in envelope["evidence"] if item.get("snapshot"))
        self.assertEqual(cited["content_hash"], cited["content_sha256"])
        self.assertEqual(cited["captured_at"], cited["retrieved_at"])
        self.assertEqual(cited["capture_date"], str(cited["retrieved_at"])[:10])
        self.assertEqual(cited["retained_response_ref"], cited["snapshot_id"])
        self.assertEqual(cited["retained_response_ref"], envelope["source_snapshots"][0]["id"])

    def test_the_audit_passes_from_the_result_alone_with_the_saved_folder_deleted(self):
        from scripts.audit_evidence import audit

        body = '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS"}'.encode()
        with tempfile.TemporaryDirectory() as directory:
            envelope, _ = self._build(directory, body)
        report = audit([envelope], Path("this-folder-does-not-exist"))
        self.assertEqual(report["failure_count"], 0, report["failures"])
        self.assertEqual(report["summary"]["body_inline_in_result"], report["summary"]["evidence_complete_in_result"])
        # tampering with the inline body is caught
        envelope["source_snapshots"][0]["body"] = envelope["source_snapshots"][0]["body"].replace("EXAMPLE", "OTHER")
        self.assertGreater(audit([envelope], Path("nowhere"))["failure_count"], 0)

    def test_pdfs_and_oversized_bodies_are_referenced_not_inlined_and_non_utf8_round_trips(self):
        import base64
        from scripts import build_output_contract as contract

        with tempfile.TemporaryDirectory() as directory:
            pdf, _ = self._build(directory, b"%PDF-1.4 binary", "pdf")
            self.assertEqual(pdf["source_snapshots"][0]["body_omitted"], "binary_pdf")
            self.assertEqual(pdf["source_snapshots"][0]["bytes"], len(b"%PDF-1.4 binary"))
            with patch.object(contract, "INLINE_MAX_BYTES", 10):
                big, _ = self._build(directory, b'{"a":"0123456789abcdef"}')
            self.assertEqual(big["source_snapshots"][0]["body_omitted"], "too_large")
            latin = '<html>Bjørn</html>'.encode("latin-1")
            odd, sha = self._build(directory, latin, "html")
        snapshot = odd["source_snapshots"][0]
        self.assertEqual(snapshot["body_encoding"], "base64")
        self.assertEqual(hashlib.sha256(base64.b64decode(snapshot["body"])).hexdigest(), sha)

    def test_line_separator_characters_in_a_saved_body_cannot_split_an_envelope_line(self):
        from scripts.build_output_contract import write_jsonl

        body = ("<p>a" + chr(0x2028) + "b" + chr(0x2029) + "c" + chr(0x85) + "d</p>").encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            envelope, sha = self._build(directory, body, "html")
            target = Path(directory) / "out.jsonl"
            write_jsonl(target, [envelope])
            text = target.read_text(encoding="utf-8")
        self.assertEqual(len(text.splitlines()), 1)
        restored = json.loads(text.splitlines()[0])["source_snapshots"][0]["body"]
        self.assertEqual(hashlib.sha256(restored.encode("utf-8")).hexdigest(), sha)

    def test_a_missing_saved_file_is_recorded_as_such_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = {"organisation_number": "923609016", "evidence": {"registry_live": evidence(
                "registry_live", "available", "official_registry_live", "https://x.test/", value={"name": "EXAMPLE AS"}, content_sha256="a" * 64, snapshot_path="snapshots/" + "a" * 64 + ".json")}}
            envelope = build_envelope(profile, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z", snapshot_root=Path(directory))
        self.assertEqual(envelope["source_snapshots"][0]["body_omitted"], "file_not_found")

    def test_a_malformed_bulk_row_with_surplus_columns_cannot_stop_the_registry_stage(self):
        # Real failure on the 1,000-company run: csv.DictReader files surplus columns under a None
        # key, and sort_keys=True cannot order None against str. It crashed the required registry stage.
        from norway_company_agent.batch import profiles_from_bulk
        import gzip as gz

        with tempfile.TemporaryDirectory() as directory:
            bulk = Path(directory) / "bulk.csv.gz"
            with gz.open(bulk, "wt", encoding="utf-8", newline="") as handle:
                handle.write('"organisasjonsnummer","navn","organisasjonsform.kode"' + chr(10))
                handle.write('"923609016","EXAMPLE AS","AS","surplus1","surplus2"' + chr(10))
                handle.write('"923609017","OTHER AS","AS"' + chr(10))
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": str(Path(directory) / "snapshots")}):
                profiles, _ = profiles_from_bulk(bulk, ["923609016", "923609017"])
            by_org = {row["organisation_number"]: row for row in profiles}
            self.assertEqual(set(by_org), {"923609016", "923609017"})
            malformed = by_org["923609016"]["evidence"]["registry"]
            self.assertEqual(malformed["status"], "available")
            self.assertEqual(malformed["spans"]["navn"], '"navn":"EXAMPLE AS"')
            saved = (Path(directory) / "snapshots" / Path(malformed["snapshot_path"]).name).read_text(encoding="utf-8")
            self.assertIn("surplus1", saved)
        with patch("norway_company_agent.batch.save_snapshot", side_effect=OSError("disk full")):
            with tempfile.TemporaryDirectory() as directory:
                bulk = Path(directory) / "bulk.csv.gz"
                with gz.open(bulk, "wt", encoding="utf-8", newline="") as handle:
                    handle.write('"organisasjonsnummer","navn","organisasjonsform.kode"' + chr(10) + '"923609016","EXAMPLE AS","AS"' + chr(10))
                profiles, _ = profiles_from_bulk(bulk, ["923609016"])
        self.assertEqual(profiles[0]["evidence"]["registry"]["status"], "available")  # fell back, did not crash

    def test_the_bulk_registry_row_is_a_retained_response_for_fallback_claims(self):
        from norway_company_agent.batch import profiles_from_bulk

        with tempfile.TemporaryDirectory() as directory:
            bulk = Path(directory) / "bulk.csv.gz"
            import gzip as gz

            with gz.open(bulk, "wt", encoding="utf-8", newline="") as handle:
                handle.write("organisasjonsnummer;navn;organisasjonsform.kode;antallAnsatte;konkurs;underAvvikling;sisteInnsendteAarsregnskap" + chr(10))
                handle.write("923609016;EXAMPLE AS;AS;4;false;false;2025" + chr(10))
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": str(Path(directory) / "snapshots")}):
                profiles, _ = profiles_from_bulk(bulk, ["923609016"])
            record = profiles[0]["evidence"]["registry"]
            self.assertEqual(record["spans"]["navn"], '"navn":"EXAMPLE AS"')
            saved = (Path(directory) / "snapshots" / Path(record["snapshot_path"]).name).read_bytes()
            self.assertEqual(hashlib.sha256(saved).hexdigest(), record["content_sha256"])
            self.assertIn("file sha256", record["note"])
            envelope = build_envelope({"organisation_number": "923609016", "evidence": {"registry": record}}, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z", snapshot_root=Path(directory))
        name = next(c for c in envelope["claims"] if c["field"] == "legal_name")
        cited = next(e for e in envelope["evidence"] if e["id"] == name["evidence_ids"][0])
        self.assertEqual((cited["claim_span"], name["availability"]), ('"navn":"EXAMPLE AS"', "available"))
        self.assertTrue(cited["snapshot_id"])


class SummarySourcesAndChangesTests(unittest.TestCase):
    ARGS = dict(run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")

    def _profile(self):
        raw = b'{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS","antallAnsatte":4}'
        live = evidence("registry_live", "available", "official_registry_live", "https://data.brreg.no/x",
                        value={"name": "EXAMPLE AS", "legal_form": "AS", "employees": 4}, content_sha256=hashlib.sha256(raw).hexdigest(),
                        spans=module_spans("registry_live", raw, json.loads(raw)))
        return {"organisation_number": "923609016", "evidence": {"registry_live": live}}

    def test_every_summary_sentence_cites_published_claims_and_their_evidence(self):
        page = _page()
        observations = news_observations(_profile([page])) + job_observations(_profile([page]), today="2026-09-01")
        envelope = build_envelope(self._profile(), observations=observations, **self.ARGS)
        published = {c["field"] for c in envelope["claims"]}
        known_evidence = {e["id"] for e in envelope["evidence"]}
        by_text = {item["text"]: item for item in envelope["summary"]["sentences"]}
        self.assertEqual(" ".join(by_text), envelope["summary"]["text"])
        for item in envelope["summary"]["sentences"]:
            self.assertTrue(set(item["fields"]) <= published, item)
            self.assertTrue(set(item["evidence_ids"]) <= known_evidence, item)
        news_sentence = next(item for text, item in by_text.items() if "dated news item" in text)
        self.assertTrue(all(field.startswith("site_news.") for field in news_sentence["fields"]))
        self.assertTrue(news_sentence["evidence_ids"])
        identity = next(item for text, item in by_text.items() if "registered in Norway" in text)
        self.assertIn("legal_name", identity["fields"])

    def test_material_changes_are_in_the_envelope_and_the_summary_and_none_when_no_previous_run(self):
        event = {"organisation_number": "923609016", "field": "registry.employees", "old_value": 4, "new_value": 6,
                 "old_content_sha256": "a" * 64, "new_content_sha256": "b" * 64, "old_retrieved_at": "2026-01-01T00:00:00Z"}
        changed = build_envelope(self._profile(), changes=[event], **self.ARGS)
        self.assertEqual(changed["changes"], [event])
        self.assertIn("1 tracked field(s) changed: registry.employees (4 -> 6)", changed["summary"]["text"])
        unchanged = build_envelope(self._profile(), changes=[], **self.ARGS)
        self.assertEqual(unchanged["changes"], [])
        self.assertIn("No tracked field has changed since the previous run.", unchanged["summary"]["text"])
        first_run = build_envelope(self._profile(), **self.ARGS)
        self.assertEqual(first_run["changes"], [])
        self.assertNotIn("previous run", first_run["summary"]["text"])

    def test_a_change_event_preserves_the_earlier_evidence(self):
        from norway_company_agent.refresh import diff_datasets

        def row(employees, sha, retrieved, snapshot):
            record = evidence("registry_live", "available", "official_registry_live", "https://data.brreg.no/x", value={}, content_sha256=sha,
                              retrieved_at=retrieved, snapshot_path=snapshot)
            return {"organisation_number": "923609016", "employees": employees, "evidence": {"registry_live": record}}

        events = diff_datasets([row(4, "a" * 64, "2026-01-01T00:00:00Z", "snapshots/old.json")], [row(6, "b" * 64, "2026-02-01T00:00:00Z", "snapshots/new.json")])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["old_value"], event["new_value"]), (4, 6))
        self.assertEqual((event["old_content_sha256"], event["old_retrieved_at"], event["old_snapshot_path"]), ("a" * 64, "2026-01-01T00:00:00Z", "snapshots/old.json"))
        self.assertEqual((event["new_content_sha256"], event["new_snapshot_path"]), ("b" * 64, "snapshots/new.json"))
        self.assertEqual(diff_datasets([row(4, "a" * 64, "t1", "s")], [row(4, "c" * 64, "t2", "s2")]), [])  # same values, new fetch: not a change


class WikidataTests(unittest.TestCase):
    BINDINGS = [{
        "o": {"type": "literal", "value": "811413682"}, "i": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1329436"},
        "iLabel": {"type": "literal", "value": "Elopak"}, "site": {"type": "uri", "value": "https://www.elopak.com/"},
        "inc": {"type": "literal", "value": "1957-01-01T00:00:00Z"}, "tw": {"type": "literal", "value": "elopak"},
    }]

    def test_facts_are_summarised_from_the_rows_for_one_organisation_number(self):
        from norway_company_agent import wikidata

        summary = wikidata.summarize(self.BINDINGS)
        self.assertEqual((summary["qid"], summary["inception"], summary["websites"]), ("Q1329436", "1957-01-01", ["https://www.elopak.com/"]))
        self.assertEqual(summary["social_links"], {"x": "https://x.com/elopak"})
        self.assertEqual(wikidata.group_by_organisation(self.BINDINGS * 2)["811413682"].__len__(), 2)

    def test_each_claim_cites_a_literal_slice_of_the_saved_company_response_and_survives_offline_respanning(self):
        from norway_company_agent import wikidata
        from scripts.build_output_contract import derive_spans_from_snapshots
        from scripts.run_wikidata_enrichment import record_for

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SIGNALPOST_SNAPSHOT_DIR": str(Path(directory) / "snapshots")}):
                record = record_for("811413682", self.BINDINGS, None, "2026-10-05T00:00:00Z")
            profile = {"organisation_number": "811413682", "evidence": {"wikidata": record}}
            derive_spans_from_snapshots([profile], Path(directory))  # the converter recomputes spans from the saved bodies
            envelope = build_envelope(profile, run_id="r", started_at="2026-10-05T00:00:00Z", completed_at="2026-10-05T00:01:00Z", snapshot_root=Path(directory))
            self.assertEqual(record["content_sha256"], hashlib.sha256(wikidata.retained_body("811413682", self.BINDINGS)).hexdigest())
            by_field = {c["field"]: c for c in envelope["claims"]}
            self.assertLessEqual({"wikidata_entity", "inception_date", "social_profile.x"}, set(by_field))
            cited = {c["field"]: next(e for e in envelope["evidence"] if e["id"] == c["evidence_ids"][0]) for c in envelope["claims"]}
            self.assertIn("Q1329436", cited["wikidata_entity"]["claim_span"])
            self.assertIn("1957-01-01", cited["inception_date"]["claim_span"])
            self.assertIn("elopak", cited["social_profile.x"]["claim_span"])
            from scripts.audit_evidence import audit

            report = audit([envelope], Path(directory))
            self.assertEqual(report["failure_count"], 0, report["failures"])

    def test_an_item_that_does_not_exist_is_an_explicit_not_found_claim(self):
        from scripts.run_wikidata_enrichment import record_for

        record = record_for("923609016", None, None, "2026-10-05T00:00:00Z")
        envelope = build_envelope({"organisation_number": "923609016", "evidence": {"wikidata": record}}, run_id="r", started_at="x", completed_at="y")
        claim = next(c for c in envelope["claims"] if c["field"] == "wikidata_entity")
        self.assertEqual((claim["availability"], claim["value"]), ("not_available", None))
        failed = build_envelope({"organisation_number": "923609016", "evidence": {"wikidata": record_for("923609016", None, "HTTP 429", "t")}}, run_id="r", started_at="x", completed_at="y")
        self.assertEqual(next(c for c in failed["claims"] if c["field"] == "wikidata_entity")["availability"], "failed")


class DomainProbeTests(unittest.TestCase):
    def test_candidates_are_derived_from_the_legal_name_with_norwegian_letter_variants(self):
        from norway_company_agent.domain_probe import domain_candidates

        self.assertEqual(domain_candidates("TRUCK INVEST AS"), ["truckinvest.no", "truck-invest.no", "truckinvest.com"])
        self.assertEqual(domain_candidates("BJØRN & SØNN BYGG AS")[:2], ["bjornsonnbygg.no", "bjorn-sonn-bygg.no"])
        self.assertIn("bjoernsoennbygg.no", domain_candidates("BJØRN & SØNN BYGG AS"))
        self.assertEqual(domain_candidates("AS"), [])
        self.assertEqual(domain_candidates("X AS"), [])
        self.assertLessEqual(len(domain_candidates("KNUT OLAV HALLAND BRØYTING OG GRAVING AS")), 4)

    def test_the_organisation_number_must_be_a_delimited_number(self):
        from norway_company_agent.domain_probe import org_number_on_page

        for text in ("Org.nr. 923 609 016 MVA", "923609016", "NO 923.609.016", "org 923 609 016"):
            self.assertTrue(org_number_on_page(text, "923609016"), text)
        for text in ("tlf 99923609016", "9236090161", "923 609 017", ""):
            self.assertFalse(org_number_on_page(text, "923609016"), text)

    def test_the_registered_place_needs_both_postcode_and_town(self):
        from norway_company_agent.domain_probe import place_on_page

        self.assertTrue(place_on_page("Storgata 1, 2004 Lillestrøm", "2004", "LILLESTRØM"))
        self.assertFalse(place_on_page("Storgata 1, 2004 Oslo", "2004", "LILLESTRØM"))
        self.assertFalse(place_on_page("Storgata 1, 9999 Lillestrøm", "2004", "LILLESTRØM"))
        self.assertFalse(place_on_page("anything", None, "LILLESTRØM"))

    def _row(self):
        return {"organisation_number": "923609016", "name": "EXAMPLE TOOLS AS", "evidence": {"registry_live": {"status": "available", "value": {"name": "EXAMPLE TOOLS AS", "business_address": {"postnummer": "2004", "poststed": "LILLESTRØM"}}}}}

    def test_a_site_is_a_hit_only_with_proof_on_its_own_domain(self):
        from scripts import run_domain_probe as probe

        class Response:
            def __init__(self, body, url):
                self._body, self._url = body, url
                self.headers = {"content-type": "text/html"}
            def read(self, n=-1): return self._body
            def geturl(self): return self._url
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def opener(body, url):
            return type("Opener", (), {"open": staticmethod(lambda request, timeout=None: Response(body, url))})

        page = "<html><body><footer>Example Tools AS, org.nr. 923 609 016</footer></body></html>".encode()
        with patch.object(probe, "assert_public_url", lambda url: None), patch.object(probe, "_robots_allowed", lambda url, timeout: True):
            with patch.object(probe, "SAFE_OPENER", opener(page, "https://exampletools.no/")):
                self.assertEqual(probe.light_probe("exampletools.no", "923609016", 5)[:2], ("https://exampletools.no/", "organisation_number"))
            with patch.object(probe, "SAFE_OPENER", opener(page.replace(b"923 609 016", b"111 222 333"), "https://exampletools.no/")):
                self.assertEqual(probe.light_probe("exampletools.no", "923609016", 5)[:2], (None, None))
                self.assertEqual(probe.light_probe("exampletools.no", "923609016", 5, ("2004", "LILLESTRØM"))[:2], (None, None))  # place not on the page either
            placed = "<html><body>Example Tools, Storgata 1, 2004 Lillestr" + chr(0xF8) + "m</body></html>"
            with patch.object(probe, "SAFE_OPENER", opener(placed.encode("utf-8"), "https://exampletools.no/")):
                self.assertEqual(probe.light_probe("exampletools.no", "923609016", 5, ("2004", "LILLESTRØM"))[:2], ("https://exampletools.no/", "registered_place"))
            with patch.object(probe, "SAFE_OPENER", opener(page, "https://elsewhere.example.com/")):
                self.assertEqual(probe.light_probe("exampletools.no", "923609016", 5)[:2], (None, None))  # redirected off the candidate domain

    def test_an_unresolvable_guess_costs_no_http_request(self):
        from scripts import run_domain_probe as probe

        def refuse(url):
            raise ValueError("Hostname did not resolve")

        with patch.object(probe, "assert_public_url", refuse):
            self.assertEqual(probe.light_probe("nonexistent-guess.no", "923609016", 5), (None, None, 0))


class AnnualReportCandidateTests(unittest.TestCase):
    def test_the_report_url_is_derived_from_the_bulk_filing_year_without_the_history_module(self):
        from scripts.run_annual_report_workforce_connector import pdf_candidates

        profile = {"organisation_number": "923609016", "latest_submitted_accounts": "2025", "evidence": {"registry": {"value": {}}}}
        self.assertEqual(pdf_candidates(profile), [{"year": "2025", "url": "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/923609016/2025"}])
        from_registry_row = {"organisation_number": "923609016", "evidence": {"registry": {"value": {"sisteInnsendteAarsregnskap": "2024"}}}}
        self.assertEqual(pdf_candidates(from_registry_row)[0]["year"], "2024")
        self.assertEqual(pdf_candidates({"organisation_number": "923609016", "evidence": {}}), [])

    def test_the_history_module_still_wins_when_it_ran(self):
        from scripts.run_annual_report_workforce_connector import pdf_candidates

        profile = {"organisation_number": "923609016", "latest_submitted_accounts": "2025", "evidence": {"financial_history": {"value": {"pdfs": [
            {"year": "2023", "url": "u23"}, {"year": "2024", "url": "u24"}]}}}}
        self.assertEqual([item["year"] for item in pdf_candidates(profile)], ["2024", "2023"])


class OcrLanguageTests(unittest.TestCase):
    def _settings(self, langs_by_dir):
        from types import SimpleNamespace
        from scripts import run_annual_report_workforce_connector as connector

        def fake_run(command, **kwargs):
            key = "bundled" if "--tessdata-dir" in command else "system"
            names = langs_by_dir.get(key, [])
            header = "List of available languages (%d):" % len(names)
            return SimpleNamespace(stdout=chr(10).join([header, *names]) + chr(10), stderr="")

        connector.ocr_settings.cache_clear()
        try:
            with patch.object(connector.subprocess, "run", fake_run):
                return connector.ocr_settings()
        finally:
            connector.ocr_settings.cache_clear()

    def test_the_system_norwegian_pack_is_preferred(self):
        self.assertEqual(self._settings({"system": ["eng", "nor"], "bundled": ["nor"]}), ([], "nor"))

    def test_the_bundled_pack_is_used_via_tessdata_dir_when_the_system_has_no_norwegian(self):
        args, language = self._settings({"system": ["eng"], "bundled": ["nor"]})
        self.assertEqual(language, "nor")
        self.assertEqual(args[0], "--tessdata-dir")
        self.assertTrue(Path(args[1], "nor.traineddata").exists())  # the pack really ships in the repo

    def test_english_is_the_degraded_fallback_and_missing_tesseract_does_not_crash(self):
        self.assertEqual(self._settings({"system": ["eng"], "bundled": []}), ([], "eng"))
        self.assertEqual(self._settings({}), ([], "nor"))


class TimeBudgetTests(unittest.TestCase):
    def test_no_budget_means_no_limits(self):
        from scripts.run_agent import plan_stages

        plan = plan_stages(1000, None)
        self.assertTrue(plan["history"])
        self.assertIsNone(plan["crawl_seconds"])
        self.assertIn("financial_history", plan["modules"])

    def test_a_tight_budget_drops_the_rate_limited_history_module_but_keeps_everything_else(self):
        from scripts.run_agent import plan_stages

        tight = plan_stages(1000, 45 * 60)  # 1,000 companies at 30 history requests/minute alone is 36 minutes
        self.assertFalse(tight["history"])
        self.assertNotIn("financial_history", tight["modules"])
        for module in ("registry", "registry_live", "financials", "roles", "locations", "website"):
            self.assertIn(module, tight["modules"])
        self.assertGreaterEqual(tight["reserve_seconds"], 360)  # 1,000 companies keep about six minutes to write results
        self.assertLess(plan_stages(100, 12 * 60)["reserve_seconds"], 180)  # a small batch does not waste its budget
        roomy = plan_stages(1000, 300 * 60)
        self.assertTrue(roomy["history"])
        self.assertEqual(plan_stages(100, 45 * 60)["history"], True)

    def test_a_dropped_history_module_is_an_explicit_not_available_claim(self):
        envelope = build_envelope({"organisation_number": "923609016", "evidence": {}}, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        claim = next(c for c in envelope["claims"] if c["field"] == "annual_accounts_years_on_file")
        self.assertEqual((claim["availability"], claim["value"]), ("not_available", None))

    def test_the_ocr_connector_starts_no_new_company_after_its_deadline(self):
        import time as time_module
        from scripts.run_annual_report_workforce_connector import collect

        profile = {"organisation_number": "923609016", "evidence": {"financial_history": {"value": {"pdfs": [{"year": "2025", "url": "https://x.test/a.pdf"}]}}}}
        with tempfile.TemporaryDirectory() as directory:
            observation, status = collect(profile, Path(directory), ocr_pages=1, ocr_dpi=50, deadline=time_module.time() - 1)
        self.assertIsNone(observation)
        self.assertEqual(status["status"], "skipped_time_budget")


class ContractEvidenceTests(unittest.TestCase):
    def _profile(self):
        raw = '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS","organisasjonsform":{"kode":"AS"},"antallAnsatte":4,"sisteInnsendteAarsregnskap":"2025"}'
        body = json.loads(raw)
        live = evidence(
            "registry_live", "available", "official_registry_live", "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
            value={"name": "EXAMPLE AS", "legal_form": "AS", "employees": 4, "latest_submitted_accounts": "2025"},
            content_sha256=hashlib.sha256(raw.encode()).hexdigest(), snapshot_path="snapshots/reg.json",
            spans=module_spans("registry_live", raw.encode(), body), extraction_method="official_api_json",
        )
        obligation = evidence("accounting_obligation", "available", "official_rule_interpretation", "https://www.brreg.no/rules", value={"classification": "filing_observed", "ruleset_version": "rules_v1"}, content_sha256="f" * 64)
        return {"organisation_number": "923609016", "evidence": {"registry_live": live, "accounting_obligation": obligation}}

    def test_every_claim_has_its_own_evidence_entry_with_snapshot_and_exact_excerpt(self):
        envelope = build_envelope(self._profile(), run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        evidence_by_id = {item["id"]: item for item in envelope["evidence"]}
        self.assertEqual(len(evidence_by_id), len(envelope["evidence"]))
        by_field = {claim["field"]: evidence_by_id[claim["evidence_ids"][0]] for claim in envelope["claims"]}
        self.assertEqual(by_field["legal_name"]["claim_span"], '"navn":"EXAMPLE AS"')
        self.assertEqual(by_field["employees"]["claim_span"], '"antallAnsatte":4')
        self.assertEqual(by_field["legal_name"]["snapshot"], "snapshots/reg.json")
        self.assertEqual(by_field["legal_name"]["extraction_method"], "official_api_json")

    def test_accounting_obligation_cites_the_registry_fact_the_rule_was_applied_to(self):
        envelope = build_envelope(self._profile(), run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        claim = next(c for c in envelope["claims"] if c["field"] == "accounting_obligation")
        cited = next(e for e in envelope["evidence"] if e["id"] == claim["evidence_ids"][0])
        self.assertEqual(cited["claim_span"], '"sisteInnsendteAarsregnskap":"2025"')
        self.assertEqual(cited["snapshot"], "snapshots/reg.json")
        self.assertEqual(cited["extraction_method"], "rules_v1")

    def test_accounting_obligation_falls_back_to_the_bulk_registry_row_when_live_omits_the_fact(self):
        profile = self._profile()
        profile["evidence"]["registry_live"]["spans"].pop("sisteInnsendteAarsregnskap")
        profile["evidence"]["registry"] = evidence(
            "registry", "available", "official_registry_bulk", "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
            value={"sisteInnsendteAarsregnskap": "2025"}, content_sha256="a" * 64,
        )
        envelope = build_envelope(profile, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        claim = next(c for c in envelope["claims"] if c["field"] == "accounting_obligation")
        cited = next(e for e in envelope["evidence"] if e["id"] == claim["evidence_ids"][0])
        self.assertEqual((cited["claim_span"], cited["content_sha256"]), ('{"sisteInnsendteAarsregnskap":"2025"}', "a" * 64))

    def test_a_role_with_no_person_name_is_cited_to_its_role_type_object_only(self):
        raw = json.dumps({"rollegrupper": [{"roller": [
            {"fratraadt": False, "person": {"foedselsdato": "1970-05-10"}, "rekkefolge": 0, "type": {"_links": {"self": {"href": "https://x/BOBE"}}, "beskrivelse": "Bostyrer", "kode": "BOBE"}},
        ]}]}, separators=(",", ":"))
        roles = {"rollegrupper": [{"roller": [{"person": {"foedselsdato": "1970-05-10"}, "type": {"kode": "BOBE", "beskrivelse": "Bostyrer"}}]}]}
        span = roles_spans(raw, roles)[0]
        self.assertIn(span, raw)
        self.assertIn('"kode":"BOBE"', span)
        self.assertNotIn("1970", span)

    def test_job_and_news_observations_become_claims_with_snapshot_and_span(self):
        page = _page()
        observations = job_observations(_profile([page]), today="2026-09-01") + news_observations(_profile([page]))
        envelope = build_envelope(self._profile(), run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z", observations=observations)
        evidence_by_id = {item["id"]: item for item in envelope["evidence"]}
        jobs = [c for c in envelope["claims"] if c["field"].startswith("job_posting.")]
        news = [c for c in envelope["claims"] if c["field"].startswith("site_news.")]
        self.assertEqual(len(jobs), 3)
        self.assertGreaterEqual(len(news), 2)
        for claim in jobs + news:
            cited = evidence_by_id[claim["evidence_ids"][0]]
            self.assertTrue(cited["claim_span"] and cited["snapshot"])
        self.assertIn("published_at", news[0]["value"])
        self.assertIn("title", jobs[0]["value"])

    def _website(self, publishable):
        return evidence(
            "website", "available", "registry_linked_company_website", "https://www.eksempel.no/", content_sha256="1" * 64,
            snapshot_path="snapshots/home.html",
            value={"final_url": "https://www.eksempel.no/", "title_span": "<title>Eksempel</title>", "pages": [], "social_links": [], "identity_assessment": {"publishable": publishable, "score": 0.3 if not publishable else 0.95}},
        )

    def test_a_site_the_identity_gate_could_not_verify_is_ambiguous_not_available(self):
        args = dict(run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        unverified = build_envelope({"organisation_number": "923609016", "evidence": {"website": self._website(False)}}, **args)
        claim = next(c for c in unverified["claims"] if c["field"] == "official_website")
        self.assertEqual((claim["availability"], claim["confidence"]), ("ambiguous", 0.3))
        self.assertNotIn("verified official website", unverified["summary"]["text"])
        self.assertTrue(any(item.startswith("official website") for item in unverified["summary"]["unknown_fields"]))
        verified = build_envelope({"organisation_number": "923609016", "evidence": {"website": self._website(True)}}, **args)
        self.assertEqual(next(c for c in verified["claims"] if c["field"] == "official_website")["availability"], "available")
        self.assertIn("verified official website", verified["summary"]["text"])

    def test_a_checked_source_with_no_subunits_is_an_empty_list_cited_to_the_response(self):
        raw = b'{"_links":{},"page":{"number":0,"size":1000,"totalElements":0,"totalPages":0}}'
        body = json.loads(raw)
        locations = evidence(
            "locations", "available", "official_subunits", "https://data.brreg.no/x", value={"locations": []},
            content_sha256=hashlib.sha256(raw).hexdigest(), snapshot_path="snapshots/loc.json", spans=module_spans("locations", raw, body),
        )
        envelope = build_envelope({"organisation_number": "923609016", "evidence": {"locations": locations}}, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        claim = next(c for c in envelope["claims"] if c["field"] == "registered_workplaces")
        cited = next(e for e in envelope["evidence"] if e["id"] == claim["evidence_ids"][0])
        self.assertEqual((claim["availability"], claim["value"]), ("available", []))
        self.assertEqual(cited["claim_span"], '"totalElements":0')

    def test_spans_can_be_rederived_offline_from_the_saved_snapshots(self):
        from scripts.build_output_contract import derive_spans_from_snapshots

        raw = b'{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS"}'
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "snapshots").mkdir()
            (Path(directory) / "snapshots" / "reg.json").write_bytes(raw)
            profile = {"organisation_number": "923609016", "evidence": {"registry_live": evidence(
                "registry_live", "available", "official_registry_live", "https://x", value={}, snapshot_path="snapshots/reg.json",
            )}}
            self.assertEqual(derive_spans_from_snapshots([profile], Path(directory)), 1)
        self.assertEqual(profile["evidence"]["registry_live"]["spans"]["navn"], '"navn":"EXAMPLE AS"')

    def test_a_social_link_found_on_an_inner_page_cites_that_pages_snapshot(self):
        home = {"url": "https://www.eksempel.no/", "content_sha256": "1" * 64, "snapshot_path": "snapshots/home.html", "retrieved_at": "2026-09-01T00:00:00Z"}
        contact = {"url": "https://www.eksempel.no/kontakt", "content_sha256": "2" * 64, "snapshot_path": "snapshots/contact.html", "retrieved_at": "2026-09-01T00:00:01Z"}
        link = {"platform": "facebook", "url": "https://facebook.com/eksempelas", "found_on": "https://www.eksempel.no/kontakt", "span": '<a href="https://www.facebook.com/eksempelas">'}
        website = evidence(
            "website", "available", "registry_linked_company_website", "https://www.eksempel.no/", content_sha256="1" * 64,
            snapshot_path="snapshots/home.html", value={"pages": [home, contact], "social_links": [link], "identity_assessment": {"score": 0.95}},
        )
        envelope = build_envelope({"organisation_number": "923609016", "evidence": {"website": website}}, run_id="r", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
        claim = next(c for c in envelope["claims"] if c["field"] == "social_profile.facebook")
        cited = next(e for e in envelope["evidence"] if e["id"] == claim["evidence_ids"][0])
        self.assertEqual((cited["source_url"], cited["content_sha256"], cited["snapshot"]), ("https://www.eksempel.no/kontakt", "2" * 64, "snapshots/contact.html"))
        self.assertEqual(cited["claim_span"], link["span"])

    def test_summary_names_open_roles_and_the_latest_news_and_lists_them_as_unknown_when_absent(self):
        page = _page()
        observations = job_observations(_profile([page]), today="2026-09-01") + news_observations(_profile([page]))
        text = summarize_profile(self._profile()["evidence"], observations)["text"]
        self.assertIn("open role(s) shown on its own site", text)
        self.assertIn("dated news item(s) on its own site", text)
        empty = summarize_profile(self._profile()["evidence"], [])
        self.assertIn("dated news/press", empty["unknown_fields"])
        self.assertTrue(any(item.startswith("open roles") for item in empty["unknown_fields"]))


if __name__ == "__main__":
    unittest.main()
