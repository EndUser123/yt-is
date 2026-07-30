"""Unit tests for the 3 shared modules extracted in the refactor:
csf/urls.py, csf/paths.py, csf/clusters.py
"""
import json
import tempfile
import os
from pathlib import Path

import pytest

from csf.urls import YT_URL_REGEX, extract_video_id
from csf.paths import get_batch_db_path, get_transcript_db_path
from csf.clusters import load_clusters_json, extract_video_metadata


# ── csf/urls.py ──────────────────────────────────────────────────────────

class TestExtractVideoId:
    def test_watch_url(self):
        assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert extract_video_id("https://youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        assert extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_v_url(self):
        assert extract_video_id("https://youtube.com/v/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_non_youtube_returns_none(self):
        assert extract_video_id("https://example.com/video") is None

    def test_empty_string_returns_none(self):
        assert extract_video_id("") is None

    def test_none_returns_none(self):
        assert extract_video_id(None) is None

    def test_url_with_extra_params(self):
        assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ&t=120s&feature=share") == "dQw4w9WgXcQ"

    def test_url_embedded_in_text(self):
        text = "Check this out: https://youtu.be/dQw4w9WgXcQ cool right?"
        assert extract_video_id(text) == "dQw4w9WgXcQ"

    def test_regex_direct(self):
        m = YT_URL_REGEX.search("https://youtube.com/watch?v=abc12345678")
        assert m is not None
        assert m.group(1) == "abc12345678"


# ── csf/paths.py ─────────────────────────────────────────────────────────

class TestPaths:
    def test_batch_db_path_contains_sqlite(self):
        assert "batch_status.sqlite" in str(get_batch_db_path())

    def test_transcript_db_path_contains_sqlite(self):
        assert "transcripts.sqlite" in str(get_transcript_db_path())

    def test_batch_db_path_is_path_object(self):
        assert isinstance(get_batch_db_path(), Path)

    def test_transcript_db_path_is_path_object(self):
        assert isinstance(get_transcript_db_path(), Path)

    def test_batch_db_env_override(self, monkeypatch, tmp_path):
        override = tmp_path / "test_batch.sqlite"
        monkeypatch.setenv("YTIS_BATCH_STATUS_DB_PATH", str(override))
        assert get_batch_db_path() == override

    def test_transcript_db_env_override(self, monkeypatch, tmp_path):
        override = tmp_path / "test_transcript.sqlite"
        monkeypatch.setenv("YTIS_TRANSCRIPT_CACHE_DB_PATH", str(override))
        assert get_transcript_db_path() == override


# ── csf/clusters.py ──────────────────────────────────────────────────────

class TestLoadClustersJson:
    def test_valid_json(self, tmp_path):
        data = [{"name": "test", "videos": []}]
        f = tmp_path / "clusters.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = load_clusters_json(f)
        assert result == data

    def test_missing_file(self, tmp_path):
        result = load_clusters_json(tmp_path / "nonexistent.json")
        assert result == []

    def test_malformed_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        result = load_clusters_json(f)
        assert result == []

    def test_invalid_utf8(self, tmp_path):
        """SM-001: UnicodeDecodeError must be caught, not propagated."""
        f = tmp_path / "bad_utf8.json"
        f.write_bytes(b'\xff\xfe\x00\x01 invalid utf-8')
        result = load_clusters_json(f)
        assert result == []

    def test_non_list_json(self, tmp_path):
        """Valid JSON but not a list — should return []."""
        f = tmp_path / "dict.json"
        f.write_text('{"not": "a list"}', encoding="utf-8")
        result = load_clusters_json(f)
        assert result == []

    def test_empty_list(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]", encoding="utf-8")
        result = load_clusters_json(f)
        assert result == []


class TestExtractVideoMetadata:
    def test_basic_extraction(self):
        clusters = [
            {"name": "Tech", "videos": [
                {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Rick Astley",
                 "channel": "Rick", "published_at": "2009-10-25"}
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert "dQw4w9WgXcQ" in meta
        assert meta["dQw4w9WgXcQ"]["title"] == "Rick Astley"
        assert meta["dQw4w9WgXcQ"]["channel"] == "Rick"
        assert meta["dQw4w9WgXcQ"]["published_at"] == "2009-10-25"
        assert meta["dQw4w9WgXcQ"]["cluster"] == "Tech"

    def test_short_url(self):
        clusters = [
            {"name": "Test", "videos": [
                {"url": "https://youtu.be/abc12345678", "title": "Test Video"}
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert "abc12345678" in meta

    def test_non_youtube_url_skipped(self):
        clusters = [
            {"name": "Test", "videos": [
                {"url": "https://example.com/video", "title": "Not YouTube"}
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert len(meta) == 0

    def test_missing_url_skipped(self):
        clusters = [{"name": "Test", "videos": [{"title": "No URL"}]}]
        meta = extract_video_metadata(clusters)
        assert len(meta) == 0

    def test_cluster_id_fallback(self):
        """When cluster has no 'name', fall back to 'cluster_id'."""
        clusters = [
            {"cluster_id": "c123", "videos": [
                {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Test"}
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert meta["dQw4w9WgXcQ"]["cluster"] == "c123"

    def test_date_fallback_for_published_at(self):
        """When 'published_at' is missing, fall back to 'date'."""
        clusters = [
            {"name": "Test", "videos": [
                {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "title": "Test", "date": "2023-01-01"}
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert meta["dQw4w9WgXcQ"]["published_at"] == "2023-01-01"

    def test_empty_clusters(self):
        assert extract_video_metadata([]) == {}

    def test_multiple_videos(self):
        clusters = [
            {"name": "A", "videos": [
                {"url": "https://youtube.com/watch?v=aaaaaaaaaaa", "title": "A1"},
                {"url": "https://youtu.be/bbbbbbbbbbb", "title": "A2"},
            ]}
        ]
        meta = extract_video_metadata(clusters)
        assert len(meta) == 2
        assert "aaaaaaaaaaa" in meta
        assert "bbbbbbbbbbb" in meta
