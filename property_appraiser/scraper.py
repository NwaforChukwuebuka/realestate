"""
Miami-Dade Property Appraiser scraper.

Navigates directly to the PropertySearch SPA result page for each folio:
    https://apps.miamidadepa.gov/PropertySearch/#/?folio={folio}

Extracts ownership, property details, assessment values, and sale history,
then derives absentee-owner and trust/LLC signals.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

DIRECT_URL = "https://apps.miamidadepa.gov/PropertySearch/#/?folio={folio}"

# Owner name keywords that signal non-individual ownership
_ENTITY_RE = re.compile(r"\b(LLC|TR|LTD)\b", re.IGNORECASE)



def _normalize_folio(folio: str) -> str:
    """Strip dashes and spaces so '01-0101-060-1200' → '0101010601200'."""
    return re.sub(r"[\s\-]", "", folio.strip())


def _strip(text: str) -> str:
    return " ".join(text.split()).strip()


def _detect_absentee(property_address: str, mailing_address: str) -> bool:
    """True when the mailing address does not contain the property street."""
    if not property_address or not mailing_address:
        return False
    # Compare the street number + name portion of the property address against
    # the full mailing address string (both uppercased, normalised whitespace).
    prop = _strip(property_address).upper()
    mail = _strip(mailing_address).upper()
    return prop not in mail


def _detect_entity(owner_name: str) -> bool:
    return bool(_ENTITY_RE.search(owner_name))


@dataclass
class PropertyRecord:
    folio: str
    # ownership
    property_address: str
    owner_name: str
    mailing_address: str
    subdivision: str
    # property details
    pa_primary_zone: str
    primary_land_use: str
    beds_baths_half: str
    floors: str
    living_units: str
    actual_area: str
    living_area: str
    adjusted_area: str
    lot_size: str
    year_built: str
    # sale history
    previous_sale_date: str
    previous_sale_price: str
    # derived signals
    absentee_owner: bool
    trust_or_llc_owner: bool
    quality_lead: bool       # individual (no LLC/TR/LTD) absentee owner
    search_index: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _wait_for_spa(page: Page, timeout_ms: int) -> bool:
    """
    Wait for the Angular SPA to finish rendering.
    Returns True if the property info component became visible, False on timeout.
    """
    try:
        page.locator("pa-propertyinformation").wait_for(
            state="visible", timeout=timeout_ms
        )
        # Extra settle for lazy-loaded assessment / sales tables
        page.wait_for_load_state("networkidle", timeout=min(15_000, timeout_ms))
        return True
    except Exception:
        return False


def _component_text(page: Page, selector: str) -> str:
    try:
        return page.locator(selector).first.inner_text(timeout=8_000)
    except Exception:
        return ""


def _cell_value(page: Page, header_pattern: str) -> str:
    """
    Find a table cell whose text starts with ``header_pattern`` and return
    the trailing value portion (everything after the matched prefix).
    Works for cells like 'PA Primary Zone 6106'.
    """
    pat = re.compile(header_pattern, re.IGNORECASE)
    for cell in page.locator("td, th").all():
        try:
            t = _strip(cell.inner_text(timeout=3_000))
        except Exception:
            continue
        m = pat.match(t)
        if m:
            return _strip(t[m.end():])
    return ""


def _parse_prop_info(text: str) -> dict[str, str]:
    """
    Parse the ``pa-propertyinformation`` inner text into a field dict.

    The block typically looks like (exact whitespace varies):

        Folio:
        01-0101-060-1200
        Sub-Division:
        SOME SUBDIVISION NAME
        Property Address
        155 NW 10 ST
        Owner
        OWNER NAME LINE 1
        OWNER NAME LINE 2
        Mailing Address
        123 MAIN ST MIAMI, FL 33101
        ...
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    def _between(start_re: str, end_res: list[str]) -> str:
        """Return all lines between the first line matching start_re and the
        first line matching any pattern in end_res."""
        pat = re.compile(start_re, re.IGNORECASE)
        ends = [re.compile(e, re.IGNORECASE) for e in end_res]
        collecting = False
        collected: list[str] = []
        for ln in lines:
            if not collecting:
                if pat.search(ln):
                    collecting = True
                continue
            if any(e.search(ln) for e in ends):
                break
            collected.append(ln)
        return " ".join(collected).strip()

    subdivision = _between(
        r"Sub-?Division",
        [r"Property\s+Address", r"Owner\b", r"Folio"],
    )
    prop_addr = _between(
        r"Property\s+Address",
        [r"^Owner\s*$", r"^Owner\b", r"Mailing", r"PA\s+Primary"],
    )
    owner = _between(
        r"^Owner\s*$",
        [r"Mailing", r"PA\s+Primary", r"Sub-?Division"],
    )
    mailing = _between(
        r"Mailing\s+Address",
        [r"PA\s+Primary", r"Beds", r"Year\s+Built", r"^Folio"],
    )

    # Year Built is sometimes inline in the property-details table section
    year_built = ""
    yb_re = re.compile(r"Year\s+Built\s+(\d{4})", re.IGNORECASE)
    for ln in lines:
        m = yb_re.search(ln)
        if m:
            year_built = m.group(1)
            break

    # Area / lot fields (inline in same table section)
    def _inline(label_re: str) -> str:
        pat = re.compile(label_re + r"\s+([\d,\.]+)", re.IGNORECASE)
        for ln in lines:
            m = pat.search(ln)
            if m:
                return m.group(1)
        return ""

    actual_area   = _inline(r"Actual\s+Area")
    living_area   = _inline(r"Living\s+Area")
    adjusted_area = _inline(r"Adjusted\s+Area")
    lot_size      = _inline(r"Lot\s+Size")

    return {
        "subdivision":    subdivision,
        "property_address": prop_addr,
        "owner_name":     owner,
        "mailing_address": mailing,
        "year_built":     year_built,
        "actual_area":    actual_area,
        "living_area":    living_area,
        "adjusted_area":  adjusted_area,
        "lot_size":       lot_size,
    }


def _parse_sales(page: Page) -> dict[str, str]:
    """
    Extract the most-recent sale date and price from the sales history section.
    The table has columns: Price | Date  (or Date | Price — order varies).
    """
    result = {"previous_sale_date": "", "previous_sale_price": ""}

    # Find a table that has both a price cell ($xxx) and a date cell (dd/mm/yyyy)
    date_re  = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
    price_re = re.compile(r"\$[\d,]+")

    for table in page.locator("table").all():
        try:
            rows = table.locator("tr").all()
        except Exception:
            continue
        for row in rows:
            try:
                cells = row.locator("td").all()
            except Exception:
                continue
            texts = []
            for c in cells:
                try:
                    texts.append(_strip(c.inner_text(timeout=3_000)))
                except Exception:
                    texts.append("")
            row_text = " ".join(texts)
            date_m  = date_re.search(row_text)
            price_m = price_re.search(row_text)
            if date_m and price_m:
                result["previous_sale_date"]  = date_m.group()
                result["previous_sale_price"] = price_m.group()
                return result

    return result


# ---------------------------------------------------------------------------
# Public scraper
# ---------------------------------------------------------------------------

class PropertyAppraiserScraper:
    """
    Playwright-based scraper for Miami-Dade Property Appraiser property records.

    Parameters
    ----------
    headless:
        Run Chromium without a visible window (default False).
    timeout_ms:
        Per-action timeout in milliseconds (default 30 000).
    delay_s:
        Pause between consecutive folio searches (default 1.5 s).
    slow_mo_ms:
        Pause Playwright before every action (default 0). Try 500 to watch.
    results_html_dir:
        If set, saves one HTML snapshot per folio:
        ``property_appraiser_<n>_<folio>.html``.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        delay_s: float = 1.5,
        slow_mo_ms: int = 0,
        results_html_dir: Optional[Path] = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.delay_s = delay_s
        self.slow_mo_ms = slow_mo_ms
        self.results_html_dir = results_html_dir

    def _extract_one(self, page: Page, raw_folio: str, search_index: int) -> Optional[PropertyRecord]:
        folio = _normalize_folio(raw_folio)
        url   = DIRECT_URL.format(folio=folio)

        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        rendered = _wait_for_spa(page, self.timeout_ms)

        if not rendered:
            print(
                f"[property_appraiser]   WARNING: SPA did not render for {folio}",
                file=sys.stderr, flush=True,
            )

        if self.results_html_dir is not None:
            self.results_html_dir.mkdir(parents=True, exist_ok=True)
            path = self.results_html_dir / f"property_appraiser_{search_index:03d}_{folio}.html"
            path.write_text(page.content(), encoding="utf-8")
            print(f"[property_appraiser] Saved HTML → {path}", file=sys.stderr, flush=True)

        # --- Property info component ---
        prop_text = _component_text(page, "pa-propertyinformation")
        prop      = _parse_prop_info(prop_text)

        # --- Property details table (beds, floors, zones, etc.) ---
        pa_zone   = _cell_value(page, r"PA\s+Primary\s+Zone")
        land_use  = _cell_value(page, r"Primary\s+Land\s+Use")
        beds_baths = _cell_value(page, r"Beds\s*/\s*Baths")
        floors    = _cell_value(page, r"^Floors")
        l_units   = _cell_value(page, r"Living\s+Units")

        # Area / lot fields may be in table cells too — fill in if not parsed above
        actual_area   = prop["actual_area"]   or _cell_value(page, r"Actual\s+Area")
        living_area   = prop["living_area"]   or _cell_value(page, r"Living\s+Area")
        adjusted_area = prop["adjusted_area"] or _cell_value(page, r"Adjusted\s+Area")
        lot_size      = prop["lot_size"]      or _cell_value(page, r"Lot\s+Size")

        # --- Sales history ---
        sales = _parse_sales(page)

        owner_name       = prop["owner_name"]
        property_address = prop["property_address"]
        mailing_address  = prop["mailing_address"]

        record = PropertyRecord(
            folio=folio,
            property_address=property_address,
            owner_name=owner_name,
            mailing_address=mailing_address,
            subdivision=prop["subdivision"],
            pa_primary_zone=pa_zone,
            primary_land_use=land_use,
            beds_baths_half=beds_baths,
            floors=floors,
            living_units=l_units,
            actual_area=actual_area,
            living_area=living_area,
            adjusted_area=adjusted_area,
            lot_size=lot_size,
            year_built=prop["year_built"],
            previous_sale_date=sales["previous_sale_date"],
            previous_sale_price=sales["previous_sale_price"],
            absentee_owner=_detect_absentee(property_address, mailing_address),
            trust_or_llc_owner=_detect_entity(owner_name),
            quality_lead=_detect_absentee(property_address, mailing_address) and not _detect_entity(owner_name),
            search_index=search_index,
        )

        print(
            f"[property_appraiser]   owner={owner_name!r} "
            f"absentee={record.absentee_owner} entity={record.trust_or_llc_owner} "
            f"quality_lead={record.quality_lead} "
            f"sale={sales['previous_sale_date']} @ {sales['previous_sale_price']}",
            file=sys.stderr, flush=True,
        )
        return record

    def run(self, folios: list[str]) -> list[PropertyRecord]:
        """Search every folio and return all records."""
        folios = [f.strip() for f in folios if f.strip()]
        records: list[PropertyRecord] = []

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

            total = len(folios)
            try:
                for i, folio in enumerate(folios, start=1):
                    print(
                        f"[property_appraiser] [{i}/{total}] Folio: {folio}",
                        file=sys.stderr, flush=True,
                    )
                    try:
                        rec = self._extract_one(page, folio, i)
                        if rec:
                            records.append(rec)
                    except Exception as exc:
                        print(
                            f"[property_appraiser]   ERROR: {exc}",
                            file=sys.stderr, flush=True,
                        )
                    if i < total:
                        time.sleep(self.delay_s)
            finally:
                browser.close()

        return records
