import unittest
from pathlib import Path

from healthcare_agent.agent import PatientAdvocateAgent
from healthcare_agent.external import CmsBenchmark, HospitalInfo
from healthcare_agent.mrf import HospitalMrfParser, MrfSourceService
from tests.fakes import DisabledAiService


FIXTURES = Path(__file__).parent / "fixtures"


class NoExternalEvidenceService:
    def medical_costs_api_lookup(self, cpt):
        return CmsBenchmark(code=cpt, source="Medical Costs API", status="not_configured")

    def cms_open_hospital_lookup(self, hospital_name, location):
        return [
            HospitalInfo(
                source="CMS Hospital General Information",
                status="not_found",
                message="Not used in this test.",
            )
        ]

    def discover_hospital_price_files(self, hospital_name, location, cpt):
        return []

    def discover_mrf_links(self, hospital_name, location):
        return []

    def extract_public_context(self, cpt=None):
        return []


class MrfParserTests(unittest.TestCase):
    def test_parses_cms_tall_csv_by_cpt_and_payer(self):
        parser = HospitalMrfParser()
        matches = parser.parse_source(str(FIXTURES / "cms_tall_mrf.csv"), cpt="73721", payer="Aetna")
        self.assertEqual(matches[0].status, "found")
        self.assertEqual(matches[0].hospital_name, "Example Hospital")
        self.assertEqual(matches[0].payer_name, "Aetna")
        self.assertEqual(matches[0].negotiated_dollar, 780.0)
        self.assertEqual(matches[0].median_allowed, 760.0)

    def test_parses_cms_wide_csv_by_cpt_and_payer(self):
        parser = HospitalMrfParser()
        matches = parser.parse_source(str(FIXTURES / "cms_wide_mrf.csv"), cpt="73721", payer="Aetna")
        self.assertEqual(matches[0].status, "found")
        self.assertEqual(matches[0].payer_name, "aetna")
        self.assertEqual(matches[0].plan_name, "open_access")
        self.assertEqual(matches[0].negotiated_dollar, 780.0)

    def test_parses_json_by_cpt_and_payer(self):
        parser = HospitalMrfParser()
        matches = parser.parse_source(str(FIXTURES / "cms_mrf.json"), cpt="73721", payer="Aetna")
        self.assertEqual(matches[0].status, "found")
        self.assertEqual(matches[0].file_format, "json")
        self.assertEqual(matches[0].negotiated_dollar, 780.0)

    def test_agent_includes_mrf_matches_card(self):
        mrf_service = MrfSourceService(sources=[str(FIXTURES / "cms_tall_mrf.csv")])
        agent = PatientAdvocateAgent(
            evidence_service=NoExternalEvidenceService(),
            mrf_service=mrf_service,
            ai_service=DisabledAiService(),
        )
        response = agent.respond("I was quoted $2,200 for a knee MRI at Example Hospital in Hoboken with Aetna.")
        self.assertEqual(response["cards"]["mrf_matches"][0]["status"], "found")
        self.assertIn("hospital_mrf_parse", [item["name"] for item in response["tool_trace"]])
        self.assertIn("Configured hospital MRF parsing returned", response["answer"])


if __name__ == "__main__":
    unittest.main()
