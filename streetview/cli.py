"""Manual smoke tests: python -m streetview (metadata) | python -m streetview images."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from streetview.images import StreetViewImageFetcher
from streetview.metadata import StreetViewMetadataClient


def _load_env() -> str | None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()
    return os.environ.get("GOOGLE_MAPS_API_KEY")


def cmd_metadata(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="python -m streetview",
        description="Street View metadata for one lat/lng (Google + SQLite cache)",
    )
    p.add_argument(
        "lat",
        nargs="?",
        type=float,
        default=25.772321,
        help="Latitude (default: 60 SE 2nd St Miami sample)",
    )
    p.add_argument(
        "lng",
        nargs="?",
        type=float,
        default=-80.192256,
        help="Longitude",
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=Path(".streetview_cache.sqlite"),
        help="SQLite cache path",
    )
    args = p.parse_args(argv)

    key = _load_env()
    if not key:
        print("Set GOOGLE_MAPS_API_KEY in the environment or .env", file=sys.stderr)
        return 1

    c = StreetViewMetadataClient(api_key=key, cache_db_path=args.cache)
    try:
        r = c.lookup(args.lat, args.lng)
    finally:
        c.close()

    out = asdict(r)
    out["ok"] = r.ok
    out["no_street_view"] = r.no_street_view
    print(json.dumps(out, indent=2))
    return 0 if r.ok else 1


def cmd_images(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="python -m streetview images",
        description=(
            "Street View metadata + Static API download (heading toward property). "
            "Default: one primary JPEG (off+000). Use --multi for all five offsets."
        ),
    )
    p.add_argument(
        "lat",
        nargs="?",
        type=float,
        default=25.772321,
        help="Property latitude (default: Miami sample)",
    )
    p.add_argument(
        "lng",
        nargs="?",
        type=float,
        default=-80.192256,
        help="Property longitude",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("streetview_images"),
        help="Directory to write pano subfolders and JPEGs",
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=Path(".streetview_cache.sqlite"),
        help="SQLite metadata cache path",
    )
    p.add_argument(
        "--multi",
        action="store_true",
        help="Download all five heading offsets (default: single off+000 image to save disk)",
    )
    args = p.parse_args(argv)

    key = _load_env()
    if not key:
        print("Set GOOGLE_MAPS_API_KEY in the environment or .env", file=sys.stderr)
        return 1

    meta = StreetViewMetadataClient(api_key=key, cache_db_path=args.cache)
    try:
        m = meta.lookup(args.lat, args.lng)
    finally:
        meta.close()

    if not m.ok:
        print(json.dumps({**asdict(m), "ok": False, "no_street_view": m.no_street_view}, indent=2))
        return 1
    assert m.pano_id is not None and m.pano_lat is not None and m.pano_lng is not None

    fetcher = StreetViewImageFetcher(api_key=key)
    heading_offsets = None if args.multi else (0,)
    result = fetcher.fetch_multi_angle_set(
        pano_id=m.pano_id,
        pano_lat=m.pano_lat,
        pano_lng=m.pano_lng,
        property_lat=args.lat,
        property_lng=args.lng,
        output_dir=args.out,
        heading_offsets=heading_offsets,
    )

    payload = {
        "metadata": {**asdict(m), "ok": m.ok, "no_street_view": m.no_street_view},
        "base_heading_deg": result.base_heading_deg,
        "fov": result.fov,
        "size": list(result.size),
        "output_dir": str(args.out.resolve()),
        "captures": [
            {
                "offset_deg": c.offset_deg,
                "heading_deg": c.heading_deg,
                "image_url": c.image_url,
                "local_path": str(c.local_path.resolve()) if c.local_path else None,
            }
            for c in result.captures
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "images":
        return cmd_images(argv[1:])
    return cmd_metadata(argv)


if __name__ == "__main__":
    raise SystemExit(main())
