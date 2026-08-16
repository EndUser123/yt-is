"""Tests for the shared title-to-video_id bridge (scripts/title_bridge.py).

Covers normalization, matching (exact/ambiguous/unmatched/fuzzy),
bridge merging, and clusters-file loading.  All tests are offline;
no external data, live DB, or network access is required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.title_bridge import (
    build_bridge_from_clusters,
    build_title_bridge,
    match_title,
    merge_bridges,
    normalize_title,
)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hello World", "hello world"),
        ("  Hello   World  ", "hello world"),
        ("Hello, World!", "hello world"),
        ("What's Up? — Em dash test", "what s up em dash test"),
        ("UPPERCASE", "uppercase"),
        ("Mixed.Case.Title", "mixed case title"),
        ("", ""),
        ("   ", ""),  # all whitespace strips to empty
        ("日本語のタイトル", "日本語のタイトル"),  # Unicode preserved
        ("Café & Röst — Straße", "café röst straße"),  # accented chars + entities
        ("(parenthetical) [bracketed] {braced}", "parenthetical bracketed braced"),
        ("line1\nline2\tline3", "line1 line2 line3"),  # whitespace collapses
    ],
)
def test_normalize_title_handles_edge_cases(raw, expected):
    assert normalize_title(raw) == expected


def test_normalize_title_none_becomes_empty():
    """None input yields empty string (pre-normalised sentinel)."""
    assert normalize_title(None) == ""


# ---------------------------------------------------------------------------
# match_title — exact
# ---------------------------------------------------------------------------

def test_match_title_exact_single_video_id():
    index = {"some video title": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("Some Video Title!", index)
    assert vid == "dQw4w9WgXcQ"
    assert mtype == "exact"


def test_match_title_exact_unicode_title():
    """Non-ASCII titles are normalised and matched correctly."""
    index = {"中文视频标题": ["aaaaaaaaaaa"]}
    vid, mtype = match_title("中文视频标题", index)
    assert vid == "aaaaaaaaaaa"
    assert mtype == "exact"


# ---------------------------------------------------------------------------
# match_title — ambiguous
# ---------------------------------------------------------------------------

def test_match_title_ambiguous_when_title_maps_to_multiple_video_ids():
    """Titles that collide (same normalised form → multiple video_ids)
    must return ambiguous rather than guessing."""
    index = {"duplicate title": ["vid11111111", "vid22222222"]}
    vid, mtype = match_title("Duplicate Title", index)
    assert vid is None
    assert mtype == "ambiguous"


# ---------------------------------------------------------------------------
# match_title — unmatched
# ---------------------------------------------------------------------------

def test_match_title_unmatched_when_title_not_in_index():
    index = {"known title": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("Unknown Title", index)
    assert vid is None
    assert mtype == "unmatched"


def test_match_title_unmatched_empty_title():
    index = {"any": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("", index)
    assert vid is None
    assert mtype == "unmatched"


def test_match_title_unmatched_whitespace_only_title():
    index = {"any": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("   ", index)
    assert vid is None
    assert mtype == "unmatched"


# ---------------------------------------------------------------------------
# match_title — fuzzy (enabled via threshold > 0)
# ---------------------------------------------------------------------------

def test_match_title_fuzzy_when_threshold_enabled():
    index = {"hello world": ["dQw4w9WgXcQ"]}
    # "hello wordl" is a typo — should fuzzy-match with high threshold
    vid, mtype = match_title("hello wordl", index, fuzzy_threshold=0.90)
    assert vid == "dQw4w9WgXcQ"
    assert mtype == "fuzzy"


def test_match_title_fuzzy_below_threshold_returns_unmatched():
    index = {"hello world": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("completely different", index, fuzzy_threshold=0.90)
    assert vid is None
    assert mtype == "unmatched"


def test_match_title_fuzzy_disabled_by_default():
    """fuzzy_threshold defaults to 0.0, so fuzzy matching is off."""
    index = {"hello world": ["dQw4w9WgXcQ"]}
    vid, mtype = match_title("hello wordl", index)  # no threshold arg
    assert vid is None
    assert mtype == "unmatched"


def test_match_title_fuzzy_ambiguous_when_multiple_ids():
    """Fuzzy match whose best key maps to multiple video_ids is ambiguous."""
    index = {"hello world": ["vid11111111", "vid22222222"]}
    vid, mtype = match_title("hello wordl", index, fuzzy_threshold=0.90)
    assert vid is None
    assert mtype == "ambiguous"


# ---------------------------------------------------------------------------
# merge_bridges
# ---------------------------------------------------------------------------

def test_merge_bridges_combines_non_overlapping():
    a = {"title one": ["vid11111111"]}
    b = {"title two": ["vid22222222"]}
    merged = merge_bridges(a, b)
    assert merged == {
        "title one": ["vid11111111"],
        "title two": ["vid22222222"],
    }


def test_merge_bridges_deduplicates_same_video_id():
    """When two bridges map the same title to the same video_id,
    the result must contain each video_id once (no duplicates)."""
    a = {"title one": ["vid11111111"]}
    b = {"title one": ["vid11111111"]}
    merged = merge_bridges(a, b)
    assert merged == {"title one": ["vid11111111"]}


def test_merge_bridges_merges_different_ids_for_same_title():
    """When two bridges map the same title to *different* video_ids,
    both IDs appear under the merged title (title collision)."""
    a = {"shared title": ["vid11111111"]}
    b = {"shared title": ["vid22222222"]}
    merged = merge_bridges(a, b)
    assert "shared title" in merged
    assert set(merged["shared title"]) == {"vid11111111", "vid22222222"}


def test_merge_bridges_handles_empty_inputs():
    assert merge_bridges() == {}
    assert merge_bridges({}) == {}
    assert merge_bridges({}, {}, {}) == {}


def test_merge_bridges_preserves_first_input_when_second_is_empty():
    a = {"title": ["vid11111111"]}
    assert merge_bridges(a, {}) == {"title": ["vid11111111"]}


# ---------------------------------------------------------------------------
# build_bridge_from_clusters
# ---------------------------------------------------------------------------

_CLUSTERS_FIXTURE = [
    {
        "cluster_id": "c1",
        "videos": [
            {
                "title": "Some Cool Video",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            },
            {
                "title": "Another Video — with punctuation!",
                "url": "https://youtu.be/aaaaaaaaaaa",
            },
        ],
    },
    {
        "cluster_id": "c2",
        "videos": [
            {
                "title": "Duplicate Title Here",
                "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
            },
            {
                "title": "Duplicate Title Here",
                "url": "https://www.youtube.com/watch?v=ccccccccccc",
            },
        ],
    },
]


def _write_clusters(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_build_bridge_from_valid_clusters(tmp_path):
    """Clusters with YouTube URLs must produce title→video_id mappings."""
    cpath = tmp_path / "clusters.json"
    _write_clusters(cpath, _CLUSTERS_FIXTURE)

    bridge = build_bridge_from_clusters([cpath])

    # Normalised "some cool video" → dQw4w9WgXcQ
    assert normalize_title("Some Cool Video") in bridge
    assert bridge[normalize_title("Some Cool Video")] == ["dQw4w9WgXcQ"]

    # Normalised "another video with punctuation" → aaaaaaaaaaa
    norm = normalize_title("Another Video — with punctuation!")
    assert norm in bridge
    assert bridge[norm] == ["aaaaaaaaaaa"]

    # Duplicate title → both video_ids
    dup_norm = normalize_title("Duplicate Title Here")
    assert dup_norm in bridge
    assert set(bridge[dup_norm]) == {"bbbbbbbbbbb", "ccccccccccc"}


def test_build_bridge_from_missing_file(tmp_path):
    """Missing clusters file → empty bridge (warning on stderr)."""
    cpath = tmp_path / "nonexistent.json"
    bridge = build_bridge_from_clusters([cpath])
    assert bridge == {}


def test_build_bridge_from_invalid_json(tmp_path):
    """Malformed JSON → empty bridge (warning on stderr)."""
    cpath = tmp_path / "bad.json"
    cpath.write_text("this is not json", encoding="utf-8")
    bridge = build_bridge_from_clusters([cpath])
    assert bridge == {}


def test_build_bridge_skips_non_youtube_urls(tmp_path):
    """Videos without a YouTube URL are silently skipped."""
    data = [
        {
            "cluster_id": "c1",
            "videos": [
                {"title": "No URL Video", "url": ""},
                {"title": "Vimeo Video", "url": "https://vimeo.com/123456"},
            ],
        }
    ]
    cpath = tmp_path / "clusters.json"
    _write_clusters(cpath, data)
    bridge = build_bridge_from_clusters([cpath])
    assert bridge == {}


def test_build_bridge_skips_videos_without_titles(tmp_path):
    """Videos with empty or missing titles don't pollute the bridge."""
    data = [
        {
            "cluster_id": "c1",
            "videos": [
                {"title": "", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            ],
        }
    ]
    cpath = tmp_path / "clusters.json"
    _write_clusters(cpath, data)
    bridge = build_bridge_from_clusters([cpath])
    # Empty title normalizes to "" which is falsy → skipped
    assert "" not in bridge
    assert len(bridge) == 0


def test_build_bridge_from_multiple_clusters_files(tmp_path):
    """Multiple clusters files are merged into one bridge."""
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    _write_clusters(
        a_path,
        [{"cluster_id": "a", "videos": [{"title": "Video A", "url": "https://youtu.be/vid11111111"}]}],
    )
    _write_clusters(
        b_path,
        [{"cluster_id": "b", "videos": [{"title": "Video B", "url": "https://youtu.be/vid22222222"}]}],
    )

    bridge = build_bridge_from_clusters([a_path, b_path])
    assert len(bridge) == 2
    assert normalize_title("Video A") in bridge
    assert normalize_title("Video B") in bridge


# ---------------------------------------------------------------------------
# build_title_bridge — integration
# ---------------------------------------------------------------------------

def test_build_title_bridge_clusters_only(tmp_path):
    """With include_analysis=False, only clusters files contribute."""
    cpath = tmp_path / "clusters.json"
    _write_clusters(
        cpath,
        [{"cluster_id": "c1", "videos": [{"title": "Test Video", "url": "https://youtu.be/vid11111111"}]}],
    )
    bridge = build_title_bridge(
        clusters_files=[cpath], include_analysis=False
    )
    assert normalize_title("Test Video") in bridge
    assert bridge[normalize_title("Test Video")] == ["vid11111111"]


# ---------------------------------------------------------------------------
# match_title — round-trip through full pipeline
# ---------------------------------------------------------------------------

def test_full_pipeline_clusters_to_match(tmp_path):
    """End-to-end: clusters.json → bridge → match_title."""
    cpath = tmp_path / "clusters.json"
    _write_clusters(cpath, _CLUSTERS_FIXTURE)
    bridge = build_bridge_from_clusters([cpath])

    # Exact match
    vid, mtype = match_title("Some Cool Video", bridge)
    assert vid == "dQw4w9WgXcQ"
    assert mtype == "exact"

    # Different casing/punctuation still matches
    vid, mtype = match_title("SOME COOL VIDEO!!!", bridge)
    assert vid == "dQw4w9WgXcQ"
    assert mtype == "exact"

    # Unmatched
    vid, mtype = match_title("Not In The Clusters", bridge)
    assert vid is None
    assert mtype == "unmatched"

    # Duplicate title → ambiguous
    vid, mtype = match_title("Duplicate Title Here", bridge)
    assert vid is None
    assert mtype == "ambiguous"


