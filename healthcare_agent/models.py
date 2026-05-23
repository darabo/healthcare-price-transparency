from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CaseFacts:
    raw_message: str
    case_type: str
    procedure_query: str | None = None
    cpt_candidates: list[str] = field(default_factory=list)
    amount: float | None = None
    payer: str | None = None
    location: str | None = None
    setting: str | None = None
    document_text: str | None = None
    confidence: str = "low"
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Procedure:
    code: str
    label: str
    aliases: list[str]
    setting_notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RateDistribution:
    cpt: str
    payer: str
    location: str
    p25: int
    median: int
    p75: int
    cash_low: int
    cash_high: int
    sample_size: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CareOption:
    provider: str
    facility_type: str
    location: str
    estimated_allowed: int
    cash_estimate: int
    network_status: str
    questions: list[str]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolTrace:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
