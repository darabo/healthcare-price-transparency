from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from healthcare_agent.config import get_env

if TYPE_CHECKING:
    from healthcare_agent.clickhouse_store import ClickHouseChargeStore


CMS_HPT_REPO_URL = "https://github.com/CMSgov/hospital-price-transparency"
MEDICAL_COSTS_API_BASE = "https://medical-costs-api.david-568.workers.dev"


@dataclass
class MrfChargeMatch:
    source: str
    status: str
    code: str
    code_type: str | None = None
    description: str | None = None
    setting: str | None = None
    hospital_name: str | None = None
    payer_name: str | None = None
    plan_name: str | None = None
    negotiated_dollar: float | None = None
    gross_charge: float | None = None
    discounted_cash: float | None = None
    median_allowed: float | None = None
    p10_allowed: float | None = None
    p90_allowed: float | None = None
    min_negotiated: float | None = None
    max_negotiated: float | None = None
    methodology: str | None = None
    file_format: str | None = None
    schema_reference: str = CMS_HPT_REPO_URL
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "dc": "DC", "nyc": "NY"
}

class ApiMrfService:
    def find_charges(
        self, cpt: str, payer: str | None = None, state: str | None = None, limit: int = 10
    ) -> list[MrfChargeMatch]:
        url = f"{MEDICAL_COSTS_API_BASE}/api/negotiated-rates?code={cpt}&limit={limit}"
        
        api_state = None
        if state:
            clean_state = state.strip().lower()
            api_state = US_STATES.get(clean_state)
            if not api_state and len(clean_state) == 2:
                api_state = clean_state.upper()
                
        if api_state:
            if api_state == "NY":
                return self._fetch_pra_rates(cpt, api_state, limit)
            import urllib.parse
            url += f"&state={urllib.parse.quote(api_state)}"
        if payer:
            import urllib.parse
            url += f"&payer={urllib.parse.quote(payer)}"

        try:
            req = Request(url, headers={"User-Agent": "HealthcareAgent/1.0"})
            with urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
            return [_status("MedicalCosts API", cpt, "error", f"Could not query rates: {e}")]

        if not payload.get("success") or "data" not in payload:
            return [_status("MedicalCosts API", cpt, "not_found", "No data returned from API.")]

        rates = payload["data"].get("rates", [])
        if not rates:
            return [_status("MedicalCosts API", cpt, "not_found", "No matching CPT/HCPCS rows found in API.")]

        matches = []
        for rate in rates:
            matches.append(
                MrfChargeMatch(
                    source="MedicalCosts API",
                    status="found",
                    code=rate.get("code", cpt),
                    code_type=rate.get("codeType"),
                    description=rate.get("description"),
                    setting=rate.get("setting"),
                    hospital_name=rate.get("hospitalName"),
                    payer_name=rate.get("payerName"),
                    plan_name=rate.get("planName"),
                    negotiated_dollar=rate.get("negotiatedRate"),
                    methodology=rate.get("methodology"),
                    schema_reference="https://medical-costs-site.pages.dev/api-docs/",
                )
            )

        return matches[:limit]

    def _fetch_pra_rates(self, cpt: str, state: str, limit: int) -> list[MrfChargeMatch]:
        import urllib.parse
        import urllib.request
        import json

        matches = []
        headers = {
            "Reg": "d7b8caf30dbc",
            "SessionId": "5087494111016062868872034",
            "User-Agent": "Mozilla/5.0"
        }

        # 1. Fetch facilities for state
        url = f"https://pnc.patientrightsadvocatefiles.org/facility/all?state={urllib.parse.quote(state)}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                facilities = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            return [_status("PRA API", cpt, "error", f"Could not fetch facilities: {e}")]

        if not facilities:
            return [_status("PRA API", cpt, "not_found", f"No facilities found for state {state}")]

        # Limit to 3 facilities to keep the agent fast
        for fac in facilities[:3]:
            fid = fac.get("id")
            fac_name = fac.get("name")
            if not fid:
                continue

            # 2. Search for the item code
            item_url = f"https://pnc.patientrightsadvocatefiles.org/charge/itemsearch?fid={fid}&codesearch={urllib.parse.quote(cpt)}&search="
            try:
                req = urllib.request.Request(item_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    items = json.loads(response.read().decode("utf-8"))
            except Exception:
                continue

            if not items or (isinstance(items, list) and len(items) > 0 and "error" in items[0]):
                continue

            # Just take the first matching item
            iid = items[0].get("item_id")
            if not iid:
                continue

            # 3. Fetch rates for the item
            rate_url = f"https://pnc.patientrightsadvocatefiles.org/charge/find?fid={fid}&iid={iid}"
            try:
                req = urllib.request.Request(rate_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    rates = json.loads(response.read().decode("utf-8"))
            except Exception:
                continue

            for rate in rates:
                try:
                    negotiated = float(rate.get("ppc_negotiated_charge", 0)) if rate.get("ppc_negotiated_charge") else None
                except ValueError:
                    negotiated = None

                matches.append(
                    MrfChargeMatch(
                        source="PRA API",
                        status="found",
                        code=cpt,
                        code_type=rate.get("item_code_type"),
                        description=rate.get("item_description"),
                        setting=rate.get("charge_setting"),
                        hospital_name=fac_name,
                        payer_name=rate.get("ppc_payer") or "unknown",
                        plan_name=rate.get("ppc_plan"),
                        negotiated_dollar=negotiated,
                        methodology=rate.get("ppc_negotiated_methodology"),
                        schema_reference="https://nychospitalpricefinder.patientrightsadvocate.org/"
                    )
                )
                if len(matches) >= limit:
                    return matches

        if not matches:
            return [_status("PRA API", cpt, "not_found", "No rates found in PRA API for these facilities.")]

        return matches

class MrfSourceService:
    def __init__(
        self,
        clickhouse: "ClickHouseChargeStore | None" = None,
    ) -> None:
        if clickhouse is None:
            from healthcare_agent.clickhouse_store import ClickHouseChargeStore

            clickhouse = ClickHouseChargeStore()
        self.api_service = ApiMrfService()
        self.clickhouse = clickhouse

    def find_charges(self, cpt: str, payer: str | None = None, limit: int = 10, state: str | None = None) -> list[MrfChargeMatch]:
        clickhouse_matches = self.clickhouse.find_charges(cpt=cpt, payer=payer, limit=limit)
        if clickhouse_matches:
            return clickhouse_matches

        return self.api_service.find_charges(cpt=cpt, payer=payer, state=state, limit=limit)


def _status(source: str, cpt: str, status: str, message: str) -> MrfChargeMatch:
    return MrfChargeMatch(source=source, status=status, code=cpt, message=message)
