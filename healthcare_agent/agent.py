from __future__ import annotations

import uuid
from typing import Any

from healthcare_agent.ai import AiPatientExplainer, AiProcedureClassifier
from healthcare_agent.external import PublicEvidenceService
from healthcare_agent.models import RateDistribution, ToolTrace
from healthcare_agent.mrf import MrfSourceService
from healthcare_agent.observability import annotate_span, trace_agent, trace_tool
from healthcare_agent.tools import (
    classify_case_type,
    extract_case_facts,
    extract_hospital_name,
    fairness_summary,
    find_care_options,
    generate_advocacy_artifact,
    rate_distribution_lookup,
    source_verify,
)


class PatientAdvocateAgent:
    def __init__(
        self,
        evidence_service: PublicEvidenceService | None = None,
        mrf_service: MrfSourceService | None = None,
        ai_service: AiPatientExplainer | None = None,
        classifier_service: AiProcedureClassifier | None = None,
    ) -> None:
        self._cases: dict[str, list[dict[str, Any]]] = {}
        self.evidence_service = evidence_service or PublicEvidenceService()
        self.mrf_service = mrf_service or MrfSourceService()
        self.ai_service = ai_service or AiPatientExplainer()
        self.classifier_service = classifier_service or AiProcedureClassifier()

    @trace_agent(name="patient-advocate-agent")
    def respond(self, message: str, case_id: str | None = None) -> dict[str, Any]:
        case_id = case_id or str(uuid.uuid4())
        trace: list[ToolTrace] = []
        annotate_span(input_data=message, tags={"case_id": case_id})

        case_type = classify_case_type(message)
        trace.append(ToolTrace("classify_case_type", {"message": message}, {"case_type": case_type}))

        facts = extract_case_facts(message, case_type, classifier=self.classifier_service)
        trace.append(ToolTrace("extract_case_facts", {"message": message, "case_type": case_type}, facts.to_dict()))

        # Merge conversation history context
        history = self._cases.get(case_id, [])
        if history:
            last_response = history[-1]["response"]
            last_facts = last_response.get("facts", {})
            last_case_type = last_response.get("case_type", "general_inquiry")

            # 1. Inherit case type if the new query is general_inquiry
            if case_type == "general_inquiry" and last_case_type != "general_inquiry":
                case_type = last_case_type
                facts.case_type = case_type

            # 2. Merge facts
            if not facts.cpt_candidates and last_facts.get("cpt_candidates"):
                facts.cpt_candidates = list(last_facts["cpt_candidates"])
            if not facts.procedure_query and last_facts.get("procedure_query"):
                facts.procedure_query = last_facts["procedure_query"]
            if facts.amount is None and last_facts.get("amount") is not None:
                facts.amount = last_facts["amount"]
            if facts.payer is None and last_facts.get("payer"):
                facts.payer = last_facts["payer"]
            if facts.location is None and last_facts.get("location"):
                facts.location = last_facts["location"]
            if facts.setting is None and last_facts.get("setting"):
                facts.setting = last_facts["setting"]

            # 3. Recalculate missing list
            missing = []
            if not facts.cpt_candidates:
                missing.append("procedure or CPT code")
            if case_type in {"estimate_review", "find_cheaper_care"} and not facts.payer:
                missing.append("insurance plan or cash-pay preference")
            if not facts.location:
                missing.append("location")
            if case_type == "estimate_review" and facts.amount is None:
                missing.append("quoted or billed amount")
            facts.missing = missing

            # 4. Recalculate confidence
            score = sum([bool(facts.cpt_candidates), facts.payer is not None, facts.location is not None])
            if case_type == "estimate_review":
                score += facts.amount is not None
            if score >= 4:
                facts.confidence = "high"
            elif score >= 2:
                facts.confidence = "medium"
            else:
                facts.confidence = "low"

        rate = None
        care_options = []
        mrf_matches = []
        hospital_name = extract_hospital_name(message)
        if not hospital_name and history:
            hospital_name = history[-1]["response"].get("hospital_name")
        
        if facts.cpt_candidates:
            cpt = facts.cpt_candidates[0]
            rate = rate_distribution_lookup(cpt, facts.payer, facts.location)
            trace.append(
                ToolTrace(
                    "rate_distribution_lookup",
                    {"cpt": cpt, "payer": facts.payer, "location": facts.location},
                    rate.to_dict() if rate else {"error": "no comparable sample rate found"},
                )
            )

            mrf_matches = self.mrf_service.find_charges(cpt=cpt, payer=facts.payer, state=facts.location)
            trace.append(
                ToolTrace(
                    "hospital_mrf_parse",
                    {"cpt": cpt, "payer": facts.payer},
                    {"items": [item.to_dict() for item in mrf_matches]},
                )
            )

            if not rate and mrf_matches:
                valid_charges = [m.negotiated_dollar for m in mrf_matches if m.status == "found" and m.negotiated_dollar is not None]
                if valid_charges:
                    import statistics
                    med = int(statistics.median(valid_charges))
                    low = int(min(valid_charges))
                    high = int(max(valid_charges))
                    rate = RateDistribution(
                        cpt=cpt,
                        payer=facts.payer or "unknown",
                        location=facts.location or "unknown",
                        median=med,
                        p25=low,
                        p75=high,
                        cash_low=0,
                        cash_high=0,
                        sample_size=len(valid_charges),
                        source="Dynamic MRF Parse"
                    )


            if case_type in {"find_cheaper_care", "estimate_review", "negotiate_or_dispute"}:
                care_options = find_care_options(cpt, facts.location, facts.payer)
                trace.append(
                    ToolTrace(
                        "find_care_options",
                        {"cpt": cpt, "payer": facts.payer, "location": facts.location},
                        {"count": len(care_options), "options": [option.to_dict() for option in care_options]},
                    )
                )

        sources = _collect_sources(rate, care_options)
        verification = source_verify(sources)
        trace.append(ToolTrace("source_verify", {"sources": sources}, verification))

        cms_benchmark = None
        cms_hospitals = []
        public_evidence = []
        if case_type == "general_inquiry":
            general_results = self.evidence_service.general_web_search(message)
            public_evidence.extend(general_results)
            trace.append(
                ToolTrace(
                    "general_web_search",
                    {"query": message},
                    {"items": [item.to_dict() for item in general_results]},
                )
            )
        cms_hospitals = self.evidence_service.cms_open_hospital_lookup(hospital_name, facts.location)
        trace.append(
            ToolTrace(
                "cms_open_hospital_lookup",
                {"hospital_name": hospital_name, "location": facts.location},
                {"items": [item.to_dict() for item in cms_hospitals]},
            )
        )
        if facts.cpt_candidates:
            cpt = facts.cpt_candidates[0]
            cms_benchmark = self.evidence_service.medical_costs_api_lookup(cpt)
            trace.append(
                ToolTrace(
                    "medical_costs_api_lookup",
                    {"cpt": cpt},
                    cms_benchmark.to_dict(),
                )
            )
            public_evidence = self.evidence_service.discover_hospital_price_files(
                hospital_name=hospital_name,
                location=facts.location,
                cpt=cpt,
            )
            public_evidence.extend(self.evidence_service.extract_public_context(cpt=cpt))
            trace.append(
                ToolTrace(
                    "public_evidence_lookup",
                    {"hospital_name": hospital_name, "location": facts.location, "cpt": cpt},
                    {"items": [item.to_dict() for item in public_evidence]},
                )
            )
        elif facts.procedure_query:
            query_parts = [facts.procedure_query, "cost"]
            if facts.location:
                query_parts.append(facts.location)
            query = " ".join(query_parts)
            general_results = self.evidence_service.general_web_search(query)
            public_evidence.extend(general_results)
            trace.append(
                ToolTrace(
                    "general_web_search_fallback",
                    {"query": query},
                    {"items": [item.to_dict() for item in general_results]},
                )
            )

        artifact = None
        if case_type == "negotiate_or_dispute" or _looks_actionable_for_artifact(case_type, facts.amount, rate):
            artifact = generate_advocacy_artifact(facts, rate)
            trace.append(ToolTrace("generate_advocacy_artifact", facts.to_dict(), artifact))

        summary = fairness_summary(facts.amount, rate)
        fallback_answer = self._compose_answer(
            case_type,
            facts,
            rate,
            summary,
            care_options,
            artifact,
            cms_benchmark,
            cms_hospitals,
            public_evidence,
            mrf_matches,
        )
        guardrails = [
            "This is price-navigation support, not medical advice.",
            "Published or sampled rates are not a guarantee of final out-of-pocket cost.",
            "Verify CPT/HCPCS codes, network status, prior authorization, and all billing entities before acting.",
        ]
        response = {
            "case_id": case_id,
            "case_type": case_type,
            "answer": fallback_answer,
            "facts": facts.to_dict(),
            "cards": self._cards(rate, summary, care_options, cms_benchmark, cms_hospitals, public_evidence, mrf_matches),
            "artifact": artifact,
            "tool_trace": [],
            "guardrails": guardrails,
            "hospital_name": hospital_name,
        }
        import inspect
        sig = inspect.signature(self.ai_service.explain)
        if "history" in sig.parameters:
            ai_explanation = self.ai_service.explain(response, fallback_answer=fallback_answer, history=history)
        else:
            ai_explanation = self.ai_service.explain(response, fallback_answer=fallback_answer)
        response["answer"] = ai_explanation.answer or fallback_answer
        response["cards"]["ai_explanation"] = ai_explanation.to_dict()
        trace.append(
            ToolTrace(
                "ai_patient_explanation",
                {"model": ai_explanation.model, "enabled": self.ai_service.enabled},
                {"status": ai_explanation.status, "error": ai_explanation.error},
            )
        )
        response["tool_trace"] = [item.to_dict() for item in trace]
        self._cases.setdefault(case_id, []).append({"message": message, "response": response})
        annotate_span(
            output_data=response["answer"],
            metadata={
                "case_type": case_type,
                "cpt": facts.cpt_candidates[0] if facts.cpt_candidates else None,
                "tool_count": len(trace),
            },
        )
        return response

    def _compose_answer(
        self,
        case_type: str,
        facts: Any,
        rate: RateDistribution | None,
        summary: dict[str, Any],
        care_options: list[Any],
        artifact: dict[str, str] | None,
        cms_benchmark: Any | None,
        cms_hospitals: list[Any],
        public_evidence: list[Any],
        mrf_matches: list[Any],
    ) -> str:
        procedure = facts.procedure_query or "the service"
        missing = f" I still need: {', '.join(facts.missing)}." if facts.missing else ""

        if not facts.cpt_candidates:
            return (
                "I could not confidently map this to a CPT/HCPCS code yet. "
                "Tell me the procedure name, CPT code if you have it, location, payer, and quoted amount."
                + missing
            )

        cpt = facts.cpt_candidates[0]
        benchmark = ""
        if rate:
            benchmark = (
                f" For CPT {cpt}, the sample benchmark near {rate.location} for {rate.payer} is "
                f"p25 ${rate.p25:,}, median ${rate.median:,}, and p75 ${rate.p75:,}."
            )
        external_note = self._external_note(cms_benchmark, cms_hospitals, public_evidence, mrf_matches)

        if case_type == "find_cheaper_care":
            if care_options:
                cheapest = min(care_options, key=lambda option: option.estimated_allowed)
                return (
                    f"I found lower-cost options for {procedure}.{benchmark} "
                    f"The lowest sample option is {cheapest.provider} at about ${cheapest.estimated_allowed:,} allowed "
                    f"or ${cheapest.cash_estimate:,} cash-pay. Verify network status and whether professional fees are included."
                    + external_note
                    + missing
                )
            return f"I found the likely code {cpt} for {procedure}, but no care options in the sample index yet.{benchmark}{external_note}{missing}"

        if case_type == "negotiate_or_dispute":
            return (
                f"I can help you challenge or negotiate {procedure}.{benchmark} "
                "I generated a phone script, email, and checklist focused on itemized codes, network status, and a benchmark-based adjustment request."
                + external_note
                + missing
            )

        if summary["status"] == "high":
            action = "This is worth questioning before paying or scheduling."
        elif summary["status"] == "typical":
            action = "This looks within the sampled local range, but still verify what is included."
        elif summary["status"] == "low":
            action = "This looks below the sampled local range, but confirm it is an all-in estimate."
        else:
            action = "I need a quoted amount and comparable benchmark to assess fairness."

        artifact_note = " I also drafted a negotiation script because the amount appears actionable." if artifact else ""
        return f"{summary['summary']}{benchmark} {action}{artifact_note}{external_note}{missing}"

    def _cards(
        self,
        rate: RateDistribution | None,
        summary: dict[str, Any],
        care_options: list[Any],
        cms_benchmark: Any | None,
        cms_hospitals: list[Any],
        public_evidence: list[Any],
        mrf_matches: list[Any],
    ) -> dict[str, Any]:
        return {
            "fairness": summary,
            "rate_distribution": rate.to_dict() if rate else None,
            "care_options": [option.to_dict() for option in care_options],
            "cms_benchmark": cms_benchmark.to_dict() if cms_benchmark else None,
            "cms_hospitals": [item.to_dict() for item in cms_hospitals],
            "public_evidence": [item.to_dict() for item in public_evidence],
            "mrf_matches": [item.to_dict() for item in mrf_matches],
        }

    def _external_note(
        self,
        cms_benchmark: Any | None,
        cms_hospitals: list[Any],
        public_evidence: list[Any],
        mrf_matches: list[Any],
    ) -> str:
        parts = []
        found_hospitals = [item for item in cms_hospitals if getattr(item, "status", None) == "found"]
        if found_hospitals:
            parts.append(f"CMS open hospital data returned {len(found_hospitals)} possible facility match(es).")
        if cms_benchmark and getattr(cms_benchmark, "status", None) == "found":
            parts.append("CMS Procedure Price Lookup returned a comparable Medicare benchmark.")
        elif cms_benchmark and getattr(cms_benchmark, "status", None) == "not_configured":
            parts.append("CMS Procedure Price Lookup is wired but not configured yet.")
        found_public = [item for item in public_evidence if getattr(item, "status", None) in {"found", "success"}]
        not_configured = [item for item in public_evidence if getattr(item, "status", None) == "not_configured"]
        if found_public:
            parts.append(f"Public-source lookup returned {len(found_public)} rendered evidence item(s).")
        elif not_configured:
            parts.append("Nimble public-source lookup is wired but not configured in this runtime.")
        found_mrf = [item for item in mrf_matches if getattr(item, "status", None) == "found"]
        mrf_not_configured = [item for item in mrf_matches if getattr(item, "status", None) == "not_configured"]
        if found_mrf:
            parts.append(f"Configured hospital MRF parsing returned {len(found_mrf)} charge match(es).")
        elif mrf_not_configured:
            parts.append("Hospital MRF parsing is wired; configure HOSPITAL_MRF_SOURCES to parse real files.")
        return " " + " ".join(parts) if parts else ""


def _collect_sources(rate: RateDistribution | None, care_options: list[Any]) -> list[str]:
    sources = []
    if rate:
        sources.append(rate.source)
    for option in care_options:
        sources.append(option.source)
    return sorted(set(sources))


def _looks_actionable_for_artifact(case_type: str, amount: float | None, rate: RateDistribution | None) -> bool:
    return case_type == "estimate_review" and amount is not None and rate is not None and amount > rate.p75
