from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from healthcare_agent.config import get_env
from healthcare_agent.mrf import MrfChargeMatch


DEFAULT_DATABASE = "healthcare"
DEFAULT_MRF_TABLE = "mrf_charges"


@dataclass
class ClickHouseConfig:
    url: str | None
    user: str | None
    password: str | None
    database: str
    mrf_table: str

    @classmethod
    def from_env(cls) -> "ClickHouseConfig":
        return cls(
            url=get_env("CLICKHOUSE_URL") or get_env("CLICKHOUSE_HOST"),
            user=get_env("CLICKHOUSE_USER"),
            password=get_env("CLICKHOUSE_PASSWORD"),
            database=get_env("CLICKHOUSE_DATABASE") or DEFAULT_DATABASE,
            mrf_table=get_env("CLICKHOUSE_MRF_TABLE") or DEFAULT_MRF_TABLE,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.user and self.password)


class ClickHouseHttpClient:
    def __init__(self, config: ClickHouseConfig | None = None) -> None:
        self.config = config or ClickHouseConfig.from_env()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def query_json(self, sql: str) -> dict[str, Any]:
        text = self.query(sql if " FORMAT " in sql.upper() else f"{sql} FORMAT JSON")
        payload = json.loads(text or "{}")
        return payload if isinstance(payload, dict) else {}

    def query(self, sql: str) -> str:
        if not self.enabled:
            raise ClickHouseError("CLICKHOUSE_URL, CLICKHOUSE_USER, and CLICKHOUSE_PASSWORD are required")
        request = Request(
            _query_url(self.config),
            data=sql.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": _basic_auth(self.config.user or "", self.config.password or ""),
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ClickHouseError(f"ClickHouse HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise ClickHouseError(f"ClickHouse request failed: {exc.reason}") from exc


class ClickHouseChargeStore:
    def __init__(self, client: ClickHouseHttpClient | None = None, table: str | None = None) -> None:
        self.client = client or ClickHouseHttpClient()
        self.table = table or self.client.config.mrf_table

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def ensure_schema(self) -> None:
        self.client.query(
            f"""
            CREATE TABLE IF NOT EXISTS {_identifier(self.table)} (
                source String,
                code String,
                code_type Nullable(String),
                description Nullable(String),
                setting Nullable(String),
                hospital_name Nullable(String),
                payer_name Nullable(String),
                plan_name Nullable(String),
                negotiated_dollar Nullable(Float64),
                gross_charge Nullable(Float64),
                discounted_cash Nullable(Float64),
                median_allowed Nullable(Float64),
                p10_allowed Nullable(Float64),
                p90_allowed Nullable(Float64),
                min_negotiated Nullable(Float64),
                max_negotiated Nullable(Float64),
                methodology Nullable(String),
                file_format Nullable(String),
                schema_reference String,
                loaded_at DateTime DEFAULT now()
            )
            ENGINE = MergeTree
            ORDER BY (code, hospital_name, payer_name, plan_name)
            """
        )

    def insert_matches(self, matches: list[MrfChargeMatch]) -> int:
        found = [match for match in matches if match.status == "found"]
        if not found:
            return 0
        rows = "\n".join(json.dumps(_match_row(match), separators=(",", ":")) for match in found)
        self.client.query(f"INSERT INTO {_identifier(self.table)} FORMAT JSONEachRow\n{rows}")
        return len(found)

    def find_charges(self, cpt: str, payer: str | None = None, limit: int = 10) -> list[MrfChargeMatch]:
        if not self.enabled:
            return []
        filters = [f"code = {_sql_string(cpt)}"]
        if payer:
            payer_like = f"%{payer.lower()}%"
            filters.append(f"(payer_name IS NULL OR lower(payer_name) LIKE {_sql_string(payer_like)})")
        sql = f"""
            SELECT
                source,
                code,
                code_type,
                description,
                setting,
                hospital_name,
                payer_name,
                plan_name,
                negotiated_dollar,
                gross_charge,
                discounted_cash,
                median_allowed,
                p10_allowed,
                p90_allowed,
                min_negotiated,
                max_negotiated,
                methodology,
                file_format,
                schema_reference
            FROM {_identifier(self.table)}
            WHERE {' AND '.join(filters)}
            ORDER BY negotiated_dollar IS NULL, negotiated_dollar ASC
            LIMIT {int(limit)}
        """
        try:
            payload = self.client.query_json(sql)
        except ClickHouseError:
            return []
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [_row_match(row) for row in rows if isinstance(row, dict)]


def _query_url(config: ClickHouseConfig) -> str:
    base = (config.url or "").rstrip("/")
    params = urlencode({"database": config.database})
    return f"{base}/?{params}"


def _basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _identifier(value: str) -> str:
    parts = value.split(".")
    return ".".join(f"`{part.replace('`', '``')}`" for part in parts)


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _match_row(match: MrfChargeMatch) -> dict[str, Any]:
    row = match.to_dict()
    row.pop("status", None)
    row.pop("message", None)
    return row


def _row_match(row: dict[str, Any]) -> MrfChargeMatch:
    return MrfChargeMatch(
        source=str(row.get("source") or "ClickHouse"),
        status="found",
        code=str(row.get("code") or ""),
        code_type=row.get("code_type"),
        description=row.get("description"),
        setting=row.get("setting"),
        hospital_name=row.get("hospital_name"),
        payer_name=row.get("payer_name"),
        plan_name=row.get("plan_name"),
        negotiated_dollar=_float(row.get("negotiated_dollar")),
        gross_charge=_float(row.get("gross_charge")),
        discounted_cash=_float(row.get("discounted_cash")),
        median_allowed=_float(row.get("median_allowed")),
        p10_allowed=_float(row.get("p10_allowed")),
        p90_allowed=_float(row.get("p90_allowed")),
        min_negotiated=_float(row.get("min_negotiated")),
        max_negotiated=_float(row.get("max_negotiated")),
        methodology=row.get("methodology"),
        file_format=row.get("file_format"),
        schema_reference=str(row.get("schema_reference") or ""),
    )


def _float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ClickHouseError(Exception):
    pass
