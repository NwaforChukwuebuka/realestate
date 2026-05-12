"""
Miami-Dade RER Regulation Cases scraper.

Searches https://www.miamidade.gov/Apps/RER/RegulationSupportWebViewer/ by
folio number, property address, or owner name, extracts every case row from
the results table, and flags strong wholesale lead indicators from the
``Case Type`` text.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

URL = "https://www.miamidade.gov/Apps/RER/RegulationSupportWebViewer/"

# Case type keywords that signal a strong wholesale lead (project2.md)
STRONG_LEAD_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"unsafe\s+structure",
        r"expired\s+permit",
        r"work\s+without\s+permit",
        r"without\s+permit",
        r"open\s+code\s+violation",
        r"abandoned",
        r"large\s+fine",
        r"repeat\s+violation",
    )
)


def _is_strong_lead(case_type: str) -> bool:
    return any(p.search(case_type) for p in STRONG_LEAD_PATTERNS)


def _folio_slug(value: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\-]+", "_", value.strip()).strip("_")
    return s[:max_len]


@dataclass
class RegulationCase:
    search_input: str       # folio, address, or owner name used for the search
    case_number: str
    case_type: str
    address: str
    owner_name: str
    violator: str
    folio_number: str
    is_strong_lead: bool
    # owner-name search only
    count: str = ""
    permit: str = ""
    ticket: str = ""
    search_index: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class RegulationCasesScraper:
    """
    Playwright-based scraper for Miami-Dade RER Regulation Cases.

    Parameters
    ----------
    search_by:
        ``"folio"`` (default) — uses the Folio Number tab.
        ``"address"`` — uses the Address tab.
        ``"owner"`` — uses the Owner Name tab (adds COUNT, PERMIT, TICKET columns).
    headless:
        Run Chromium without a visible window (default False).
    timeout_ms:
        Per-action timeout in milliseconds (default 30 000).
    delay_s:
        Polite pause between consecutive searches (default 1.5 s).
    slow_mo_ms:
        Pause Playwright before every action (default 0). Try 500 to watch.
    all_records:
        When False (default), return only strong-lead rows. When True,
        return every row (``is_strong_lead`` is still set).
    results_html_dir:
        If set, saves one HTML snapshot per search:
        ``regulation_cases_<n>_<slug>.html``.
    """

    def __init__(
        self,
        *,
        search_by: str = "folio",
        headless: bool = False,
        timeout_ms: int = 30_000,
        delay_s: float = 1.5,
        slow_mo_ms: int = 0,
        all_records: bool = False,
        results_html_dir: Optional[Path] = None,
    ) -> None:
        if search_by not in ("folio", "address", "owner"):
            raise ValueError("search_by must be 'folio', 'address', or 'owner'")
        self.search_by = search_by
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.delay_s = delay_s
        self.slow_mo_ms = slow_mo_ms
        self.all_records = all_records
        self.results_html_dir = results_html_dir
        self._last_row_count = 0
        self._last_strong_count = 0

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _goto_site(self, page: Page) -> None:
        page.goto(URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
        # wait for the search form to mount
        page.wait_for_load_state("networkidle", timeout=min(15_000, self.timeout_ms))

    def _open_folio_tab(self, page: Page) -> None:
        page.get_by_text("Folio Number").click(timeout=self.timeout_ms)
        page.get_by_role("textbox", name="Search by Folio Number").wait_for(
            state="visible", timeout=self.timeout_ms
        )

    def _open_address_tab(self, page: Page) -> None:
        page.get_by_text("Address", exact=True).click(timeout=self.timeout_ms)
        page.get_by_role("textbox", name="Enter Property Address").wait_for(
            state="visible", timeout=self.timeout_ms
        )

    def _open_owner_tab(self, page: Page) -> None:
        page.get_by_text("Owner Name").click(timeout=self.timeout_ms)
        page.get_by_role("textbox", name="Enter Owner Name").wait_for(
            state="visible", timeout=self.timeout_ms
        )

    # ------------------------------------------------------------------
    # Waiting for results
    # ------------------------------------------------------------------

    def _wait_for_results(self, page: Page) -> str:
        """
        Polls until the results table appears or a no-results message is shown.

        Returns ``"results"``, ``"empty"``, or ``"unknown"``.
        """
        empty_re = re.compile(r"no\s+results\s+found|no\s+records\s+found|0\s+result", re.IGNORECASE)
        results_re = re.compile(r"CASE\s+NUMBER|case number|\bVIEW\b", re.IGNORECASE)

        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
        outcome = "unknown"
        while time.monotonic() < deadline:
            try:
                body = page.locator("body").inner_text(timeout=2_000)
            except Exception:
                body = ""
            if empty_re.search(body):
                outcome = "empty"
                break
            if results_re.search(body):
                outcome = "results"
                break
            time.sleep(0.4)

        try:
            page.wait_for_load_state("networkidle", timeout=min(10_000, self.timeout_ms))
        except Exception:
            pass
        return outcome

    # ------------------------------------------------------------------
    # Table extraction
    # ------------------------------------------------------------------

    def _extract_rows(self, page: Page, search_input: str) -> list[RegulationCase]:
        """
        Read the results table. Each data row maps to one RegulationCase.

        The site renders a standard HTML table; we grab column headers first to
        build a positional map, then iterate ``<tr>`` rows.
        """
        cases: list[RegulationCase] = []

        # Locate the results table by looking for the CASE NUMBER header
        table = page.locator("table").filter(has_text=re.compile(r"CASE\s*NUMBER", re.IGNORECASE))
        if table.count() == 0:
            return cases

        table = table.first

        # --- Header row: build column-index map ---
        headers: list[str] = []
        for th in table.locator("th").all():
            try:
                headers.append(th.inner_text(timeout=3_000).strip().upper())
            except Exception:
                headers.append("")

        def _col(name_re: str) -> int:
            pat = re.compile(name_re, re.IGNORECASE)
            for i, h in enumerate(headers):
                if pat.search(h):
                    return i
            return -1

        col_case   = _col(r"CASE\s*NUMBER")
        col_type   = _col(r"CASE\s*TYPE")
        col_addr   = _col(r"ADDRESS")
        col_owner  = _col(r"OWNER")
        col_viol   = _col(r"VIOLATOR")
        col_folio  = _col(r"FOLIO")
        col_count  = _col(r"^COUNT$")
        col_permit = _col(r"^PERMIT$")
        col_ticket = _col(r"^TICKET$")

        # --- Data rows ---
        rows = table.locator("tr").all()
        for row in rows:
            cells = row.locator("td").all()
            if not cells:
                continue  # header or spacer row

            def _cell(idx: int) -> str:
                if idx < 0 or idx >= len(cells):
                    return ""
                try:
                    return " ".join(cells[idx].inner_text(timeout=3_000).split())
                except Exception:
                    return ""

            case_number = _cell(col_case)
            if not case_number:
                continue  # skip empty / separator rows

            case_type  = _cell(col_type)
            address    = _cell(col_addr)
            owner_name = _cell(col_owner)
            violator   = _cell(col_viol)
            folio      = _cell(col_folio)

            cases.append(
                RegulationCase(
                    search_input=search_input,
                    case_number=case_number,
                    case_type=case_type,
                    address=address,
                    owner_name=owner_name,
                    violator=violator,
                    folio_number=folio,
                    is_strong_lead=_is_strong_lead(case_type),
                    count=_cell(col_count),
                    permit=_cell(col_permit),
                    ticket=_cell(col_ticket),
                )
            )

        return cases

    def _extract_owner_cases(self, page: Page, search_input: str) -> list[RegulationCase]:
        """
        Owner-name results show a summary table with one VIEW button per matching
        owner. Click each VIEW button to reveal that owner's case rows, extract
        them, then reload the results page before clicking the next VIEW.

        Falls back to ``_extract_rows`` directly if no VIEW buttons are found
        (in case the site renders the table immediately).
        """
        all_cases: list[RegulationCase] = []

        # Snapshot the current URL so we can return to the owner results list
        # between VIEW clicks when there are multiple matching owners.
        results_url = page.url

        view_buttons = page.get_by_text("VIEW").all()

        if not view_buttons:
            # No VIEW step — try extracting the table directly
            return self._extract_rows(page, search_input)

        for i, _ in enumerate(view_buttons):
            try:
                # Re-fetch the button list fresh each iteration (DOM may have changed)
                btns = page.get_by_text("VIEW").all()
                if i >= len(btns):
                    break

                btns[i].click(timeout=self.timeout_ms)
                self._wait_for_results(page)

                cases = self._extract_rows(page, search_input)
                all_cases.extend(cases)

                # If more VIEW buttons remain, go back to the owner results list
                if i < len(view_buttons) - 1:
                    page.goto(results_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(10_000, self.timeout_ms))
                    except Exception:
                        pass

            except Exception as exc:
                print(
                    f"[regulation_cases]   ERROR on VIEW #{i + 1}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        return all_cases

    # ------------------------------------------------------------------
    # Per-search logic
    # ------------------------------------------------------------------

    def search_one(
        self,
        page: Page,
        value: str,
        *,
        search_index: int = 1,
    ) -> list[RegulationCase]:
        """
        Run one search (folio number or address) and return matching cases.

        The caller is responsible for having already navigated to the site
        (via ``_goto_site``). This method reloads the page between searches
        to reset form state.
        """
        # Reload to get a clean form on each search
        self._goto_site(page)

        if self.search_by == "folio":
            self._open_folio_tab(page)
            box = page.get_by_role("textbox", name="Search by Folio Number")
        elif self.search_by == "address":
            self._open_address_tab(page)
            box = page.get_by_role("textbox", name="Enter Property Address")
        else:  # owner
            self._open_owner_tab(page)
            box = page.get_by_role("textbox", name="Enter Owner Name")

        box.click(timeout=self.timeout_ms)
        box.fill(value)

        page.get_by_role("button", name=re.compile(r"^Submit$", re.I)).click(
            timeout=self.timeout_ms
        )

        outcome = self._wait_for_results(page)

        if self.results_html_dir is not None:
            self.results_html_dir.mkdir(parents=True, exist_ok=True)
            slug = _folio_slug(value) or "search"
            path = self.results_html_dir / f"regulation_cases_{search_index:03d}_{slug}.html"
            path.write_text(page.content(), encoding="utf-8")
            print(
                f"[regulation_cases] Saved HTML → {path}",
                file=sys.stderr,
                flush=True,
            )

        if outcome == "empty":
            print(
                f"[regulation_cases]   (no results for {value!r})",
                file=sys.stderr,
                flush=True,
            )
            return []
        if outcome == "unknown":
            print(
                "[regulation_cases]   WARNING: results view not confirmed before timeout.",
                file=sys.stderr,
                flush=True,
            )

        if self.search_by == "owner":
            cases = self._extract_owner_cases(page, value)
        else:
            cases = self._extract_rows(page, value)

        for c in cases:
            c.search_index = search_index

        self._last_row_count = len(cases)
        self._last_strong_count = sum(1 for c in cases if c.is_strong_lead)

        n_strong = self._last_strong_count
        n_total = self._last_row_count

        # Owner searches return everything by default; folio/address filter to strong leads.
        return_all = self.all_records or self.search_by == "owner"

        if return_all:
            msg = f"→ {n_total} case(s); {n_strong} strong lead(s); returning all."
        elif not cases:
            msg = f"→ {n_total} case(s); none are strong leads."
        else:
            strong = [c for c in cases if c.is_strong_lead]
            msg = f"→ {n_total} case(s); {n_strong} strong lead(s); returning {len(strong)}."
            cases = strong

        print(f"[regulation_cases]   {msg}", file=sys.stderr, flush=True)
        return cases if return_all else [c for c in cases if c.is_strong_lead]

    def run(self, values: list[str]) -> list[RegulationCase]:
        """
        Run searches for every folio / address and return all matching cases.

        Opens a single browser session.
        """
        values = [v.strip() for v in values if v.strip()]
        all_cases: list[RegulationCase] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            try:
                total = len(values)
                for i, value in enumerate(values, start=1):
                    print(
                        f"[regulation_cases] [{i}/{total}] Searching ({self.search_by}): {value}",
                        file=sys.stderr,
                        flush=True,
                    )
                    try:
                        cases = self.search_one(page, value, search_index=i)
                        all_cases.extend(cases)
                    except Exception as exc:
                        print(
                            f"[regulation_cases]   ERROR: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )

                    if i < total:
                        time.sleep(self.delay_s)

            finally:
                browser.close()

        return all_cases
