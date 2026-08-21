"""Daily trend-alert computation and persistence.

Imports `_topic_trends()` from `ef.warm_query_service` (do not fork the
logic), keeps only the items that pass the operator-configurable
thresholds, re-verifies series exclusion at the alert boundary (defense
in depth — `_topic_trends()` already filters is_series=1, but a future
regression there would silently leak serial content into alerts), and
writes one row per (cluster_id, window) per day. Re-running on the same
day replaces the day's rows; the table never grows unbounded.

Default thresholds are tuned for the existing corpus:
    YTIS_TREND_ALERT_PCT       = 200  # % change vs prior equal-length window
    YTIS_TREND_ALERT_MIN_CHUNKS = 50  # absolute floor; MIN_VOLUME (25) is the
                                       # noise floor; 50 is the "real movement"
                                       # bar for the daily brief

Windowless: the script is meant to run once a day from `run_all_syncs.py`
in the 06:00 task, AFTER `run_topic_assignment.py` so today's data is in.

Usage:
    python scripts/compute_trend_alerts.py            # default thresholds
    python scripts/compute_trend_alerts.py --verbose  # log decisions

Falsifier (per handoff TA-01):
    - any alert whose topic sits on an is_series cluster
    - any alert with new_chunks < MIN_VOLUME (25) — the noise-floor bypass
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef.warm_query_service import _topic_trends   # noqa: E402

# Defense-in-depth floor: `_topic_trends()` already drops items below
# MIN_VOLUME, but we re-check at the alert boundary so a regression in
# trends.py can't silently leak 3→9 blips into the daily brief.
NOISE_FLOOR_CHUNKS = 25

# Operator-tunable thresholds (env vars). Default to the handoff's
# "ship the defaults now" choice (200% / 50 chunks).
DEFAULT_PCT = 200.0
DEFAULT_MIN_CHUNKS = 50

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")


def _thresholds() -> tuple[float, int]:
    pct = float(os.environ.get("YTIS_TREND_ALERT_PCT", DEFAULT_PCT))
    min_chunks = int(os.environ.get("YTIS_TREND_ALERT_MIN_CHUNKS",
                                    DEFAULT_MIN_CHUNKS))
    return pct, min_chunks


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: create the alerts table + index if absent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trend_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,                  -- YYYY-MM-DD (UTC) the alert
                                                -- belongs to; used for the
                                                -- per-day idempotency wipe
            topic TEXT NOT NULL,
            cluster_id INTEGER NOT NULL,
            window TEXT NOT NULL,               -- '24h' | '72h' | '7d'
            pct REAL NOT NULL,                  -- 999 = "new arrival" sentinel
            new_chunks INTEGER NOT NULL,        -- current-window chunk count
            prev_chunks INTEGER NOT NULL,       -- prior-window chunk count
            computed_at TEXT NOT NULL,           -- ISO timestamp of this run
            UNIQUE (day, cluster_id, window)
        );
        CREATE INDEX IF NOT EXISTS idx_trend_alerts_day
            ON trend_alerts (day, window);
    """)
    conn.commit()


def _is_series_topics(conn: sqlite3.Connection,
                      cluster_ids: list[int]) -> set[int]:
    """Return the subset of `cluster_ids` flagged is_series=1.

    `_topic_trends()` already filters these out, but we re-check at the
    alert boundary — a single regression in trends.py would otherwise
    silently flood the daily brief with serial content. Cheap, defensive.
    """
    if not cluster_ids:
        return set()
    placeholders = ",".join("?" for _ in cluster_ids)
    rows = conn.execute(
        f"SELECT cluster_id FROM topic_clusters "
        f"WHERE cluster_id IN ({placeholders}) "
        f"  AND COALESCE(is_series, 0) = 1",
        cluster_ids).fetchall()
    return {r[0] for r in rows}


def compute_alerts_for_today(pct_threshold: float,
                             min_chunks: int,
                             day: str | None = None,
                             now: datetime | None = None
                             ) -> list[dict]:
    """Return the alerts that pass the thresholds, in display order.

    Does not touch the database; `persist_alerts()` is the writer. Splitting
    compute/persist makes the unit tests fast and the failure modes obvious.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if day is None:
        day = now.strftime("%Y-%m-%d")

    trends = _topic_trends() or {}
    out: list[dict] = []
    for window_name in ("24h", "72h", "7d"):
        bucket = trends.get(window_name, {})
        for item in (bucket.get("biggest_change") or []):
            topic = item.get("topic", "")
            pct = item.get("pct", 0.0)
            current = int(item.get("current", 0))
            previous = int(item.get("previous", 0))
            if not topic:
                continue
            if current < min_chunks:
                continue                       # absolute floor
            if current < NOISE_FLOOR_CHUNKS:
                continue                       # defense-in-depth
            if pct < pct_threshold and pct != 999.0:
                continue                       # threshold; keep new arrivals
            out.append({
                "topic": topic,
                "window": window_name,
                "pct": pct,
                "new_chunks": current,
                "prev_chunks": previous,
                "computed_at": now.isoformat(),
                "day": day,
            })
    # Stable order: window order (24h, 72h, 7d), then largest % within.
    window_rank = {"24h": 0, "72h": 1, "7d": 2}
    out.sort(key=lambda a: (window_rank.get(a["window"], 99), -a["pct"]))
    return out


def _resolve_cluster_ids(conn: sqlite3.Connection,
                         topics: list[str]) -> dict[str, int]:
    """Map topic label -> cluster_id so the alert rows can carry the join key.

    Labels are unique post-relabel (date tokens filtered, 34 series
    flagged), so first-match is safe.
    """
    out: dict[str, int] = {}
    if not topics:
        return out
    placeholders = ",".join("?" for _ in topics)
    rows = conn.execute(
        f"SELECT cluster_id, label FROM topic_clusters "
        f"WHERE label IN ({placeholders})",
        topics).fetchall()
    for cid, label in rows:
        out.setdefault(label, cid)
    return out


def persist_alerts(alerts: list[dict],
                   catalog_path: Path | str = CATALOG,
                   day: str | None = None) -> int:
    """Write `alerts` to `catalog_path`, replacing the prior row set for `day`.

    Returns the number of rows written. Idempotent per (day, cluster_id,
    window) — re-running on the same day wipes and re-inserts, so a
    later threshold change or cluster relabel takes effect immediately.
    """
    catalog_path = Path(catalog_path)
    if day is None and alerts:
        day = alerts[0]["day"]
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(catalog_path), timeout=10.0)
    try:
        _ensure_schema(conn)

        # Defense-in-depth series check on the cluster_ids we're about
        # to write. Skips the row entirely if its topic sits on a serial.
        topics = [a["topic"] for a in alerts]
        cid_by_topic = _resolve_cluster_ids(conn, topics)
        series_cids = _is_series_topics(
            conn, list({cid for cid in cid_by_topic.values()}))

        # Wipe the day's prior batch — this is the idempotency guarantee.
        conn.execute("DELETE FROM trend_alerts WHERE day = ?", (day,))

        written = 0
        for a in alerts:
            cid = cid_by_topic.get(a["topic"])
            if cid is None:
                # topic label no longer in topic_clusters (e.g. merged)
                # — skip; do not write orphan rows.
                continue
            if cid in series_cids:
                continue
            conn.execute(
                "INSERT INTO trend_alerts "
                "(day, topic, cluster_id, window, pct, new_chunks, "
                " prev_chunks, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (a["day"], a["topic"], cid, a["window"], a["pct"],
                 a["new_chunks"], a["prev_chunks"], a["computed_at"]))
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def get_today_alerts(catalog_path: Path | str = CATALOG,
                     day: str | None = None) -> list[dict]:
    """Read-only fetch of today's alerts in display order.

    Used by `_render_home_page()` and `generate_digest.py`. Returns
    empty list when the table or the day's batch is empty so callers
    can branch on truthiness without error handling.
    """
    catalog_path = Path(catalog_path)
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not catalog_path.exists():
        return []
    conn = sqlite3.connect(
        f"file:{catalog_path}?mode=ro", uri=True, timeout=5.0)
    try:
        # Caller may invoke before compute_trend_alerts has ever run;
        # the table won't exist yet. Treat that as "no alerts today".
        try:
            rows = conn.execute("""
                SELECT topic, window, pct, new_chunks, prev_chunks
                FROM trend_alerts WHERE day = ?
                ORDER BY CASE window WHEN '24h' THEN 0 WHEN '72h' THEN 1
                                      WHEN '7d' THEN 2 ELSE 9 END,
                         pct DESC
            """, (day,)).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"topic": t, "window": w, "pct": p, "new_chunks": nc,
             "prev_chunks": pc}
            for t, w, p, nc, pc in rows]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true",
                        help="Log per-window decisions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but do not write to the catalog")
    args = parser.parse_args(argv)

    pct, min_chunks = _thresholds()
    if args.verbose:
        print(f"trend alerts: pct>={pct}, new_chunks>={min_chunks}")

    alerts = compute_alerts_for_today(pct, min_chunks)
    if args.verbose:
        for a in alerts:
            print(f"  + [{a['window']}] {a['topic'][:40]:40s} "
                  f"+{a['pct']}% ({a['new_chunks']} chunks)")
        print(f"  -> {len(alerts)} alerts (dry_run={args.dry_run})")

    if args.dry_run:
        return 0
    written = persist_alerts(alerts)
    print(f"trend alerts: wrote {written} rows "
          f"(pct>={pct}, new_chunks>={min_chunks})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
