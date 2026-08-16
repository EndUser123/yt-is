#!/usr/bin/env python3
"""Morning briefing — one command that answers every operational question.

Composes all existing health checks, backlog stats, taxonomy signals,
and anomaly detections into a single report. Deterministic (no LLM);
designed to be run manually or scheduled. A companion skill wraps this
with interpretation and action recommendations.

Usage:
    python scripts/morning_briefing.py                    # full briefing
    python scripts/morning_briefing.py --json             # machine-readable
    python scripts/morning_briefing.py --section health   # one section only

Sections: health, backlog, taxonomy, anomalies, changes
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_batch_db_path, get_transcript_db_path, load_workspace_env

STATE_FILE = Path("P:/.data/yt-is/unattended-backlog/state.json")
ALERT_FILE = Path("P:/.data/yt-is/pipeline-alert.txt")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def section_health() -> dict:
    """Supervisor alive? Auth healthy? Anything alerted?"""
    result = {"section": "health"}

    # Supervisor state
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            result["supervisor_status"] = state.get("status", "unknown")
            chunks = state.get("chunks", [])
            result["chunks_processed"] = len(chunks)
            if chunks:
                last = chunks[-1]
                result["last_chunk_status"] = last.get("status")
                result["last_chunk_returncode"] = last.get("returncode")
        except (json.JSONDecodeError, OSError):
            result["supervisor_status"] = "state-unreadable"
    else:
        result["supervisor_status"] = "no-state"

    # Alert file
    result["alert"] = ALERT_FILE.read_text(encoding="utf-8").strip() if ALERT_FILE.exists() else None

    # Pre-flight summary (subset: disk, integrity, backups)
    try:
        disk, disk_detail = __import__("scripts.preflight_safety", fromlist=["check_disk_space"]).check_disk_space()
        result["disk"] = f"{disk}: {disk_detail}"
    except Exception:
        pass
    try:
        backup, backup_detail = __import__("scripts.preflight_safety", fromlist=["check_backup_freshness"]).check_backup_freshness()
        result["backup_age"] = f"{backup}: {backup_detail}"
    except Exception:
        pass

    # Health watcher (quick checks, skip the auth probe for speed)
    result["health_watcher_hint"] = "run pipeline_health_watch.py for full checks"

    # Transcript storage
    tdb = sqlite3.connect(f"file:{get_transcript_db_path()}?mode=ro", uri=True)
    result["transcripts_cached"] = tdb.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0]
    result["transcripts_empty"] = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE transcript IS NULL OR TRIM(transcript)=''"
    ).fetchone()[0]
    # Quality metrics coverage
    result["quality_metrics_coverage"] = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE metadata_json LIKE '%transcript_chars%'"
    ).fetchone()[0]
    # Stage timing freshness
    latest_timing = tdb.execute("SELECT MAX(recorded_at) FROM stage_timing").fetchone()[0]
    result["latest_stage_timing"] = latest_timing
    tdb.close()

    # DB integrity
    bdb = sqlite3.connect(f"file:{get_batch_db_path()}?mode=ro", uri=True)
    result["batch_db_integrity"] = bdb.execute("PRAGMA integrity_check").fetchone()[0]
    bdb.close()

    return result


def section_backlog() -> dict:
    """Pending, complete, failed, blocked, success rate."""
    bdb = sqlite3.connect(f"file:{get_batch_db_path()}?mode=ro", uri=True)
    pending = bdb.execute("SELECT COUNT(*) FROM analysis_status WHERE status='pending'").fetchone()[0]
    complete = bdb.execute("SELECT COUNT(*) FROM analysis_status WHERE status='complete'").fetchone()[0]
    failed = bdb.execute("SELECT COUNT(*) FROM analysis_status WHERE status='failed'").fetchone()[0]

    blocked_pending = bdb.execute("""
        SELECT COUNT(*) FROM analysis_status a
        WHERE a.status='pending'
        AND a.source IN (SELECT channel_url FROM channel_blocklist)
    """).fetchone()[0]

    # Recently completed (last 24h)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    recent_complete = bdb.execute(
        "SELECT COUNT(*) FROM analysis_status WHERE status='complete' AND updated_at > ?",
        (yesterday,),
    ).fetchone()[0]

    # Top failure reasons
    failures = {}
    for reason, n in bdb.execute("""
        SELECT COALESCE(failure_reason,'(null)'), COUNT(*)
        FROM analysis_status WHERE status='failed'
        GROUP BY failure_reason ORDER BY 2 DESC LIMIT 5
    """):
        failures[reason[:60]] = n

    # Channel counts
    total_channels = bdb.execute("SELECT COUNT(*) FROM channel_metadata").fetchone()[0]
    blocked_channels = bdb.execute("SELECT COUNT(*) FROM channel_blocklist").fetchone()[0]
    active_channels = total_channels - blocked_channels

    bdb.close()

    # Success rate from supervisor state
    success_rate = None
    recent_rates = []
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            chunks = state.get("chunks", [])
            for c in chunks[-10:]:
                sel = c.get("selected_count", 0)
                comp = c.get("selected_complete_count", 0)
                if sel > 0:
                    recent_rates.append(comp / sel)
            if recent_rates:
                success_rate = sum(recent_rates) / len(recent_rates)
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "section": "backlog",
        "pending": pending,
        "pending_active": pending - blocked_pending,
        "pending_blocked_channel": blocked_pending,
        "complete": complete,
        "failed": failed,
        "recently_completed_24h": recent_complete,
        "success_rate_last10": round(success_rate * 100, 1) if success_rate else None,
        "top_failures": failures,
        "total_channels": total_channels,
        "active_channels": active_channels,
        "blocked_channels": blocked_channels,
    }


def section_taxonomy() -> dict:
    """Category distribution, Other count, corrections signal."""
    bdb = sqlite3.connect(f"file:{get_batch_db_path()}?mode=ro", uri=True)
    distribution = {}
    for cat, n in bdb.execute(
        "SELECT category, COUNT(*) FROM channel_metadata WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC"
    ):
        distribution[cat] = n
    null_count = bdb.execute(
        "SELECT COUNT(*) FROM channel_metadata WHERE category IS NULL"
    ).fetchone()[0]
    other_count = distribution.pop("Other", 0)
    # Provenance split
    manual = bdb.execute(
        "SELECT COUNT(*) FROM channel_metadata WHERE category_source='manual'"
    ).fetchone()[0]
    llm = bdb.execute(
        "SELECT COUNT(*) FROM channel_metadata WHERE category_source='llm'"
    ).fetchone()[0]
    # Dead channels
    dead = bdb.execute(
        "SELECT COUNT(*) FROM channel_metadata WHERE channel_status IS NOT NULL"
    ).fetchone()[0]
    bdb.close()
    return {
        "section": "taxonomy",
        "categories": len(distribution),
        "distribution": distribution,
        "other": other_count,
        "unclassified": null_count,
        "provenance_manual": manual,
        "provenance_llm": llm,
        "dead_channels": dead,
    }


def section_anomalies() -> dict:
    """Suspect transcripts, orphans, stale state, resource leaks."""
    anomalies = []

    tdb = sqlite3.connect(f"file:{get_transcript_db_path()}?mode=ro", uri=True)
    suspects = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE transcript IS NOT NULL AND LENGTH(transcript) < 50"
    ).fetchone()[0]
    if suspects > 0:
        anomalies.append(f"{suspects} suspect short transcripts (<50 chars)")

    empty = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE transcript IS NULL OR TRIM(transcript)=''"
    ).fetchone()[0]
    if empty > 0:
        anomalies.append(f"{empty} empty/null transcript cache entries")
    tdb.close()

    bdb = sqlite3.connect(f"file:{get_batch_db_path()}?mode=ro", uri=True)
    bdb.execute(f"ATTACH DATABASE 'file:{get_transcript_db_path()}?mode=ro' AS tc")
    orphans = bdb.execute("""
        SELECT COUNT(*) FROM main.analysis_status a
        WHERE a.status='complete'
        AND NOT EXISTS (SELECT 1 FROM tc.transcript_cache tc WHERE tc.video_id=a.video_id)
    """).fetchone()[0]
    if orphans > 0:
        anomalies.append(f"{orphans} complete rows without transcript cache (orphans)")
    bdb.close()

    if ALERT_FILE.exists():
        alert_text = ALERT_FILE.read_text(encoding="utf-8").strip()
        if alert_text:
            anomalies.append(f"active alert: {alert_text.splitlines()[0]}")

    return {"section": "anomalies", "count": len(anomalies), "items": anomalies}


def section_changes() -> dict:
    """What changed recently: commits, new failures, category additions."""
    # Recent commits
    result = subprocess.run(
        ["git", "log", "--oneline", "--since=1 day ago", "--", "."],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    commits = [l for l in result.stdout.splitlines() if l.strip()][:5]

    # New failures in last 24h
    bdb = sqlite3.connect(f"file:{get_batch_db_path()}?mode=ro", uri=True)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    new_failures = bdb.execute(
        "SELECT COUNT(*) FROM analysis_status WHERE status='failed' AND updated_at > ?",
        (yesterday,),
    ).fetchone()[0]
    bdb.close()

    return {
        "section": "changes",
        "recent_commits": commits,
        "new_failures_24h": new_failures,
    }


SECTIONS = {
    "health": section_health,
    "backlog": section_backlog,
    "taxonomy": section_taxonomy,
    "anomalies": section_anomalies,
    "changes": section_changes,
}


def format_briefing(data: dict) -> str:
    """Format the JSON data as a human-readable briefing."""
    lines = [f"=== MORNING BRIEFING — {data.get('_timestamp', _ts())} ===", ""]

    # HEALTH
    h = data.get("health", {})
    lines.append("HEALTH")
    sup = h.get("supervisor_status", "?")
    icon = "✓" if sup in ("running", "no-state") else "⚠"
    lines.append(f"  {icon} Supervisor: {sup}")
    if h.get("chunks_processed"):
        lines.append(f"    chunks processed: {h['chunks_processed']}")
    if h.get("alert"):
        lines.append(f"  ⚠ ACTIVE ALERT: {h['alert'][:100]}")
    else:
        lines.append("  ✓ No active alerts")
    cached = h.get("transcripts_cached", 0)
    empty = h.get("transcripts_empty", 0)
    quality = h.get("quality_metrics_coverage", 0)
    lines.append(f"  ✓ {cached:,} transcripts cached ({empty} empty)")
    lines.append(f"  ✓ Quality metrics on {quality:,} transcripts")
    lines.append(f"  ✓ DB integrity: {h.get('batch_db_integrity', '?')}")
    lines.append("")

    # BACKLOG
    b = data.get("backlog", {})
    lines.append("BACKLOG")
    lines.append(f"  Pending: {b.get('pending', 0):,} "
                 f"({b.get('pending_active', 0):,} active + {b.get('pending_blocked_channel', 0):,} blocked-channel)")
    lines.append(f"  Complete: {b.get('complete', 0):,} | Failed: {b.get('failed', 0):,}")
    lines.append(f"  Recently completed (24h): +{b.get('recently_completed_24h', 0):,}")
    rate = b.get("success_rate_last10")
    if rate:
        icon = "✓" if rate >= 80 else "⚠"
        lines.append(f"  {icon} Success rate (last 10 chunks): {rate}%")
    lines.append(f"  Channels: {b.get('active_channels', 0):,} active / {b.get('blocked_channels', 0):,} blocked")
    for reason, n in list(b.get("top_failures", {}).items())[:3]:
        lines.append(f"    failure: {n:,} × {reason}")
    lines.append("")

    # TAXONOMY
    t = data.get("taxonomy", {})
    lines.append("TAXONOMY")
    lines.append(f"  {t.get('categories', 0)} categories | Other: {t.get('other', 0)} | "
                 f"Unclassified: {t.get('unclassified', 0)}")
    dist = t.get("distribution", {})
    top3 = sorted(dist.items(), key=lambda x: x[1], reverse=True)[:3]
    lines.append(f"  Top: {', '.join(f'{c} ({n})' for c, n in top3)}")
    lines.append(f"  Provenance: {t.get('provenance_manual', 0):,} manual / {t.get('provenance_llm', 0):,} auto")
    lines.append("")

    # ANOMALIES
    a = data.get("anomalies", {})
    lines.append("ANOMALIES")
    if a.get("count", 0) == 0:
        lines.append("  ✓ None detected")
    else:
        for item in a.get("items", []):
            lines.append(f"  ⚠ {item}")
    lines.append("")

    # CHANGES
    c = data.get("changes", {})
    lines.append("CHANGES")
    for commit in c.get("recent_commits", []):
        lines.append(f"  {commit}")
    lines.append(f"  New failures (24h): {c.get('new_failures_24h', 0):,}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of text")
    parser.add_argument("--section", choices=list(SECTIONS), default=None,
                        help="Show only one section")
    args = parser.parse_args(argv)

    load_workspace_env()

    if args.section:
        data = SECTIONS[args.section]()
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True, default=str))
        else:
            print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0

    data = {"_timestamp": _ts()}
    for name, fn in SECTIONS.items():
        data[name] = fn()

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
    else:
        print(format_briefing(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
