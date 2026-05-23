from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from healthcare_agent.config import get_env


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash"


@dataclass
class AiExplanation:
    status: str
    answer: str | None = None
    model: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or get_env("GOOGLE_AI_API_KEY") or get_env("GEMINI_API_KEY")
        self.model = model or get_env("GEMINI_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or get_env("GEMINI_BASE_URL") or GEMINI_BASE_URL).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def generate_text(self, prompt: str) -> str:
        if not self.enabled:
            raise AiServiceError("GOOGLE_AI_API_KEY is not configured")
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "maxOutputTokens": 1200,
                "responseMimeType": "application/json",
            },
        }
        request = Request(
            f"{self.base_url}/models/{self.model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key or "",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AiServiceError(f"Gemini HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AiServiceError(f"Gemini request failed: {exc.reason}") from exc

        try:
            return response_payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise AiServiceError("Gemini response did not contain generated text") from exc


class AiPatientExplainer:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def explain(self, case_payload: dict[str, Any], fallback_answer: str) -> AiExplanation:
        if not self.enabled:
            return AiExplanation(status="not_configured", answer=fallback_answer, model=self.client.model)

        prompt = _build_prompt(case_payload)
        try:
            generated = self.client.generate_text(prompt)
        except AiServiceError as exc:
            return AiExplanation(status="error", answer=fallback_answer, model=self.client.model, error=str(exc))

        answer = _extract_answer(generated)
        if not _looks_complete(answer):
            return AiExplanation(
                status="invalid_response",
                answer=fallback_answer,
                model=self.client.model,
                error="Gemini returned an empty or incomplete explanation.",
            )
        return AiExplanation(status="success", answer=answer, model=self.client.model)


def _build_prompt(case_payload: dict[str, Any]) -> str:
    compact_payload = _compact_case_payload(case_payload)
    return (
        "You are a patient billing advocate for a healthcare price transparency MVP.\n"
        "Use only the JSON facts below. Do not invent CPT codes, providers, prices, payer names, legal claims, or medical advice.\n"
        "Explain the likely CPT code in plain English, say whether the quoted/billed amount looks high/fair/low based on the benchmark, "
        "and give concrete next steps for verifying the bill or estimate. If evidence is missing, say exactly what is missing.\n"
        "Mention that published rates are not a guarantee of final out-of-pocket cost. Keep the answer under 220 words.\n"
        "Return exactly one JSON object with this shape: {\"answer\":\"...\"}. Do not wrap it in markdown.\n\n"
        f"Case JSON:\n{json.dumps(compact_payload, indent=2, sort_keys=True)}"
    )


def _compact_case_payload(case_payload: dict[str, Any]) -> dict[str, Any]:
    cards = case_payload.get("cards", {})
    return {
        "case_type": case_payload.get("case_type"),
        "facts": case_payload.get("facts"),
        "fairness": cards.get("fairness"),
        "rate_distribution": cards.get("rate_distribution"),
        "cms_benchmark": cards.get("cms_benchmark"),
        "mrf_matches": _first_items(cards.get("mrf_matches"), 3),
        "care_options": _first_items(cards.get("care_options"), 3),
        "public_evidence": _first_items(cards.get("public_evidence"), 3),
        "artifact_available": bool(case_payload.get("artifact")),
        "guardrails": case_payload.get("guardrails"),
    }


def _first_items(value: Any, limit: int) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _extract_answer(generated: str) -> str:
    cleaned = generated.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned
    if isinstance(payload, dict):
        answer = payload.get("answer")
        return str(answer).strip() if answer else cleaned
    return cleaned


def _looks_complete(answer: str) -> bool:
    text = answer.strip()
    if len(text) < 80:
        return False
    if text.endswith((".", "!", "?")):
        return True
    if text.endswith((".”", "!”", "?”")):
        return True
    return False


class AiServiceError(Exception):
    pass


class AiProcedureClassifier:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def classify(self, procedure_query: str) -> dict[str, str] | None:
        if not self.enabled or not procedure_query.strip():
            return None

        prompt = (
            "You are a medical coding expert. Map the following procedure description to the most likely CPT or HCPCS code.\n"
            "Return EXACTLY one JSON object with this shape: {\"code\": \"12345\", \"label\": \"Procedure name\"}.\n"
            "Do not include markdown formatting or any other text. If you cannot determine a code, return an empty JSON object {}.\n\n"
            f"Procedure query: {procedure_query}"
        )

        try:
            generated = self.client.generate_text(prompt)
            answer = _extract_answer(generated)
            if not answer:
                 return None
            # _extract_answer already tries to parse json if there are backticks, but let's parse it directly.
            try:
                data = json.loads(answer)
                if isinstance(data, dict) and "code" in data and "label" in data:
                    return {"code": str(data["code"]), "label": str(data["label"])}
            except json.JSONDecodeError:
                # If _extract_answer returned the raw string that wasn't backticked, let's try to parse it
                try:
                    data = json.loads(generated.strip())
                    if isinstance(data, dict) and "code" in data and "label" in data:
                        return {"code": str(data["code"]), "label": str(data["label"])}
                except json.JSONDecodeError:
                    pass
            return None
        except AiServiceError:
            return None
