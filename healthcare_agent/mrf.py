from __future__ import annotations

import csv
import gzip
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from healthcare_agent.config import get_env

if TYPE_CHECKING:
    from healthcare_agent.clickhouse_store import ClickHouseChargeStore


CMS_HPT_REPO_URL = "https://github.com/CMSgov/hospital-price-transparency"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024


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


class HospitalMrfParser:
    def parse_source(
        self,
        source: str,
        cpt: str,
        payer: str | None = None,
        limit: int = 10,
        max_bytes: int | None = None,
    ) -> list[MrfChargeMatch]:
        try:
            content, inner_name = _read_source(source, max_bytes=max_bytes or _max_bytes())
        except MrfReadError as exc:
            return [_status(source, cpt, "error", str(exc))]

        try:
            return self.parse_bytes(content, source=inner_name or source, cpt=cpt, payer=payer, limit=limit)
        except (csv.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return [_status(source, cpt, "error", f"Could not parse MRF source: {exc}")]

    def parse_bytes(
        self,
        content: bytes,
        source: str,
        cpt: str,
        payer: str | None = None,
        limit: int = 10,
    ) -> list[MrfChargeMatch]:
        expanded, inner_name = _expand_container(content, source)
        source_name = inner_name or source
        if _looks_like_json(expanded, source_name):
            matches = _parse_json(expanded, source_name, cpt, payer, limit)
        else:
            matches = _parse_csv(expanded, source_name, cpt, payer, limit)
        return matches or [_status(source_name, cpt, "not_found", "No matching CPT/HCPCS rows found in the configured MRF source.")]


class MrfSourceService:
    def __init__(
        self,
        parser: HospitalMrfParser | None = None,
        sources: list[str] | None = None,
        clickhouse: "ClickHouseChargeStore | None" = None,
    ) -> None:
        if clickhouse is None:
            from healthcare_agent.clickhouse_store import ClickHouseChargeStore

            clickhouse = ClickHouseChargeStore()
        self.parser = parser or HospitalMrfParser()
        self.sources = sources if sources is not None else _configured_sources()
        self.clickhouse = clickhouse

    def find_charges(self, cpt: str, payer: str | None = None, limit: int = 10) -> list[MrfChargeMatch]:
        clickhouse_matches = self.clickhouse.find_charges(cpt=cpt, payer=payer, limit=limit)
        if clickhouse_matches:
            return clickhouse_matches

        if not self.sources:
            return [
                _status(
                    "HOSPITAL_MRF_SOURCES",
                    cpt,
                    "not_configured",
                    "Set HOSPITAL_MRF_SOURCES to one or more CMS-template CSV/JSON MRF file paths or URLs.",
                )
            ]

        matches: list[MrfChargeMatch] = []
        per_source_limit = max(1, limit - len(matches))
        for source in self.sources:
            matches.extend(self.parser.parse_source(source, cpt=cpt, payer=payer, limit=per_source_limit))
            found_count = len([match for match in matches if match.status == "found"])
            if found_count >= limit:
                break

        found = [match for match in matches if match.status == "found"]
        if found:
            return found[:limit]
        return matches[:limit]


def _parse_csv(content: bytes, source: str, cpt: str, payer: str | None, limit: int) -> list[MrfChargeMatch]:
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    header_index = _find_csv_header_index(rows)
    if header_index is None:
        raise ValueError("no CMS-like CSV header row found")

    hospital_name = _csv_hospital_name(rows, header_index)
    headers = rows[header_index]
    matches: list[MrfChargeMatch] = []
    for raw_row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in raw_row):
            continue
        row = {headers[index]: raw_row[index] if index < len(raw_row) else "" for index in range(len(headers))}
        if not _row_has_code(row, cpt):
            continue
        matches.extend(_csv_matches_for_row(row, source, cpt, payer, hospital_name))
        if len(matches) >= limit:
            break
    return matches[:limit]


def _csv_matches_for_row(
    row: dict[str, str],
    source: str,
    cpt: str,
    payer: str | None,
    hospital_name: str | None,
) -> list[MrfChargeMatch]:
    description = _first_row_value(row, ["description", "general_description"])
    setting = _first_row_value(row, ["setting"])
    code_type = _code_type(row)
    base = {
        "gross_charge": _money_from_headers(row, ["standard_charge", "gross"]),
        "discounted_cash": _money_from_headers(row, ["standard_charge", "discounted_cash"]),
        "min_negotiated": _money_from_headers(row, ["standard_charge", "min"]),
        "max_negotiated": _money_from_headers(row, ["standard_charge", "max"]),
    }

    row_payer = _first_row_value(row, ["payer_name"])
    row_plan = _first_row_value(row, ["plan_name"])
    tall_charge = _tall_money_from_header(row, ["standard_charge", "negotiated_dollar"])
    if row_payer or tall_charge is not None:
        if _payer_matches(row_payer, payer):
            return [
                MrfChargeMatch(
                    source=source,
                    status="found",
                    code=cpt,
                    code_type=code_type,
                    description=description,
                    setting=setting,
                    hospital_name=hospital_name,
                    payer_name=row_payer,
                    plan_name=row_plan,
                    negotiated_dollar=tall_charge,
                    median_allowed=_first_money(row, ["median_amount", "estimated_allowed_amount"]),
                    p10_allowed=_first_money(row, ["10th_percentile"]),
                    p90_allowed=_first_money(row, ["90th_percentile"]),
                    methodology=_tall_value_from_header(row, ["standard_charge", "methodology"]) or _first_row_value(row, ["methodology"]),
                    file_format="csv",
                    **base,
                )
            ]
        return []

    wide_groups = _wide_payer_groups(row)
    if wide_groups:
        matches = []
        for (wide_payer, wide_plan), values in wide_groups.items():
            if not _payer_matches(wide_payer, payer):
                continue
            matches.append(
                MrfChargeMatch(
                    source=source,
                    status="found",
                    code=cpt,
                    code_type=code_type,
                    description=description,
                    setting=setting,
                    hospital_name=hospital_name,
                    payer_name=wide_payer,
                    plan_name=wide_plan,
                    negotiated_dollar=values.get("negotiated_dollar"),
                    median_allowed=values.get("median_amount"),
                    p10_allowed=values.get("10th_percentile"),
                    p90_allowed=values.get("90th_percentile"),
                    methodology=values.get("methodology_text"),
                    file_format="csv",
                    **base,
                )
            )
        return matches

    return [
        MrfChargeMatch(
            source=source,
            status="found",
            code=cpt,
            code_type=code_type,
            description=description,
            setting=setting,
            hospital_name=hospital_name,
            file_format="csv",
            **base,
        )
    ]


def _parse_json(content: bytes, source: str, cpt: str, payer: str | None, limit: int) -> list[MrfChargeMatch]:
    payload = json.loads(content.decode("utf-8-sig"))
    hospital_name = _json_first(payload, ["hospital_name", "hospitalName", "name"])
    matches: list[MrfChargeMatch] = []
    for item in _walk_json_dicts(payload, max_items=20000):
        if not _json_item_has_code(item, cpt):
            continue
        matches.extend(_json_matches_for_item(item, source, cpt, payer, hospital_name))
        if len(matches) >= limit:
            break
    return matches[:limit]


def _json_matches_for_item(
    item: dict[str, Any],
    source: str,
    cpt: str,
    payer: str | None,
    hospital_name: str | None,
) -> list[MrfChargeMatch]:
    description = _json_first(item, ["description", "general_description", "generalDescription"])
    setting = _json_first(item, ["setting"])
    code_type = _json_code_type(item, cpt)
    base = {
        "gross_charge": _json_money(item, ["gross_charge", "gross", "standard_charge_gross"]),
        "discounted_cash": _json_money(item, ["discounted_cash", "discounted_cash_price"]),
        "min_negotiated": _json_money(item, ["minimum", "min", "deidentified_min"]),
        "max_negotiated": _json_money(item, ["maximum", "max", "deidentified_max"]),
    }

    charge_dicts = [
        charge
        for charge in _nested_dicts(item)
        if any(_normalize_key(key) in _CHARGE_KEYS for key in charge)
    ]
    if not charge_dicts:
        return [
            MrfChargeMatch(
                source=source,
                status="found",
                code=cpt,
                code_type=code_type,
                description=description,
                setting=setting,
                hospital_name=hospital_name,
                file_format="json",
                **base,
            )
        ]

    matches = []
    for charge in charge_dicts:
        charge_payer = _json_first(charge, ["payer_name", "payerName", "payer"])
        if not _payer_matches(charge_payer, payer):
            continue
        matches.append(
            MrfChargeMatch(
                source=source,
                status="found",
                code=cpt,
                code_type=code_type,
                description=description,
                setting=setting,
                hospital_name=hospital_name,
                payer_name=charge_payer,
                plan_name=_json_first(charge, ["plan_name", "planName", "plan"]),
                negotiated_dollar=_json_money(charge, ["negotiated_dollar", "payer_specific_negotiated_charge", "dollar_amount"]),
                median_allowed=_json_money(charge, ["median_amount", "median_allowed_amount"]),
                p10_allowed=_json_money(charge, ["10th_percentile", "tenth_percentile"]),
                p90_allowed=_json_money(charge, ["90th_percentile", "ninetieth_percentile"]),
                methodology=_json_first(charge, ["methodology", "standard_charge_methodology"]),
                file_format="json",
                **base,
            )
        )
    return matches


def _find_csv_header_index(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows[:20]):
        normalized = {_normalize_key(cell) for cell in row}
        if "description" in normalized and any(cell.startswith("code") for cell in normalized):
            return index
    return None


def _csv_hospital_name(rows: list[list[str]], header_index: int) -> str | None:
    if header_index < 2:
        return None
    general_headers = rows[0]
    general_values = rows[1]
    for index, header in enumerate(general_headers):
        if _normalize_key(header) == "hospital_name" and index < len(general_values):
            return general_values[index].strip() or None
    return None


def _row_has_code(row: dict[str, str], cpt: str) -> bool:
    for header, value in row.items():
        key = _normalize_key(header)
        if key.startswith("code") and not key.endswith("type") and _contains_code(value, cpt):
            return True
    return False


def _code_type(row: dict[str, str]) -> str | None:
    for header, value in row.items():
        key = _normalize_key(header)
        if key.startswith("code") and key.endswith("type") and value.strip():
            return value.strip()
    return None


def _wide_payer_groups(row: dict[str, str]) -> dict[tuple[str | None, str | None], dict[str, Any]]:
    groups: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for header, value in row.items():
        parts = _header_parts(header)
        if len(parts) < 4 or parts[0] != "standard_charge":
            continue
        field = parts[-1]
        if field not in {"negotiated_dollar", "median_amount", "10th_percentile", "90th_percentile", "methodology"}:
            continue
        payer = parts[1]
        plan = parts[2]
        group = groups.setdefault((payer, plan), {})
        if field == "methodology":
            group["methodology_text"] = value.strip() or None
        else:
            group[field] = _money(value)
    return groups


def _money_from_headers(row: dict[str, str], expected_parts: list[str]) -> float | None:
    for header, value in row.items():
        parts = _header_parts(header)
        if all(part in parts for part in expected_parts):
            amount = _money(value)
            if amount is not None:
                return amount
    return None


def _tall_money_from_header(row: dict[str, str], expected_parts: list[str]) -> float | None:
    for header, value in row.items():
        parts = _header_parts(header)
        if parts == expected_parts:
            amount = _money(value)
            if amount is not None:
                return amount
    return None


def _tall_value_from_header(row: dict[str, str], expected_parts: list[str]) -> str | None:
    for header, value in row.items():
        if _header_parts(header) == expected_parts and value.strip():
            return value.strip()
    return None


def _first_row_value(row: dict[str, str], keys: list[str]) -> str | None:
    normalized_keys = {_normalize_key(key) for key in keys}
    for header, value in row.items():
        if _normalize_key(header) in normalized_keys and value.strip():
            return value.strip()
    return None


def _first_money(row: dict[str, str], keys: list[str]) -> float | None:
    normalized_keys = {_normalize_key(key) for key in keys}
    for header, value in row.items():
        if _normalize_key(header) in normalized_keys:
            amount = _money(value)
            if amount is not None:
                return amount
    return None


def _json_item_has_code(item: dict[str, Any], cpt: str) -> bool:
    for key, value in item.items():
        normalized = _normalize_key(str(key))
        if "code" in normalized and _contains_code(value, cpt):
            return True
    return False


def _json_code_type(item: dict[str, Any], cpt: str) -> str | None:
    direct = _json_first(item, ["code_type", "codeType", "type"])
    if direct:
        return direct
    for value in item.values():
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and _json_item_has_code(entry, cpt):
                    code_type = _json_first(entry, ["type", "code_type", "codeType"])
                    if code_type:
                        return code_type
    return None


def _json_first(item: Any, keys: list[str]) -> str | None:
    if not isinstance(item, dict):
        return None
    normalized_keys = {_normalize_key(key) for key in keys}
    for key, value in item.items():
        if _normalize_key(str(key)) in normalized_keys and value not in {None, ""}:
            return str(value)
    return None


def _json_money(item: dict[str, Any], keys: list[str]) -> float | None:
    normalized_keys = {_normalize_key(key) for key in keys}
    for key, value in item.items():
        if _normalize_key(str(key)) in normalized_keys:
            amount = _money(value)
            if amount is not None:
                return amount
    return None


def _walk_json_dicts(value: Any, max_items: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack and len(found) < max_items:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _nested_dicts(value: Any) -> list[dict[str, Any]]:
    dicts = []
    if isinstance(value, dict):
        dicts.append(value)
        for child in value.values():
            dicts.extend(_nested_dicts(child))
    elif isinstance(value, list):
        for child in value:
            dicts.extend(_nested_dicts(child))
    return dicts


def _contains_code(value: Any, cpt: str) -> bool:
    if isinstance(value, list):
        return any(_contains_code(item, cpt) for item in value)
    if isinstance(value, dict):
        return any(_contains_code(item, cpt) for item in value.values())
    return cpt in re.findall(r"[A-Z]?\d{4,5}[A-Z]?", str(value))


def _payer_matches(value: str | None, payer: str | None) -> bool:
    if not payer:
        return True
    if not value:
        return False
    payer_tokens = set(re.findall(r"[a-z0-9]+", payer.lower()))
    value_tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
    return bool(payer_tokens & value_tokens) or payer.lower() in value.lower()


def _header_parts(header: str) -> list[str]:
    return [_normalize_key(part) for part in header.split("|") if part.strip()]


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _money(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _looks_like_json(content: bytes, source: str) -> bool:
    if source.lower().endswith(".json"):
        return True
    stripped = content.lstrip()
    return stripped.startswith(b"{") or stripped.startswith(b"[")


def _read_source(source: str, max_bytes: int) -> tuple[bytes, str | None]:
    if re.match(r"https?://", source):
        return _read_url(source, max_bytes), None
    path = Path(source).expanduser()
    if not path.exists():
        raise MrfReadError(f"MRF source does not exist: {source}")
    size = path.stat().st_size
    if size > max_bytes:
        raise MrfReadError(f"MRF source is {size:,} bytes, above configured cap of {max_bytes:,} bytes")
    return path.read_bytes(), None


def _read_url(url: str, max_bytes: int) -> bytes:
    request = Request(url, method="GET", headers={"Accept": "text/csv,application/json,*/*"})
    try:
        with urlopen(request, timeout=45) as response:
            return _read_capped(response, max_bytes)
    except HTTPError as exc:
        raise MrfReadError(f"HTTP {exc.code} while reading MRF URL") from exc
    except URLError as exc:
        raise MrfReadError(f"Network error while reading MRF URL: {exc.reason}") from exc


def _read_capped(response: Any, max_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise MrfReadError(f"MRF URL exceeded configured cap of {max_bytes:,} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _expand_container(content: bytes, source: str) -> tuple[bytes, str | None]:
    lower = source.lower()
    if lower.endswith(".gz"):
        return gzip.decompress(content), source[:-3]
    if lower.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".csv", ".json")):
                    return archive.read(name), name
        raise ValueError("zip file did not contain a CSV or JSON MRF")
    return content, None


def _configured_sources() -> list[str]:
    raw = get_env("HOSPITAL_MRF_SOURCES")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _max_bytes() -> int:
    raw = get_env("HOSPITAL_MRF_MAX_BYTES")
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_BYTES


def _status(source: str, cpt: str, status: str, message: str) -> MrfChargeMatch:
    return MrfChargeMatch(source=source, status=status, code=cpt, message=message)


_CHARGE_KEYS = {
    "negotiated_dollar",
    "payer_specific_negotiated_charge",
    "dollar_amount",
    "median_amount",
    "median_allowed_amount",
}


class MrfReadError(Exception):
    pass
