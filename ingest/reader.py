from __future__ import annotations

import csv
import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

# Miami-Dade municipal extract uses this column as the parcel identifier.
FOLIO_FIELD = "Folio"


def open_munroll_text(path: Path) -> TextIO:
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def _line_looks_like_header(cells: list[str]) -> bool:
    if not cells:
        return False
    return cells[0].strip() == FOLIO_FIELD and len(cells) > 1


def iter_munroll_records(csv_path: Path) -> Iterator[dict[str, str | None]]:
    """Stream MunRoll CSV rows as dicts after the county preamble and header line.

    The official extract begins with disclaimer lines; the data header row starts
    with ``Folio``. Rows are read with :mod:`csv` (handles quoted fields) without
    loading the file into memory.
    """
    with open_munroll_text(csv_path) as f:
        header_line: str | None = None
        for line in f:
            cells = next(csv.reader([line]))
            if _line_looks_like_header(cells):
                header_line = line
                break
        if header_line is None:
            msg = f"No MunRoll header row (first field {FOLIO_FIELD!r}) found in {csv_path}"
            raise ValueError(msg)

        reader = csv.DictReader(itertools.chain([header_line], f))
        if reader.fieldnames is None:
            return
        for row in reader:
            # DictReader may yield None values for missing cells; normalize to str|None
            out: dict[str, str | None] = {}
            for k, v in row.items():
                if k is None:
                    continue
                out[k] = v
            yield out


def peek_folio_field(csv_path: Path) -> str | None:
    """Return the Folio value of the first data row, or None if file has no data."""
    it = iter_munroll_records(csv_path)
    try:
        first = next(it)
    except StopIteration:
        return None
    v = first.get(FOLIO_FIELD)
    return (v or "").strip() or None
