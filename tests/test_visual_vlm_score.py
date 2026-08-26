"""Tests for the VLM visual intake parser and thumbnail URL handling."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.visual_vlm_score import (  # noqa: E402
    hq_thumbnail_url,
    parse_vlm_json,
)


def test_parse_clean_json_all_flags():
    verdict = parse_vlm_json(
        '{"density": 8, "text": true, "code": true, "diagram": false, '
        '"chart": true, "face": false, "type": "annotated code screencast"}'
    )
    assert verdict == {
        "density": 8,
        "has_text": 1,
        "has_code": 1,
        "has_diagram": 0,
        "has_chart": 1,
        "has_face": 0,
        "content_type": "annotated code screencast",
    }


def test_parse_prose_wrapped_json_with_string_flags():
    verdict = parse_vlm_json(
        'Sure! Here is the assessment: {"density": 2, "text": "yes", '
        '"code": "no", "diagram": false, "chart": false, "face": "true", '
        '"type": "talking head"} — hope that helps.'
    )
    assert verdict is not None
    assert verdict["density"] == 2
    assert verdict["has_text"] == 1
    assert verdict["has_code"] == 0
    assert verdict["has_face"] == 1


def test_parse_rejects_out_of_range_and_missing_density():
    assert parse_vlm_json('{"density": 0}') is None
    assert parse_vlm_json('{"density": 11}') is None
    assert parse_vlm_json('{"text": true}') is None


def test_parse_rejects_non_json():
    assert parse_vlm_json("The image shows a man at a desk.") is None
    assert parse_vlm_json("") is None


def test_hq_thumbnail_url_upgrade_and_fallback():
    assert (
        hq_thumbnail_url("abc", "https://i.ytimg.com/vi/abc/default.jpg")
        == "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    )
    assert (
        hq_thumbnail_url("abc", "https://i.ytimg.com/vi/abc/maxresdefault.jpg")
        == "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
    )
    assert (
        hq_thumbnail_url("abc", None)
        == "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    )
