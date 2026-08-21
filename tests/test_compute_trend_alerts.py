"""Tests for scripts/compute_trend_alerts.py.

Covers:
    - is_series clusters excluded (defense in depth)
    - below-threshold rows dropped
    - new arrivals (pct=999) preserved when new_chunks >= min
    - persistence is idempotent per day
    - env-configurable thresholds
    - get_today_alerts returns empty when table or batch is empty

Pattern: tmp_path catalog, monkeypatched module-level CATALOG path,
seeded with topic_clusters + chunk_clusters rows. No network, no LLM,
no live services (per the handoff's UNIT_TEST + no-live-services rule).
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _seed_catalog(catalog: Path,
                  now: datetime,
                  topics: list[dict]) -> None:
    """Seed topic_clusters + chunk_clusters + chunk assignments.

    Each topic dict: {cluster_id, label, is_series, current_assignments,
                      prev_assignments, days_back=7}
    `current_assignments` and `prev_assignments` are integer counts of
    chunk rows to insert; we spread them through the windows so
    `_topic_trends` can compute a stable pct change.
    """
    catalog.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(catalog))
    conn.executescript("""
        CREATE TABLE topic_clusters (
            cluster_id INTEGER PRIMARY KEY,
            label TEXT,
            is_series INTEGER DEFAULT 0,
            member_count INTEGER DEFAULT 0
        );
        CREATE TABLE chunk_clusters (
            chunk_id TEXT PRIMARY KEY,
            point_id INTEGER,
            video_id TEXT,
            cluster_id INTEGER,
            assigned_at TEXT NOT NULL
        );
    """)
    for t in topics:
        conn.execute(
            "INSERT INTO topic_clusters (cluster_id, label, is_series) "
            "VALUES (?, ?, ?)",
            (t["cluster_id"], t["label"], t.get("is_series", 0)))
        for i in range(t["current_assignments"]):
            at = (now - timedelta(hours=12)).isoformat()  # within 24h window
            conn.execute(
                "INSERT INTO chunk_clusters "
                "(chunk_id, cluster_id, assigned_at) "
                "VALUES (?, ?, ?)",
                (f"c{t['cluster_id']}c{i}", t["cluster_id"], at))
        for i in range(t["prev_assignments"]):
            at = (now - timedelta(days=2)).isoformat()  # in prior window
            conn.execute(
                "INSERT INTO chunk_clusters "
                "(chunk_id, cluster_id, assigned_at) "
                "VALUES (?, ?, ?)",
                (f"c{t['cluster_id']}p{i}", t["cluster_id"], at))
    conn.commit()
    conn.close()


def _trends_payload(topics: list[dict]) -> dict:
    """Build a fake `_topic_trends()` return value covering all windows.

    Each window gets the same shape `_topic_trends` produces:
    {window_name: {most_new: [...], biggest_change: [...]}}.
    """
    out: dict = {}
    for w in ("24h", "72h", "7d"):
        items = []
        for t in topics:
            cur, prev = t["current_assignments"], t["prev_assignments"]
            if cur == 0:
                continue
            if prev == 0:
                pct = 999.0
            else:
                pct = round((cur - prev) / prev * 100.0, 1)
            items.append({
                "topic": t["label"],
                "pct": pct,
                "current": cur,
                "previous": prev,
            })
        items.sort(key=lambda x: -x["pct"])
        out[w] = {"most_new": [], "biggest_change": items}
    return out


@pytest.fixture
def alerts_mod(tmp_path, monkeypatch):
    """Load compute_trend_alerts pointed at a tmp catalog.

    Stubs `_topic_trends` so the test doesn't depend on real DB state.
    """
    catalog = tmp_path / "catalog.sqlite"
    # Seed the catalog so persist_alerts can resolve cluster_id by label
    now = datetime.now(timezone.utc)
    _seed_catalog(catalog, now, [
        {"cluster_id": 1, "label": "Rising Topic",
         "current_assignments": 80, "prev_assignments": 20},   # +300%
        {"cluster_id": 2, "label": "Below Threshold",
         "current_assignments": 60, "prev_assignments": 30},   # +100% (under 200)
        {"cluster_id": 3, "label": "Series Topic",
         "is_series": 1,
         "current_assignments": 100, "prev_assignments": 10},  # +900% but is_series
        {"cluster_id": 4, "label": "Tiny Topic",
         "current_assignments": 10, "prev_assignments": 2},    # noise floor
        {"cluster_id": 5, "label": "New Arrival",
         "current_assignments": 60, "prev_assignments": 0},    # pct=999, >= min
    ])

    monkeypatch.setenv("YTIS_TREND_ALERT_PCT", "200")
    monkeypatch.setenv("YTIS_TREND_ALERT_MIN_CHUNKS", "50")

    # Import after env vars so module-level constants see them at first call
    if "scripts.compute_trend_alerts" in sys.modules:
        del sys.modules["scripts.compute_trend_alerts"]
    sys.path.insert(0, str(_ROOT))
    mod = importlib.import_module("scripts.compute_trend_alerts")
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "CATALOG", catalog)

    # Stub _topic_trends on the warm_query_service module so the test
    # doesn't touch the live catalog. The real one would re-derive
    # exactly the same payload for this seed.
    import ef.warm_query_service as wqs
    fake = _trends_payload([
        {"cluster_id": 1, "label": "Rising Topic",
         "current_assignments": 80, "prev_assignments": 20},
        {"cluster_id": 2, "label": "Below Threshold",
         "current_assignments": 60, "prev_assignments": 30},
        {"cluster_id": 3, "label": "Series Topic",
         "current_assignments": 100, "prev_assignments": 10},
        {"cluster_id": 4, "label": "Tiny Topic",
         "current_assignments": 10, "prev_assignments": 2},
        {"cluster_id": 5, "label": "New Arrival",
         "current_assignments": 60, "prev_assignments": 0},
    ])
    monkeypatch.setattr(wqs, "_topic_trends", lambda: fake)
    # Also patch the reference the alerts module already imported
    monkeypatch.setattr(mod, "_topic_trends", lambda: fake)

    return mod, catalog


def test_thresholds_default_to_handoff_values(monkeypatch):
    monkeypatch.delenv("YTIS_TREND_ALERT_PCT", raising=False)
    monkeypatch.delenv("YTIS_TREND_ALERT_MIN_CHUNKS", raising=False)
    if "scripts.compute_trend_alerts" in sys.modules:
        del sys.modules["scripts.compute_trend_alerts"]
    mod = importlib.import_module("scripts.compute_trend_alerts")
    pct, mn = mod._thresholds()
    assert pct == 200.0
    assert mn == 50


def test_thresholds_pick_up_env(monkeypatch):
    monkeypatch.setenv("YTIS_TREND_ALERT_PCT", "350")
    monkeypatch.setenv("YTIS_TREND_ALERT_MIN_CHUNKS", "75")
    if "scripts.compute_trend_alerts" in sys.modules:
        del sys.modules["scripts.compute_trend_alerts"]
    mod = importlib.import_module("scripts.compute_trend_alerts")
    pct, mn = mod._thresholds()
    assert pct == 350.0
    assert mn == 75


def test_compute_filters_below_threshold(alerts_mod):
    mod, _ = alerts_mod
    alerts = mod.compute_alerts_for_today(200.0, 50)
    topics = {(a["window"], a["topic"]) for a in alerts}
    # Rising Topic (+300% >= 200) and Series Topic (+900% but is_series
    # is filtered by _topic_trends, so the fake still has it because
    # the stub returns the raw payload — the series filter is applied
    # in persist_alerts via _is_series_topics, not in compute.
    # Below Threshold (+100%) is dropped. Tiny Topic (10 < 50) is dropped.
    # New Arrival (pct=999, 60 >= 50) is kept.
    by_topic = {a["topic"]: a for a in alerts}
    assert "Below Threshold" not in by_topic
    assert "Tiny Topic" not in by_topic
    assert "New Arrival" in by_topic
    assert "Rising Topic" in by_topic


def test_persist_excludes_series_clusters(alerts_mod):
    mod, catalog = alerts_mod
    alerts = mod.compute_alerts_for_today(200.0, 50)
    written = mod.persist_alerts(alerts, catalog_path=catalog)
    # Rising Topic + New Arrival across 3 windows each = 6 rows.
    # Series Topic is dropped by _is_series_topics defense in depth.
    # Below Threshold + Tiny Topic are dropped by compute.
    assert written == 6
    rows = list(alerts_mod_rows(catalog))
    topics = {r[1] for r in rows}
    assert "Series Topic" not in topics
    assert "Rising Topic" in topics
    assert "New Arrival" in topics


def test_persist_idempotent_per_day(alerts_mod):
    mod, catalog = alerts_mod
    alerts1 = mod.compute_alerts_for_today(200.0, 50)
    n1 = mod.persist_alerts(alerts1, catalog_path=catalog)
    # Re-running replaces the day's batch, not appends.
    n2 = mod.persist_alerts(alerts1, catalog_path=catalog)
    assert n1 == n2
    total = sum(1 for _ in alerts_mod_rows(catalog))
    assert total == n1


def test_persist_replaces_with_empty_batch(alerts_mod):
    mod, catalog = alerts_mod
    mod.persist_alerts(mod.compute_alerts_for_today(200.0, 50),
                       catalog_path=catalog)
    # If compute produces nothing (e.g. thresholds tightened), the day's
    # prior batch is wiped — the panel should not display stale alerts.
    mod.persist_alerts([], catalog_path=catalog)
    rows = list(alerts_mod_rows(catalog))
    assert rows == []


def test_get_today_alerts_returns_persisted_rows(alerts_mod):
    mod, catalog = alerts_mod
    mod.persist_alerts(mod.compute_alerts_for_today(200.0, 50),
                       catalog_path=catalog)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = mod.get_today_alerts(catalog_path=catalog, day=today)
    assert len(rows) == 6
    # Display order: 24h, 72h, 7d; within each window, pct desc.
    windows = [r["window"] for r in rows]
    assert windows == sorted(windows, key=lambda w: ({"24h": 0, "72h": 1, "7d": 2}.get(w, 99)))


def test_get_today_alerts_empty_when_no_batch(alerts_mod):
    mod, catalog = alerts_mod
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert mod.get_today_alerts(catalog_path=catalog, day=today) == []


def test_get_today_alerts_handles_missing_catalog(tmp_path, alerts_mod):
    mod, _ = alerts_mod
    missing = tmp_path / "nope" / "catalog.sqlite"
    assert mod.get_today_alerts(catalog_path=missing) == []


def test_ensure_schema_creates_table_and_index(tmp_path):
    catalog = tmp_path / "fresh.sqlite"
    if "scripts.compute_trend_alerts" in sys.modules:
        del sys.modules["scripts.compute_trend_alerts"]
    mod = importlib.import_module("scripts.compute_trend_alerts")
    conn = sqlite3.connect(str(catalog))
    try:
        mod._ensure_schema(conn)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "trend_alerts" in names
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_trend_alerts_day" in idx
    finally:
        conn.close()


# --- helpers ---------------------------------------------------------------

def alerts_mod_rows(catalog: Path):
    """Yield (day, topic, cluster_id, window) tuples from the catalog."""
    conn = sqlite3.connect(str(catalog))
    try:
        for r in conn.execute(
            "SELECT day, topic, cluster_id, window "
            "FROM trend_alerts ORDER BY day, window, topic"
        ):
            yield r
    finally:
        conn.close()
