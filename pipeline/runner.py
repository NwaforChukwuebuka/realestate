from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO

import requests

from geocoding.geocoder import Geocoder
from pipeline.models import PendingParcelWork, PipelineRow
from pipeline.store import PipelineStore
from streetview.images import StreetViewImageFetcher
from streetview.metadata import StreetViewMetadataClient
from verification.scorer import (
    PropertyVerifier,
    VerificationError,
    select_primary_streetview_frame,
)

ParcelOutcome = Literal["done", "failed", "skipped_no_street_view", "not_pending", "stopped_after_images"]


def safe_parcel_image_dir(parcel_id: str) -> str:
    """Filesystem-safe single segment under ``--images-dir``."""
    s = parcel_id.strip()
    s = s.replace("/", "_").replace("\\", "_")
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.UNICODE)
    return s or "unknown_parcel"


def _work_one_line_address(work: PendingParcelWork) -> str:
    parts = [
        (work.property_address or "").strip(),
        (work.city or "").strip(),
        (work.state or "").strip(),
        (work.zip or "").strip(),
    ]
    return ", ".join(p for p in parts if p) or "(no address on row)"


def verification_context(work: PendingParcelWork) -> str:
    parts = [
        f"parcel_id={work.parcel_id}",
        "site:",
        (work.property_address or "").strip(),
        (work.city or "").strip(),
        (work.state or "").strip(),
        (work.zip or "").strip(),
    ]
    return " ".join(p for p in parts if p).strip()


def _valid_image_paths_from_row(row: PipelineRow) -> list[Path] | None:
    raw = row.image_paths_json
    if not raw or not raw.strip():
        return None
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or not arr:
        return None
    paths = [Path(str(p)) for p in arr]
    if not paths:
        return None
    if not all(p.is_file() for p in paths):
        return None
    return paths


@dataclass
class RunBatchStats:
    attempted: int = 0
    done: int = 0
    failed: int = 0
    skipped_no_street_view: int = 0
    stopped_after_images: int = 0
    not_pending: int = 0
    errors: list[str] = field(default_factory=list)


def process_one_parcel(
    *,
    pstore: PipelineStore,
    geocoder: Geocoder,
    sv_meta: StreetViewMetadataClient,
    fetcher: StreetViewImageFetcher,
    verifier: PropertyVerifier | None,
    work: PendingParcelWork,
    images_root: Path,
    stop_after_images: bool = False,
) -> ParcelOutcome:
    """Run geocode → Street View metadata → images → (optional) verification for one parcel."""
    pid = work.parcel_id.strip()
    row = pstore.get(pid)
    if row is None:
        return "not_pending"
    if row.pipeline_status != "pending":
        return "not_pending"

    street = (work.property_address or "").strip()
    city = (work.city or "").strip()
    state = (work.state or "FL").strip()
    zip_code = (work.zip or "").strip() or None

    try:
        if row.geocode_lat is None or row.geocode_lng is None:
            g = geocoder.geocode(street, city, state, zip_code)
            pstore.apply_geocode(pid, g)
            row = pstore.get(pid)
            if row is None or row.pipeline_status != "pending":
                return "failed" if row and row.pipeline_status == "failed" else "not_pending"

        if row.geocode_lat is None or row.geocode_lng is None:
            pstore.mark_failed(pid, "Geocode did not produce coordinates")
            return "failed"

        lat = float(row.geocode_lat)
        lng = float(row.geocode_lng)

        needs_sv_meta = (
            not (row.sv_pano_id or "").strip()
            or row.sv_pano_lat is None
            or row.sv_pano_lng is None
        )
        if needs_sv_meta:
            m = sv_meta.lookup(lat, lng)
            pstore.apply_streetview_metadata(pid, m)
            row = pstore.get(pid)
            if row is None:
                return "not_pending"
            if row.pipeline_status == "skipped_no_street_view":
                return "skipped_no_street_view"
            if row.pipeline_status == "failed":
                return "failed"
            if not (row.sv_pano_id or "").strip() or row.sv_pano_lat is None or row.sv_pano_lng is None:
                pstore.mark_failed(pid, "Street View metadata did not return a pano")
                return "failed"

        pano_id = str(row.sv_pano_id)
        pano_lat = float(row.sv_pano_lat)
        pano_lng = float(row.sv_pano_lng)

        img_paths = _valid_image_paths_from_row(row)
        if img_paths is None:
            out_dir = images_root / safe_parcel_image_dir(pid)
            fr = fetcher.fetch_multi_angle_set(
                pano_id=pano_id,
                pano_lat=pano_lat,
                pano_lng=pano_lng,
                property_lat=lat,
                property_lng=lng,
                output_dir=out_dir,
                heading_offsets=(0,),
            )
            img_paths = [c.local_path for c in fr.captures if c.local_path is not None]
            if not img_paths:
                pstore.mark_failed(pid, "Street View image download produced no files")
                return "failed"
            pstore.apply_images(pid, img_paths)

        if stop_after_images:
            return "stopped_after_images"

        assert verifier is not None

        if not img_paths:
            pstore.mark_failed(pid, "No valid image files on disk for verification")
            return "failed"

        ctx = verification_context(work)
        verify_paths = select_primary_streetview_frame(img_paths)
        v = verifier.analyze_images(verify_paths, user_context=ctx)
        pstore.apply_verification(pid, v)
        return "done"

    except (requests.RequestException, VerificationError, ValueError, OSError) as e:
        msg = f"{type(e).__name__}: {e}"
        pstore.mark_failed(pid, msg[:2000])
        return "failed"


def run_pending_batch(
    *,
    db_path: Path,
    google_api_key: str,
    openai_api_key: str | None,
    limit: int,
    images_root: Path,
    geocode_cache: Path,
    streetview_cache: Path,
    openai_model: str = "gpt-4o-mini",
    stop_after_images: bool = False,
    progress: bool = True,
    progress_stream: TextIO | None = None,
) -> RunBatchStats:
    """Process up to ``limit`` pending parcels; opens/closes API clients for the batch.

    When ``progress`` is True, prints one line per parcel (before and after) to ``progress_stream``
    (default stderr) so long runs show which parcel is active and the outcome.
    """
    log: TextIO = progress_stream if progress_stream is not None else sys.stderr
    stats = RunBatchStats()
    verifier: PropertyVerifier | None = None
    if not stop_after_images:
        if not openai_api_key:
            raise ValueError("openai_api_key is required unless stop_after_images=True")
        verifier = PropertyVerifier(api_key=openai_api_key, model=openai_model)

    geocoder = Geocoder(api_key=google_api_key, cache_db_path=geocode_cache)
    sv_meta = StreetViewMetadataClient(api_key=google_api_key, cache_db_path=streetview_cache)
    fetcher = StreetViewImageFetcher(api_key=google_api_key)

    try:
        with PipelineStore(db_path) as pstore:
            pstore.init_schema()
            batch = pstore.fetch_pending_with_addresses(limit)
            total = len(batch)
            if progress:
                if total == 0:
                    print(
                        "[pipeline] 0 pending parcel(s) — nothing to do "
                        f"(batch limit was {limit}; only rows with pipeline_status='pending' are run).",
                        file=log,
                        flush=True,
                    )
                else:
                    first_id = batch[0].parcel_id
                    last_id = batch[-1].parcel_id
                    print(
                        f"[pipeline] This run: {total} pending parcel(s) "
                        f"(limit {limit}). Ordered by parcel_id; "
                        f"range in this batch {first_id} … {last_id}. "
                        f"Counters [i/{total}] count items in this run only, not total DB position.",
                        file=log,
                        flush=True,
                    )
            for idx, work in enumerate(batch, start=1):
                stats.attempted += 1
                if progress:
                    print(
                        f"[pipeline] [{idx}/{total} this run] START {work.parcel_id} — "
                        f"{_work_one_line_address(work)}",
                        file=log,
                        flush=True,
                    )
                try:
                    outcome = process_one_parcel(
                        pstore=pstore,
                        geocoder=geocoder,
                        sv_meta=sv_meta,
                        fetcher=fetcher,
                        verifier=verifier,
                        work=work,
                        images_root=images_root,
                        stop_after_images=stop_after_images,
                    )
                except Exception as e:  # noqa: BLE001 — last resort; mark failed and continue
                    pstore.mark_failed(work.parcel_id.strip(), f"{type(e).__name__}: {e}"[:2000])
                    outcome = "failed"
                    stats.errors.append(f"{work.parcel_id}: {e!s}")

                if progress:
                    print(
                        f"[pipeline] [{idx}/{total} this run] {outcome.upper()} {work.parcel_id}",
                        file=log,
                        flush=True,
                    )

                if outcome == "done":
                    stats.done += 1
                elif outcome == "failed":
                    stats.failed += 1
                elif outcome == "skipped_no_street_view":
                    stats.skipped_no_street_view += 1
                elif outcome == "stopped_after_images":
                    stats.stopped_after_images += 1
                elif outcome == "not_pending":
                    stats.not_pending += 1
    finally:
        geocoder.close()
        sv_meta.close()

    return stats
