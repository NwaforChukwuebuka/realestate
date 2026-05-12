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
