"""Map Miami-Dade MunRoll columns to normalized pipeline fields."""

from __future__ import annotations

from normalize.mapper import map_munroll_record, map_munroll_row_json
from normalize.models import NormalizedProperty
from normalize.store import NormalizedStore, RebuildResult

__all__ = [
    "NormalizedProperty",
    "NormalizedStore",
    "RebuildResult",
    "map_munroll_record",
    "map_munroll_row_json",
]
