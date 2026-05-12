"""
Miami-Dade Official Records scraper.

Runs one broad Property/Condo search per address (no per-document-type filter),
scrapes every row visible on the results listing (card fields only — no
opening individual instruments), and flags wholesale lead indicators from the
``Document Type`` text on each card.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Locator, Page, sync_playwright

URL = "https://onlineservices.miamidadeclerk.gov/officialrecords"

# Reference: exact ``<option value>`` strings on the site for each wholesale code
# (used for documentation / cross-check with project2.md; search is broad — we
# classify leads from listing card text, not by iterating these selects).
HIGH_VALUE_WEB_OPTION_VALUES: tuple[str, ...] = (
    "LIS PENDENS - LIS",
    "NOTICE OF TAX LIEN - NTL",
    "LIEN - LIE",
    "FEDERAL TAX LIEN  - FTL",
    "PROBATE & ADMINISTRATION - PAD",
    "BANKRUPTCY  - BAN",
    "JUDGEMENT - JUD",
    "ANY LIEN JUDGMENT - LNJUD",
    "CIVIL COURT  PAPER - CVP",
    "QUIT CLAIM DEED - QCD",
    "DISSOLUTION OF MARRIAGE - DOM",
    "PROBATE ORDER OF DISTRIBUTION - PRO",
    "AFFIDAVIT WITH JUDGMENT ATTACHED - AJ",
)

# Short codes → human labels (project playbook); codes match the website suffix after " - ".
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


def _document_type_code_from_site_label(doc_type_text: str) -> Optional[str]:
    """
    Extract the clerk document-type code from the site's label, which uses
    ``FULL NAME - CODE`` (e.g. ``LIS PENDENS - LIS``). Prefer this over a
    free-text regex so phrases like "Lis Pendens" inside other titles do not
    false-match wholesale codes.
    """
    t = " ".join(doc_type_text.split()).strip()
    if not t:
        return None
    if " - " in t:
        tail = t.rsplit(" - ", 1)[-1].strip().upper()
        return tail or None
    m = _DOC_CODE_RE.search(t)
    return m.group(1).upper() if m else None


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
    # 1-based index of the search in a batch run (for merging with input CSV rows).
    search_index: int = 0

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

    code = _document_type_code_from_site_label(text)
    if code is None:
        m = _DOC_CODE_RE.search(text)
        if m:
            code = m.group(1).upper()
    label = HIGH_VALUE_DOC_TYPES.get(code) if code else None

    return OfficialRecord(
        address=address,
        clerks_file_number=file_num,
        party_names=party,
        recorded_date=date,
        doc_type_code=code,
        doc_type_label=label,
        is_high_value=bool(code and code in HIGH_VALUE_DOC_TYPES),
        raw_text=text,
        search_index=0,
    )


def _field_from_card(card: Locator, data_id: str) -> str:
    """Read a result-card value from ``<p data-id=\"…\">`` (see TitleSearchTab markup)."""
    try:
        loc = card.locator(f'p[data-id="{data_id}"]')
        if loc.count() == 0:
            return ""
        t = loc.first.inner_text(timeout=5_000)
        return " ".join(t.split()).strip()
    except Exception:
        return ""


def _record_from_result_card(address: str, card: Locator) -> Optional[OfficialRecord]:
    """
    Build an ``OfficialRecord`` from one ``.TitleSearchTab`` result card.

    The live site exposes labeled fields via ``data-id`` on ``<p>`` value
    nodes (e.g. Document Type), not only plain-text blobs or 3-letter codes.
    """
    try:
        raw_text = card.inner_text(timeout=5_000).strip()
    except Exception:
        raw_text = ""

    header = ""
    try:
        hb = card.locator("p.fs-5.fw-bold")
        if hb.count() > 0:
            header = hb.first.inner_text(timeout=5_000)
    except Exception:
        pass

    m = _FILE_NUM_RE.search(header) or _FILE_NUM_RE.search(raw_text)
    file_num = m.group(1).strip() if m else ""
    if not file_num:
        return None

    party = _field_from_card(card, "Party Name")
    recorded = _field_from_card(card, "Rec Date")
    if not recorded:
        md = _DATE_RE.search(raw_text)
        recorded = md.group(1) if md else ""

    doc_type_text = _field_from_card(card, "Document Type")
    code = _document_type_code_from_site_label(doc_type_text)
    if code is None and header:
        code = _document_type_code_from_site_label(header)
    map_label = HIGH_VALUE_DOC_TYPES.get(code) if code else None
    doc_label: Optional[str] = doc_type_text if doc_type_text else map_label

    return OfficialRecord(
        address=address,
        clerks_file_number=file_num,
        party_names=party,
        recorded_date=recorded,
        doc_type_code=code,
        doc_type_label=doc_label,
        is_high_value=bool(code and code in HIGH_VALUE_DOC_TYPES),
        raw_text=raw_text,
        search_index=0,
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
        When False (default), run one broad Property/Condo search (document type
        cleared), scrape every result card from the listing page without opening
        detail views, and return only rows whose ``Document Type`` matches a
        wholesale indicator. When True, return every listing row (same single
        search); ``is_high_value`` is still set from the card line.
    results_html_dir:
        If set, after each address search writes one HTML snapshot:
        ``official_records_<n>_<address_slug>.html``.
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
        self._last_listing_row_count = 0
        self._last_wholesale_row_count = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scrape_listing_rows_unfiltered(
        self, page: Page, address: str
    ) -> list[OfficialRecord]:
        """
        Every parsed row on the current results listing (card view), with no
        wholesale filter and no navigation into instrument detail pages.
        """
        all_rows: list[OfficialRecord] = []

        tabs = page.locator(".TitleSearchTab")
        n = tabs.count()
        seen: set[str] = set()

        for i in range(n):
            card = tabs.nth(i)
            rec = _record_from_result_card(address, card)
            if rec is None:
                continue
            if rec.clerks_file_number in seen:
                continue
            seen.add(rec.clerks_file_number)
            all_rows.append(rec)

        if n > 0:
            return all_rows

        items = page.locator(
            "xpath=//*[contains(text(),'File Number') or contains(text(),'File Number')]"
            "/ancestor-or-self::*[self::a or self::li or self::tr or self::div][1]"
        ).all()
        if not items:
            items = page.locator("text=Clerk").all()

        for el in items:
            try:
                text = el.inner_text(timeout=5_000)
            except Exception:
                continue
            if "File Number" not in text and "File Number" not in text:
                continue
            m = _FILE_NUM_RE.search(text)
            key = m.group(1).strip() if m else text[:60]
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(_extract_record(address, text))

        return all_rows

    def _goto_app_and_open_property_tab(self, page: Page) -> None:
        """Navigate to the site and open the Property/Condo search tab."""
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

    def _setup_page(self, page: Page) -> None:
        """First-load navigation (same as returning to the Property/Condo form)."""
        self._goto_app_and_open_property_tab(page)

    def _ensure_property_search_form(self, page: Page) -> None:
        """If a prior search left the results view, reload the Property/Condo form."""
        try:
            if page.locator("#addressNoUnit").is_visible(timeout=1_500):
                return
        except Exception:
            pass
        self._goto_app_and_open_property_tab(page)

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

    def _run_one_property_search(
        self,
        page: Page,
        address: str,
        doc_option_value: Optional[str],
        *,
        log_empty: bool = True,
    ) -> None:
        """
        Fill address, optionally set ``#documentType`` to a site option *value*,
        and submit. ``doc_option_value`` ``None`` clears the filter (all types).
        """
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
        try:
            page.locator("#documentType").select_option(
                value=doc_option_value or "",
                timeout=min(10_000, self.timeout_ms),
            )
        except Exception:
            pass
        submit = page.locator(".search-form button[type='submit']").filter(
            has_text=re.compile(r"Search", re.I)
        )
        submit.click(timeout=self.timeout_ms)
        outcome = self._wait_for_results_ui(page)
        if outcome == "empty" and log_empty:
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
        """
        Scrape the listing page once, then either return every row or only
        wholesale-indicator rows (``is_high_value``).
        """
        all_rows = self._scrape_listing_rows_unfiltered(page, address)
        self._last_listing_row_count = len(all_rows)
        self._last_wholesale_row_count = sum(1 for r in all_rows if r.is_high_value)
        if self.all_records:
            return all_rows
        return [r for r in all_rows if r.is_high_value]

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
        """
        One broad Property/Condo search, then bulk scrape of listing cards only
        (no opening individual instruments).
        """
        slug = _address_slug(address) or "address"
        self._ensure_property_search_form(page)
        self._run_one_property_search(page, address, None, log_empty=True)

        if self.results_html_dir is not None:
            self.results_html_dir.mkdir(parents=True, exist_ok=True)
            path = self.results_html_dir / (
                f"official_records_{search_index:03d}_{slug}.html"
            )
            path.write_text(page.content(), encoding="utf-8")
            print(
                f"[official_records] Saved results page HTML → {path}",
                file=sys.stderr,
                flush=True,
            )

        records = self._parse_results(page, address)
        for rec in records:
            rec.search_index = search_index
        n_list = self._last_listing_row_count
        n_hv = self._last_wholesale_row_count

        if self.all_records:
            print(
                f"[official_records]   → {n_list} listing row(s); "
                f"{n_hv} wholesale-indicator; returning all rows.",
                file=sys.stderr,
                flush=True,
            )
        elif not records:
            print(
                "[official_records]   → low-value lead: broad search returned "
                f"{n_list} listing row(s); none match wholesale document types.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"[official_records]   → {n_list} listing row(s); "
                f"{n_hv} wholesale-indicator; returning {len(records)} row(s).",
                file=sys.stderr,
                flush=True,
            )
        return records

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
