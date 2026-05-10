"""
Miami-Dade Official Records scraper.

Searches the Miami-Dade Clerk's public records by property address and
surfaces high-value wholesale lead indicators (liens, judgments, probate,
bankruptcy, etc.).
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

URL = "https://onlineservices.miamidadeclerk.gov/officialrecords"

# Document type codes rated 8/10+ as wholesale lead indicators
HIGH_VALUE_DOC_TYPES: dict[str, str] = {
    "LIS":   "Lis Pendens",
    "NTL":   "Notice of Tax Lien",
    "LIE":   "Lien",
    "FTL":   "Federal Tax Lien",
    "PAD":   "Probate & Administration",
    "BAN":   "Bankruptcy",
    "JUD":   "Judgment",
    "LNJUD": "Any Lien Judgment",
    "CVP":   "Civil Court Paper",
    "QCD":   "Quit Claim Deed",
    "DOM":   "Dissolution of Marriage",
    "PRO":   "Probate Order of Distribution",
    "AJ":    "Affidavit with Judgment Attached",
}

_DOC_CODE_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in HIGH_VALUE_DOC_TYPES) + r")\b",
    re.IGNORECASE,
)
_FILE_NUM_RE = re.compile(r"Clerk['']s\s+File\s+Number[:\s]+([^\n,]+)", re.IGNORECASE)
_PARTY_RE    = re.compile(r"Party\s+Name\s*(.+)", re.IGNORECASE | re.DOTALL)
_DATE_RE     = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b")


def _address_slug(address: str, max_len: int = 80) -> str:
    s = re.sub(r"[^\w\-]+", "_", address.strip()).strip("_")
    return s[:max_len] if len(s) > max_len else s


@dataclass
class OfficialRecord:
    address: str
    clerks_file_number: str
    party_names: str
    recorded_date: str
    doc_type_code: Optional[str]
    doc_type_label: Optional[str]
    is_high_value: bool
    raw_text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_record(address: str, text: str) -> OfficialRecord:
    text = text.strip()

    m = _FILE_NUM_RE.search(text)
    file_num = m.group(1).strip() if m else ""

    m = _PARTY_RE.search(text)
    party = re.sub(r"\s+", " ", m.group(1).strip()) if m else ""

    m = _DATE_RE.search(text)
    date = m.group(1) if m else ""

    m = _DOC_CODE_RE.search(text)
    code: Optional[str] = None
    label: Optional[str] = None
    if m:
        code = m.group(1).upper()
        label = HIGH_VALUE_DOC_TYPES.get(code)

    return OfficialRecord(
        address=address,
        clerks_file_number=file_num,
        party_names=party,
        recorded_date=date,
        doc_type_code=code,
        doc_type_label=label,
        is_high_value=code in HIGH_VALUE_DOC_TYPES if code else False,
        raw_text=text,
    )


class OfficialRecordsScraper:
    """
    Playwright-based scraper for Miami-Dade Clerk's Official Records.

    Parameters
    ----------
    headless:
        Run Chromium without a visible window (default True).
    timeout_ms:
        Per-action timeout in milliseconds (default 30 000).
    delay_s:
        Polite pause between consecutive address searches (default 1.5 s).
    slow_mo_ms:
        Pause Playwright before every action by this many ms so you can
        watch the browser as it works (default 0 = no slowdown). Try 500.
    all_records:
        When False (default) only return high-value lead records.
        When True return every result regardless of doc type.
    results_html_dir:
        If set, after each search the full results page HTML is written to
        ``<dir>/official_records_<n>_<address_slug>.html`` for offline analysis.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        delay_s: float = 1.5,
        slow_mo_ms: int = 0,
        all_records: bool = False,
        results_html_dir: Optional[Path] = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.delay_s = delay_s
        self.slow_mo_ms = slow_mo_ms
        self.all_records = all_records
        self.results_html_dir = results_html_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _setup_page(self, page: Page) -> None:
        """Navigate to the site and click the Property/Condo search tab."""
        page.goto(URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
        # The site may show a splash / standard-search landing first
        try:
            page.get_by_role("button", name=re.compile(r"Standard Search", re.I)).click(timeout=5_000)
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass  # button may not exist — already on search page

        page.get_by_role("button", name="Property/Condo").click(timeout=self.timeout_ms)
        page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        # React SPA: wait until the Property/Condo address field is mounted
        page.locator("#addressNoUnit").wait_for(state="visible", timeout=self.timeout_ms)

    def _select_doc_types(self, page: Page) -> None:
        """
        Attempt to select all high-value document types in the Document Type
        field.  Silently skipped if the control cannot be found.
        """
        doc_type_codes = list(HIGH_VALUE_DOC_TYPES.keys())

        # Try a <select multiple> first
        try:
            sel = page.locator("select").filter(has_text=re.compile(r"LIS|NTL|LIE|FTL|BAN|JUD", re.I))
            if sel.count():
                sel.first.select_option(doc_type_codes)
                return
        except Exception:
            pass

        # Try an autocomplete / combobox labelled "Document Type"
        try:
            combo = page.get_by_role("combobox", name=re.compile(r"Document Type", re.I))
            if combo.count():
                for code in doc_type_codes:
                    combo.fill(code)
                    page.keyboard.press("Enter")
                    time.sleep(0.3)
                return
        except Exception:
            pass

    def _wait_for_results_ui(self, page: Page) -> "str":
        """
        The Official Records UI is a React SPA: clicking Search does not trigger
        a full navigation, so domcontentloaded fires while the form is still
        showing. The real results view renders distinct UI ("SEARCH RESULTS",
        "<n> RESULTS RETURNED", "Download CSV", and cards labelled
        "Clerk's File Number: <value>, Group: <n>").

        Returns "results" if a populated results view rendered, "empty" if the
        no-results toast appeared, or "unknown" if neither was confirmed before
        timeout (we still let the caller proceed and snapshot whatever exists).
        """
        empty_re = re.compile(
            r"no\s+results\s+found|no\s+matching\s+record|0\s+results?\s+returned",
            re.IGNORECASE,
        )
        results_re = re.compile(
            r"Clerk['\u2019]s\s+File\s+Number\s*:\s*\S|Results?\s+Returned|Download\s+CSV",
            re.IGNORECASE,
        )
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

        # Let spinners / recaptcha / late row renders settle once the view appears
        try:
            page.wait_for_load_state("networkidle", timeout=min(15_000, self.timeout_ms))
        except Exception:
            pass
        return outcome

    def _do_search(self, page: Page, address: str) -> None:
        """Fill address and hit Search."""
        addr_box = page.locator("#addressNoUnit")
        addr_box.wait_for(state="visible", timeout=self.timeout_ms)
        addr_box.click(timeout=self.timeout_ms)
        # The form is a React controlled input; .fill() sometimes assigns the
        # value without firing the input event the React state listens to,
        # which causes the submit handler to use empty state and the API to
        # return "No results found". Type keystroke-by-keystroke and Tab off
        # so the change/blur handlers definitely run.
        addr_box.fill("")
        addr_box.press_sequentially(address, delay=40)
        addr_box.press("Tab")
        # Document-type filters must run on the form before submit (not on
        # results). Skip them entirely when callers want every record.
        if not self.all_records:
            self._select_doc_types(page)
        # Submit button is type="submit" inside the Property/Condo form (not always
        # matched reliably by get_by_role strict name matching).
        submit = page.locator(".search-form button[type='submit']").filter(
            has_text=re.compile(r"Search", re.I)
        )
        submit.click(timeout=self.timeout_ms)
        outcome = self._wait_for_results_ui(page)
        if outcome == "empty":
            print(
                "[official_records]   (no results returned by site)",
                file=sys.stderr,
                flush=True,
            )
        elif outcome == "unknown":
            print(
                "[official_records]   WARNING: results view not detected before "
                "timeout; saving whatever rendered.",
                file=sys.stderr,
                flush=True,
            )

    def _parse_results(self, page: Page, address: str) -> list[OfficialRecord]:
        """Scrape all result items from the current page."""
        records: list[OfficialRecord] = []

        # Results appear as clickable items containing "Clerk's File Number"
        items = page.locator(
            "xpath=//*[contains(text(),'File Number') or contains(text(),'File Number')]"
            "/ancestor-or-self::*[self::a or self::li or self::tr or self::div][1]"
        ).all()

        if not items:
            # Broader fallback: any element whose text contains the file number pattern
            items = page.locator("text=Clerk").all()

        seen: set[str] = set()
        for el in items:
            try:
                text = el.inner_text(timeout=5_000)
            except Exception:
                continue

            if "File Number" not in text and "File Number" not in text:
                continue

            # Deduplicate by file number
            m = _FILE_NUM_RE.search(text)
            key = m.group(1).strip() if m else text[:60]
            if key in seen:
                continue
            seen.add(key)

            rec = _extract_record(address, text)
            if self.all_records or rec.is_high_value:
                records.append(rec)

        return records

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_one(
        self,
        page: Page,
        address: str,
        *,
        search_index: int = 1,
    ) -> list[OfficialRecord]:
        """Search a single address on an already-set-up page."""
        self._do_search(page, address)
        if self.results_html_dir is not None:
            self.results_html_dir.mkdir(parents=True, exist_ok=True)
            slug = _address_slug(address) or "address"
            path = self.results_html_dir / (
                f"official_records_{search_index:03d}_{slug}.html"
            )
            path.write_text(page.content(), encoding="utf-8")
            print(
                f"[official_records] Saved results page HTML → {path}",
                file=sys.stderr,
                flush=True,
            )
        return self._parse_results(page, address)

    def run(self, addresses: list[str]) -> list[OfficialRecord]:
        """
        Run searches for every address and return all matching records.

        Opens a single browser session and reuses the page for each address.
        """
        addresses = [a.strip() for a in addresses if a.strip()]
        all_records: list[OfficialRecord] = []

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
                print(f"[official_records] Navigating to {URL}", file=sys.stderr)
                self._setup_page(page)

                total = len(addresses)
                for i, address in enumerate(addresses, start=1):
                    print(
                        f"[official_records] [{i}/{total}] Searching: {address}",
                        file=sys.stderr,
                        flush=True,
                    )
                    try:
                        records = self.search_one(page, address, search_index=i)
                        hv = sum(1 for r in records if r.is_high_value)
                        print(
                            f"[official_records]   → {len(records)} record(s) found "
                            + (f"({hv} high-value)" if not self.all_records else ""),
                            file=sys.stderr,
                            flush=True,
                        )
                        all_records.extend(records)
                    except Exception as exc:
                        print(
                            f"[official_records]   ERROR: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )

                    if i < total:
                        time.sleep(self.delay_s)

            finally:
                browser.close()

        return all_records
