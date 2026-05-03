from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Sequence, cast

from geocoding.models import GeocodeResult
from normalize.store import DEFAULT_NORMALIZED_TABLE
from streetview.models import StreetViewMetadataResult
from verification.models import PropertyVerificationResult

from pipeline.models import (
    PIPELINE_STATUS_VALUES,
    PendingParcelWork,
    PipelineRow,
    PipelineStatus,
)

DEFAULT_PIPELINE_TABLE = "property_pipeline"


def _bool_to_sql(b: bool | None) -> int | None:
    if b is None:
        return None
    return 1 if b else 0


def _sql_to_bool(v: object | None) -> bool | None:
    if v is None:
        return None
    return bool(int(v))


def verification_to_json(v: PropertyVerificationResult) -> str:
    d = asdict(v)
    d["visible_signs"] = list(v.visible_signs)
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


class PipelineStore:
    """Pipeline state keyed by ``parcel_id`` (same SQLite DB as MunRoll / normalized)."""

    def __init__(
        self,
        db_path: Path,
        *,
        pipeline_table: str = DEFAULT_PIPELINE_TABLE,
        normalized_table: str = DEFAULT_NORMALIZED_TABLE,
    ) -> None:
        self.db_path = db_path
        self.pipeline_table = pipeline_table
        self.normalized_table = normalized_table
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> PipelineStore:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def init_schema(self) -> None:
        conn = self.connect()
        t = self.pipeline_table
        statuses = ", ".join(f"'{s}'" for s in sorted(PIPELINE_STATUS_VALUES))
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {t} (
                parcel_id TEXT PRIMARY KEY NOT NULL,
                pipeline_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (pipeline_status IN ({statuses})),

                geocode_cache_key TEXT,
                geocode_lat REAL,
                geocode_lng REAL,
                geocode_formatted_address TEXT,
                geocode_location_type TEXT,
                geocode_partial_match INTEGER,
                geocode_api_status TEXT,
                geocode_confidence INTEGER,

                sv_cache_key TEXT,
                sv_query_lat REAL,
                sv_query_lng REAL,
                sv_pano_id TEXT,
                sv_pano_lat REAL,
                sv_pano_lng REAL,
                sv_image_date TEXT,
                sv_api_status TEXT,

                image_paths_json TEXT,
                verification_json TEXT,

                last_error TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{t}_pipeline_status
            ON {t} (pipeline_status);
            """
        )
        conn.commit()

    def sync_from_normalized(self) -> int:
        """Insert ``pending`` rows for any ``parcel_id`` in normalized missing from pipeline.

        Returns number of rows inserted (0 if all already present).
        """
        self.init_schema()
        conn = self.connect()
        cur = conn.execute(
            f"""
            INSERT OR IGNORE INTO {self.pipeline_table} (parcel_id)
            SELECT n.parcel_id FROM {self.normalized_table} AS n;
            """
        )
        conn.commit()
        return int(cur.rowcount) if cur.rowcount is not None else 0

    def count_rows(self) -> int:
        conn = self.connect()
        cur = conn.execute(f"SELECT COUNT(*) FROM {self.pipeline_table};")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def count_by_status(self) -> dict[str, int]:
        conn = self.connect()
        cur = conn.execute(
            f"""
            SELECT pipeline_status, COUNT(*) FROM {self.pipeline_table}
            GROUP BY pipeline_status;
            """
        )
        return {str(r[0]): int(r[1]) for r in cur.fetchall()}

    def fetch_pending_with_addresses(self, limit: int) -> list[PendingParcelWork]:
        """Return up to ``limit`` pending parcels with situs columns from normalized."""
        if limit < 1:
            return []
        self.init_schema()
        conn = self.connect()
        prev = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                f"""
                SELECT n.parcel_id AS parcel_id,
                       n.property_address AS property_address,
                       n.city AS city,
                       n.state AS state,
                       n.zip AS zip
                FROM {self.pipeline_table} AS p
                JOIN {self.normalized_table} AS n ON n.parcel_id = p.parcel_id
                WHERE p.pipeline_status = 'pending'
                ORDER BY n.parcel_id
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.row_factory = prev
        out: list[PendingParcelWork] = []
        for r in rows:
            out.append(
                PendingParcelWork(
                    parcel_id=str(r["parcel_id"]),
                    property_address=r["property_address"],
                    city=r["city"],
                    state=r["state"],
                    zip=r["zip"],
                ),
            )
        return out

    def get(self, parcel_id: str) -> PipelineRow | None:
        conn = self.connect()
        prev = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                f"""
                SELECT parcel_id, pipeline_status,
                       geocode_cache_key, geocode_lat, geocode_lng,
                       geocode_formatted_address, geocode_location_type,
                       geocode_partial_match, geocode_api_status, geocode_confidence,
                       sv_cache_key, sv_query_lat, sv_query_lng,
                       sv_pano_id, sv_pano_lat, sv_pano_lng, sv_image_date, sv_api_status,
                       image_paths_json, verification_json,
                       last_error, updated_at
                FROM {self.pipeline_table}
                WHERE parcel_id = ?;
                """,
                (parcel_id.strip(),),
            )
            r = cur.fetchone()
        finally:
            conn.row_factory = prev
        if r is None:
            return None
        st = str(r["pipeline_status"])
        if st not in PIPELINE_STATUS_VALUES:
            st = "failed"
        return PipelineRow(
            parcel_id=str(r["parcel_id"]),
            pipeline_status=cast(PipelineStatus, st),
            geocode_cache_key=r["geocode_cache_key"],
            geocode_lat=r["geocode_lat"],
            geocode_lng=r["geocode_lng"],
            geocode_formatted_address=r["geocode_formatted_address"],
            geocode_location_type=r["geocode_location_type"],
            geocode_partial_match=_sql_to_bool(r["geocode_partial_match"]),
            geocode_api_status=r["geocode_api_status"],
            geocode_confidence=r["geocode_confidence"],
            sv_cache_key=r["sv_cache_key"],
            sv_query_lat=r["sv_query_lat"],
            sv_query_lng=r["sv_query_lng"],
            sv_pano_id=r["sv_pano_id"],
            sv_pano_lat=r["sv_pano_lat"],
            sv_pano_lng=r["sv_pano_lng"],
            sv_image_date=r["sv_image_date"],
            sv_api_status=r["sv_api_status"],
            image_paths_json=r["image_paths_json"],
            verification_json=r["verification_json"],
            last_error=r["last_error"],
            updated_at=r["updated_at"],
        )

    def mark_failed(self, parcel_id: str, message: str) -> None:
        conn = self.connect()
        conn.execute(
            f"""
            UPDATE {self.pipeline_table}
            SET pipeline_status = 'failed',
                last_error = ?,
                updated_at = datetime('now')
            WHERE parcel_id = ?;
            """,
            (message, parcel_id.strip()),
        )
        conn.commit()

    def apply_geocode(self, parcel_id: str, g: GeocodeResult) -> None:
        """Persist geocode fields. On failure sets ``failed`` and ``last_error``."""
        conn = self.connect()
        pid = parcel_id.strip()
        if g.ok:
            conn.execute(
                f"""
                UPDATE {self.pipeline_table}
                SET geocode_cache_key = ?,
                    geocode_lat = ?,
                    geocode_lng = ?,
                    geocode_formatted_address = ?,
                    geocode_location_type = ?,
                    geocode_partial_match = ?,
                    geocode_api_status = ?,
                    geocode_confidence = ?,
                    pipeline_status = CASE
                        WHEN pipeline_status = 'done' THEN 'done'
                        WHEN pipeline_status = 'skipped_no_street_view' THEN 'skipped_no_street_view'
                        ELSE 'pending'
                    END,
                    last_error = NULL,
                    updated_at = datetime('now')
                WHERE parcel_id = ?;
                """,
                (
                    g.cache_key,
                    g.lat,
                    g.lng,
                    g.formatted_address,
                    g.location_type,
                    _bool_to_sql(g.partial_match),
                    g.api_status,
                    g.confidence,
                    pid,
                ),
            )
        else:
            err = g.api_status
            conn.execute(
                f"""
                UPDATE {self.pipeline_table}
                SET geocode_cache_key = ?,
                    geocode_lat = ?,
                    geocode_lng = ?,
                    geocode_formatted_address = ?,
                    geocode_location_type = ?,
                    geocode_partial_match = ?,
                    geocode_api_status = ?,
                    geocode_confidence = ?,
                    pipeline_status = 'failed',
                    last_error = ?,
                    updated_at = datetime('now')
                WHERE parcel_id = ?;
                """,
                (
                    g.cache_key,
                    g.lat,
                    g.lng,
                    g.formatted_address,
                    g.location_type,
                    _bool_to_sql(g.partial_match),
                    g.api_status,
                    g.confidence,
                    err,
                    pid,
                ),
            )
        conn.commit()

    def apply_streetview_metadata(self, parcel_id: str, m: StreetViewMetadataResult) -> None:
        """Persist Street View metadata. Sets ``skipped_no_street_view`` or ``failed`` when not usable."""
        conn = self.connect()
        pid = parcel_id.strip()
        if m.no_street_view:
            conn.execute(
                f"""
                UPDATE {self.pipeline_table}
                SET sv_cache_key = ?,
                    sv_query_lat = ?,
                    sv_query_lng = ?,
                    sv_pano_id = ?,
                    sv_pano_lat = ?,
                    sv_pano_lng = ?,
                    sv_image_date = ?,
                    sv_api_status = ?,
                    pipeline_status = 'skipped_no_street_view',
                    last_error = NULL,
                    updated_at = datetime('now')
                WHERE parcel_id = ?;
                """,
                (
                    m.cache_key,
                    m.query_lat,
                    m.query_lng,
                    m.pano_id,
                    m.pano_lat,
                    m.pano_lng,
                    m.image_date,
                    m.api_status,
                    pid,
                ),
            )
            conn.commit()
            return
        if m.ok:
            conn.execute(
                f"""
                UPDATE {self.pipeline_table}
                SET sv_cache_key = ?,
                    sv_query_lat = ?,
                    sv_query_lng = ?,
                    sv_pano_id = ?,
                    sv_pano_lat = ?,
                    sv_pano_lng = ?,
                    sv_image_date = ?,
                    sv_api_status = ?,
                    pipeline_status = CASE
                        WHEN pipeline_status = 'done' THEN 'done'
                        WHEN pipeline_status = 'skipped_no_street_view' THEN 'skipped_no_street_view'
                        ELSE 'pending'
                    END,
                    last_error = NULL,
                    updated_at = datetime('now')
                WHERE parcel_id = ?;
                """,
                (
                    m.cache_key,
                    m.query_lat,
                    m.query_lng,
                    m.pano_id,
                    m.pano_lat,
                    m.pano_lng,
                    m.image_date,
                    m.api_status,
                    pid,
                ),
            )
            conn.commit()
            return

        conn.execute(
            f"""
            UPDATE {self.pipeline_table}
            SET sv_cache_key = ?,
                sv_query_lat = ?,
                sv_query_lng = ?,
                sv_pano_id = ?,
                sv_pano_lat = ?,
                sv_pano_lng = ?,
                sv_image_date = ?,
                sv_api_status = ?,
                pipeline_status = 'failed',
                last_error = ?,
                updated_at = datetime('now')
            WHERE parcel_id = ?;
            """,
            (
                m.cache_key,
                m.query_lat,
                m.query_lng,
                m.pano_id,
                m.pano_lat,
                m.pano_lng,
                m.image_date,
                m.api_status,
                m.api_status,
                pid,
            ),
        )
        conn.commit()

    def apply_images(self, parcel_id: str, paths: Sequence[str | Path]) -> None:
        """Store ordered image paths as JSON array. Does not change terminal statuses."""
        conn = self.connect()
        as_str = [str(Path(p)) for p in paths]
        payload = json.dumps(as_str, ensure_ascii=False, separators=(",", ":"))
        pid = parcel_id.strip()
        conn.execute(
            f"""
            UPDATE {self.pipeline_table}
            SET image_paths_json = ?,
                pipeline_status = CASE
                    WHEN pipeline_status = 'done' THEN 'done'
                    WHEN pipeline_status = 'skipped_no_street_view' THEN 'skipped_no_street_view'
                    ELSE 'pending'
                END,
                updated_at = datetime('now')
            WHERE parcel_id = ?;
            """,
            (payload, pid),
        )
        conn.commit()

    def apply_verification(self, parcel_id: str, v: PropertyVerificationResult) -> None:
        """Persist verification JSON and mark ``done``."""
        conn = self.connect()
        payload = verification_to_json(v)
        conn.execute(
            f"""
            UPDATE {self.pipeline_table}
            SET verification_json = ?,
                pipeline_status = 'done',
                last_error = NULL,
                updated_at = datetime('now')
            WHERE parcel_id = ?;
            """,
            (payload, parcel_id.strip()),
        )
        conn.commit()
