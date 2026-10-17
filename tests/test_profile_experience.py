"""The profile a person reads: sectioned, cited summaries, the registry's own description, refresh, and the viewer."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.page_signals import description_span  # noqa: E402
from scripts.build_output_contract import SUMMARY_SECTIONS, build_envelope  # noqa: E402
from scripts.build_viewer import PAGE_TEMPLATE, embed_safe, flatten_envelope  # noqa: E402
from scripts.run_agent import archive_finished_run, discovery_stages  # noqa: E402

ARGS = {"run_id": "r", "started_at": "2026-10-14T00:00:00Z", "completed_at": "2026-10-14T00:01:00Z"}

LIVE_BODY = (
    '{"organisasjonsnummer":"923609016","navn":"EXAMPLE AS","organisasjonsform":{"kode":"AS"},"antallAnsatte":12,'
    '"aktivitet":["Utvikling og salg av programvare"],"vedtektsfestetFormaal":["Selskapets formaal er IT"],"stiftelsesdato":"2015-03-02",'
    '"naeringskode1":{"beskrivelse":"Programmeringstjenester","kode":"62.010"},"forretningsadresse":{"kommune":"OSLO","poststed":"OSLO"}}'
)


def profile(*, with_site: bool = False) -> dict:
    from norway_company_agent.evidence_spans import module_spans

    body = LIVE_BODY.encode()
    parsed = json.loads(LIVE_BODY)
    live = evidence(
        "registry_live", "available", "official_registry_live", "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
        value={"name": parsed["navn"], "legal_form": "AS", "employees": 12, "industry": parsed["naeringskode1"], "business_address": parsed["forretningsadresse"],
               "activity": parsed["aktivitet"], "purpose": parsed["vedtektsfestetFormaal"], "founded": parsed["stiftelsesdato"]},
        content_sha256="a" * 64, spans=module_spans("registry_live", body, parsed),
    )
    row = {"organisation_number": "923609016", "evidence": {"registry_live": live}}
    if with_site:
        html = '<html><head><title>Example</title><meta name="description" content="We build software for banks."></head></html>'
        row["evidence"]["website"] = evidence(
            "website", "available", "company_site", "https://example.no/", value={
                "final_url": "https://example.no/", "title": "Example", "description": "We build software for banks.",
                "description_span": description_span(html), "title_span": "<title>Example</title>", "identity_assessment": {"publishable": True},
            }, content_sha256="b" * 64,
        )
    return row


class SummaryTests(unittest.TestCase):
    def test_the_registrys_own_description_and_founding_date_are_cited_claims(self):
        envelope = build_envelope(profile(), **ARGS)
        claims = {c["field"]: c for c in envelope["claims"] if c["availability"] == "available"}
        self.assertEqual(claims["business_activity"]["value"], "Utvikling og salg av programvare")
        self.assertEqual(claims["registered_purpose"]["value"], "Selskapets formaal er IT")
        self.assertEqual(claims["founded_date"]["value"], "2015-03-02")
        spans = {e["id"]: e["claim_span"] for e in envelope["evidence"]}
        self.assertIn('"aktivitet"', spans[claims["business_activity"]["evidence_ids"][0]])
        text = envelope["summary"]["text"]
        self.assertIn('Its registered activity is: "Utvikling og salg av programvare".', text)
        self.assertIn("founded on 2015-03-02", text)

    def test_the_website_description_is_a_claim_with_the_meta_tag_as_its_excerpt(self):
        envelope = build_envelope(profile(with_site=True), **ARGS)
        claim = next(c for c in envelope["claims"] if c["field"] == "website_description")
        excerpt = next(e["claim_span"] for e in envelope["evidence"] if e["id"] == claim["evidence_ids"][0])
        self.assertEqual(claim["value"], "We build software for banks.")
        self.assertTrue(excerpt.startswith("<meta") and "We build software for banks." in excerpt)
        self.assertIn('Its own website describes it as: "We build software for banks".', envelope["summary"]["text"])

    def test_the_summary_is_sectioned_cites_numbered_sources_and_says_what_is_unknown(self):
        envelope = build_envelope(profile(), **ARGS)
        summary = envelope["summary"]
        titles = [item["section"] for item in summary["sentences"]]
        self.assertEqual(titles, sorted(titles, key=SUMMARY_SECTIONS.index))
        self.assertIn("What it does:", summary["text"])
        self.assertIn("Not yet determined:", summary["text"])
        self.assertEqual(summary["text"].count("Not yet determined:"), 1)
        self.assertIn("\n\nSources:\n[1] https://data.brreg.no/enhetsregisteret/api/enheter/923609016", summary["text"])
        by_number = {item["n"]: item for item in summary["sources"]}
        for sentence in summary["sentences"]:
            for number in sentence["citations"]:
                self.assertIn(f"[{number}]", summary["text"])
                self.assertIn(number, by_number)

    def test_an_older_captured_year_is_not_called_the_latest_when_the_registry_shows_a_later_filing(self):
        row = profile()
        row["evidence"]["registry_live"]["value"]["latest_submitted_accounts"] = "2025"
        row["evidence"]["financials"] = evidence(
            "financials", "available", "official_financials", "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
            value={"records": [{"period": {"tilDato": "2024-12-31"}, "revenue": 1000.0, "annual_result": 10.0}]}, content_sha256="e" * 64,
        )
        text = build_envelope(row, **ARGS)["summary"]["text"]
        self.assertIn("The latest accounts with captured figures are for 2024", text)
        self.assertIn("a later filing (2025) whose figures were not captured", text)
        self.assertNotIn("most recently filed accounts (2024)", text)

    def test_group_and_company_accounts_for_one_year_are_separate_claims_and_the_summary_uses_the_companys_own(self):
        row = profile()
        period = {"fraDato": "2023-01-01", "tilDato": "2023-12-31"}
        row["evidence"]["financials"] = evidence(
            "financials", "available", "official_financials", "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
            value={"records": [
                {"record_id": 2, "account_type": "KONSERN", "period": period, "revenue": 208764678.0, "annual_result": 3825143.0},
                {"record_id": 1, "account_type": "SELSKAP", "period": period, "revenue": 142268177.0, "annual_result": 3978330.0},
            ]}, content_sha256="f" * 64,
        )
        envelope = build_envelope(row, **ARGS)
        fields = [c["field"] for c in envelope["claims"] if c["field"].startswith("annual_accounts.") and c["availability"] == "available"]
        self.assertEqual(sorted(fields), ["annual_accounts.2023", "annual_accounts.2023.consolidated"])
        text = envelope["summary"]["text"]
        self.assertIn("revenue of 142,268,177 NOK", text)  # the company's own accounts
        self.assertIn("The consolidated group accounts for 2023 report revenue of 208,764,678 NOK, a different scope", text)  # the group figure, labelled as such
        flat = flatten_envelope(envelope)
        self.assertEqual((flat["facts"]["revenue"], flat["facts"]["year"]), (142268177.0, "2023"))
        self.assertEqual(sorted(a["scope"] for a in flat["accounts"]), ["company", "group"])

    def test_the_summary_opens_with_one_supported_assessment_of_the_whole_company(self):
        from norway_company_agent.official import normalize_financials

        row = profile()
        row["evidence"]["financials"] = evidence(
            "financials", "available", "official_financials", "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
            value=normalize_financials(FinancialRecordOrderTests()._body()), content_sha256="d" * 64,
        )
        summary = build_envelope(row, **ARGS)["summary"]
        first = summary["sentences"][0]
        self.assertEqual(first["section"], "In short")
        self.assertTrue(first["text"].startswith("In short: EXAMPLE AS was profitable in 2025"), first["text"])
        self.assertIn("no open roles were found on its own site", first["text"])
        self.assertTrue(first["evidence_ids"] and first["citations"])  # every clause rests on cited claims
        self.assertTrue(summary["text"].startswith("In short: EXAMPLE AS"))
        self.assertEqual(summary["text"].count("In short:"), 1)

    def test_the_assessment_stays_grammatical_and_honest_when_no_accounts_were_captured(self):
        text = build_envelope(profile(), **ARGS)["summary"]["text"]
        self.assertIn("In short: EXAMPLE AS has no annual-accounts figures captured; the registry records 12 employees; it has no verified website", text)

    def test_a_first_snapshot_says_so_and_a_refresh_reports_the_change(self):
        first = build_envelope(profile(), **ARGS)["summary"]["text"]
        self.assertIn("first recorded snapshot", first)
        event = {"organisation_number": "923609016", "field": "registry.employees", "old_value": 4, "new_value": 12}
        changed = build_envelope(profile(), changes=[event], **ARGS)["summary"]["text"]
        self.assertIn("registry.employees (4 -> 12)", changed)
        self.assertNotIn("first recorded snapshot", changed)

    def test_a_group_structure_that_exists_is_not_reported_as_unknown(self):
        row = profile()
        row["evidence"]["group"] = evidence("group", "available", "official_group", "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/923609016",
                                            value={"navn": "PARENT AS", "organisasjonsnummer": "912805174", "children": [{"navn": "A"}, {"navn": "B"}]}, content_sha256="c" * 64)
        summary = build_envelope(row, **ARGS)["summary"]
        self.assertIn("headed by PARENT AS (912805174), with 2 directly linked companies", summary["text"])
        self.assertNotIn("group/ownership structure", summary["unknown_fields"])

    def test_news_and_job_claims_carry_title_url_date_and_the_exact_company(self):
        observations = [
            {"id": "obs-1", "signal_type": "public_post", "source_url": "https://example.no/news/1", "source_class": "company_owned", "retrieved_at": "2026-10-14T00:00:00Z", "content_sha256": "d" * 64,
             "excerpt": "<time datetime='2026-10-01'>1 Oct</time>", "metrics": {"headline": "We launch", "published_at": "2026-10-01T00:00:00Z", "url": "https://example.no/news/1"}},
        ]
        envelope = build_envelope(profile(), observations=observations, **ARGS)
        value = next(c["value"] for c in envelope["claims"] if c["field"] == "site_news.0")
        self.assertEqual((value["title"], value["url"], value["published_at"]), ("We launch", "https://example.no/news/1", "2026-10-01T00:00:00Z"))
        self.assertEqual(value["company"], {"organisation_number": "923609016", "legal_name": "EXAMPLE AS"})


class FinancialRecordOrderTests(unittest.TestCase):
    """The register returns accounts oldest-first; the newest filings must not be the ones that get dropped."""

    def _body(self):
        def record(identifier, year, kind="SELSKAP", revenue=1000):
            return {"id": identifier, "regnskapstype": kind, "regnskapsperiode": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}, "valuta": "NOK",
                    "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": revenue + int(year)}}, "aarsresultat": 5}}
        return [record(101, "2019"), record(102, "2020"), record(103, "2021"), record(104, "2022"), record(105, "2023"),
                record(106, "2024"), record(107, "2025", "KONSERN", 9000), record(108, "2025")]

    def test_the_newest_filings_are_kept_newest_first_with_the_companys_own_accounts_ahead_of_the_groups(self):
        from norway_company_agent.official import normalize_financials

        records = normalize_financials(self._body())["records"]
        self.assertEqual([(r["period"]["tilDato"][:4], r["account_type"]) for r in records],
                         [("2025", "SELSKAP"), ("2025", "KONSERN"), ("2024", "SELSKAP"), ("2023", "SELSKAP"), ("2022", "SELSKAP"), ("2021", "SELSKAP")])

    def test_each_kept_record_has_an_excerpt_from_its_own_part_of_the_response(self):
        from norway_company_agent.evidence_spans import financials_spans

        body = self._body()
        raw = json.dumps(body, separators=(",", ":"))
        spans = financials_spans(raw, body)
        self.assertEqual(len(spans), 6)
        self.assertTrue(all(span["period"] in raw for span in spans))  # a literal slice of the saved response
        self.assertIn("2025-12-31", spans[0]["period"])
        self.assertIn("2024-12-31", spans[2]["period"])

    def test_the_summary_names_the_newest_year_not_the_oldest_captured(self):
        from norway_company_agent.official import normalize_financials

        row = profile()
        row["evidence"]["registry_live"]["value"]["latest_submitted_accounts"] = "2025"
        row["evidence"]["financials"] = evidence(
            "financials", "available", "official_financials", "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
            value=normalize_financials(self._body()), content_sha256="d" * 64,
        )
        text = build_envelope(row, **ARGS)["summary"]["text"]
        self.assertIn("most recently filed accounts (2025)", text)
        self.assertIn("Revenue", text)
        self.assertNotIn("most recently filed accounts (2021)", text)


class DescriptionSpanTests(unittest.TestCase):
    def test_meta_description_variants_are_found_literally(self):
        for tag in ('<meta name="description" content="A">', "<meta property='og:description' content='A'>", '<META NAME=description CONTENT="A">'):
            self.assertEqual(description_span(f"<head>{tag}</head>"), tag)
        self.assertIsNone(description_span('<meta name="keywords" content="a">'))


class RefreshTests(unittest.TestCase):
    def test_a_finished_run_is_kept_as_previous_and_an_unfinished_one_is_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "profiles.jsonl").write_text('{"organisation_number":"1"}\n', encoding="utf-8")
            self.assertIsNone(archive_finished_run(out))  # no run-summary.json: an unfinished run, resumed in place
            self.assertTrue((out / "profiles.jsonl").exists())
            (out / "run-summary.json").write_text("{}", encoding="utf-8")
            (out / "envelopes.jsonl").write_text("{}\n", encoding="utf-8")
            previous = archive_finished_run(out)
            self.assertEqual(Path(previous), out / "previous" / "profiles.jsonl")
            self.assertEqual(json.loads((out / "previous" / "profiles.jsonl").read_text(encoding="utf-8")), {"organisation_number": "1"})
            self.assertTrue((out / "previous" / "envelopes.jsonl").exists())
            self.assertFalse((out / "profiles.jsonl").exists())  # fetched fresh this time
            self.assertFalse((out / "run-summary.json").exists())
            self.assertIsNone(archive_finished_run(out))  # a crash now would resume, not archive again


class ViewerTests(unittest.TestCase):
    def test_the_viewer_flattens_an_envelope_for_find_compare_and_verify(self):
        envelope = build_envelope(profile(with_site=True), **ARGS)
        flat = flatten_envelope(envelope)
        self.assertEqual((flat["org"], flat["name"], flat["form"]), ("923609016", "EXAMPLE AS", "AS"))
        self.assertEqual(flat["facts"]["website"], "https://example.no/")
        self.assertEqual(flat["facts"]["employees"], 12)
        self.assertEqual([s["title"] for s in flat["summary_sections"]][:2], ["In short", "What it does"])
        complete, available = flat["evidence_complete"]
        self.assertGreater(available, 0)
        self.assertLessEqual(complete, available)
        self.assertTrue(all(claim["source_url"] for claim in flat["claims"] if claim["availability"] == "available"))

    def test_the_page_embeds_its_data_without_being_able_to_close_its_own_script(self):
        flat = flatten_envelope(build_envelope(profile(), **ARGS))
        flat["claims"][0]["excerpt"] = "</script><script>alert(1)</script>"
        page = PAGE_TEMPLATE.replace("__DATA_JSON__", embed_safe(json.dumps([flat], ensure_ascii=False)))
        self.assertEqual(page.count("</script>"), 1)  # only the page's own closing tag
        for needed in ('id="compare-btn"', 'id="search"', 'viewport', 'prefers-color-scheme', 'data-view="list"', "max-width: 860px"):
            self.assertIn(needed, page)


class SearchProviderTests(unittest.TestCase):
    def test_providers_run_only_when_their_key_is_set_in_the_documented_order(self):
        self.assertEqual(discovery_stages({}), [])
        both = discovery_stages({"TAVILY_API_KEY": "t", "BRAVE_API_KEY": "b", "EXA_API_KEY": " "})
        self.assertEqual([(n, v) for n, _, v in both], [("brave", "BRAVE_API_KEY"), ("tavily", "TAVILY_API_KEY")])
        self.assertEqual(discovery_stages({"BRAVE_SEARCH_API_KEY": "x"})[0][2], "BRAVE_SEARCH_API_KEY")

    def test_the_brave_stage_crawls_a_candidate_and_publishes_only_what_the_gate_accepts(self):
        import os
        from unittest.mock import patch

        import scripts.run_brave_discovery as brave

        rows = [
            {"organisation_number": "923609016", "name": "EXAMPLE TOOLS AS", "evidence": {}},
            {"organisation_number": "923609017", "name": "HAS SITE AS", "website": "https://has.no", "evidence": {}},
        ]
        results = [{"url": "https://exampletools.no/", "title": "Example Tools AS - 923609016", "snippet": "Example Tools AS 923609016", "rank": 1, "provider": "brave_search_api", "query": "q"}]
        page = evidence("website", "available", "company_site", "https://exampletools.no/", value={
            "final_url": "https://exampletools.no/", "title": "Example Tools AS", "main_text_excerpt": "Example Tools AS organisasjonsnummer 923 609 016. " * 4,
        })
        with tempfile.TemporaryDirectory() as directory:
            src, out, report = Path(directory) / "in.jsonl", Path(directory) / "out.jsonl", Path(directory) / "r.json"
            src.write_text(chr(10).join(json.dumps(r) for r in rows) + chr(10), encoding="utf-8")
            argv = ["run_brave_discovery.py", "--input", str(src), "--output", str(out), "--report", str(report), "--limit", "5", "--promote-verified", "--min-interval", "0"]
            with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test"}), patch.object(sys, "argv", argv),                     patch.object(brave, "brave_search", return_value=(results, {"status": 200, "latency_ms": 5, "bytes": 10, "query_sha256": "a" * 64})) as search,                     patch.object(brave, "fetch_website", return_value=(page, {"requests": 3})):
                brave.main()
            written = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(search.call_count, 1)  # the company that already has a site is not queried
        found = written[0]["evidence"]
        self.assertEqual(found["website_discovery"]["status"], "available")
        self.assertEqual(found["website"]["value"]["final_url"], "https://exampletools.no/")
        self.assertNotIn("Example Tools AS - 923609016", json.dumps(found["website_discovery"]))  # the search title and snippet are never kept
        self.assertNotIn("evidence", {k: v for k, v in written[1].items() if k == "website_discovery"})


if __name__ == "__main__":
    unittest.main()
