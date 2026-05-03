from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

from streetview.heading import headings_for_offsets, initial_bearing_degrees

GOOGLE_STREETVIEW_STATIC_URL = "https://maps.googleapis.com/maps/api/streetview"

DEFAULT_FOV = 90
DEFAULT_SIZE = (640, 640)
DEFAULT_PITCH = 0


@dataclass(frozen=True)
class StreetViewAngleCapture:
    """One Static API viewpoint: offset from base bearing, URL, and optional local file."""

    offset_deg: int
    heading_deg: float
    image_url: str
    local_path: Path | None


@dataclass(frozen=True)
class StreetViewImageFetchResult:
    pano_id: str
    base_heading_deg: float
    pano_lat: float
    pano_lng: float
    property_lat: float
    property_lng: float
    fov: int
    size: tuple[int, int]
    captures: tuple[StreetViewAngleCapture, ...]


def build_streetview_static_url(
    *,
    api_key: str,
    pano_id: str,
    heading: float,
    fov: int = DEFAULT_FOV,
    size: tuple[int, int] = DEFAULT_SIZE,
    pitch: int = DEFAULT_PITCH,
) -> str:
    """HTTPS URL for the Street View Static API (JPEG by default)."""
    w, h = size
    params = {
        "size": f"{int(w)}x{int(h)}",
        "pano": pano_id,
        "heading": f"{heading:.6f}",
        "pitch": str(pitch),
        "fov": str(int(fov)),
        "key": api_key,
    }
    return f"{GOOGLE_STREETVIEW_STATIC_URL}?{urlencode(params)}"


def _safe_pano_dir_name(pano_id: str) -> str:
    return pano_id.replace("/", "_").replace("\\", "_")


def _capture_filename(offset_deg: int, heading_deg: float, fov: int) -> str:
    h = int(round(heading_deg % 360.0)) % 360
    return f"off{offset_deg:+04d}_heading_{h:03d}_fov_{fov}.jpg"


class StreetViewImageFetcher:
    """Compute pano→property heading and download multi-angle Static API images."""

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        *,
        default_fov: int = DEFAULT_FOV,
        default_size: tuple[int, int] = DEFAULT_SIZE,
        default_pitch: int = DEFAULT_PITCH,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._default_fov = default_fov
        self._default_size = default_size
        self._default_pitch = default_pitch

    def fetch_multi_angle_set(
        self,
        *,
        pano_id: str,
        pano_lat: float,
        pano_lng: float,
        property_lat: float,
        property_lng: float,
        output_dir: str | Path,
        fov: int | None = None,
        size: tuple[int, int] | None = None,
        pitch: int | None = None,
    ) -> StreetViewImageFetchResult:
        fov_i = int(fov if fov is not None else self._default_fov)
        size_t = size if size is not None else self._default_size
        pitch_i = int(pitch if pitch is not None else self._default_pitch)

        base = initial_bearing_degrees(pano_lat, pano_lng, property_lat, property_lng)
        out = Path(output_dir) / _safe_pano_dir_name(pano_id)
        out.mkdir(parents=True, exist_ok=True)

        captures: list[StreetViewAngleCapture] = []
        for offset, heading in headings_for_offsets(base):
            url = build_streetview_static_url(
                api_key=self._api_key,
                pano_id=pano_id,
                heading=heading,
                fov=fov_i,
                size=size_t,
                pitch=pitch_i,
            )
            fname = _capture_filename(offset, heading, fov_i)
            dest = out / fname
            r = self._session.get(url, timeout=60)
            r.raise_for_status()
            if r.content[:1] == b"{":
                text = r.text[:500] if r.text else ""
                raise ValueError(f"Street View Static API returned JSON instead of an image: {text}")
            is_jpeg = len(r.content) >= 2 and r.content[:2] == b"\xff\xd8"
            ctype = (r.headers.get("Content-Type") or "").lower()
            if not is_jpeg and "image" not in ctype:
                text = r.text[:500] if r.text else ""
                raise ValueError(
                    f"Street View Static API did not return an image (Content-Type={ctype!r}): {text}"
                )
            dest.write_bytes(r.content)
            captures.append(
                StreetViewAngleCapture(
                    offset_deg=offset,
                    heading_deg=heading,
                    image_url=url,
                    local_path=dest,
                )
            )

        return StreetViewImageFetchResult(
            pano_id=pano_id,
            base_heading_deg=base,
            pano_lat=pano_lat,
            pano_lng=pano_lng,
            property_lat=property_lat,
            property_lng=property_lng,
            fov=fov_i,
            size=size_t,
            captures=tuple(captures),
        )
