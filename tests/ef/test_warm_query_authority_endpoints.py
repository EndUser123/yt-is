"""Tests for the authority endpoints added to ef.warm_query_service:
library_lookup (read-only presence) and reopen_exact (exact span reopen).

Follows the _chs_search test pattern: helpers take injectable DB paths;
production databases are never touched. These tests cover the extension's
authority contract: presence without content, exact-length reopen text,
and fail-closed behavior for unknown or over-long spans.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ef.warm_query_service import library_lookup, reopen_exact


@pytest.fixture()
def dbs(tmp_path):
    catalog = tmp_path / "catalog.sqlite"
    transcripts = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(catalog)
    conn.executescript("""
        CREATE TABLE eu (
            eu_id text primary key,
            media_kind text not null,
            video_id text not null,
            channel_id text not null default '',
            channel_title text not null default '',
            title text not null default '',
            lang text not null,
            source text not null,
            authority_ref text not null,
            content_hash text not null,
            captured_at text not null,
            published_at text not null default '',
            duration_s integer not null default 0,
            char_length integer not null
        );
    """)
    conn.execute(
        "insert into eu values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("vidA:transcript", "transcript", "vidA", "chan", "Channel", "Title",
         "en", "android-player", "key-A", "hash", "2026-08-01T00:00:00Z",
         "", 100, 1000))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(transcripts)
    conn.execute(
        "CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY, "
        "video_id TEXT, transcript TEXT)")
    conn.execute(
        "insert into transcript_cache values ('key-A','vidA',?)",
        ("0123456789" * 10,))
    conn.commit()
    conn.close()
    return catalog, transcripts


def test_library_lookup_reports_presence_without_content(dbs):
    catalog, _ = dbs
    result = library_lookup("vidA", catalog_db=catalog)
    assert result["status"] == "in_library"
    assert result["video_id"] == "vidA"
    assert result["eu_id"] == "vidA:transcript"
    assert result["transcript_chars"] == 1000
    assert result["transcript_source"] == "android-player"
    assert result["cached_at"] == "2026-08-01T00:00:00Z"
    assert "transcript" not in result or result.get("transcript") is None


def test_library_lookup_missing_video_is_not_found(dbs):
    catalog, _ = dbs
    assert library_lookup("missing", catalog_db=catalog) == {
        "video_id": "missing", "status": "not_found"}


def test_reopen_exact_returns_exact_length_span(dbs):
    catalog, transcripts = dbs
    result = reopen_exact(
        "vidA:transcript", 10, 20,
        catalog_db=catalog, transcripts_db=transcripts)
    assert result is not None
    assert result["eu_id"] == "vidA:transcript"
    assert result["video_id"] == "vidA"
    assert result["start_char"] == 10
    assert result["end_char"] == 20
    assert result["text"] == "0123456789"
    assert len(result["text"]) == result["end_char"] - result["start_char"]


def test_reopen_exact_unknown_eu_fails_closed(dbs):
    catalog, transcripts = dbs
    assert reopen_exact(
        "nope:transcript", 0, 5,
        catalog_db=catalog, transcripts_db=transcripts) is None


def test_reopen_exact_span_past_authority_fails_closed(dbs):
    catalog, transcripts = dbs
    # substr returns fewer chars than requested when the span runs past
    # the authority text; the mismatch must fail closed, not truncate.
    assert reopen_exact(
        "vidA:transcript", 90, 5000,
        catalog_db=catalog, transcripts_db=transcripts) is None


def _transcripts_db(tmp_path):
    import sqlite3
    db = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY, "
        "video_id TEXT, lang TEXT, source TEXT, transcript TEXT, "
        "metadata_json TEXT, cached_at TEXT, terminal_id TEXT)")
    conn.commit()
    conn.close()
    return db


def test_ingest_extension_is_idempotent_per_video(tmp_path):
    from ef.warm_query_service import ingest_extension
    db = _transcripts_db(tmp_path)
    payload = {"videoId": "vidNEW", "provider": "timedtext", "title": "T",
               "url": "https://youtu.be/vidNEW",
               "segments": [{"text": "hello world"}, {"text": "second line"}]}
    assert ingest_extension(payload, transcripts_db=db) == (
        200, {"status": "saved", "transcriptChars": 23})
    # same save twice -> one source authority
    assert ingest_extension(payload, transcripts_db=db)[1]["status"] == "already_present"
    # another provider for the same video never duplicates the authority
    other = dict(payload, provider="notebooklm")
    assert ingest_extension(other, transcripts_db=db)[1]["status"] == "already_present"
    # same cache_key with different content is a conflict, not a rewrite
    conflict = dict(payload, segments=[{"text": "different"}])
    assert ingest_extension(conflict, transcripts_db=db) == (
        409, {"error": "existing_transcript_differs"})


def test_ingest_extension_rejects_malformed_payloads(tmp_path):
    from ef.warm_query_service import ingest_extension
    db = _transcripts_db(tmp_path)
    good_segments = [{"text": "x"}]
    for bad in [
        {"videoId": "../evil", "segments": good_segments},
        {"videoId": "ok", "segments": []},
        {"videoId": "ok", "segments": [{"text": "   "}]},
        {"videoId": "", "segments": good_segments},
    ]:
        assert ingest_extension(bad, transcripts_db=db)[0] == 400
