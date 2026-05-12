"""
SQLite integration for the research pipeline.

Reads candidate folios from ``properties_normalized`` with optional pre-filters,
writes results to a ``research_results`` table in the same database, and skips
parcels that have already been researched.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from research.pipeline import ResearchReport

DB_PATH = Path(".munroll_raw.sqlite")

_CREATE_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS research_results (
    parcel_id               TEXT PRIMARY KEY,
    researched_at           TEXT,
    rer_search_method       TEXT,
    regulation_cases_count  INTEGER,
    strong_violations_count INTEGER,
    official_records_count  INTEGER,
    high_value_doc_types    TEXT,
    triggered_signals       TEXT,
    categories              TEXT,
    lead_score              INTEGER,
    is_wholesale_opportunity INTEGER,
    summary                 TEXT
)
"""

_UPSERT_RESULT = """
INSERT INTO research_results (
    parcel_id, researched_at, rer_search_method,
    regulation_cases_count, strong_violations_count,
    official_records_count, high_value_doc_types,
    triggered_signals, categories, lead_score,
    is_wholesale_opportunity, summary
) VALUES (
    :parcel_id, :researched_at, :rer_search_method,
    :regulation_cases_count, :strong_violations_count,
    :official_records_count, :high_value_doc_types,
    :triggered_signals, :categories, :lead_score,
    :is_wholesale_opportunity, :summary
)
ON CONFLICT(parcel_id) DO UPDATE SET
    researched_at            = excluded.researched_at,
    rer_search_method        = excluded.rer_search_method,
    regulation_cases_count   = excluded.regulation_cases_count,
    strong_violations_count  = excluded.strong_violations_count,
    official_records_count   = excluded.official_records_count,
    high_value_doc_types     = excluded.high_value_doc_types,
    triggered_signals        = excluded.triggered_signals,
    categories               = excluded.categories,
    lead_score               = excluded.lead_score,
    is_wholesale_opportunity = excluded.is_wholesale_opportunity,
    summary                  = excluded.summary
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def ensure_results_table(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as con:
        con.execute(_CREATE_RESULTS_TABLE)


def load_candidates(
    db_path: Path = DB_PATH,
    *,
    absentee_only: bool = False,
    min_years_owned: int = 0,
    old_property_only: bool = False,
    out_of_state_only: bool = False,
    skip_researched: bool = True,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    """
    Query ``properties_normalized`` with optional pre-filters.

    Returns a list of row dicts ordered by strongest pre-signals first
    (absentee + old + long ownership).
    """
    ensure_results_table(db_path)

    clauses: list[str] = []
    if absentee_only:
        clauses.append("n.absentee_owner = 1")
    if min_years_owned > 0:
        clauses.append(f"n.years_owned >= {int(min_years_owned)}")
    if old_property_only:
        clauses.append("n.old_property = 1")
    if out_of_state_only:
        clauses.append("n.out_of_state_owner = 1")
    if skip_researched:
        clauses.append("r.parcel_id IS NULL")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    offset_clause = f"OFFSET {int(offset)}" if offset else ""

    sql = f"""
        SELECT n.*
        FROM   properties_normalized n
        LEFT JOIN research_results r ON r.parcel_id = n.parcel_id
        {where}
        ORDER BY
            (n.absentee_owner + n.old_property) DESC,
            n.years_owned DESC
        {limit_clause} {offset_clause}
    """

    with _connect(db_path) as con:
        rows = con.execute(sql).fetchall()
    return [dict(r) for r in rows]


def count_candidates(
    db_path: Path = DB_PATH,
    *,
    absentee_only: bool = False,
    min_years_owned: int = 0,
    old_property_only: bool = False,
    out_of_state_only: bool = False,
    skip_researched: bool = True,
) -> int:
    ensure_results_table(db_path)

    clauses: list[str] = []
    if absentee_only:
        clauses.append("n.absentee_owner = 1")
    if min_years_owned > 0:
        clauses.append(f"n.years_owned >= {int(min_years_owned)}")
    if old_property_only:
        clauses.append("n.old_property = 1")
    if out_of_state_only:
        clauses.append("n.out_of_state_owner = 1")
    if skip_researched:
        clauses.append("r.parcel_id IS NULL")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT COUNT(*)
        FROM   properties_normalized n
        LEFT JOIN research_results r ON r.parcel_id = n.parcel_id
        {where}
    """
    with _connect(db_path) as con:
        return con.execute(sql).fetchone()[0]


def save_result(report: ResearchReport, db_path: Path = DB_PATH) -> None:
    """Write (or update) one research result row."""
    ensure_results_table(db_path)

    doc_types = ", ".join(
        r.doc_type_code
        for r in report.official_records
        if r.is_high_value and r.doc_type_code
    )

    row = {
        "parcel_id":               report.folio,
        "researched_at":           datetime.utcnow().isoformat(timespec="seconds"),
        "rer_search_method":       report.rer_search_method,
        "regulation_cases_count":  len(report.regulation_cases),
        "strong_violations_count": sum(1 for c in report.regulation_cases if c.is_strong_lead),
        "official_records_count":  len(report.official_records),
        "high_value_doc_types":    doc_types,
        "triggered_signals":       " | ".join(report.triggered_signals),
        "categories":              " | ".join(report.categories),
        "lead_score":              report.lead_score,
        "is_wholesale_opportunity":int(report.is_wholesale_opportunity),
        "summary":                 report.summary,
    }

    with _connect(db_path) as con:
        con.execute(_UPSERT_RESULT, row)


def load_opportunities(db_path: Path = DB_PATH) -> list[dict]:
    """Return all researched properties flagged as wholesale opportunities."""
    with _connect(db_path) as con:
        rows = con.execute("""
            SELECT r.*, n.property_address, n.owner_name, n.mailing_address,
                   n.year_built, n.last_sale_date, n.assessed_value,
                   n.absentee_owner, n.years_owned
            FROM   research_results r
            JOIN   properties_normalized n ON n.parcel_id = r.parcel_id
            WHERE  r.is_wholesale_opportunity = 1
            ORDER BY r.lead_score DESC, r.researched_at DESC
        """).fetchall()
    return [dict(r) for r in rows]
