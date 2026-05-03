# Setup on a new machine (Windows RDP)

This guide assumes a fresh Windows session (RDP), PowerShell, and that you will copy or clone the project folder onto the machine.

## 1. Install Python

1. Install **Python 3.11 or newer** (64-bit) from [python.org](https://www.python.org/downloads/windows/).
2. During setup, enable **“Add python.exe to PATH”** (or add it manually later).
3. Open a new **PowerShell** window and confirm:

```powershell
python --version
pip --version
```

## 2. Project folder

Place the repo in a path without spaces if possible (optional but avoids quoting issues), for example:

```text
C:\work\realestate
```

```powershell
Set-Location C:\work\realestate
```

## 3. Virtual environment (recommended)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If execution policy blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

## 4. Install dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Environment variables

1. Copy the example env file:

```powershell
Copy-Item .env.example .env
```

2. Edit `.env` in the project root and set:

| Variable | Purpose |
|----------|---------|
| `GOOGLE_MAPS_API_KEY` | Geocoding API, Street View **metadata** API, Street View **Static** API (image download) |
| `OPENAI_API_KEY` | AI verification + distress scoring (`python -m pipeline run` unless you use `--stop-after-images`) |

3. **Google Cloud / Maps Platform**

   - Create or use a project with **Maps Platform** / Places products as needed.
   - Enable at least: **Geocoding API**, **Street View Static API** (metadata uses the Street View metadata endpoint; ensure billing/API access matches your Google setup).
   - Restrict the key by API where possible; the app only uses server-side HTTP calls from this machine.

4. **OpenAI**

   - Create an API key with access to a vision-capable model (default in code: `gpt-4o-mini`).

## 6. Smoke test (optional)

With the venv activated:

```powershell
python -m pytest tests -q -m "not integration"
```

Integration tests call live APIs and are skipped unless you opt in with keys/markers.

## 7. Typical data pipeline on this machine

Paths below assume you run commands from the project root with the venv activated.

1. **Import** MunRoll CSV into SQLite (adjust CSV path):

   ```powershell
   python -m ingest "C:\path\to\MunRoll - 00 RE - All Properties.csv" --db .munroll_raw.sqlite
   ```

2. **Normalize** (and optionally residential-only at insert time):

   ```powershell
   python -m normalize --db .munroll_raw.sqlite
   ```

3. **Residential filter** (if you did not use `normalize --residential-only`):

   ```powershell
   python -m filters apply --db .munroll_raw.sqlite
   ```

4. **Pipeline table** — create `property_pipeline` rows for each normalized parcel:

   ```powershell
   python -m pipeline sync --db .munroll_raw.sqlite
   ```

5. **Run geocode → Street View → verification** (start with a small limit on RDP to control cost):

   ```powershell
   python -m pipeline run --db .munroll_raw.sqlite --limit 5 --images-dir streetview_images
   ```

   Images and SQLite caches default next to the project:

   - `streetview_images\` — downloaded JPEGs (per parcel subfolders)
   - `.geocode_cache.sqlite` — geocode cache
   - `.streetview_cache.sqlite` — Street View metadata cache

   To fetch images only (no OpenAI), then verify in a second pass:

   ```powershell
   python -m pipeline run --db .munroll_raw.sqlite --limit 5 --stop-after-images
   python -m pipeline run --db .munroll_raw.sqlite --limit 5
   ```

### Inspect completed rows in SQLite (PowerShell)

From the project root, this one-liner works in **PowerShell**: the SQL is wrapped in Python `'''...'''` so you avoid nested-quote issues with `WHERE pipeline_status = 'done'`.

```powershell
python -c "import sqlite3; c=sqlite3.connect('.munroll_raw.sqlite'); q = '''SELECT parcel_id, pipeline_status, geocode_lat, geocode_lng, geocode_formatted_address, sv_pano_id, verification_json, updated_at FROM property_pipeline WHERE pipeline_status = 'done' ORDER BY updated_at DESC LIMIT 5'''; print(c.execute(q).fetchall()); c.close()"
```

Change `.munroll_raw.sqlite` if you use a different `--db` path; increase `LIMIT 5` as needed.

## 8. Disk and performance notes (RDP)

- The MunRoll CSV is very large; keep the SQLite DB and CSV on a disk with enough free space.
- Use `--limit` and batch scheduling for `pipeline run` so RDP sessions are not overloaded and API quotas stay predictable.
- SQLite uses WAL mode; avoid putting the database on a very slow network share if possible.

## 9. Troubleshooting

| Issue | What to check |
|--------|----------------|
| `python` not found | PATH, or use `py -3` launcher on Windows |
| `GOOGLE_MAPS_API_KEY` errors | Key set in `.env`, APIs enabled, billing/quota |
| `OPENAI_API_KEY` errors | Key set; model name matches your account (`--openai-model` on `pipeline run`) |
| Pipeline says database not found | Run ingest/normalize first; `--db` points at the real `.sqlite` path |
| `python -m pipeline` only shows help | Use an explicit subcommand: `python -m pipeline sync` or `python -m pipeline run` |

## 10. Deactivate venv

```powershell
deactivate
```
