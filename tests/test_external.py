import unittest

from healthcare_agent.agent import PatientAdvocateAgent
from healthcare_agent.external import CmsBenchmark, HospitalInfo, WebEvidence
from tests.fakes import DisabledAiService


class FakeEvidenceService:
    def medical_costs_api_lookup(self, cpt):
        return CmsBenchmark(
            code=cpt,
            source="CMS Procedure Price Lookup API",
            status="found",
            title="MRI lower extremity joint without contrast",
            hospital_outpatient_payment=320.0,
            ambulatory_surgical_center_payment=250.0,
            beneficiary_copay=64.0,
            raw={"code": cpt},
        )

    def cms_open_hospital_lookup(self, hospital_name, location):
        return [
            HospitalInfo(
                source="CMS Hospital General Information",
                status="found",
                facility_id="310000",
                facility_name="Example Hospital",
                city="Hoboken",
                state="NJ",
                zip_code="07030",
                hospital_type="Acute Care Hospitals",
                rating="4",
            )
        ]

    def discover_hospital_price_files(self, hospital_name, location, cpt):
        return [
            WebEvidence(
                source="Nimble Search",
                url="https://hospitalpricingfiles.org/details/example",
                status="found",
                title="Example hospital pricing file",
                summary=f"Found a hospital price transparency page mentioning {cpt}.",
                matches=[f"{cpt} sample matching line"],
            )
        ]

    def discover_mrf_links(self, hospital_name, location):
        return ["https://example.com/hospital_mrf.json"]

    def extract_public_context(self, cpt=None):
        return [
            WebEvidence(
                source="CMS Hospital Price Transparency",
                url="https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency",
                status="success",
                title="CMS HPT",
                summary="Hospitals must publish machine-readable files and shoppable-service information.",
                matches=[],
            )
        ]


class ExternalEvidenceTests(unittest.TestCase):
    def test_agent_includes_cms_and_public_evidence_cards(self):
        agent = PatientAdvocateAgent(evidence_service=FakeEvidenceService(), ai_service=DisabledAiService())
        response = agent.respond("I was quoted $2,200 for a knee MRI at Example Hospital in Hoboken with Aetna.")
        self.assertEqual(response["cards"]["cms_benchmark"]["status"], "found")
        self.assertEqual(response["cards"]["cms_hospitals"][0]["status"], "found")
        self.assertEqual(len(response["cards"]["public_evidence"]), 2)
        self.assertIn("CMS Procedure Price Lookup returned", response["answer"])
        self.assertIn("cms_open_hospital_lookup", [item["name"] for item in response["tool_trace"]])
        self.assertIn("public_evidence_lookup", [item["name"] for item in response["tool_trace"]])


if __name__ == "__main__":
    unittest.main()
