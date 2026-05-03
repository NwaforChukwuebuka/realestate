from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class StreetViewMetadataCache:
    """SQLite-backed cache so repeated coordinate lookups do not hit the API."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS streetview_metadata_cache (
                cache_key TEXT PRIMARY KEY,
                query_lat REAL,
                query_lng REAL,
                pano_id TEXT,
                pano_lat REAL,
                pano_lng REAL,
                image_date TEXT,
                api_status TEXT NOT NULL,
                raw_json TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM streetview_metadata_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def set(
        self,
        cache_key: str,
        query_lat: float | None,
        query_lng: float | None,
        pano_id: str | None,
        pano_lat: float | None,
        pano_lng: float | None,
        image_date: str | None,
        api_status: str,
        raw_json: dict[str, Any] | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO streetview_metadata_cache (
                cache_key, query_lat, query_lng, pano_id, pano_lat, pano_lng,
                image_date, api_status, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                query_lat,
                query_lng,
                pano_id,
                pano_lat,
                pano_lng,
                image_date,
                api_status,
                json.dumps(raw_json) if raw_json is not None else None,
                time.time(),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
