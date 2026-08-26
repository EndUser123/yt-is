"""Add monitor-performance indexes to production DBs (one-time migration).

The compute_health profiler (docs/operations/compute-health-profile.md,
2026-08-25) found three queries costing 15-24s per watcher tick in full
table scans because the queried columns had no indexes:

  F1: MAX(cached_at) on transcripts.sqlite transcript_cache (4.25 GB,
      293K rows) → full scan, 7-14s
  F2a: GROUP BY has_captions WHERE status='pending' on batch_status.sqlite
      analysis_status (1.24M rows) → full scan, 4-5s
  F2b: GROUP BY on updated_at WHERE updated_at > now-12h on the same
      table → full scan, 4-5s

This script creates the three indexes idempotently (IF NOT EXISTS) and
verifies with EXPLAIN QUERY PLAN. Both DBs are WAL mode: CREATE INDEX
acquires a write lock but does not block readers.

Prudence measures (operator directive 2026-08-25):
- Idempotent: safe to re-run
- Descriptive names: not idx_1
- EXPLAIN verification: confirms the optimizer uses each index
- Before/after timing: the receipt
- No application code changes needed: SQLite maintains indexes
  automatically on every INSERT/UPDATE/DELETE
- Index maintenance overhead: negligible (low-churn metadata columns,
  the nightly drain updates a few thousand rows; each index entry
  update is nanoseconds)
"""

from __future__ import annotations

import sqlite3
import time

DB_TRANSCRIPTS = "P:/.data/yt-is/transcripts.sqlite"
DB_BATCH = "P:/.data/yt-is/batch_status.sqlite"

INDEXES = [
    (
        DB_TRANSCRIPTS,
        "transcript_cache",
        "idx_transcript_cache_cached_at",
        "CREATE INDEX IF NOT EXISTS idx_transcript_cache_cached_at ON transcript_cache(cached_at)",
        "SELECT MAX(cached_at) FROM transcript_cache",
        "F1: MAX(cached_at) — was full-scan 7-14s on 4.25GB",
    ),
    (
        DB_BATCH,
        "analysis_status",
        "idx_analysis_status_status_has_captions",
        "CREATE INDEX IF NOT EXISTS idx_analysis_status_status_has_captions "
        "ON analysis_status(status, has_captions)",
        "SELECT has_captions, COUNT(*) FROM analysis_status WHERE status='pending' GROUP BY has_captions",
        "F2a: pending drain composition — was full-scan 4-5s on 1.24M rows",
    ),
    (
        DB_BATCH,
        "analysis_status",
        "idx_analysis_status_updated_at",
        "CREATE INDEX IF NOT EXISTS idx_analysis_status_updated_at "
        "ON analysis_status(updated_at)",
        "SELECT COUNT(*) FROM analysis_status WHERE updated_at > datetime('now', '-12 hours')",
        "F2b: 12h freshness window — was full-scan 4-5s",
    ),
    (
        DB_BATCH,
        "analysis_status",
        "idx_analysis_status_updated_at_status",
        "CREATE INDEX IF NOT EXISTS idx_analysis_status_updated_at_status "
        "ON analysis_status(updated_at, status)",
        "SELECT status, COUNT(*) FROM analysis_status "
        "WHERE updated_at > datetime('now', '-12 hours') GROUP BY status",
        "F2c: covering index for freshness+composition (review follow-up "
        "2026-08-26): answers the 12h window GROUP BY from the index alone",
    ),
]

ANALYZE_DBS = [DB_TRANSCRIPTS, DB_BATCH]


def time_query(con: sqlite3.Connection, sql: str) -> float:
    t0 = time.perf_counter()
    con.execute(sql).fetchall()
    return time.perf_counter() - t0


def explain(con: sqlite3.Connection, sql: str) -> str:
    plan = con.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    return " | ".join(row[3] for row in plan)


def main() -> int:
    all_ok = True
    for db_path, table, idx_name, ddl, probe, description in INDEXES:
        print(f"\n=== {idx_name} ({description}) ===")
        ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        # Before
        before_s = time_query(ro, probe)
        before_plan = explain(ro, probe)
        print(f"  BEFORE: {before_s:.2f}s | plan: {before_plan[:90]}")
        ro.close()

        # Create (write mode — WAL allows concurrent readers)
        rw = sqlite3.connect(db_path, timeout=30)
        t0 = time.perf_counter()
        rw.execute(ddl)
        rw.commit()
        create_s = time.perf_counter() - t0
        print(f"  CREATE: {create_s:.2f}s")

        # After
        ro2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        after_s = time_query(ro2, probe)
        after_plan = explain(ro2, probe)
        print(f"  AFTER:  {after_s:.3f}s | plan: {after_plan[:90]}")
        ro2.close()
        rw.close()

        speedup = before_s / max(after_s, 0.0001)
        uses_index = "USING INDEX" in after_plan.upper() or "USING COVERING INDEX" in after_plan.upper()
        status = "OK" if uses_index else "WARN: optimizer may not use this index"
        print(f"  RESULT: {speedup:.0f}x speedup | {status}")
        if not uses_index:
            all_ok = False

    # ANALYZE: refresh optimizer statistics so the new indexes are chosen
    # (review follow-up 2026-08-26; ANALYZE on WAL DBs is online-safe)
    for db_path in ANALYZE_DBS:
        con = sqlite3.connect(db_path, timeout=60)
        t0 = time.perf_counter()
        con.execute("ANALYZE")
        con.commit()
        con.close()
        print(f"\nANALYZE {db_path}: {time.perf_counter() - t0:.1f}s")

    print(f"\n{'ALL INDEXES VERIFIED' if all_ok else 'SOME INDEXES NOT USED BY OPTIMIZER — review plans above'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
