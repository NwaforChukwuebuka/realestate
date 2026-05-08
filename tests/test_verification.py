from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from verification.models import MIN_CONFIDENCE_FOR_DISTRESS, PropertyVerificationResult
from verification.scorer import (
    PropertyVerifier,
    VerificationError,
    _extract_json_object,
    normalize_verification_dict,
)


def test_normalize_low_confidence_zeroes_distress() -> None:
    r = normalize_verification_dict(
        {
            "target_confidence": 65,
            "distress_score": 90,
            "visible_signs": ["roof damage", "broken windows"],
            "condition_summary": "Looks rough",
            "recommended_action": "call_now",
        }
    )
    assert r.target_confidence == 65
    assert r.distress_score == 0
    assert r.visible_signs == ()
    assert r.condition_summary == "Looks rough"


def test_normalize_high_confidence_keeps_distress() -> None:
    r = normalize_verification_dict(
        {
            "target_confidence": MIN_CONFIDENCE_FOR_DISTRESS,
            "distress_score": 55,
            "visible_signs": ["overgrown grass"],
            "condition_summary": "Yard neglected",
            "recommended_action": "verify",
        }
    )
    assert r.distress_score == 55
    assert r.visible_signs == ("overgrown grass",)


def test_normalize_clamps_scores() -> None:
    r = normalize_verification_dict(
        {
            "target_confidence": 150,
            "distress_score": -10,
            "visible_signs": [],
            "condition_summary": "",
            "recommended_action": "skip",
        }
    )
    assert r.target_confidence == 100
    assert r.distress_score == 0  # high confidence but distress clamped to 0 from -10 -> 0


def test_normalize_recommended_action_aliases() -> None:
    assert (
        normalize_verification_dict(
            {
                "target_confidence": 80,
                "distress_score": 10,
                "visible_signs": [],
                "condition_summary": "",
                "recommended_action": "CALL NOW",
            }
        ).recommended_action
        == "call_now"
    )
    assert (
        normalize_verification_dict(
            {
                "target_confidence": 80,
                "distress_score": 10,
                "visible_signs": [],
                "condition_summary": "",
                "recommended_action": "call_now",
            }
        ).recommended_action
        == "call_now"
    )


def test_extract_json_object_strips_fence() -> None:
    text = """Here is JSON:
```json
{"target_confidence": 72, "distress_score": 40, "visible_signs": ["peeling paint"], "condition_summary": "x", "recommended_action": "verify"}
```
"""
    obj = _extract_json_object(text)
    assert obj["target_confidence"] == 72


def test_normalize_invalid_number_raises() -> None:
    with pytest.raises(VerificationError, match="target_confidence"):
        normalize_verification_dict(
            {
                "target_confidence": "high",
                "distress_score": 0,
                "visible_signs": [],
                "condition_summary": "",
                "recommended_action": "skip",
            }
        )


def test_select_primary_streetview_frame_prefers_off000(tmp_path: Path) -> None:
    from verification.scorer import select_primary_streetview_frame

    primary = tmp_path / "off+000_heading_090_fov_90.jpg"
    other = tmp_path / "off+015_heading_105_fov_90.jpg"
    primary.write_bytes(b"1")
    other.write_bytes(b"2")
    assert select_primary_streetview_frame([other, primary]) == [primary]


def test_select_primary_streetview_frame_fallback_first(tmp_path: Path) -> None:
    from verification.scorer import select_primary_streetview_frame

    first = tmp_path / "cap_a.jpg"
    second = tmp_path / "cap_b.jpg"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    assert select_primary_streetview_frame([first, second]) == [first]


def test_select_primary_streetview_frame_empty_raises() -> None:
    from verification.scorer import select_primary_streetview_frame

    with pytest.raises(ValueError, match="empty"):
        select_primary_streetview_frame([])


def _fake_jpeg(path: Path) -> None:
    path.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG EOI


def test_property_verifier_mock_http(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    _fake_jpeg(img)

    assistant = json.dumps(
        {
            "target_confidence": 85,
            "distress_score": 60,
            "visible_signs": ["broken fence", "trash/debris"],
            "condition_summary": "Fence down; junk in yard.",
            "recommended_action": "call_now",
        }
    )
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": assistant}}],
    }
    session = requests.Session()
    session.post = MagicMock(return_value=mock_resp)  # type: ignore[method-assign]

    v = PropertyVerifier(api_key="sk-test", session=session, model="gpt-4o-mini")
    r = v.analyze_images([img])

    assert r.target_confidence == 85
    assert r.distress_score == 60
    assert "broken fence" in r.visible_signs
    assert r.recommended_action == "call_now"
    session.post.assert_called_once()


def test_property_verifier_http_error(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    _fake_jpeg(img)
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    session = requests.Session()
    session.post = MagicMock(return_value=mock_resp)  # type: ignore[method-assign]

    v = PropertyVerifier(api_key="bad", session=session)
    with pytest.raises(VerificationError, match="HTTP 401"):
        v.analyze_images([img])


def test_property_verifier_missing_file(tmp_path: Path) -> None:
    v = PropertyVerifier(api_key="k", session=requests.Session())
    missing = tmp_path / "nope.jpg"
    with pytest.raises(FileNotFoundError):
        v.analyze_images([missing])


def test_collect_image_paths_from_dir(tmp_path: Path) -> None:
    from verification.cli import collect_image_paths

    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    args = argparse.Namespace(dir=tmp_path, images=[], one=False)
    paths, err = collect_image_paths(args)
    assert err is None
    assert [p.name for p in paths] == ["a.jpg", "b.png"]


def test_collect_image_paths_dir_one_prefers_off000(tmp_path: Path) -> None:
    from verification.cli import collect_image_paths

    (tmp_path / "off-015_heading_350_fov_90.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (tmp_path / "off+000_heading_005_fov_90.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    args = argparse.Namespace(dir=tmp_path, images=[], one=True)
    paths, err = collect_image_paths(args)
    assert err is None
    assert len(paths) == 1
    assert paths[0].name == "off+000_heading_005_fov_90.jpg"


def test_cli_main_mock_verifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from verification import cli as cli_mod

    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake = PropertyVerificationResult(
        target_confidence=80,
        distress_score=20,
        visible_signs=("overgrown grass",),
        condition_summary="Test",
        recommended_action="verify",
    )
    with patch.object(cli_mod, "PropertyVerifier") as MockV:
        MockV.return_value.analyze_images.return_value = fake
        rc = cli_mod.main([str(img)])
    assert rc == 0
    MockV.return_value.analyze_images.assert_called_once()
    call_kw = MockV.return_value.analyze_images.call_args.kwargs
    assert call_kw.get("user_context", "") == ""


def test_cli_exits_when_no_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from verification import cli as cli_mod

    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = cli_mod.main([str(img)])
    assert rc == 1


def test_cli_returns_1_when_image_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from verification import cli as cli_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    missing = tmp_path / "does_not_exist.jpg"
    rc = cli_mod.main([str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not readable files" in err
    assert "streetview images" in err.lower() or "streetview" in err.lower()


@pytest.mark.integration
def test_live_openai_if_key_present(tmp_path: Path) -> None:
    import os

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("Set OPENAI_API_KEY for live OpenAI vision test")
    img = tmp_path / "stub.jpg"
    _fake_jpeg(img)
    v = PropertyVerifier(api_key=key)
    r = v.analyze_images([img], user_context="Synthetic tiny JPEG for pipeline smoke test.")
    assert 0 <= r.target_confidence <= 100
    assert 0 <= r.distress_score <= 100
    if r.target_confidence < MIN_CONFIDENCE_FOR_DISTRESS:
        assert r.distress_score == 0
        assert r.visible_signs == ()
