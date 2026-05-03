"""Pipeline CLI: ``sync`` (DB schema + rows) and ``run`` (geocode → Street View → verify)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _cmd_sync(args: argparse.Namespace) -> int:
    if not args.db.is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1

    from pipeline.store import PipelineStore

    with PipelineStore(args.db) as store:
        store.init_schema()
        inserted = 0 if args.schema_only else store.sync_from_normalized()
        total = store.count_rows()
        by_status = store.count_by_status()

    print("rows_inserted_this_sync", inserted)
    print("pipeline_rows", total)
    print("by_status", by_status)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.db.is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()

    google_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not google_key:
        print("Set GOOGLE_MAPS_API_KEY (env or .env)", file=sys.stderr)
        return 1

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not args.stop_after_images and not openai_key:
        print("Set OPENAI_API_KEY or use --stop-after-images", file=sys.stderr)
        return 1

    from pipeline.runner import run_pending_batch

    stats = run_pending_batch(
        db_path=args.db,
        google_api_key=google_key,
        openai_api_key=openai_key,
        limit=args.limit,
        images_root=args.images_dir,
        geocode_cache=args.geocode_cache,
        streetview_cache=args.streetview_cache,
        openai_model=args.openai_model,
        stop_after_images=args.stop_after_images,
    )

    print(
        "attempted",
        stats.attempted,
        "done",
        stats.done,
        "failed",
        stats.failed,
        "skipped_no_street_view",
        stats.skipped_no_street_view,
        "stopped_after_images",
        stats.stopped_after_images,
        "not_pending",
        stats.not_pending,
    )
    if stats.errors:
        print("errors", len(stats.errors), file=sys.stderr)
        for line in stats.errors[:20]:
            print(line, file=sys.stderr)
    return 0 if stats.failed == 0 and not stats.errors else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Property pipeline: sync SQLite state, then run geocode / Street View / AI.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sync_p = sub.add_parser(
        "sync",
        help="Create ``property_pipeline`` and insert missing parcel_id rows from normalized",
    )
    sync_p.add_argument(
        "--db",
        type=Path,
        default=Path(".munroll_raw.sqlite"),
        help="SQLite database path",
    )
    sync_p.add_argument(
        "--schema-only",
        action="store_true",
        help="Create table/indexes only; do not insert from normalized",
    )
    sync_p.set_defaults(_handler=_cmd_sync)

    run_p = sub.add_parser(
        "run",
        help="Process pending parcels (Google Geocoding, Street View, OpenAI verification)",
    )
    run_p.add_argument(
        "--db",
        type=Path,
        default=Path(".munroll_raw.sqlite"),
        help="SQLite database path",
    )
    run_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max pending parcels to process in this invocation",
    )
    run_p.add_argument(
        "--images-dir",
        type=Path,
        default=Path("streetview_images"),
        help="Root directory for per-parcel Street View JPEG folders",
    )
    run_p.add_argument(
        "--geocode-cache",
        type=Path,
        default=Path(".geocode_cache.sqlite"),
        help="SQLite path for geocode cache",
    )
    run_p.add_argument(
        "--streetview-cache",
        type=Path,
        default=Path(".streetview_cache.sqlite"),
        help="SQLite path for Street View metadata cache",
    )
    run_p.add_argument(
        "--openai-model",
        default="gpt-4o-mini",
        help="OpenAI vision model for verification",
    )
    run_p.add_argument(
        "--stop-after-images",
        action="store_true",
        help="Download Street View JPEGs only; leave pipeline_status pending (no OpenAI)",
    )
    run_p.set_defaults(_handler=_cmd_run)

    args = p.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        p.print_help()
        return 1
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
