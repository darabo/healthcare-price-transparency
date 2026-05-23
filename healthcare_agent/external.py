from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from healthcare_agent.cache import ResponseCache
from healthcare_agent.config import get_env


NIMBLE_BASE_URL = "https://sdk.nimbleway.com"
HOSPITAL_PRICING_URL = "https://hospitalpricingfiles.org/"
CMS_HPT_URL = "https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency"
CMS_PROVIDER_DATASTORE = "https://data.cms.gov/provider-data/api/1/datastore/query"
CMS_HOSPITAL_GENERAL_INFO_DATASET = "xubh-q36u"


@dataclass
class CmsBenchmark:
    code: str
    source: str
    status: str
    title: str | None = None
    hospital_outpatient_payment: float | None = None
    ambulatory_surgical_center_payment: float | None = None
    beneficiary_copay: float | None = None
    raw: dict[str, Any] | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WebEvidence:
    source: str
    url: str
    status: str
    title: str
    summary: str
    matches: list[str]
    raw_status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HospitalInfo:
    source: str
    status: str
    facility_id: str | None = None
    facility_name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    hospital_type: str | None = None
    ownership: str | None = None
    rating: str | None = None
    emergency_services: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExternalServiceError(Exception):
    pass


class NimbleClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self.api_key = api_key or get_env("NIMBLE_API_KEY")
        self.base_url = (base_url or get_env("NIMBLE_BASE_URL") or NIMBLE_BASE_URL).rstrip("/")
        self.cache = cache or ResponseCache()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def extract_url(self, url: str, render: bool = True) -> dict[str, Any]:
        payload = {
            "url": url,
            "render": render,
            "country": "US",
            "locale": "en-US",
            "formats": ["markdown", "links"],
            "render_options": {
                "render_type": "idle2",
                "timeout": 30000,
                "include_iframes": True,
            },
        }
        cached = self.cache.get("nimble_extract", payload, ttl_seconds=60 * 60 * 24)
        if cached:
            return cached
        response = self._post("/v1/extract", payload)
        self.cache.set("nimble_extract", payload, response)
        return response

    def search(self, query: str, include_domains: list[str] | None = None, max_results: int = 5) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "country": "US",
            "locale": "en-US",
            "output_format": "markdown",
            "max_results": max_results,
            "search_depth": "lite",
        }
        if include_domains:
            payload["include_domains"] = include_domains
        cached = self.cache.get("nimble_search", payload, ttl_seconds=60 * 60 * 24)
        if cached:
            return cached
        response = self._post("/v1/search", payload)
        self.cache.set("nimble_search", payload, response)
        return response

    def run_agent(self, agent_id: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "agent": agent_id,
            "params": params,
        }
        cached = self.cache.get("nimble_agent", payload, ttl_seconds=60 * 60 * 24)
        if cached:
            return cached
        response = self._post("/v1/agents/run", payload)
        self.cache.set("nimble_agent", payload, response)
        return response

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ExternalServiceError("NIMBLE_API_KEY is not configured")
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExternalServiceError(f"Nimble HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise ExternalServiceError(f"Nimble request failed: {exc.reason}") from exc


class CmsPplClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, cache: ResponseCache | None = None) -> None:
        self.api_key = api_key or get_env("CMS_PPL_API_KEY")
        self.base_url = (base_url or get_env("CMS_PPL_BASE_URL") or "").rstrip("/")
        self.cache = cache or ResponseCache()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url)

    def lookup(self, cpt: str) -> CmsBenchmark:
        if not self.enabled:
            return CmsBenchmark(
                code=cpt,
                source="CMS Procedure Price Lookup API",
                status="not_configured",
                message="Set CMS_PPL_API_KEY and CMS_PPL_BASE_URL after accepting the CMS/AMA terms.",
            )

        params = {"q": cpt}
        cache_key = {"base_url": self.base_url, "params": params}
        cached = self.cache.get("cms_ppl", cache_key, ttl_seconds=60 * 60 * 24 * 7)
        if cached:
            return self._normalize(cpt, cached)

        request = Request(
            f"{self.base_url}?{urlencode(params)}",
            method="GET",
            headers={"x-api-key": self.api_key or "", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return CmsBenchmark(
                code=cpt,
                source="CMS Procedure Price Lookup API",
                status="error",
                message=f"CMS PPL HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}",
            )
        except URLError as exc:
            return CmsBenchmark(
                code=cpt,
                source="CMS Procedure Price Lookup API",
                status="error",
                message=f"CMS PPL request failed: {exc.reason}",
            )
        self.cache.set("cms_ppl", cache_key, payload)
        return self._normalize(cpt, payload)

    def _normalize(self, cpt: str, payload: dict[str, Any]) -> CmsBenchmark:
        rows = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = payload.get("results") if isinstance(payload, dict) else []
        if not rows:
            return CmsBenchmark(
                code=cpt,
                source="CMS Procedure Price Lookup API",
                status="not_found",
                raw=payload if isinstance(payload, dict) else None,
            )

        row = rows[0]
        return CmsBenchmark(
            code=cpt,
            source="CMS Procedure Price Lookup API",
            status="found",
            title=_first_present(row, ["procedure", "description", "name", "title"]),
            hospital_outpatient_payment=_first_money(row, ["hopd_payment", "hospital_outpatient_payment", "national_payment"]),
            ambulatory_surgical_center_payment=_first_money(row, ["asc_payment", "ambulatory_surgical_center_payment"]),
            beneficiary_copay=_first_money(row, ["copay", "beneficiary_copay", "patient_copay"]),
            raw=row,
        )


class CmsOpenDataClient:
    def __init__(self, cache: ResponseCache | None = None) -> None:
        self.cache = cache or ResponseCache()

    def search_hospitals(self, hospital_name: str | None, location: str | None, limit: int = 5) -> list[HospitalInfo]:
        state = _state_from_location(location)
        if not state and not hospital_name:
            return [
                HospitalInfo(
                    source="CMS Hospital General Information",
                    status="insufficient_input",
                    message="Provide a hospital name or recognizable state/location to search CMS open hospital data.",
                )
            ]

        params = {
            "count": "true",
            "results": "true",
            "schema": "false",
            "keys": "true",
            "format": "json",
            "rowIds": "false",
            "limit": "100" if hospital_name else str(limit),
        }
        if state:
            params.update(
                {
                    "conditions[0][property]": "State",
                    "conditions[0][value]": state,
                    "conditions[0][operator]": "=",
                }
            )

        cache_key = {"dataset": CMS_HOSPITAL_GENERAL_INFO_DATASET, "params": params, "hospital_name": hospital_name}
        cached = self.cache.get("cms_hospital_general_info", cache_key, ttl_seconds=60 * 60 * 24 * 7)
        if cached is None:
            url = f"{CMS_PROVIDER_DATASTORE}/{CMS_HOSPITAL_GENERAL_INFO_DATASET}/0?{urlencode(params)}"
            try:
                with urlopen(Request(url, method="GET", headers={"Accept": "application/json"}), timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                return [
                    HospitalInfo(
                        source="CMS Hospital General Information",
                        status="error",
                        message=f"CMS open-data HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}",
                    )
                ]
            except URLError as exc:
                return [
                    HospitalInfo(
                        source="CMS Hospital General Information",
                        status="error",
                        message=f"CMS open-data request failed: {exc.reason}",
                    )
                ]
            self.cache.set("cms_hospital_general_info", cache_key, payload)
        else:
            payload = cached

        rows = payload.get("results", []) if isinstance(payload, dict) else []
        if hospital_name:
            rows = _rank_hospital_rows(rows, hospital_name)
        rows = rows[:limit]
        if not rows:
            return [
                HospitalInfo(
                    source="CMS Hospital General Information",
                    status="not_found",
                    message="CMS Hospital General Information returned no matching hospitals.",
                )
            ]
        return [_hospital_info(row) for row in rows]


class PublicEvidenceService:
    def __init__(
        self,
        nimble: NimbleClient | None = None,
        cms: CmsPplClient | None = None,
        cms_open_data: CmsOpenDataClient | None = None,
    ) -> None:
        self.nimble = nimble or NimbleClient()
        self.cms = cms or CmsPplClient()
        self.cms_open_data = cms_open_data or CmsOpenDataClient()

    def cms_ppl_lookup(self, cpt: str) -> CmsBenchmark:
        return self.cms.lookup(cpt)

    def cms_open_hospital_lookup(self, hospital_name: str | None, location: str | None) -> list[HospitalInfo]:
        return self.cms_open_data.search_hospitals(hospital_name=hospital_name, location=location)

    def discover_hospital_price_files(self, hospital_name: str | None, location: str | None, cpt: str | None) -> list[WebEvidence]:
        if not self.nimble.enabled:
            return [
                WebEvidence(
                    source="Nimble Search",
                    url=HOSPITAL_PRICING_URL,
                    status="not_configured",
                    title="Hospital price file discovery skipped",
                    summary="Set NIMBLE_API_KEY to enable rendered search/extraction from hospitalpricingfiles.org.",
                    matches=[],
                )
            ]

        query_parts = [part for part in [hospital_name, location, cpt, "hospital price transparency file"] if part]
        query = " ".join(query_parts) or "hospital price transparency files"
        try:
            payload = self.nimble.search(query, include_domains=["hospitalpricingfiles.org"], max_results=5)
        except ExternalServiceError as exc:
            return [_error_evidence("Nimble Search", HOSPITAL_PRICING_URL, str(exc))]

        results = payload.get("results", []) if isinstance(payload, dict) else []
        evidence = []
        for result in results[:5]:
            url = result.get("url") or HOSPITAL_PRICING_URL
            content = " ".join(str(result.get(field, "")) for field in ["title", "description", "content"])
            evidence.append(
                WebEvidence(
                    source="Nimble Search",
                    url=url,
                    status="found",
                    title=str(result.get("title") or "Hospital pricing result"),
                    summary=_compact(content),
                    matches=_matching_lines(content, cpt),
                )
            )
        return evidence or [
            WebEvidence(
                source="Nimble Search",
                url=HOSPITAL_PRICING_URL,
                status="not_found",
                title="No hospital pricing result found",
                summary="Nimble search did not return a hospitalpricingfiles.org match for this case.",
                matches=[],
            )
        ]

    def discover_mrf_links(self, hospital_name: str | None, location: str | None) -> list[str]:
        if not self.nimble.enabled:
            return []
        
        query_parts = [part for part in [hospital_name, location] if part]
        query = " ".join(query_parts)
        if not query:
            return []
            
        try:
            payload = self.nimble.run_agent(
                agent_id="hospitalpricingfiles_mrf_links_2026_05_23_ef2glucl",
                params={"search_query": query}
            )
            return _extract_urls_from_agent_response(payload)
        except ExternalServiceError:
            # Handle gracefully if template not found or error
            return []

    def extract_public_context(self, cpt: str | None = None) -> list[WebEvidence]:
        evidence = []
        for source, url in [
            ("HospitalPricingFiles", HOSPITAL_PRICING_URL),
            ("CMS Hospital Price Transparency", CMS_HPT_URL),
        ]:
            evidence.append(self._extract_context(source, url, cpt))
        return evidence

    def _extract_context(self, source: str, url: str, cpt: str | None) -> WebEvidence:
        if not self.nimble.enabled:
            return WebEvidence(
                source=source,
                url=url,
                status="not_configured",
                title=f"{source} extraction skipped",
                summary="Set NIMBLE_API_KEY to enable rendered public-source extraction.",
                matches=[],
            )
        try:
            payload = self.nimble.extract_url(url, render=True)
        except ExternalServiceError as exc:
            return _error_evidence(source, url, str(exc))
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        markdown = data.get("markdown") or data.get("html") or ""
        return WebEvidence(
            source=source,
            url=str(payload.get("url") or url),
            status=str(payload.get("status") or "unknown"),
            title=source,
            summary=_compact(markdown),
            matches=_matching_lines(markdown, cpt),
            raw_status_code=payload.get("status_code"),
        )


def _error_evidence(source: str, url: str, message: str) -> WebEvidence:
    return WebEvidence(
        source=source,
        url=url,
        status="error",
        title=f"{source} error",
        summary=message,
        matches=[],
    )


def _matching_lines(text: str, cpt: str | None) -> list[str]:
    if not cpt:
        return []
    lines = [line.strip() for line in text.splitlines() if cpt in line]
    return [_compact(line, limit=220) for line in lines[:5]]


def _compact(text: str, limit: int = 500) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


def _first_present(row: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)
    return None


def _first_money(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(str(value).replace("$", "").replace(",", ""))
        except ValueError:
            continue
    return None


def _hospital_info(row: dict[str, Any]) -> HospitalInfo:
    return HospitalInfo(
        source="CMS Hospital General Information",
        status="found",
        facility_id=row.get("facility_id"),
        facility_name=row.get("facility_name"),
        address=row.get("address"),
        city=row.get("citytown"),
        state=row.get("state"),
        zip_code=row.get("zip_code"),
        hospital_type=row.get("hospital_type"),
        ownership=row.get("hospital_ownership"),
        rating=row.get("hospital_overall_rating"),
        emergency_services=row.get("emergency_services"),
    )


def _rank_hospital_rows(rows: list[dict[str, Any]], hospital_name: str) -> list[dict[str, Any]]:
    exactish = [row for row in rows if hospital_name.lower() in row.get("facility_name", "").lower()]
    if exactish:
        return exactish
    query_tokens = _tokens(hospital_name)
    scored = []
    for row in rows:
        name = row.get("facility_name", "")
        name_tokens = _tokens(name)
        overlap = len(query_tokens & name_tokens)
        contains = hospital_name.lower() in name.lower()
        if overlap or contains:
            scored.append((overlap + (5 if contains else 0), row))
    return [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)]


def _tokens(value: str) -> set[str]:
    ignored = {"hospital", "medical", "center", "health", "the", "at", "of"}
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if token not in ignored}


def _state_from_location(location: str | None) -> str | None:
    if not location:
        return None
    direct = re.search(r"\b([A-Z]{2})\b", location)
    if direct:
        return direct.group(1)
    mapping = {
        "hoboken": "NJ",
        "jersey city": "NJ",
        "newark": "NJ",
        "new york": "NY",
        "nyc": "NY",
        "eden": "NC",
        "raleigh": "NC",
    }
    return mapping.get(location.lower())


def _extract_urls_from_agent_response(payload: dict[str, Any]) -> list[str]:
    urls = []

    def extract(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith("http"):
                    # Basic heuristic for finding URL strings in dicts
                    urls.append(v)
                else:
                    extract(v)
        elif isinstance(obj, list):
            for item in obj:
                extract(item)
        elif isinstance(obj, str) and obj.startswith("http"):
             urls.append(obj)

    extract(payload)
    
    # Deduplicate while preserving order
    seen = set()
    return [u for u in urls if not (u in seen or seen.add(u))]
