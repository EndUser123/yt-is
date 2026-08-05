from __future__ import annotations

import json

import pytest

from csf.video_selection_manifest import (
    load_video_selection_manifest,
    select_manifest_entries,
)


def test_load_manifest_validates_order_and_fingerprint(tmp_path):
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "2026-08-05T00:00:00+00:00",
                "selection_name": "test-selection",
                "videos": [
                    {"video_id": "aaaaaaaaaaa", "source_note": "row 1"},
                    {"video_id": "bbbbbbbbbbb"},
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_video_selection_manifest(path)

    assert [item.video_id for item in manifest.items] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert manifest.items[0].source_note == "row 1"
    assert manifest.fingerprint.startswith("sha256:")


@pytest.mark.parametrize(
    "videos, message",
    [
        ([{"video_id": "short"}], "11-character"),
        ([{"video_id": "aaaaaaaaaaa"}, {"video_id": "aaaaaaaaaaa"}], "duplicate"),
    ],
)
def test_load_manifest_rejects_invalid_or_duplicate_ids(tmp_path, videos, message):
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "bad",
                "videos": videos,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_video_selection_manifest(path)


def test_select_manifest_entries_preserves_order_and_reports_skips(tmp_path):
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "selection",
                "videos": [
                    {"video_id": "aaaaaaaaaaa"},
                    {"video_id": "bbbbbbbbbbb"},
                    {"video_id": "ccccccccccc"},
                    {"video_id": "ddddddddddd"},
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_video_selection_manifest(path)
    rows = {
        "aaaaaaaaaaa": {"video_id": "aaaaaaaaaaa", "status": "pending", "source": "source-a"},
        "bbbbbbbbbbb": {"video_id": "bbbbbbbbbbb", "status": "complete", "source": "source-b"},
        "ccccccccccc": {"video_id": "ccccccccccc", "status": "pending", "source": "source-c"},
    }

    selection = select_manifest_entries(manifest, rows, max_items=1)

    assert [row["video_id"] for row in selection.selected_entries] == ["aaaaaaaaaaa"]
    assert selection.missing_ids == ("ddddddddddd",)
    assert selection.non_pending_by_status == {"complete": ("bbbbbbbbbbb",)}
    assert selection.limit_omitted_ids == ("ccccccccccc",)
    assert selection.fingerprint.startswith("sha256:")
