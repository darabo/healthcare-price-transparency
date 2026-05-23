from __future__ import annotations

from healthcare_agent.models import CareOption, Procedure, RateDistribution


PROCEDURES: list[Procedure] = [
    Procedure(
        code="73721",
        label="MRI lower extremity joint without contrast",
        aliases=["knee mri", "mri knee", "lower extremity mri", "knee scan"],
        setting_notes="Often billed as a technical facility charge plus a professional radiology interpretation.",
    ),
    Procedure(
        code="72148",
        label="MRI lumbar spine without contrast",
        aliases=["back mri", "lumbar mri", "spine mri", "lower back mri"],
        setting_notes="Can vary sharply between hospital outpatient departments and independent imaging centers.",
    ),
    Procedure(
        code="45378",
        label="Diagnostic colonoscopy",
        aliases=["colonoscopy", "screening colonoscopy", "diagnostic colonoscopy"],
        setting_notes="Total cost may include facility, physician, anesthesia, and pathology if biopsies are taken.",
    ),
    Procedure(
        code="99284",
        label="Emergency department visit, high severity",
        aliases=["er visit", "emergency room", "ed visit", "hospital bill"],
        setting_notes="Final level depends on clinical complexity and hospital coding.",
    ),
]


RATE_DISTRIBUTIONS: list[RateDistribution] = [
    RateDistribution("73721", "aetna", "hoboken", 520, 760, 1180, 350, 700, 128, "sample normalized commercial rates"),
    RateDistribution("73721", "aetna", "new york", 600, 890, 1450, 390, 850, 214, "sample normalized commercial rates"),
    RateDistribution("73721", "united", "hoboken", 540, 810, 1300, 350, 720, 91, "sample normalized commercial rates"),
    RateDistribution("72148", "aetna", "hoboken", 570, 840, 1325, 375, 780, 102, "sample normalized commercial rates"),
    RateDistribution("45378", "aetna", "hoboken", 1050, 1650, 2600, 850, 1900, 77, "sample normalized commercial rates"),
    RateDistribution("99284", "aetna", "hoboken", 950, 1500, 2400, 700, 1700, 66, "sample normalized commercial rates"),
    RateDistribution("71046", "cash", "new york", 50, 110, 250, 45, 100, 310, "sample normalized cash rates"),
]


CARE_OPTIONS: dict[str, list[CareOption]] = {
    "73721": [
        CareOption(
            provider="Hudson Independent Imaging",
            facility_type="Independent imaging center",
            location="Hoboken, NJ",
            estimated_allowed=610,
            cash_estimate=425,
            network_status="Likely in-network for major commercial plans; verify before scheduling.",
            questions=[
                "Is CPT 73721 the exact code for the scan?",
                "Does the quote include the radiologist read?",
                "Will any facility fee be billed separately?",
            ],
            source="sample care option index",
        ),
        CareOption(
            provider="Downtown Radiology Associates",
            facility_type="Independent imaging center",
            location="Jersey City, NJ",
            estimated_allowed=690,
            cash_estimate=475,
            network_status="Network varies by plan product.",
            questions=[
                "Are both the imaging site and radiologist in-network?",
                "Can they provide a written all-in estimate?",
            ],
            source="sample care option index",
        ),
    ],
    "72148": [
        CareOption(
            provider="Hudson Spine Imaging",
            facility_type="Independent imaging center",
            location="Hoboken, NJ",
            estimated_allowed=695,
            cash_estimate=500,
            network_status="Verify plan-specific network before booking.",
            questions=[
                "Is prior authorization required?",
                "Does the quote include contrast if the order changes?",
            ],
            source="sample care option index",
        )
    ],
    "45378": [
        CareOption(
            provider="Garden State Endoscopy Center",
            facility_type="Ambulatory surgery center",
            location="Jersey City, NJ",
            estimated_allowed=1325,
            cash_estimate=950,
            network_status="Often lower than hospital outpatient departments; verify physician and anesthesia network.",
            questions=[
                "Does the estimate include anesthesia?",
                "How are biopsies and pathology billed if needed?",
            ],
            source="sample care option index",
        )
    ],
}


SOURCE_REGISTRY = {
    "sample normalized commercial rates": {
        "status": "mock",
        "note": "Replace with Serif Health, HealthCorum, or ClickHouse-backed normalized rates before production.",
    },
    "sample care option index": {
        "status": "mock",
        "note": "Replace with provider directory and price API results before production.",
    },
}
