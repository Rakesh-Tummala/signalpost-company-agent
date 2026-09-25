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
