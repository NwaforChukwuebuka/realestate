"""Smoke test: python -m verification (needs OPENAI_API_KEY). See --help epilog for examples."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from verification.scorer import (
    PropertyVerifier,
    VerificationError,
    select_primary_streetview_frame,
)

_EPILOG = """Examples (use real paths — placeholders like img1.jpg will not exist):

  python -m verification --dir streetview_images\\<pano_subfolder> --one
      Single image: prefers a frame named with off+000 (centered), else first file. Uses detail=low for fewer vision tokens.

  python -m verification --dir streetview_images\\<pano_subfolder>
      All .jpg/.jpeg/.png in that folder

  python -m verification path\\to\\off+000_heading_005_fov_90.jpg
      One explicit image (recommended for lowest token use)

Tip: run Street View download first, then pass the folder or one JPEG."""


def _load_env() -> str | None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv()
    return os.environ.get("OPENAI_API_KEY")


def collect_image_paths(args: argparse.Namespace) -> tuple[list[Path], str | None]:
    """Resolve image paths from CLI args. On error, returns ([], message)."""
    if args.dir is not None:
        d = args.dir.resolve()
        if not d.is_dir():
            return [], f"Not a directory: {d}"
        paths = sorted(d.glob("*.jpg")) + sorted(d.glob("*.jpeg")) + sorted(d.glob("*.png"))
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in paths:
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                unique.append(rp)
        if not unique:
            return [], f"No .jpg / .jpeg / .png files in {d}"
        if getattr(args, "one", False):
            return select_primary_streetview_frame(unique), None
        return unique, None
    if not args.images:
        return [], "Pass one or more image paths, or use --dir DIR"
    return [Path(p).resolve() for p in args.images], None


def _missing_or_invalid_files(paths: list[Path]) -> list[Path]:
    bad: list[Path] = []
    for path in paths:
        try:
            if not path.is_file():
                bad.append(path)
        except OSError:
            bad.append(path)
    return bad


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m verification",
        description="Run AI target verification + distress scoring on Street View (or other) images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    p.add_argument(
        "images",
        nargs="*",
        type=str,
        help="Image files (.jpg / .png). Omit if using --dir",
    )
    p.add_argument(
        "--dir",
        type=Path,
        metavar="DIR",
        help="Analyze all .jpg/.jpeg/.png in this directory (sorted)",
    )
    p.add_argument(
        "--one",
        action="store_true",
        help="With --dir only: send one image (prefers filename containing off+000, else first file). Saves vision tokens.",
    )
    p.add_argument(
        "-c",
        "--context",
        default="",
        help="Optional short note passed to the model (address, folio, etc.)",
    )
    p.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI vision model (default: gpt-4o-mini)",
    )
    args = p.parse_args(argv)

    if args.dir is not None and args.images:
        p.error("Use either image paths or --dir, not both")
    if args.one and args.dir is None:
        p.error("--one requires --dir")

    key = _load_env()
    if not key:
        print("Set OPENAI_API_KEY in the environment or .env", file=sys.stderr)
        return 1

    paths, err = collect_image_paths(args)
    if err:
        print(err, file=sys.stderr)
        return 1

    bad = _missing_or_invalid_files(paths)
    if bad:
        print(
            "These paths are not readable files (missing, typo, or not a file):\n  "
            + "\n  ".join(str(p) for p in bad),
            file=sys.stderr,
        )
        print(
            "\nUse real JPEG/PNG paths, or download frames first, e.g.:\n"
            "  python -m streetview images\n"
            "  python -m verification --dir streetview_images\\<pano_folder>\n"
            "See: python -m verification --help",
            file=sys.stderr,
        )
        return 1

    verifier = PropertyVerifier(api_key=key, model=args.model)
    try:
        result = verifier.analyze_images(paths, user_context=args.context)
    except VerificationError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1

    payload = {
        **asdict(result),
        "images": [str(p) for p in paths],
        "model": args.model,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
