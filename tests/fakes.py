from healthcare_agent.ai import AiExplanation


class DisabledAiService:
    enabled = False

    def explain(self, case_payload, fallback_answer):
        return AiExplanation(status="not_configured", answer=fallback_answer, model="test-disabled")

class FakeProcedureClassifier:
    enabled = True

    def classify(self, procedure_query: str):
        if "x-ray of the lungs" in procedure_query.lower():
            return {"code": "71045", "label": "Radiologic examination, chest"}
        return None
