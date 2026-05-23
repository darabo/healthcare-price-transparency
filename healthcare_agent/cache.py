from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, path: Path | str = ".cache/evidence.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get(self, namespace: str, key_data: dict[str, Any], ttl_seconds: int) -> dict[str, Any] | None:
        cache_key = self._key(namespace, key_data)
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "select payload, created_at from cache where cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        payload, created_at = row
        if time.time() - created_at > ttl_seconds:
            return None
        return json.loads(payload)

    def set(self, namespace: str, key_data: dict[str, Any], payload: dict[str, Any]) -> None:
        cache_key = self._key(namespace, key_data)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                insert into cache(cache_key, namespace, payload, created_at)
                values (?, ?, ?, ?)
                on conflict(cache_key) do update set
                  payload = excluded.payload,
                  created_at = excluded.created_at
                """,
                (cache_key, namespace, json.dumps(payload), time.time()),
            )
            conn.commit()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                create table if not exists cache (
                  cache_key text primary key,
                  namespace text not null,
                  payload text not null,
                  created_at real not null
                )
                """
            )
            conn.commit()

    def _key(self, namespace: str, key_data: dict[str, Any]) -> str:
        serialized = json.dumps(key_data, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"{namespace}:{digest}"
