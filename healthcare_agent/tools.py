from __future__ import annotations

import re
from statistics import mean

from healthcare_agent.models import CareOption, CaseFacts, Procedure, RateDistribution
from healthcare_agent.sample_data import CARE_OPTIONS, PROCEDURES, RATE_DISTRIBUTIONS, SOURCE_REGISTRY


PAYER_ALIASES = {
    "aetna": ["aetna"],
    "united": ["united", "uhc", "unitedhealthcare"],
    "cigna": ["cigna"],
    "blue cross": ["blue cross", "bcbs", "horizon"],
}

LOCATION_HINTS = ["hoboken", "jersey city", "new york", "nyc", "newark", "eden", "raleigh"]


def classify_case_type(message: str) -> str:
    text = message.lower()
    if any(token in text for token in ["dispute", "appeal", "negotiate", "letter", "script", "collections"]):
        return "negotiate_or_dispute"
    if any(token in text for token in ["cheaper", "find", "where", "option", "place", "shop"]):
        return "find_cheaper_care"
    if any(token in text for token in ["bill", "estimate", "quoted", "charged", "fair", "eob", "$"]):
        return "estimate_review"
    return "general_inquiry"


def extract_case_facts(message: str, case_type: str, classifier: Any | None = None) -> CaseFacts:
    text = message.lower()
    procedure = procedure_lookup(message, classifier)
    amount = _extract_amount(message)
    payer = _extract_payer(text)
    location = _extract_location(text)
    setting = _extract_setting(text)

    missing = []
    if not procedure or not procedure.code:
        missing.append("procedure or CPT code")
    if case_type in {"estimate_review", "find_cheaper_care"} and not payer:
        missing.append("insurance plan or cash-pay preference")
    if not location:
        missing.append("location")
    if case_type == "estimate_review" and amount is None:
        missing.append("quoted or billed amount")

    confidence = _confidence(procedure, amount, payer, location, case_type)
    return CaseFacts(
        raw_message=message,
        case_type=case_type,
        procedure_query=procedure.label if procedure else None,
        cpt_candidates=[procedure.code] if procedure and procedure.code and procedure.code != "None" else [],
        amount=amount,
        payer=payer,
        location=location,
        setting=setting,
        document_text=message if len(message) > 500 else None,
        confidence=confidence,
        missing=missing,
    )


def extract_hospital_name(message: str) -> str | None:
    patterns = [
        r"\bat\s+([A-Z][A-Za-z&.\- ]{2,80}?(?:Hospital|Medical Center|Health|Clinic|Surgery Center))\b",
        r"\bfrom\s+([A-Z][A-Za-z&.\- ]{2,80}?(?:Hospital|Medical Center|Health|Clinic|Surgery Center))\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).strip()
    return None


def procedure_lookup(query: str, classifier: Any | None = None) -> Procedure | None:
    text = query.lower()
    cpt_match = re.search(r"\b\d{5}\b", text)
    if cpt_match:
        code = cpt_match.group(0)
        for procedure in PROCEDURES:
            if procedure.code == code:
                return procedure
        # If user explicitly gave a CPT code that isn't in our list, we could return a synthetic one.
        # But let's see if the AI can label it or just return a generic Procedure.

    scored: list[tuple[int, Procedure]] = []
    for procedure in PROCEDURES:
        score = 0
        for alias in procedure.aliases:
            if alias in text:
                score += 10 + len(alias.split())
        for word in procedure.label.lower().split():
            if len(word) > 3 and word in text:
                score += 1
        if score:
            scored.append((score, procedure))
            
    if scored:
        return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]
        
    if classifier:
        result = classifier.classify(query)
        if result and "label" in result:
            return Procedure(
                code=result.get("code") or "",
                label=result["label"],
                aliases=[],
                setting_notes="Resolved via AI classification fallback."
            )
            
    return None


def rate_distribution_lookup(cpt: str, payer: str | None, location: str | None) -> RateDistribution | None:
    payer = (payer or "aetna").lower()
    location = (location or "hoboken").lower()
    candidates = [rate for rate in RATE_DISTRIBUTIONS if rate.cpt == cpt]
    if not candidates:
        return None

    exact = [
        rate
        for rate in candidates
        if rate.payer.lower() == payer and rate.location.lower() == location
    ]
    if exact:
        return exact[0]

    payer_matches = [rate for rate in candidates if rate.payer.lower() == payer]
    if payer_matches:
        return _blend_rates(cpt, payer, location, payer_matches)

    return _blend_rates(cpt, payer, location, candidates)


def find_care_options(cpt: str, location: str | None, payer: str | None) -> list[CareOption]:
    del location, payer
    return CARE_OPTIONS.get(cpt, [])


def source_verify(sources: list[str]) -> dict[str, dict[str, str]]:
    return {source: SOURCE_REGISTRY.get(source, {"status": "unknown", "note": "No source metadata available."}) for source in sources}


def generate_advocacy_artifact(facts: CaseFacts, rate: RateDistribution | None) -> dict[str, str]:
    procedure = facts.procedure_query or "the service"
    amount = _money(facts.amount) if facts.amount else "the quoted amount"
    benchmark = _money(rate.median) if rate else "the local market median"
    payer = facts.payer or "my health plan"

    phone_script = (
        f"Hi, I am calling about {procedure}. I was quoted or billed {amount}. "
        f"Can you confirm the CPT/HCPCS code, billing provider, place of service, and whether all clinicians are in-network with {payer}? "
        f"I am seeing a local benchmark around {benchmark}. Can you review whether a lower allowed amount, financial assistance, "
        "or a written cash-pay estimate is available?"
    )

    email = (
        "Subject: Request for itemized review and price adjustment\n\n"
        f"Hello,\n\nI am requesting an itemized review for {procedure}. The amount at issue is {amount}. "
        f"Please provide the CPT/HCPCS codes, NPI and billing entity, network status, allowed amount, and any facility or professional fees. "
        f"Based on available local benchmark data, the market median appears to be about {benchmark}, so I am asking for a review, "
        "correction of any coding/network errors, and any available discount or payment adjustment.\n\nThank you."
    )

    checklist = "\n".join(
        [
            "Ask for itemized CPT/HCPCS codes and all billing entities.",
            "Confirm in-network status for facility, professional, anesthesia, and pathology charges.",
            "Request the plan allowed amount and patient responsibility calculation.",
            "Ask whether a lower cash-pay or financial-assistance price exists.",
            "Keep names, dates, reference numbers, and written estimates.",
        ]
    )
    return {"phone_script": phone_script, "email": email, "checklist": checklist}


def fairness_summary(amount: float | None, rate: RateDistribution | None) -> dict[str, str | float | None]:
    if amount is None or rate is None:
        return {
            "status": "needs_more_information",
            "ratio_to_median": None,
            "summary": "I need both a price and a comparable local benchmark before calling it high or low.",
        }

    ratio = amount / rate.median
    if amount > rate.p75:
        status = "high"
        summary = f"The amount is above the sample 75th percentile of {_money(rate.p75)}."
    elif amount < rate.p25:
        status = "low"
        summary = f"The amount is below the sample 25th percentile of {_money(rate.p25)}."
    else:
        status = "typical"
        summary = f"The amount falls within the sample interquartile range of {_money(rate.p25)} to {_money(rate.p75)}."

    return {"status": status, "ratio_to_median": round(ratio, 2), "summary": summary}


def _extract_amount(message: str) -> float | None:
    matches = re.findall(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", message)
    if not matches:
        return None
    values = [float(match.replace(",", "")) for match in matches]
    return max(values)


def _extract_payer(text: str) -> str | None:
    for payer, aliases in PAYER_ALIASES.items():
        if any(alias in text for alias in aliases):
            return payer
    if "cash" in text or "self pay" in text or "self-pay" in text:
        return "cash"
    return None


def _extract_location(text: str) -> str | None:
    for hint in LOCATION_HINTS:
        if hint in text:
            return "new york" if hint == "nyc" else hint
    zip_match = re.search(r"\b\d{5}\b", text)
    if zip_match:
        return zip_match.group(0)
    return None


def _extract_setting(text: str) -> str | None:
    if "hospital" in text or "outpatient" in text:
        return "hospital outpatient"
    if "imaging center" in text or "independent" in text:
        return "independent facility"
    if "asc" in text or "surgery center" in text:
        return "ambulatory surgery center"
    return None


def _confidence(
    procedure: Procedure | None,
    amount: float | None,
    payer: str | None,
    location: str | None,
    case_type: str,
) -> str:
    score = sum([procedure is not None, payer is not None, location is not None])
    if case_type == "estimate_review":
        score += amount is not None
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _blend_rates(cpt: str, payer: str, location: str, rates: list[RateDistribution]) -> RateDistribution:
    return RateDistribution(
        cpt=cpt,
        payer=payer,
        location=location,
        p25=round(mean(rate.p25 for rate in rates)),
        median=round(mean(rate.median for rate in rates)),
        p75=round(mean(rate.p75 for rate in rates)),
        cash_low=round(mean(rate.cash_low for rate in rates)),
        cash_high=round(mean(rate.cash_high for rate in rates)),
        sample_size=sum(rate.sample_size for rate in rates),
        source="sample normalized commercial rates",
    )


def _money(value: float | int) -> str:
    return f"${value:,.0f}"
