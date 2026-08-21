"""Tests for the channel-purge script.

Covers the destructive CLI's read-only and bookkeeping paths:
- argparse flag validation
- `select_channels` for each criteria flag + the explicit urls-file path
- `build_plan` per-channel and per-store counting

`execute_purge` is NOT exercised — it touches Qdrant, the EF catalog and
the FTS5 index, all of which would require live services. Its behaviors
are covered indirectly by the unit-tested plan.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import textwrap
from argparse import Namespace
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --- minimal schema fixtures -----------------------------------------------

CHANNEL_METADATA_DDL = textwrap.dedent("""
    CREATE TABLE channel_metadata (
        channel_url TEXT PRIMARY KEY,
        channel_id TEXT,
        channel_title TEXT,
        description TEXT,
        thumbnail_url TEXT,
        channel_status TEXT
    );
    CREATE TABLE analysis_status (
        video_id TEXT PRIMARY KEY,
        channel_id TEXT,
        status TEXT,
        published_at TEXT
    );
    CREATE TABLE channel_blocklist (
        channel_url TEXT PRIMARY KEY,
        blocked_at TEXT,
        channel_id TEXT,
        reason TEXT
    );
""").strip()


def _build_db(tmp_path, *, channels=(), videos=()):
    """Build a temp batch_status.sqlite with channel + analysis rows."""
    db = tmp_path / "batch_status.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(CHANNEL_METADATA_DDL)
    conn.executemany(
        "INSERT INTO channel_metadata (channel_url, channel_id, "
        "channel_title, description, thumbnail_url, channel_status) "
        "VALUES (?, ?, ?, ?, ?, ?)", channels)
    conn.executemany(
        "INSERT INTO analysis_status (video_id, channel_id, status, "
        "published_at) VALUES (?, ?, ?, ?)", videos)
    conn.commit()
    conn.close()
    return db


# --- module-level fixture: point at tmp paths ------------------------------


@pytest.fixture
def purge(tmp_path, monkeypatch):
    import scripts.purge_channels as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "DB", tmp_path / "batch_status.sqlite")
    monkeypatch.setattr(mod, "TDB", tmp_path / "transcripts.sqlite")
    monkeypatch.setattr(mod, "CATALOG", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(mod, "FTS", tmp_path / "fts5.sqlite")
    monkeypatch.setattr(mod, "VISUAL_ROOT", tmp_path / "visual")
    monkeypatch.setattr(mod, "RECEIPT_DIR", tmp_path / "receipts")
    return mod


def _args(**kw):
    """Build a minimal Namespace with safe defaults."""
    base = dict(
        urls_file=None,
        missing_description=False,
        missing_thumbnail=False,
        dead=False,
        no_completion=False,
        stale_days=None,
        require_all=False,
        confirm=False,
        and_blacklist=False,
    )
    base.update(kw)
    return Namespace(**base)


# === argparse validation ===================================================


def test_main_requires_at_least_one_selection_flag(purge, tmp_path, capsys):
    from scripts import purge_channels
    with pytest.raises(SystemExit) as exc:
        purge_channels.main([])
    assert exc.value.code == 2  # argparse error exit code
    err = capsys.readouterr().err
    assert "select channels" in err


def test_main_no_args_dry_run_writes_receipt(purge, tmp_path):
    """No matching channels means no-op exit 0."""
    _build_db(tmp_path)
    rc = purge.main(["--missing-description"])
    assert rc == 0
    # No matching channels -> no receipt written
    assert list(purge.RECEIPT_DIR.glob("*.json")) == []


# === select_channels: each criteria flag ==================================


def test_select_missing_description(purge, tmp_path):
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", "has desc", "thumb", None),
        ("https://x/b", "UC2", "Beta", None, "thumb", None),
        ("https://x/c", "UC3", "Gamma", "", "thumb", None),
    ])
    rows = purge.select_channels(_args(missing_description=True))
    urls = sorted(r["url"] for r in rows)
    assert urls == ["https://x/b", "https://x/c"]


def test_select_missing_thumbnail(purge, tmp_path):
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", "d", "thumb", None),
        ("https://x/b", "UC2", "Beta", "d", None, None),
        ("https://x/c", "UC3", "Gamma", "d", "", None),
    ])
    rows = purge.select_channels(_args(missing_thumbnail=True))
    assert sorted(r["url"] for r in rows) == ["https://x/b", "https://x/c"]


def test_select_dead(purge, tmp_path):
    """Channels with non-empty channel_status are dead."""
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", "d", "t", None),
        ("https://x/b", "UC2", "Beta", "d", "t", "deleted"),
        ("https://x/c", "UC3", "Gamma", "d", "t", ""),
    ])
    rows = purge.select_channels(_args(dead=True))
    assert [r["url"] for r in rows] == ["https://x/b"]


def test_select_no_completion(purge, tmp_path):
    """Channels with zero completed analyses match."""
    _build_db(tmp_path,
              channels=[
                  ("https://x/a", "UC1", "Alpha", "d", "t", None),
                  ("https://x/b", "UC2", "Beta", "d", "t", None),
              ],
              videos=[
                  ("v1", "UC1", "complete", "2026-08-19"),
                  ("v2", "UC1", "complete", "2026-08-20"),
                  ("v3", "UC2", "pending", "2026-08-19"),
              ])
    rows = purge.select_channels(_args(no_completion=True))
    urls = [r["url"] for r in rows]
    # UC1 has 2 complete videos, so NOT no_completion; UC2 has 0 complete.
    assert urls == ["https://x/b"]


def test_select_stale_days(purge, tmp_path):
    """Channels whose newest video is older than N days match."""
    _build_db(tmp_path,
              channels=[
                  ("https://x/a", "UC1", "Alpha", "d", "t", None),
                  ("https://x/b", "UC2", "Beta", "d", "t", None),
              ],
              videos=[
                  ("v1", "UC1", "complete", "2026-08-19"),
                  ("v2", "UC2", "complete", "2024-01-01"),  # ancient
              ])
    rows = purge.select_channels(_args(stale_days=30))
    # UC1's newest is 2026-08-19 (~2 days ago); UC2's is 2024-01-01.
    assert [r["url"] for r in rows] == ["https://x/b"]


def test_select_stale_days_handles_null_published_at(purge, tmp_path):
    """The COALESCE(null, '') < date-N means NULL published_at is treated as
    the empty string, which is always older than any concrete date. So
    videos without a published_at date count as stale."""
    _build_db(tmp_path,
              channels=[("https://x/a", "UC1", "Alpha", "d", "t", None)],
              videos=[
                  ("v1", "UC1", "complete", None),
              ])
    rows = purge.select_channels(_args(stale_days=30))
    assert [r["url"] for r in rows] == ["https://x/a"]


def test_select_require_all_intersects(purge, tmp_path):
    """`--require-all` joins criteria with AND instead of OR."""
    _build_db(tmp_path, channels=[
        # missing-description + dead
        ("https://x/a", "UC1", "Alpha", None, "t", "deleted"),
        # missing-description only
        ("https://x/b", "UC2", "Beta", None, "t", None),
        # dead only
        ("https://x/c", "UC3", "Gamma", "d", "t", "deleted"),
    ])
    rows = purge.select_channels(_args(
        missing_description=True, dead=True, require_all=True))
    assert [r["url"] for r in rows] == ["https://x/a"]


def test_select_via_urls_file(purge, tmp_path):
    """The urls-file path matches by channel_url OR channel_id."""
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", "d", "t", None),
        ("https://x/b", "UC2", "Beta", "d", "t", None),
        ("https://x/c", "UC3", "Gamma", "d", "t", None),
    ])
    urls = tmp_path / "channels.txt"
    urls.write_text(
        "https://x/a\nUC3\n# comment\n\n",
        encoding="utf-8")
    rows = purge.select_channels(_args(urls_file=str(urls)))
    urls_matched = sorted(r["url"] for r in rows)
    assert urls_matched == ["https://x/a", "https://x/c"]


def test_select_returns_empty_when_nothing_matches(purge, tmp_path):
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", "d", "t", None),
    ])
    rows = purge.select_channels(_args(dead=True))
    assert rows == []


# === build_plan ============================================================


def _stub_tdb(tmp_path, *, rows=()):
    tdb = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(tdb)
    conn.executescript("""
        CREATE TABLE transcript_cache (
            cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL,
            transcript TEXT);
    """)
    conn.executemany(
        "INSERT INTO transcript_cache (cache_key, video_id, transcript) "
        "VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _stub_catalog(tmp_path, *, rows=()):
    cat = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(cat)
    conn.executescript("""
        CREATE TABLE eu (eu_id TEXT PRIMARY KEY, video_id TEXT);
        CREATE TABLE chunk (chunk_id TEXT PRIMARY KEY, eu_id TEXT);
    """)
    conn.executemany(
        "INSERT INTO eu (eu_id, video_id) VALUES (?, ?)",
        [(f"eu{i}", vid) for i, (vid,) in enumerate(rows)])
    conn.executemany(
        "INSERT INTO chunk (chunk_id, eu_id) VALUES (?, ?)",
        [(f"ck{i}", f"eu{i}") for i in range(len(rows))])
    conn.commit()
    conn.close()


def test_build_plan_zero_for_empty_set(purge, tmp_path):
    _build_db(tmp_path)
    plan = purge.build_plan([])
    assert plan["channels"] == []
    assert plan["videos"] == 0
    assert plan["transcripts"] == 0
    assert plan["chunks"] == 0
    assert plan["visual_dirs"] == 0
    assert plan["per_channel"] == []


def test_build_plan_counts_per_channel(purge, tmp_path):
    _build_db(tmp_path,
              channels=[
                  ("https://x/a", "UC1", "Alpha", "d", "t", None),
                  ("https://x/b", "UC2", "Beta", "d", "t", None),
              ],
              videos=[
                  ("v1", "UC1", "complete", "2026-08-19"),
                  ("v2", "UC1", "complete", "2026-08-19"),
                  ("v3", "UC1", "pending", "2026-08-19"),
                  ("v4", "UC2", "complete", "2026-08-19"),
              ])
    _stub_tdb(tmp_path, rows=[
        ("k1", "v1", "transcript 1"),
        ("k2", "v2", "transcript 2"),
        ("k3", "v3", "transcript 3"),
        ("k4", "v4", "transcript 4"),
    ])
    _stub_catalog(tmp_path, rows=[("v1",), ("v2",), ("v3",), ("v4",)])

    plan = purge.build_plan([
        {"url": "https://x/a", "id": "UC1", "title": "Alpha"},
        {"url": "https://x/b", "id": "UC2", "title": "Beta"},
    ])
    assert plan["videos"] == 4
    assert plan["transcripts"] == 4
    assert plan["chunks"] == 4
    per = {p["url"]: p for p in plan["per_channel"]}
    assert per["https://x/a"]["videos"] == 3
    assert per["https://x/a"]["complete"] == 2
    assert per["https://x/b"]["videos"] == 1
    assert per["https://x/b"]["complete"] == 1


def test_build_plan_counts_visual_dirs(purge, tmp_path):
    """Only existing video_id dirs under VISUAL_ROOT are counted."""
    _build_db(tmp_path,
              channels=[("https://x/a", "UC1", "Alpha", "d", "t", None)],
              videos=[("v1", "UC1", "complete", "2026-08-19")])
    (purge.VISUAL_ROOT / "v1").mkdir(parents=True)
    (purge.VISUAL_ROOT / "v2").mkdir(parents=True)  # orphan, not counted

    _stub_tdb(tmp_path)
    _stub_catalog(tmp_path)

    plan = purge.build_plan([{"url": "https://x/a", "id": "UC1",
                              "title": "Alpha"}])
    assert plan["visual_dirs"] == 1


def test_build_plan_returns_video_ids_for_execute(purge, tmp_path):
    """The plan must include the per-video id list so execute_purge can
    target the correct rows in transcript_cache / chunk / chunk_clusters."""
    _build_db(tmp_path,
              channels=[("https://x/a", "UC1", "Alpha", "d", "t", None)],
              videos=[
                  ("v1", "UC1", "complete", "2026-08-19"),
                  ("v2", "UC1", "complete", "2026-08-19"),
              ])
    _stub_tdb(tmp_path)
    _stub_catalog(tmp_path)
    plan = purge.build_plan([{"url": "https://x/a", "id": "UC1",
                              "title": "Alpha"}])
    assert sorted(plan["video_ids"]) == ["v1", "v2"]


def test_build_plan_early_returns_when_all_ids_null(purge, tmp_path):
    """When every selected channel has a NULL id, build_plan cannot
    target any store rows, so it returns an empty plan (no per_channel
    entries, no video_ids). Documenting the actual behavior."""
    _build_db(tmp_path,
              channels=[("https://x/a", None, "Anonymous", "d", "t", None)],
              videos=[])
    plan = purge.build_plan([{"url": "https://x/a", "id": None,
                              "title": "Anonymous"}])
    assert plan["videos"] == 0
    assert plan["per_channel"] == []
    assert "video_ids" not in plan or plan["video_ids"] == []


# === main() with --confirm: receipt roundtrip ==============================


def test_main_dry_run_writes_receipt(purge, tmp_path):
    _build_db(tmp_path, channels=[
        # None description -> matches --missing-description
        ("https://x/a", "UC1", "Alpha", None, "t", None),
    ])
    rc = purge.main(["--missing-description"])
    assert rc == 0
    receipt_files = list(purge.RECEIPT_DIR.glob("*.json"))
    assert len(receipt_files) == 1
    receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
    assert receipt["mode"] == "dry-run"
    assert receipt["blacklist"] is False
    assert len(receipt["plan"]["channels"]) == 1


def test_main_dry_run_receipt_includes_matched_channels(purge, tmp_path):
    _build_db(tmp_path, channels=[
        ("https://x/a", "UC1", "Alpha", None, "t", None),  # missing desc
    ])
    rc = purge.main(["--missing-description"])
    assert rc == 0
    receipt = json.loads(
        list(purge.RECEIPT_DIR.glob("*.json"))[0].read_text(encoding="utf-8"))
    assert len(receipt["plan"]["channels"]) == 1
    assert receipt["plan"]["channels"][0]["url"] == "https://x/a"
