"""
Persistent-browser batch worker.

Opens ONE browser for the entire assigned folio batch, reusing the same
three pages (PA / RER / OR) across every folio instead of launching and
closing a browser per search. Pages are recycled every RESTART_EVERY
folios to prevent memory creep. Individual folio failures are caught,
logged, and skipped — the batch never stops for one bad folio.

Multiple workers can run in parallel via multiprocessing; each has its
own browser and writes results independently to the shared SQLite DB
(WAL mode handles concurrent writes safely).
"""

from __future__ import annotations

import multiprocessing
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from official_records.scraper import OfficialRecord, OfficialRecordsScraper
from property_appraiser.scraper import PropertyAppraiserScraper, PropertyRecord
from regulation_cases.scraper import RegulationCase, RegulationCasesScraper
from research.db import DB_PATH, save_result
from research.pipeline import (
    ResearchReport,
    _OPPORTUNITY_THRESHOLD,
    _build_summary,
)
from research.signals import evaluate

RESTART_EVERY = 200  # recycle browser context every N folios

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class PersistentResearchWorker:
    """
    Single worker: one browser, three shared pages, processes a list of
    folios sequentially.  Call ``run(folios)`` to start.
    """

    def __init__(
        self,
        worker_id: int = 0,
        headless: bool = True,
        timeout_ms: int = 30_000,
        db_path: Path = DB_PATH,
        log_path: Optional[Path] = None,
    ) -> None:
        self.worker_id  = worker_id
        self.headless   = headless
        self.timeout_ms = timeout_ms
        self.db_path    = db_path
        self.log_path   = log_path

        # Scraper instances — we bypass their run() and call the
        # internal page-level methods directly with shared pages.
        _kw = dict(headless=headless, timeout_ms=timeout_ms)
        self._pa         = PropertyAppraiserScraper(**_kw)
        self._rer_folio  = RegulationCasesScraper(search_by="folio",   all_records=True, **_kw)
        self._rer_addr   = RegulationCasesScraper(search_by="address", all_records=True, **_kw)
        self._rer_owner  = RegulationCasesScraper(search_by="owner",   all_records=True, **_kw)
        self._or         = OfficialRecordsScraper(all_records=True, **_kw)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        ts   = datetime.utcnow().strftime("%H:%M:%S")
        line = f"[W{self.worker_id}|{ts}] {msg}"
        print(line, flush=True)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    # ------------------------------------------------------------------
    # Browser / page management
    # ------------------------------------------------------------------

    def _new_pages(self, browser):
        ctx = browser.new_context(user_agent=_UA)
        return ctx, ctx.new_page(), ctx.new_page(), ctx.new_page()

    def _close_ctx(self, ctx) -> None:
        try:
            ctx.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, folios: list[str]) -> None:
        total  = len(folios)
        done   = 0
        errors = 0

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless, slow_mo=0)
            ctx, pa_page, rer_page, or_page = self._new_pages(browser)

            try:
                for i, folio in enumerate(folios):
                    # Recycle pages periodically
                    if i > 0 and i % RESTART_EVERY == 0:
                        self._log(f"Recycling pages  {i}/{total}")
                        self._close_ctx(ctx)
                        ctx, pa_page, rer_page, or_page = self._new_pages(browser)

                    self._log(f"{i+1}/{total}  folio={folio}")

                    try:
                        report = self._research_one(
                            folio, pa_page, rer_page, or_page, i + 1
                        )
                        save_result(report, self.db_path)
                        opp = "★ OPPORTUNITY" if report.is_wholesale_opportunity else ""
                        self._log(
                            f"  score={report.lead_score}  "
                            f"rer={report.rer_search_method}  "
                            f"or={len(report.official_records)}  {opp}"
                        )
                        done += 1

                    except Exception as exc:
                        errors += 1
                        self._log(f"  ERROR: {exc}")
                        # Fresh pages so the next folio starts clean
                        self._close_ctx(ctx)
                        try:
                            ctx, pa_page, rer_page, or_page = self._new_pages(browser)
                        except Exception:
                            pass

            finally:
                self._close_ctx(ctx)
                try:
                    browser.close()
                except Exception:
                    pass

        self._log(f"Done — done={done}  errors={errors}  total={total}")

    # ------------------------------------------------------------------
    # Per-folio research (uses shared pages, no browser open/close)
    # ------------------------------------------------------------------

    def _research_one(
        self,
        folio: str,
        pa_page,
        rer_page,
        or_page,
        search_index: int,
    ) -> ResearchReport:

        # 1. Property Appraiser
        pa = self._pa._extract_one(pa_page, folio, search_index)
        property_address = pa.property_address if pa else ""
        owner_name       = pa.owner_name       if pa else ""

        # 2. Regulation Cases — folio → address → owner fallback
        rer_cases:  list[RegulationCase] = []
        rer_method = "none"

        rer_cases = self._rer_folio.search_one(rer_page, folio, search_index=search_index)
        if rer_cases:
            rer_method = "folio"
        elif property_address:
            rer_cases = self._rer_addr.search_one(rer_page, property_address, search_index=search_index)
            if rer_cases:
                rer_method = "address"

        if not rer_cases and owner_name:
            rer_cases = self._rer_owner.search_one(rer_page, owner_name, search_index=search_index)
            if rer_cases:
                rer_method = "owner"

        # 3. Official Records
        official: list[OfficialRecord] = []
        if property_address:
            official = self._or.search_one(or_page, property_address, search_index=search_index)

        # 4. Signals
        signals, categories = evaluate(pa, rer_cases, official)
        score          = len(signals)
        is_opportunity = score >= _OPPORTUNITY_THRESHOLD

        summary = _build_summary(
            folio, pa, rer_cases, rer_method, official,
            signals, categories, score, is_opportunity,
        )

        return ResearchReport(
            folio=folio,
            property_record=pa,
            regulation_cases=rer_cases,
            rer_search_method=rer_method,
            official_records=official,
            triggered_signals=signals,
            categories=categories,
            lead_score=score,
            is_wholesale_opportunity=is_opportunity,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Multiprocessing helpers
# ---------------------------------------------------------------------------

def _worker_entry(args: tuple) -> None:
    """Top-level picklable entry point for multiprocessing.Pool."""
    worker_id, folios, headless, timeout_ms, db_path_str, log_dir_str = args
    log_path = (
        Path(log_dir_str) / f"worker_{worker_id}.log"
        if log_dir_str else None
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    worker = PersistentResearchWorker(
        worker_id=worker_id,
        headless=headless,
        timeout_ms=timeout_ms,
        db_path=Path(db_path_str),
        log_path=log_path,
    )
    worker.run(folios)


def run_workers(
    folios: list[str],
    n_workers: int = 1,
    headless: bool = True,
    timeout_ms: int = 30_000,
    db_path: Path = DB_PATH,
    log_dir: Optional[Path] = None,
) -> None:
    """
    Distribute ``folios`` across ``n_workers`` parallel processes, each
    running its own persistent-browser worker.
    """
    if not folios:
        print("[research] No folios to process.", flush=True)
        return

    # Split into N roughly equal chunks
    chunk_size = (len(folios) + n_workers - 1) // n_workers
    chunks = [folios[i : i + chunk_size] for i in range(0, len(folios), chunk_size)]

    print(
        f"[research] {len(folios):,} folios  →  {len(chunks)} worker(s)  "
        f"× ~{chunk_size:,} folios each",
        flush=True,
    )

    args = [
        (
            i,
            chunk,
            headless,
            timeout_ms,
            str(db_path),
            str(log_dir) if log_dir else None,
        )
        for i, chunk in enumerate(chunks)
    ]

    if n_workers == 1:
        # Run in-process so stack traces are visible directly
        _worker_entry(args[0])
    else:
        # Each worker is a separate OS process with its own browser
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            pool.map(_worker_entry, args)
