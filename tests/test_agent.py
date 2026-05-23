import unittest

from healthcare_agent.agent import PatientAdvocateAgent
from healthcare_agent.tools import classify_case_type, procedure_lookup
from tests.fakes import DisabledAiService


class FakeAiService:
    enabled = True

    def explain(self, case_payload, fallback_answer):
        del fallback_answer
        from healthcare_agent.ai import AiExplanation

        code = case_payload["facts"]["cpt_candidates"][0]
        return AiExplanation(
            status="success",
            answer=f"AI says CPT {code} is for the MRI and the quote should be questioned.",
            model="fake-gemini",
        )


class AgentTests(unittest.TestCase):
    def test_classifies_estimate_review(self):
        self.assertEqual(
            classify_case_type("I was quoted $2200 for a knee MRI. Is this fair?"),
            "estimate_review",
        )

    def test_maps_knee_mri_to_cpt(self):
        procedure = procedure_lookup("knee MRI in Hoboken")
        self.assertIsNotNone(procedure)
        self.assertEqual(procedure.code, "73721")

    def test_high_estimate_generates_artifact(self):
        agent = PatientAdvocateAgent(ai_service=DisabledAiService())
        response = agent.respond("I was quoted $2,200 for a knee MRI in Hoboken with Aetna. Is this fair?")
        self.assertEqual(response["case_type"], "estimate_review")
        self.assertEqual(response["facts"]["cpt_candidates"], ["73721"])
        self.assertEqual(response["cards"]["fairness"]["status"], "high")
        self.assertIsNotNone(response["artifact"])
        self.assertGreaterEqual(len(response["tool_trace"]), 5)
        self.assertEqual(response["cards"]["ai_explanation"]["status"], "not_configured")

    def test_find_cheaper_care_returns_options(self):
        agent = PatientAdvocateAgent(ai_service=DisabledAiService())
        response = agent.respond("Find me a cheaper place for a knee MRI near Hoboken with Aetna.")
        self.assertEqual(response["case_type"], "find_cheaper_care")
        self.assertGreater(len(response["cards"]["care_options"]), 0)

    def test_missing_procedure_requests_clarification(self):
        agent = PatientAdvocateAgent(ai_service=DisabledAiService())
        response = agent.respond("I got a confusing bill from Aetna for $500 in Hoboken.")
        self.assertIn("procedure or CPT code", response["facts"]["missing"])
        self.assertIn("could not confidently map", response["answer"])

    def test_ai_explanation_replaces_fallback_answer(self):
        agent = PatientAdvocateAgent(ai_service=FakeAiService())
        response = agent.respond("I was quoted $2,200 for a knee MRI in Hoboken with Aetna. Is this fair?")
        self.assertIn("AI says CPT 73721", response["answer"])
        self.assertEqual(response["cards"]["ai_explanation"]["status"], "success")
        self.assertIn("ai_patient_explanation", [item["name"] for item in response["tool_trace"]])

    def test_follow_up_conversation_resolves_context(self):
        agent = PatientAdvocateAgent(ai_service=DisabledAiService())
        case_id = "test-session-1"

        # Turn 1: Quoted estimate for a knee MRI in Hoboken
        res1 = agent.respond("I was quoted $2,200 for a knee MRI in Hoboken with Aetna. Is this fair?", case_id=case_id)
        self.assertEqual(res1["case_type"], "estimate_review")
        self.assertEqual(res1["facts"]["cpt_candidates"], ["73721"])
        self.assertEqual(res1["facts"]["location"], "hoboken")
        self.assertEqual(res1["facts"]["payer"], "aetna")

        # Turn 2: Follow-up asking for cheaper provider (no code/location/payer mentioned)
        res2 = agent.respond("Can you find a cheaper provider?", case_id=case_id)
        self.assertEqual(res2["case_type"], "find_cheaper_care")
        self.assertEqual(res2["facts"]["cpt_candidates"], ["73721"])
        self.assertEqual(res2["facts"]["location"], "hoboken")
        self.assertEqual(res2["facts"]["payer"], "aetna")
        # Care options should be populated because the CPT was inherited
        self.assertGreater(len(res2["cards"]["care_options"]), 0)

        # Turn 3: Follow-up changing the payer (no code/location/intent mentioned)
        res3 = agent.respond("What about for United?", case_id=case_id)
        # Should carry over case_type find_cheaper_care and location/CPT, but update the payer to united
        self.assertEqual(res3["case_type"], "find_cheaper_care")
        self.assertEqual(res3["facts"]["cpt_candidates"], ["73721"])
        self.assertEqual(res3["facts"]["location"], "hoboken")
        self.assertEqual(res3["facts"]["payer"], "united")


if __name__ == "__main__":
    unittest.main()
