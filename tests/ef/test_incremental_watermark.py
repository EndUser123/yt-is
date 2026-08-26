"""Watermark tie-split regression tests for ef.freshness.incremental_update.

The live authority has cached_at ties of up to 7106 rows (bulk imports
write thousands of rows per second). The old code advanced the watermark
to the batch boundary's timestamp even when the batch limit had split a
tie, making the unselected rows invisible to the next run's
`cached_at > watermark` — permanently unindexed. These tests pin the
tie guard, plus NULL terminal_id inclusion (the browser-extension ingest
path writes terminal_id NULL; `NULL not like 'test%'` is NULL in SQL and
used to drop those rows silently).

Fully hermetic: authority/status/catalog/fts/state all monkeypatched to
tmp paths; encoder, qdrant client, and status emission stubbed. The
production databases are never touched.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ef import authority, buildspec, catalog, embedding, freshness, server
from ef import projection_server as ps

GEN = 9
BUILD_ID = f"generation/gen{GEN}-t"
TEXT = "word " * 40  # >100 chars, chunks into at least one piece


class _Enc:
    def encode(self, texts):
        import numpy as np
        return np.zeros((len(texts), 4)), [[] for _ in texts]


class _QC:
    def delete(self, *a, **k):
        raise AssertionError("delete must not run on the add-only happy path")


@pytest.fixture()
def hermetic(tmp_path, monkeypatch):
    """Redirect every live path and service to tmp; return helpers."""
    tdb = tmp_path / "transcripts.sqlite"
    sdb = tmp_path / "status.sqlite"
    conn = sqlite3.connect(tdb)
    conn.executescript("""
        CREATE TABLE transcript_cache (
            cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT,
            source TEXT, transcript TEXT, cached_at TEXT, terminal_id TEXT);
    """)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(sdb)
    conn.executescript("""
        CREATE TABLE analysis_status (
            video_id TEXT PRIMARY KEY, title TEXT, channel_id TEXT,
            published_at TEXT, duration INTEGER);
        CREATE TABLE channel_metadata (
            channel_id TEXT PRIMARY KEY, channel_title TEXT);
    """)
    conn.commit()
    conn.close()

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"indexed_watermark":
                                      "2026-01-01T00:00:00Z"}),
                          encoding="utf-8")
    efdata = tmp_path / "ef"
    efdata.mkdir()
    fts = sqlite3.connect(efdata / "fts5.sqlite")
    fts.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED)")
    fts.commit()
    fts.close()

    catalog_path = tmp_path / "catalog.sqlite"
    real_connect = catalog.connect
    seed = real_connect(db_path=catalog_path)
    seed.execute("insert or ignore into build_claims (generation, build_id,"
                 " kind, spec_digest) values (?, ?, 'production', 't')",
                 (GEN, BUILD_ID))
    seed.commit()
    seed.close()

    def _connect():
        # incremental_update closes its catalog connection in finally;
        # hand each call a fresh one on the same seeded file
        return real_connect(db_path=catalog_path)

    monkeypatch.setattr(authority, "TRANSCRIPTS_DB", tdb)
    monkeypatch.setattr(authority, "STATUS_DB", sdb)
    monkeypatch.setattr(authority, "QUARANTINED_VIDEO_IDS", ())
    monkeypatch.setattr(buildspec, "load_spec", lambda: {"generation": GEN})
    monkeypatch.setattr(buildspec, "spec_digest", lambda spec: "t")
    monkeypatch.setattr(buildspec, "active_generation", lambda: GEN)
    monkeypatch.setattr(embedding, "BGEM3Dual", lambda: _Enc())
    monkeypatch.setattr(server, "client", lambda: _QC())
    monkeypatch.setattr(ps, "upsert_chunks", lambda *a, **k: None)
    monkeypatch.setattr(freshness, "STATE_PATH", state_path)
    monkeypatch.setattr(freshness, "EF_DATA", efdata)
    monkeypatch.setattr(freshness, "emit_status", lambda *a, **k: {})
    monkeypatch.setattr(catalog, "connect", _connect)
    return {"tdb": tdb, "state": state_path, "open_cat": _connect}


def _add_row(hermetic, video_id, cached_at, terminal_id="term-1"):
    conn = sqlite3.connect(hermetic["tdb"])
    conn.execute(
        "insert into transcript_cache (cache_key, video_id, lang, source,"
        " transcript, cached_at, terminal_id) values (?,?,?,?,?,?,?)",
        (f"{video_id}:en:t", video_id, "en", "extension", TEXT,
         cached_at, terminal_id))
    conn.commit()
    conn.close()


def test_watermark_does_not_advance_past_split_tie(hermetic):
    # 2 rows at T2 (complete tie), 3 rows at T3 (tie that batch_limit=4
    # splits), 1 row at T4
    for vid in ("v1", "v2"):
        _add_row(hermetic, vid, "2026-01-02T00:00:00Z")
    for vid in ("v3", "v4", "v5"):
        _add_row(hermetic, vid, "2026-01-03T00:00:00Z")
    _add_row(hermetic, "v6", "2026-01-04T00:00:00Z")

    r1 = freshness.incremental_update(batch_limit=4)
    assert r1["processed"] == 4
    # old code advanced to T3 here, orphaning v5 forever
    assert r1["indexed_watermark"] == "2026-01-02T00:00:00Z"

    r2 = freshness.incremental_update(batch_limit=10)
    assert r2["processed"] == 4          # v3, v4 hash-skip; v5, v6 added
    assert r2["added"] == 2
    assert r2["indexed_watermark"] == "2026-01-04T00:00:00Z"

    cat = hermetic["open_cat"]()
    n = cat.execute("select count(*) from eu").fetchone()[0]
    cat.close()
    assert n == 6                        # nothing orphaned


def test_null_terminal_id_rows_are_indexed(hermetic):
    # ingest_extension writes terminal_id NULL; `NULL not like 'test%'`
    # is NULL in SQL and used to exclude the row from indexing entirely
    _add_row(hermetic, "ext1", "2026-01-02T00:00:00Z", terminal_id=None)
    r = freshness.incremental_update(batch_limit=10)
    assert r["processed"] == 1
    assert r["added"] == 1
    cat = hermetic["open_cat"]()
    n = cat.execute(
        "select count(*) from eu where video_id='ext1'").fetchone()[0]
    cat.close()
    assert n == 1


def test_test_terminal_rows_still_excluded(hermetic):
    _add_row(hermetic, "t1", "2026-01-02T00:00:00Z", terminal_id="test-123")
    r = freshness.incremental_update(batch_limit=10)
    assert r["processed"] == 0
    assert r["added"] == 0


def test_watermark_advances_when_boundary_tie_has_ineligible_rows(hermetic):
    """The boundary count must apply the batch's eligibility predicates:
    an ineligible row (here: an excluded source) sharing the boundary
    timestamp used to pin the watermark below it forever, stalling all
    later indexing. Caught by the codex /tp lens 2026-08-25."""
    # eligible: 2 rows at T2; boundary T3 has 2 eligible + 1 EXCLUDED-source row
    for vid in ("v1", "v2"):
        _add_row(hermetic, vid, "2026-01-02T00:00:00Z")
    for vid in ("v3", "v4"):
        _add_row(hermetic, vid, "2026-01-03T00:00:00Z")
    conn = sqlite3.connect(hermetic["tdb"])
    conn.execute(
        "insert into transcript_cache (cache_key, video_id, lang, source,"
        " transcript, cached_at, terminal_id) values (?,?,?,?,?,?,?)",
        ("inelig:en:reddit", "inelig", "en", "reddit", TEXT,
         "2026-01-03T00:00:00Z", "term-1"))
    conn.commit()
    conn.close()

    # batch_limit 4 selects every eligible row incl. both T3 rows; the
    # ineligible reddit row must NOT keep boundary_total above the
    # selected count — the watermark advances to T3, not stuck at T2
    r = freshness.incremental_update(batch_limit=4)
    assert r["processed"] == 4
    assert r["indexed_watermark"] == "2026-01-03T00:00:00Z"
    # and the next pass is a no-op (nothing left past the watermark)
    r2 = freshness.incremental_update(batch_limit=4)
    assert r2["processed"] == 0
