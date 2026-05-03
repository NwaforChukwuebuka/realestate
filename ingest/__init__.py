"""Miami-Dade MunRoll CSV ingest: stream read, store raw rows, dedupe by Folio."""

from __future__ import annotations

from ingest.reader import iter_munroll_records
from ingest.store import ImportResult, MunrollStore

__all__ = ["ImportResult", "MunrollStore", "iter_munroll_records"]
