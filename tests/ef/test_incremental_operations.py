"""K-gate #3/#6: idempotence, resume, and outage-tolerance of incremental.

Hermetic rewrite (2026-08-25): the original tests ran against LIVE state —
emit_status() wrote P:/.data/yt-is/ef/operational-status.json and
incremental_update() ran against the production catalog/qdrant/fts. That
made any worktree suite run a live-state write vector (the browser
extension's status surface got test-composed payloads) and made results
depend on the live backlog. Everything now redirectss to tmp paths with
stubbed encoder/qdrant, following the test_incremental_watermark.py
pattern; the live system is never touched.
"""

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import authority, buildspec, catalog, embedding, freshness, server
from ef import projection_server as ps

GEN = 9
BUILD_ID = f"generation/gen{GEN}-t"
TEXT = "word " * 40


class _Enc:
    def encode(self, texts):
        import numpy as np
        return np.zeros((len(texts), 4)), [[] for _ in texts]


class _DeadQC:
    """Simulates an unreachable Qdrant: every call raises."""

    def delete(self, *a, **k):
        raise ConnectionError("simulated qdrant outage")


class _QuietQC:
    def delete(self, *a, **k):
        raise AssertionError("delete must not run on the add-only path")


@pytest.fixture()
def hermetic(tmp_path, monkeypatch):
    tdb = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(tdb)
    conn.executescript("""
        CREATE TABLE transcript_cache (
            cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT,
            source TEXT, transcript TEXT, cached_at TEXT, terminal_id TEXT);
        CREATE TABLE analysis_status (
            video_id TEXT PRIMARY KEY, title TEXT, channel_id TEXT,
            published_at TEXT, duration INTEGER);
        CREATE TABLE channel_metadata (
            channel_id TEXT PRIMARY KEY, channel_title TEXT);
    """)
    conn.commit()
    conn.close()

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(
        {"indexed_watermark": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    efdata = tmp_path / "ef"
    efdata.mkdir()
    fts = sqlite3.connect(efdata / "fts5.sqlite")
    fts.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED)")
    fts.commit()
    fts.close()

    catalog_path = tmp_path / "catalog.sqlite"
    real_connect = catalog.connect
    seed = real_connect(db_path=catalog_path)
    seed.execute("insert into build_claims (generation, build_id, kind,"
                 " spec_digest) values (?, ?, 'production', 't')",
                 (GEN, BUILD_ID))
    seed.commit()
    seed.close()

    monkeypatch.setattr(authority, "TRANSCRIPTS_DB", tdb)
    monkeypatch.setattr(authority, "STATUS_DB", tdb)
    monkeypatch.setattr(authority, "QUARANTINED_VIDEO_IDS", ())
    monkeypatch.setattr(buildspec, "load_spec", lambda: {"generation": GEN})
    monkeypatch.setattr(buildspec, "spec_digest", lambda spec: "t")
    monkeypatch.setattr(buildspec, "active_generation", lambda: GEN)
    monkeypatch.setattr(embedding, "BGEM3Dual", lambda: _Enc())
    monkeypatch.setattr(server, "client", lambda: _QuietQC())
    monkeypatch.setattr(ps, "upsert_chunks", lambda *a, **k: None)
    monkeypatch.setattr(freshness, "STATE_PATH", state_path)
    monkeypatch.setattr(freshness, "STATUS_PATH", tmp_path / "operational-status.json")
    monkeypatch.setattr(freshness, "EF_DATA", efdata)
    from ef import readiness as _readiness
    monkeypatch.setattr(_readiness, "READY_FILE", efdata / "readiness.json")
    monkeypatch.setattr(catalog, "connect", lambda *a, **k: real_connect(
        db_path=catalog_path))
    return {"tdb": tdb, "state": state_path,
            "status": tmp_path / "operational-status.json"}


def _add(hermetic, video_id, cached_at, transcript=TEXT):
    conn = sqlite3.connect(hermetic["tdb"])
    conn.execute(
        "insert into transcript_cache (cache_key, video_id, lang, source,"
        " transcript, cached_at, terminal_id) values (?,?,?,?,?,?,?)",
        (f"{video_id}:en:t", video_id, "en", "ytdlp", transcript,
         cached_at, "term-1"))
    conn.commit()
    conn.close()


def test_incremental_idempotent_no_new_rows(hermetic):
    """Running incremental twice on an unchanged authority adds nothing
    the second time (content-hash short-circuit)."""
    _add(hermetic, "v1", "2026-01-02T00:00:00Z")
    _add(hermetic, "v2", "2026-01-03T00:00:00Z")
    r1 = freshness.incremental_update(batch_limit=50)
    r2 = freshness.incremental_update(batch_limit=50)
    assert r1["added"] == 2
    # the first pass advanced the watermark past both rows; the second
    # pass re-selects nothing and duplicates nothing
    assert r2["added"] == 0
    assert r2["processed"] == 0
    assert r2["indexed_watermark"] == "2026-01-03T00:00:00Z"


def test_status_surface_complete(hermetic):
    _add(hermetic, "v1", "2026-01-02T00:00:00Z")
    freshness.incremental_update(batch_limit=5)
    st = freshness.emit_status()
    required = ["active_generation", "build_id", "authority_watermark",
                "indexed_watermark", "index_lag_count",
                "oldest_unindexed_age_s", "last_index_success",
                "last_index_error", "incremental_worker_state",
                "readiness", "qdrant", "last_promotion",
                "rollback_generation", "sealed_future_shards"]
    for k in required:
        assert k in st, f"status missing {k}"
    assert st["qdrant"]["reachable"] in (True, False)
    assert "state" in st["readiness"]
    # the status surface is durable for the external monitor
    assert hermetic["status"].exists()
    assert json.loads(hermetic["status"].read_text(encoding="utf-8"))["emitted_at"]


def test_readiness_contract_states(hermetic):
    from ef import readiness
    st = readiness.get_state()
    assert st.get("state") in ("starting", "warming", "ready", "degraded",
                               "unknown")


def test_fetch_isolation_on_qdrant_outage(hermetic, monkeypatch):
    """A Qdrant failure must record an error and keep the watermark —
    never silently advance past unprocessed rows."""
    _add(hermetic, "v1", "2026-01-02T00:00:00Z")
    _add(hermetic, "v2", "2026-01-03T00:00:00Z")

    def boom():
        raise ConnectionError("simulated qdrant outage")

    monkeypatch.setattr(server, "client", boom)
    # the qdrant failure surfaces as an exception (the service layer
    # catches + retries); what must NOT happen is a watermark advance
    with pytest.raises(ConnectionError):
        freshness.incremental_update(batch_limit=5)
    st = json.loads(hermetic["state"].read_text(encoding="utf-8"))
    assert st["indexed_watermark"] == "2026-01-01T00:00:00Z"  # unchanged
    assert st.get("last_indexing_error")
