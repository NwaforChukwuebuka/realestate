from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class GeocodeCache:
    """SQLite-backed cache so identical normalized addresses are not re-geocoded."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                cache_key TEXT PRIMARY KEY,
                lat REAL,
                lng REAL,
                formatted_address TEXT,
                location_type TEXT,
                partial_match INTEGER NOT NULL DEFAULT 0,
                api_status TEXT NOT NULL,
                confidence INTEGER,
                raw_json TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM geocode_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def set(
        self,
        cache_key: str,
        lat: float | None,
        lng: float | None,
        formatted_address: str | None,
        location_type: str | None,
        partial_match: bool,
        api_status: str,
        confidence: int | None,
        raw_json: dict[str, Any] | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO geocode_cache (
                cache_key, lat, lng, formatted_address, location_type,
                partial_match, api_status, confidence, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                lat,
                lng,
                formatted_address,
                location_type,
                1 if partial_match else 0,
                api_status,
                confidence,
                json.dumps(raw_json) if raw_json is not None else None,
                time.time(),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
