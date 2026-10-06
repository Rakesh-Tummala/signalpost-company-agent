"""Registry-declared contact proofs: organisation number, registered phone and e-mail on a company's own pages."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import website as website_module  # noqa: E402
from norway_company_agent.contacts import GENERIC_SUFFIX_WORDS, identifier_proofs, own_email_domain, page_identifiers, registered_contacts  # noqa: E402
from norway_company_agent.domain_probe import candidate_hosts, contact_links, domain_candidates  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import assess_website_identity  # noqa: E402
from norway_company_agent.page_signals import extract_page_signals  # noqa: E402
from norway_company_agent.website import _priority_links  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

BODY = "Vi leverer tjenester til bedrifter over hele landet. " * 4  # a substantive homepage


def profile(name: str, org: str = "923609016", *, final_url: str, title: str, identifiers: list[dict] | None = None, registry: dict | None = None,
            body: str = BODY) -> dict:
    pages = [{"title": title, "main_text_excerpt": body, "signals": {"identifiers": item}} for item in (identifiers or [])]
    return {
        "organisation_number": org,
        "name": name,
        "evidence": {
            "registry": {"status": "available", "value": registry or {}},
            "website": evidence("website", "available", "company_site", final_url, value={
                "final_url": final_url, "title": title, "main_text_excerpt": body, "pages": pages,
            }),
        },
    }


def ids(org: str = "", phone: str = "", email: str = "", extra_orgs: list[str] | None = None) -> dict:
    return {"org_numbers": sorted(filter(None, [org, *(extra_orgs or [])])), "phones": [phone] if phone else [], "emails": [email] if email else []}


class PageIdentifierTests(unittest.TestCase):
    def test_numbers_phones_and_addresses_are_read_from_visible_text_and_links_not_scripts(self):
        html = (
            "<html><body><p>Tlf 55 12 34 56, mobil +47 400 12 345. Org.nr 923 609 016</p>"
            '<a href="mailto:Post@Example.no">mail</a><script>var id=923609017;</script></body></html>'
        )
        found = extract_page_signals(html, "https://example.no/")["identifiers"]
        self.assertEqual(found["org_numbers"], ["923609016"])
        self.assertEqual(found["phones"], ["40012345", "55123456"])
        self.assertEqual(found["emails"], ["post@example.no"])

    def test_a_longer_digit_run_is_not_an_organisation_number(self):
        self.assertEqual(page_identifiers("Konto 1234 5678901234 og 9236090160")["org_numbers"], [])

    def test_proofs_compare_against_what_the_company_registered(self):
        contacts = registered_contacts({"mobil": "400 12 345", "telefon": "", "epostadresse": "Post@Example.no"})
        self.assertEqual(contacts["phones"], {"40012345"})
        self.assertEqual(contacts["email_domain"], "example.no")
        self.assertEqual(identifier_proofs("923609016", contacts, [ids(org="923609016", phone="40012345", email="post@example.no")]),
                         ["organisation_number", "registered_phone", "registered_email"])
        self.assertEqual(identifier_proofs("923609016", contacts, [ids(phone="99999999")]), [])

    def test_mail_providers_and_isps_are_not_a_company_domain(self):
        self.assertEqual(own_email_domain("gmail.com"), "")
        self.assertEqual(own_email_domain("online.no"), "")
        self.assertEqual(own_email_domain("example.no"), "example.no")


class GateWithContactProofTests(unittest.TestCase):
    def test_the_organisation_number_on_a_contact_page_publishes_a_site_that_carries_the_name(self):
        row = profile("Kilen Motor AS", final_url="https://kilenmotor.no/", title="Kilen motor AS - Helt 100", identifiers=[ids(), ids(org="923609016")])
        result = assess_website_identity(row)
        self.assertTrue(result["publishable"], result)
        self.assertIn("organisation_number", result["registry_contact_proofs"])

    def test_a_group_page_listing_a_subsidiarys_number_does_not_publish_the_group_site(self):
        # Real shape: SKS Produksjon AS appears on its parent's contact page next to other subsidiaries.
        row = profile("SKS Produksjon AS", org="915637353", final_url="https://sks.no/", title="Konsern - SKS - Forside",
                      identifiers=[ids(), ids(org="915637353", extra_orgs=["915637354", "915637355"])])
        self.assertFalse(assess_website_identity(row)["publishable"])

    def test_the_registered_phone_with_the_name_in_the_hostname_publishes(self):
        row = profile("Brandmaker AS", final_url="https://brandmaker.no/", title="Webdesign Bergen", identifiers=[ids(phone="40012345")],
                      registry={"mobil": "400 12 345"})
        result = assess_website_identity(row)
        self.assertTrue(result["publishable"], result)
        self.assertEqual(result["registry_contact_proofs"], ["registered_phone"])

    def test_an_accountants_site_showing_a_clients_registered_phone_is_not_the_clients_site(self):
        row = profile("Anne Guri Ellingsen AS", final_url="https://tfjelland.no/", title="T. Fjelland & Co. B2B", identifiers=[ids(phone="40012345")],
                      registry={"mobil": "400 12 345"})
        self.assertFalse(assess_website_identity(row)["publishable"])

    def test_a_managers_site_on_the_registered_email_domain_without_the_name_is_not_published(self):
        row = profile("Nasle Borettslag", final_url="https://www.vbbl.no/", title="VBBL", identifiers=[ids(email="post@vbbl.no")],
                      registry={"epostadresse": "post@vbbl.no"})
        self.assertFalse(assess_website_identity(row)["publishable"])

    def test_the_registered_email_domain_with_the_name_in_the_host_publishes_only_with_a_real_page(self):
        registry = {"epostadresse": "post@badeog.net"}
        real = profile("Bådeog AS", final_url="https://badeog.net/", title="Hjem - BådeOg", registry=registry)
        self.assertTrue(assess_website_identity(real)["publishable"])
        thin = profile("Bådeog AS", final_url="https://badeog.net/", title="Hjem - BådeOg", registry=registry, body="")
        self.assertFalse(assess_website_identity(thin)["publishable"])

    def test_a_registrar_placeholder_is_not_a_website(self):
        row = profile("Mesco AS", final_url="https://mesco.no/", title="ADATA.NO REGISTRERT DOMENE", registry={"epostadresse": "post@mesco.no"})
        self.assertFalse(assess_website_identity(row)["publishable"])

    def test_a_generic_trailing_word_may_be_missing_from_the_domain(self):
        row = profile("Inselo Norge AS", final_url="https://inselo.no/", title="Varmepumpe og ladeboks", identifiers=[ids(org="923609016")])
        self.assertTrue(assess_website_identity(row)["publishable"])
        self.assertIn("norge", GENERIC_SUFFIX_WORDS)


class DiscoveryCandidateTests(unittest.TestCase):
    def test_the_registered_email_domain_is_tried_first_unless_it_is_a_mail_provider(self):
        self.assertEqual(candidate_hosts("Arkitektfirma Jon Vikøren AS", {"epostadresse": "post@arkjv.no"})[0], "arkjv.no")
        self.assertNotIn("gmail.com", candidate_hosts("Example AS", {"epostadresse": "someone@gmail.com"}))

    def test_a_trailing_generic_word_is_also_tried_off(self):
        self.assertIn("auro.no", domain_candidates("AURO HOLDING AS"))

    def test_contact_about_and_privacy_links_on_the_same_site_are_followed(self):
        links = contact_links("https://example.no/", [("/kontakt-oss", "Kontakt"), ("https://other.no/kontakt", "x"), ("/produkter", "Produkter"), ("/personvern", "")], "example.no")
        self.assertEqual(links, ["https://example.no/kontakt-oss", "https://example.no/personvern"])


class TransientFetchTests(unittest.TestCase):
    def test_a_server_error_is_retried_once_but_a_refusal_is_not(self):
        broken = evidence("website", "source_error", "registry_linked_company_website", "https://example.no/", note="HTTP 503")
        fine = evidence("website", "available", "registry_linked_company_website", "https://example.no/", value={"title": "ok"})
        metrics = {"requests": 2, "bytes": 0, "latencies_ms": [10]}
        with patch.object(website_module, "_fetch_website_once", side_effect=[(broken, dict(metrics)), (fine, dict(metrics))]) as once, patch.object(website_module.time, "sleep"):
            record, total = website_module.fetch_website("https://example.no/")
        self.assertEqual((record["status"], once.call_count, total["requests"]), ("available", 2, 4))
        refused = evidence("website", "blocked", "registry_linked_company_website", "https://example.no/", note="robots.txt disallows this user agent")
        with patch.object(website_module, "_fetch_website_once", side_effect=[(refused, dict(metrics))]) as once, patch.object(website_module.time, "sleep"):
            record, _ = website_module.fetch_website("https://example.no/")
        self.assertEqual((record["status"], once.call_count), ("blocked", 1))


class CrawlBudgetTests(unittest.TestCase):
    def _links(self, hrefs):
        html = "<html><body>" + "".join(f'<a href="{href}">{label}</a>' for href, label in hrefs) + "</body></html>"
        return _priority_links("https://example.com/", BeautifulSoup(html, "lxml"))

    def test_a_careers_page_is_not_crowded_out_by_many_about_subpages(self):
        # Real case: shgroup.dk has six /about/... pages and a /career page with six open roles.
        links = self._links([("/about", "About"), ("/about/certifications", "Certifications"), ("/about/hse", "HSE"), ("/about/management", "Management"),
                             ("/about/history", "History"), ("/about/organisation", "Organisation"), ("/contact", "Contact"), ("/career", "Career"), ("/news", "News")])
        self.assertIn("https://example.com/career", links)
        self.assertIn("https://example.com/news", links)
        self.assertLessEqual(len([link for link in links if "/about" in link or "/contact" in link]), 3)

    def test_shallow_pages_win_and_article_slugs_that_mention_jobs_lose(self):
        links = self._links([("/artikkel/endelig-fikk-hun-drommejobben", "Les mer"), ("/artikkel/enda-en-jobb-historie", "Les mer"), ("/artikkel/tredje-jobb", "Les mer"), ("/karriere", "Karriere")])
        self.assertEqual(links[0], "https://example.com/karriere")

    def test_a_careers_link_under_about_is_still_a_jobs_page(self):
        links = self._links([("/about/careers", "Careers"), ("/about", "About"), ("/about/a", "A"), ("/about/b", "B"), ("/about/c", "C")])
        self.assertIn("https://example.com/about/careers", links)


if __name__ == "__main__":
    unittest.main()
