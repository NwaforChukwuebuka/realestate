# Miami-Dade Property Research — Scrapers

Three Playwright-based scrapers for wholesale real estate lead research in Miami-Dade County.

---

## Requirements

```bash
pip install playwright
playwright install chromium
```

---

## 1. Property Appraiser

**Source:** `https://apps.miamidadepa.gov/PropertySearch/#/?folio={folio}`

Looks up ownership, property details, and sale history by folio number. Derives:
- `absentee_owner` — mailing address differs from property address
- `trust_or_llc_owner` — owner name ends with LLC, TR, or LTD
- `quality_lead` — absentee individual owner (no LLC / TR / LTD)

### Commands

```bash
# Single folio (dashes optional)
python -m property_appraiser 0101010501080
python -m property_appraiser 01-0101-050-1080

# Multiple folios from a text file (one per line)
python -m property_appraiser --file folios.txt

# From a CSV column
python -m property_appraiser --csv pipeline.csv --folio-col folio_number

# Save output
python -m property_appraiser --out results.json 0101010501080
python -m property_appraiser --out results.csv  0101010501080

# Run headless (no browser window)
python -m property_appraiser --headless 0101010501080

# Save raw HTML snapshot for debugging selectors
python -m property_appraiser --save-html ./html_dumps 0101010501080
```

### Output fields

| Field | Description |
|---|---|
| `folio` | Normalized folio (no dashes) |
| `property_address` | Physical property address |
| `owner_name` | Owner name as recorded |
| `mailing_address` | Owner mailing address |
| `subdivision` | Subdivision name |
| `pa_primary_zone` | Zoning code and description |
| `primary_land_use` | Land use code and description |
| `beds_baths_half` | Beds / baths / half-baths |
| `floors` | Number of floors |
| `living_units` | Number of living units |
| `actual_area` | Total area (sq ft) |
| `living_area` | Living area (sq ft) |
| `adjusted_area` | Adjusted area (sq ft) |
| `lot_size` | Lot size (sq ft) |
| `year_built` | Year built |
| `previous_sale_date` | Most recent sale date |
| `previous_sale_price` | Most recent sale price |
| `absentee_owner` | `true` if mailing ≠ property address |
| `trust_or_llc_owner` | `true` if LLC / TR / LTD in owner name |
| `quality_lead` | `true` if absentee individual owner |

### Options

| Flag | Default | Description |
|---|---|---|
| `--folio-col COL` | `folio_number` | Column name when using `--csv` |
| `--out PATH` | stdout | Output file (`.json` or `.csv`) |
| `--headless` | off | Run without a visible browser window |
| `--delay SEC` | `1.5` | Seconds between searches |
| `--timeout MS` | `30000` | Per-action timeout |
| `--slow-mo MS` | `0` | Pause before every action (try `500` to watch) |
| `--save-html DIR` | off | Save page HTML after each search |

---

## 2. Regulation Cases (RER)

**Source:** `https://www.miamidade.gov/Apps/RER/RegulationSupportWebViewer/`

Searches Miami-Dade code enforcement cases. Strong lead indicators:
unsafe structures, expired permits, work without permits, open code violations,
abandoned property, large fines, repeat violations.

### Search modes

| Mode | Tab | Input |
|---|---|---|
| `folio` (default) | Folio Number | Folio number e.g. `3021350210590` |
| `address` | Address | Property address e.g. `14225 SW 272 ST` |
| `owner` | Owner Name | Owner name e.g. `Jose Broche` |

> **Owner searches return all cases** (not filtered to strong leads). Folio and address searches return only strong-lead cases by default; use `--all-records` to return everything.

### Commands

```bash
# By folio number (default)
python -m regulation_cases 3021350210590

# By property address
python -m regulation_cases --by address "14225 SW 272 ST"

# By owner name
python -m regulation_cases --by owner "Jose Broche"

# Multiple values from a text file
python -m regulation_cases --file folios.txt
python -m regulation_cases --by address --file addresses.txt

# From a CSV column
python -m regulation_cases --csv pipeline.csv --folio-col folio_number
python -m regulation_cases --by address --csv pipeline.csv --folio-col property_address

# Return every case row (not just strong leads)
python -m regulation_cases --all-records 3021350210590

# Save output
python -m regulation_cases --out results.json 3021350210590
python -m regulation_cases --out results.csv  3021350210590

# Run headless
python -m regulation_cases --headless 3021350210590

# Save raw HTML snapshot for debugging
python -m regulation_cases --save-html ./html_dumps 3021350210590
```

### Output fields

| Field | Description |
|---|---|
| `search_input` | Folio / address / owner name used |
| `case_number` | RER case number |
| `case_type` | Type of violation |
| `address` | Property address on the case |
| `owner_name` | Owner name on the case |
| `violator` | Violator name |
| `folio_number` | Folio as displayed on the case |
| `is_strong_lead` | `true` if case type matches a wholesale indicator |
| `count` | Case count (owner search only) |
| `permit` | Permit number (owner search only) |
| `ticket` | Ticket number (owner search only) |

### Options

| Flag | Default | Description |
|---|---|---|
| `--by MODE` | `folio` | Search mode: `folio`, `address`, or `owner` |
| `--folio-col COL` | `folio_number` | Column name when using `--csv` |
| `--all-records` | off | Return all cases, not just strong leads |
| `--out PATH` | stdout | Output file (`.json` or `.csv`) |
| `--headless` | off | Run without a visible browser window |
| `--delay SEC` | `1.5` | Seconds between searches |
| `--timeout MS` | `30000` | Per-action timeout |
| `--slow-mo MS` | `0` | Pause before every action (try `500` to watch) |
| `--save-html DIR` | off | Save page HTML after each search |

---

## 3. Official Records (Clerk)

**Source:** `https://onlineservices.miamidadeclerk.gov/officialrecords`

Searches Miami-Dade Clerk's Official Records for wholesale lead document types:
Lis Pendens, Tax Liens, Bankruptcy, Judgments, Quit Claim Deeds,
Probate, Dissolution of Marriage, and more.

### Commands

```bash
# Single address (words as positional args)
python -m official_records 243 NW 10 ST

# Multiple addresses from a text file
python -m official_records --file addresses.txt

# From a CSV column
python -m official_records --csv pipeline.csv --address-col property_address

# Headerless CSV (address is the only column)
python -m official_records --csv addresses.csv --csv-no-header

# Return all document rows (not just wholesale indicators)
python -m official_records --all-records 243 NW 10 ST

# Return only high-value rows even when using --all-records
python -m official_records --all-records --high-value-only 243 NW 10 ST

# Merge input CSV columns onto each output row
python -m official_records --csv pipeline.csv --address-col property_address --merge-csv

# Save output
python -m official_records --out results.json 243 NW 10 ST
python -m official_records --out results.csv  243 NW 10 ST

# Run headless
python -m official_records --headless 243 NW 10 ST

# Save raw HTML snapshot for debugging
python -m official_records --save-html ./html_dumps 243 NW 10 ST
```

### Output fields

| Field | Description |
|---|---|
| `address` | Address used for the search |
| `clerks_file_number` | Clerk's instrument file number |
| `party_names` | Party names on the instrument |
| `recorded_date` | Date the instrument was recorded |
| `doc_type_code` | Short document type code (e.g. `LIS`, `NTL`) |
| `doc_type_label` | Human-readable document type label |
| `is_high_value` | `true` if document type is a wholesale indicator |
| `raw_text` | Full raw text extracted from the result card |

### High-value document types

| Code | Label |
|---|---|
| `LIS` | Lis Pendens |
| `NTL` | Notice of Tax Lien |
| `LIE` | Lien |
| `FTL` | Federal Tax Lien |
| `PAD` | Probate & Administration |
| `BAN` | Bankruptcy |
| `JUD` | Judgment |
| `LNJUD` | Any Lien Judgment |
| `CVP` | Civil Court Paper |
| `QCD` | Quit Claim Deed |
| `DOM` | Dissolution of Marriage |
| `PRO` | Probate Order of Distribution |
| `AJ` | Affidavit with Judgment Attached |

### Options

| Flag | Default | Description |
|---|---|---|
| `--address-col COL` | `property_address` | Column name when using `--csv` |
| `--csv-no-header` | off | Treat CSV as headerless; address is column 0 |
| `--csv-column N` | `0` | Column index when using `--csv-no-header` |
| `--all-records` | off | Return every document row, not just high-value |
| `--high-value-only` | off | Filter to high-value rows after `--all-records` |
| `--merge-csv` | off | Append input CSV columns to each output row |
| `--out PATH` | stdout | Output file (`.json` or `.csv`) |
| `--headless` | off | Run without a visible browser window |
| `--delay SEC` | `1.5` | Seconds between searches |
| `--timeout MS` | `30000` | Per-action timeout |
| `--slow-mo MS` | `0` | Pause before every action (try `500` to watch) |
| `--save-html DIR` | off | Save page HTML after each search |

---

## 4. Research Pipeline (`research`)

Combines all three scrapers into one automated pipeline. Reads candidate folios from `.munroll_raw.sqlite` (`properties_normalized` table), runs Property Appraiser → Regulation Cases → Official Records for each folio, scores the result, and writes findings back to a `research_results` table in the same database.

### Full flow

```
.munroll_raw.sqlite
  └─ properties_normalized   ← 419k normalized parcel rows
  └─ property_prescores      ← fast pre-scores (no browser)
  └─ research_results        ← per-parcel scraper output
```

**Step 1 — Pre-score all properties (no browser, runs in seconds)**

```bash
python -m research --pre-score
```

Scores every row in `properties_normalized` using DB-only signals (absentee ownership, years owned, old property, trust/LLC) and stores results in `property_prescores`. Run this once after importing your data.

**Step 2 — (Optional) Explore pre-scored leads before scraping**

```bash
# Export everything with a pre-score ≥ 1
python -m research --export-scores --out all_leads.csv

# Only strong leads (score ≥ 2)
python -m research --export-scores --min-score 2 --out strong_leads.csv

# Filter to one category
python -m research --export-scores --category motivated_sellers --out motivated.csv
python -m research --export-scores --category distressed_ownership --out distressed.csv
```

**Step 3 — Check how many candidates match your filters**

```bash
python -m research --db --count-only
python -m research --db --count-only --absentee-only --min-years 10
```

**Step 4 — Run the full 3-scraper pipeline**

```bash
# Top 50 absentee owners not yet researched
python -m research --db --absentee-only --limit 50

# Absentee + old property, save results to CSV
python -m research --db --absentee-only --old-property --limit 100 --out opps.csv

# Owned 15+ years, headless, 4 parallel workers, logs saved
python -m research --db --min-years 15 --workers 4 --headless --log-dir logs/

# Re-research parcels that already have a result
python -m research --db --absentee-only --limit 20 --rerun
```

Each folio is saved to `research_results` immediately after it is processed (crash-safe). Already-researched parcels are skipped automatically unless `--rerun` is passed.

**Step 5 — Export wholesale opportunities**

```bash
# Print to stdout
python -m research --db --export-opportunities

# Save to file
python -m research --db --export-opportunities --out opportunities.csv
python -m research --db --export-opportunities --out opportunities.json
```

### Ad-hoc / non-DB usage

```bash
# Single folio
python -m research 0101010501080

# Multiple folios from a file
python -m research --file folios.txt

# From a CSV column
python -m research --csv pipeline.csv --folio-col folio_number
```

### Output fields (flat CSV / `--out *.csv`)

| Field | Description |
|---|---|
| `folio` | Parcel ID |
| `property_address` | Physical address |
| `owner_name` | Owner as recorded |
| `mailing_address` | Owner mailing address |
| `year_built` | Year built |
| `last_sale_date` | Most recent sale date |
| `last_sale_price` | Most recent sale price |
| `absentee_owner` | Mailing ≠ property address |
| `trust_or_llc` | LLC / TR / LTD in owner name |
| `quality_lead` | Absentee individual owner |
| `rer_found_by` | RER search method used (`folio` / `address` / `owner` / `none`) |
| `regulation_cases` | Total RER cases found |
| `strong_violations` | Cases flagged as strong lead indicators |
| `official_records` | Total official record documents found |
| `high_value_doc_types` | Comma-separated high-value doc type codes |
| `triggered_signals` | Pipe-separated signal names |
| `categories` | Pipe-separated category names |
| `lead_score` | Number of triggered signals |
| `is_wholesale_opportunity` | `True` if `lead_score ≥ 2` |

### `--db` options

| Flag | Default | Description |
|---|---|---|
| `--absentee-only` | off | Only absentee owners |
| `--old-property` | off | Only `old_property = 1` |
| `--out-of-state` | off | Only out-of-state owners |
| `--min-years N` | `0` | Only properties owned ≥ N years |
| `--limit N` | none | Max folios to process |
| `--offset N` | `0` | Skip first N rows (resume a batch) |
| `--rerun` | off | Re-research already-processed parcels |
| `--count-only` | off | Print matching candidate count and exit |
| `--export-opportunities` | off | Export wholesale opportunities and exit |

### Browser / worker options

| Flag | Default | Description |
|---|---|---|
| `--workers N` | `1` | Parallel browser workers |
| `--log-dir DIR` | none | Directory for per-worker log files |
| `--headless` | off | Run without a visible browser window |
| `--delay SEC` | `1.5` | Seconds between searches (single-worker) |
| `--timeout MS` | `30000` | Per-action timeout |
| `--slow-mo MS` | `0` | Pause before every action |
| `--save-html DIR` | off | Save page HTML snapshots for debugging |
