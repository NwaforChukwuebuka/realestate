from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Sequence

import requests

from verification.models import (
    MIN_CONFIDENCE_FOR_DISTRESS,
    PropertyVerificationResult,
    RecommendedAction,
)

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """You analyze Google Street View-style images of a residential property.

Step 1 — Target verification (always):
- Is the target house/building clearly visible (not blocked by trees/vehicles/other buildings)?
- Is it reasonably centered or clear enough to assess condition?
- Assign target_confidence: integer 0–100 (0 = not visible/usable, 100 = very clear).

Step 2 — Distress (only if target_confidence >= 70):
If target_confidence is below 70, you must set distress_score to 0 and visible_signs to [].
If target_confidence is 70 or above, score visible distress 0–100 (higher = more neglect/damage).

Look for: boarded windows, broken windows, overgrown grass, roof damage, roof tarp, peeling paint, trash/debris, abandoned vehicles, broken fence, vacancy signs, general neglect. List only signs you have reasonable evidence for in visible_signs (short snake_case or lowercase phrases).

recommended_action:
- call_now: strong distress signals or high confidence distress + motivation to contact owner
- verify: ambiguous; human should look again or gather more angles
- skip: property not visible enough, or clearly not the target / not useful

Respond with ONLY a single JSON object (no markdown) matching this shape:
{"target_confidence":0,"distress_score":0,"visible_signs":[],"condition_summary":"","recommended_action":"verify"}
"""


class VerificationError(RuntimeError):
    """OpenAI API failure or unparseable model output."""


def _image_mime_and_b64(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    else:
        guessed, _ = mimetypes.guess_type(path.name)
        mime = guessed or "application/octet-stream"
    b64 = base64.standard_b64encode(data).decode("ascii")
    return mime, b64


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    # Model sometimes wraps in ```json ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start : end + 1])
        else:
            raise VerificationError(f"Model did not return valid JSON: {text[:500]!r}") from None
    if not isinstance(obj, dict):
        raise VerificationError("Model JSON root must be an object")
    return obj


def _clamp_int(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VerificationError(f"{name} must be a number, got {type(value).__name__}")
    n = int(round(float(value)))
    return max(lo, min(hi, n))


def _normalize_action(raw: Any) -> RecommendedAction:
    if not isinstance(raw, str):
        return "verify"
    s = raw.strip().lower().replace(" ", "_")
    if s in ("call_now", "call-now", "callnow"):
        return "call_now"
    if s == "skip":
        return "skip"
    if s in ("verify", "verification", "review"):
        return "verify"
    return "verify"


def _normalize_visible_signs(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def normalize_verification_dict(data: dict[str, Any]) -> PropertyVerificationResult:
    """Build a result dict from parsed JSON and enforce pipeline rules."""
    tc = _clamp_int("target_confidence", data.get("target_confidence"), 0, 100)
    ds = _clamp_int("distress_score", data.get("distress_score"), 0, 100)
    signs = _normalize_visible_signs(data.get("visible_signs"))
    summary = data.get("condition_summary")
    summary_s = summary.strip() if isinstance(summary, str) else ""
    action = _normalize_action(data.get("recommended_action"))
    if tc < MIN_CONFIDENCE_FOR_DISTRESS:
        ds = 0
        signs = ()
    return PropertyVerificationResult(
        target_confidence=tc,
        distress_score=ds,
        visible_signs=signs,
        condition_summary=summary_s,
        recommended_action=action,
    )


class PropertyVerifier:
    """OpenAI vision (chat completions) for target check + distress scoring."""

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        *,
        model: str = "gpt-4o-mini",
        timeout_sec: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._model = model
        self._timeout_sec = timeout_sec

    def analyze_images(
        self,
        image_paths: Sequence[str | Path],
        *,
        user_context: str = "",
    ) -> PropertyVerificationResult:
        paths = [Path(p) for p in image_paths]
        for p in paths:
            if not p.is_file():
                raise FileNotFoundError(f"Image not found: {p}")

        user_parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "These are Street View captures of the same property from slightly different headings. "
                    + (user_context.strip() + "\n\n" if user_context and user_context.strip() else "")
                    + "Return only the JSON object described in the system message."
                ),
            }
        ]
        for p in paths:
            mime, b64 = _image_mime_and_b64(p)
            user_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_parts},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        r = self._session.post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers=headers,
            data=json.dumps(body),
            timeout=self._timeout_sec,
        )
        if not r.ok:
            raise VerificationError(
                f"OpenAI HTTP {r.status_code}: {(r.text or '')[:800]}"
            )
        payload = r.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise VerificationError(f"Unexpected OpenAI response: {payload!r}") from e
        if not isinstance(content, str):
            raise VerificationError("OpenAI message content is not a string")
        parsed = _extract_json_object(content)
        return normalize_verification_dict(parsed)
