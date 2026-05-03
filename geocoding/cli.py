"""Manual smoke test: python -m geocoding (needs GOOGLE_MAPS_API_KEY)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from geocoding.geocoder import Geocoder


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        print("Set GOOGLE_MAPS_API_KEY in the environment or .env", file=sys.stderr)
        return 1

    p = argparse.ArgumentParser(description="Geocode one address (Google + SQLite cache)")
    p.add_argument("street", nargs="?", default="200 S Miami Ave")
    p.add_argument("city", nargs="?", default="Miami")
    p.add_argument("--state", default="FL")
    p.add_argument("--zip", default="33130-0000")
    p.add_argument(
        "--cache",
        type=Path,
        default=Path(".geocode_cache.sqlite"),
        help="SQLite cache path",
    )
    args = p.parse_args(argv)

    g = Geocoder(api_key=key, cache_db_path=args.cache)
    try:
        r = g.geocode(args.street, args.city, args.state, args.zip)
    finally:
        g.close()

    print(json.dumps(asdict(r), indent=2))
    return 0 if r.ok or r.api_status == "CACHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
