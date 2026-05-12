"""
Fast pre-scoring pass over all rows in ``properties_normalized``.

Uses only data already in the DB (no scrapers, no browser).
Runs in seconds against all 419k rows and stores results in
``property_prescores``.

Pre-signals derived from existing columns
-----------------------------------------
absentee_owner      → motivated_sellers, distressed_ownership, potential_wholesale
out_of_state_owner  → motivated_sellers (stronger absentee)
years_owned >= 10   → motivated_sellers, potential_wholesale
years_owned >= 20   → stronger long-ownership signal
old_property = 1    → distressed_ownership, potential_wholesale
trust_llc_owner     → inherited_properties (TR / LLC / LTD in owner_name)
trust_llc + long    → inherited_properties (stronger)

These are PRELIMINARY categories. The full 3-scraper pipeline adds
official-record and code-enforcement signals on top.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(".munroll_raw.sqlite")

_CREATE = """
CREATE TABLE IF NOT EXISTS property_prescores (
    parcel_id             TEXT PRIMARY KEY,
    pre_score             INTEGER,
    motivated_sellers     INTEGER DEFAULT 0,
    financial_distress    INTEGER DEFAULT 0,
    inherited_properties  INTEGER DEFAULT 0,
    legal_pressure        INTEGER DEFAULT 0,
    distressed_ownership  INTEGER DEFAULT 0,
    potential_wholesale   INTEGER DEFAULT 0,
    pre_categories        TEXT,
    scored_at             TEXT
)
"""

# SQL expressions for each pre-signal (SQLite-compatible)
_ABSENTEE       = "n.absentee_owner"
_OUT_OF_STATE   = "n.out_of_state_owner"
_LONG_OWN_10    = "(CASE WHEN n.years_owned >= 10 THEN 1 ELSE 0 END)"
_LONG_OWN_20    = "(CASE WHEN n.years_owned >= 20 THEN 1 ELSE 0 END)"
_OLD_PROP       = "n.old_property"
_TRUST_LLC      = """(CASE WHEN
    n.owner_name LIKE '% TR'   OR n.owner_name LIKE '% TR %'
 OR n.owner_name LIKE '%LLC%' OR n.owner_name LIKE '%LTD%'
 THEN 1 ELSE 0 END)"""

_UPSERT = f"""
INSERT INTO property_prescores (
    parcel_id,
    pre_score,
    motivated_sellers,
    financial_distress,
    inherited_properties,
    legal_pressure,
    distressed_ownership,
    potential_wholesale,
    pre_categories,
    scored_at
)
SELECT
    n.parcel_id,

    -- pre_score: sum of all binary signals
    (
        {_ABSENTEE}
      + {_OUT_OF_STATE}
      + {_LONG_OWN_10}
      + {_LONG_OWN_20}
      + {_OLD_PROP}
      + {_TRUST_LLC}
    ) AS pre_score,

    -- motivated_sellers: absentee + long ownership (10+ yrs) OR out-of-state
    (CASE WHEN ({_ABSENTEE} = 1 AND {_LONG_OWN_10} = 1)
              OR {_OUT_OF_STATE} = 1
          THEN 1 ELSE 0 END) AS motivated_sellers,

    -- financial_distress: not derivable from PA data alone — set to 0;
    --   the full scraper pipeline fills this in via liens / bankruptcy / etc.
    0 AS financial_distress,

    -- inherited_properties: trust/LLC owner + owned 20+ years
    (CASE WHEN {_TRUST_LLC} = 1 AND {_LONG_OWN_20} = 1
          THEN 1 ELSE 0 END) AS inherited_properties,

    -- legal_pressure: not derivable from PA data alone — set to 0
    0 AS legal_pressure,

    -- distressed_ownership: absentee + old property
    (CASE WHEN {_ABSENTEE} = 1 AND {_OLD_PROP} = 1
          THEN 1 ELSE 0 END) AS distressed_ownership,

    -- potential_wholesale: absentee + (old OR long ownership)
    (CASE WHEN {_ABSENTEE} = 1 AND ({_OLD_PROP} = 1 OR {_LONG_OWN_10} = 1)
          THEN 1 ELSE 0 END) AS potential_wholesale,

    -- pre_categories: pipe-separated list of triggered categories
    TRIM(
        CASE WHEN ({_ABSENTEE} = 1 AND {_LONG_OWN_10} = 1) OR {_OUT_OF_STATE} = 1
             THEN 'motivated_sellers | ' ELSE '' END
      ||
        CASE WHEN {_TRUST_LLC} = 1 AND {_LONG_OWN_20} = 1
             THEN 'inherited_properties | ' ELSE '' END
      ||
        CASE WHEN {_ABSENTEE} = 1 AND {_OLD_PROP} = 1
             THEN 'distressed_ownership | ' ELSE '' END
      ||
        CASE WHEN {_ABSENTEE} = 1 AND ({_OLD_PROP} = 1 OR {_LONG_OWN_10} = 1)
             THEN 'potential_wholesale' ELSE '' END,
        ' |'
    ) AS pre_categories,

    '{datetime.utcnow().isoformat(timespec="seconds")}' AS scored_at

FROM properties_normalized n
ON CONFLICT(parcel_id) DO UPDATE SET
    pre_score            = excluded.pre_score,
    motivated_sellers    = excluded.motivated_sellers,
    financial_distress   = excluded.financial_distress,
    inherited_properties = excluded.inherited_properties,
    legal_pressure       = excluded.legal_pressure,
    distressed_ownership = excluded.distressed_ownership,
    potential_wholesale  = excluded.potential_wholesale,
    pre_categories       = excluded.pre_categories,
    scored_at            = excluded.scored_at
"""


def run_prescore(db_path: Path = DB_PATH) -> dict[str, int]:
    """
    Score all rows in ``properties_normalized`` and store in
    ``property_prescores``.  Returns a summary dict.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute(_CREATE)
    con.commit()

    print("[prescore] Scoring all properties…", flush=True)
    con.execute(_UPSERT)
    con.commit()

    cur = con.cursor()
    summary = {}
    for col in (
        "pre_score >= 1", "pre_score >= 2", "pre_score >= 3",
        "motivated_sellers = 1", "inherited_properties = 1",
        "distressed_ownership = 1", "potential_wholesale = 1",
    ):
        cur.execute(f"SELECT COUNT(*) FROM property_prescores WHERE {col}")
        summary[col] = cur.fetchone()[0]

    con.close()
    return summary


def export_prescores(
    db_path: Path = DB_PATH,
    *,
    min_score: int = 1,
    category: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Return pre-scored rows joined with property details, filtered and ranked.

    Parameters
    ----------
    min_score:
        Only return rows with pre_score >= this value (default 1).
    category:
        Filter to rows where this category column = 1.
        One of: motivated_sellers, inherited_properties,
        distressed_ownership, potential_wholesale.
    limit:
        Cap the number of rows returned.
    """
    clauses = [f"p.pre_score >= {int(min_score)}"]
    if category:
        valid = {"motivated_sellers", "inherited_properties",
                 "distressed_ownership", "potential_wholesale"}
        if category not in valid:
            raise ValueError(f"category must be one of {valid}")
        clauses.append(f"p.{category} = 1")

    where = "WHERE " + " AND ".join(clauses)
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    sql = f"""
        SELECT
            n.parcel_id,
            n.property_address,
            n.city,
            n.state,
            n.zip,
            n.owner_name,
            n.mailing_address,
            n.property_type,
            n.year_built,
            n.last_sale_date,
            n.assessed_value,
            n.absentee_owner,
            n.out_of_state_owner,
            n.years_owned,
            n.old_property,
            p.pre_score,
            p.pre_categories,
            p.motivated_sellers,
            p.inherited_properties,
            p.distressed_ownership,
            p.potential_wholesale
        FROM   property_prescores p
        JOIN   properties_normalized n ON n.parcel_id = p.parcel_id
        {where}
        ORDER BY p.pre_score DESC, n.years_owned DESC
        {limit_clause}
    """

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql).fetchall()
    con.close()
    return [dict(r) for r in rows]
