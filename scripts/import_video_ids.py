#!/usr/bin/env python3
"""Import video IDs from playlist.json + history.csv into yt-is pending queue."""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.batch_status import BatchEntry, import_video_ids
from csf.paths import get_batch_db_path
from csf.playlist_imports import finish_playlist_import_run, record_video_import_run

YT_WATCH_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/v/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)


def write_decision_report(path, report, *, overwrite=False):
    """Write a decision report atomically beside its final destination."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(report, temp_file, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        if overwrite:
            os.replace(temp_path, path)
            temp_path = None
        else:
            os.link(temp_path, path)
            temp_path.unlink()
            temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def validate_report_destination(path):
    """Verify the report directory can accept a temporary output file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.preflight.",
        suffix=".tmp",
        delete=False,
    ) as probe:
        probe_path = Path(probe.name)
    probe_path.unlink()


def parse_playlist_jsonl(path, *, return_stats=False):
    """Parse yt-dlp JSONL playlist output into BatchEntry list."""
    entries = []
    seen = set()
    stats = {
        "lines_seen": 0,
        "blank_lines": 0,
        "invalid_json": 0,
        "invalid_record": 0,
        "invalid_id": 0,
        "duplicate_id": 0,
        "accepted": 0,
    }
    with open(path, encoding="utf-8") as f:
        for line in f:
            stats["lines_seen"] += 1
            line = line.strip()
            if not line:
                stats["blank_lines"] += 1
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats["invalid_json"] += 1
                continue
            if not isinstance(obj, dict):
                stats["invalid_record"] += 1
                continue
            vid = obj.get("id")
            if not vid or not isinstance(vid, str) or len(vid) != 11:
                stats["invalid_id"] += 1
                continue
            if vid in seen:
                stats["duplicate_id"] += 1
                continue
            seen.add(vid)
            channel_id = obj.get("channel_id") or obj.get("uploader_id")
            title = obj.get("title")
            published_at = None
            ts = obj.get("timestamp")
            if ts:
                try:
                    published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except (OSError, ValueError):
                    pass
            duration = obj.get("duration")
            if isinstance(duration, float):
                duration = int(duration)
            elif not isinstance(duration, int):
                duration = None
            entry = BatchEntry(
                video_id=vid, status="pending",
                source="playlist:watch-later-temp",
                published_at=published_at, title=title,
                channel_id=channel_id, duration=duration,
                description=obj.get("description"),
                thumbnail=(obj.get("thumbnail") or obj.get("thumbnails", [{}])[0].get("url") if obj.get("thumbnails") else None),
            )
            entries.append(entry)
            stats["accepted"] += 1
    return (entries, stats) if return_stats else entries


def parse_history_csv(path, limit=5000, *, return_stats=False):
    """Parse Chrome history CSV, extract YouTube URLs from last N rows."""
    entries = []
    seen = set()
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
    rows = all_rows[-limit:] if limit is not None and len(all_rows) > limit else all_rows
    stats = {
        "rows_in_file": len(all_rows),
        "rows_considered": len(rows),
        "rows_omitted_by_limit": len(all_rows) - len(rows),
        "non_youtube_row": 0,
        "duplicate_id": 0,
        "accepted": 0,
    }
    for row in rows:
        url = row.get("url") or ""
        m = YT_WATCH_RE.search(url)
        if not m:
            stats["non_youtube_row"] += 1
            continue
        vid = m.group(1)
        if vid in seen:
            stats["duplicate_id"] += 1
            continue
        seen.add(vid)
        title = row.get("title", "")
        date_str = row.get("date", "")
        published_at = None
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%m/%d/%Y")
                published_at = dt.replace(tzinfo=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass
        entry = BatchEntry(
            video_id=vid, status="pending",
            source="history:2026-07-14",
            published_at=published_at, title=title,
        )
        entries.append(entry)
        stats["accepted"] += 1
    return (entries, stats) if return_stats else entries


def _resolved_path(path):
    return Path(path).expanduser().resolve()


def _input_fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode("utf-8"))
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _decision_signature(result):
    return [
        (decision.video_id, decision.decision, decision.reason)
        for decision in result.decisions
    ]


def main():
    parser = argparse.ArgumentParser(description="Import video IDs to yt-is pending queue")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the reviewed import plan; without this flag the command is a dry run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request the default dry-run behavior",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON path for the per-video import decision report",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional batch_status.sqlite path (useful for staging and review)",
    )
    parser.add_argument(
        "--overwrite-report",
        action="store_true",
        help="Allow replacing an existing report path",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Dry-run report to validate before --execute",
    )
    parser.add_argument("--playlist", default=r"C:\Users\brsth\Downloads\playlist.json")
    parser.add_argument("--history", default=r"C:\Users\brsth\Downloads\history.csv")
    args = parser.parse_args()
    if args.execute and args.dry_run:
        parser.error("--execute and --dry-run cannot be used together")
    if args.execute and not args.plan:
        parser.error("--execute requires --plan from a prior dry-run report")
    if args.execute and not args.report:
        parser.error("--execute requires --report for a durable execution receipt")
    if args.plan and not args.execute:
        parser.error("--plan is only valid with --execute")

    playlist_path = _resolved_path(args.playlist)
    history_path = _resolved_path(args.history)
    effective_db_path = _resolved_path(args.db_path or get_batch_db_path())
    report_path = _resolved_path(args.report) if args.report else None
    plan_path = _resolved_path(args.plan) if args.plan else None
    if report_path:
        protected_paths = {playlist_path, history_path, effective_db_path}
        if report_path in protected_paths:
            parser.error("--report must not replace the playlist, history, or database path")
        if report_path.exists() and not args.overwrite_report:
            parser.error("report exists; pass --overwrite-report to replace it")
        try:
            validate_report_destination(report_path)
        except OSError as exc:
            parser.error(f"report destination is not writable: {exc}")
    plan_data = None
    if plan_path:
        if not plan_path.exists():
            parser.error(f"plan does not exist: {plan_path}")
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read plan: {exc}")
        if not isinstance(plan_data, dict):
            parser.error("--plan must contain a JSON object")
        if plan_data.get("mode") != "dry_run":
            parser.error("--plan must reference a dry-run report")

    print("=== Parsing playlist.json ===")
    playlist_entries, playlist_stats = parse_playlist_jsonl(playlist_path, return_stats=True)
    print(f"  Videos: {len(playlist_entries)}")

    print("=== Parsing history.csv (last 5000 rows) ===")
    history_entries, history_stats = parse_history_csv(
        history_path, limit=5000, return_stats=True
    )
    print(f"  YouTube watch URLs found: {len(history_entries)}")
    print(f"  History rows omitted by limit: {history_stats['rows_omitted_by_limit']}")
    print(f"  Playlist rows rejected/duplicated: "
          f"{playlist_stats['invalid_json'] + playlist_stats['invalid_record'] + playlist_stats['invalid_id'] + playlist_stats['duplicate_id']}")
    print(f"  History rows rejected/duplicated: "
          f"{history_stats['non_youtube_row'] + history_stats['duplicate_id']}")

    input_fingerprint = _input_fingerprint([playlist_path, history_path])
    if plan_data:
        if plan_data.get("db_path") != str(effective_db_path):
            parser.error("plan database path does not match the current --db-path")
        if plan_data.get("input_fingerprint") != input_fingerprint:
            parser.error("playlist/history inputs changed since the dry-run plan")
        if plan_data.get("counts", {}).get("blocked", 0):
            parser.error("cannot execute a plan blocked by database schema incompatibility")

    # Don't pre-filter history against playlist — let COALESCE in the UPSERT
    # merge them. This allows history's published_at to enrich playlist entries
    # that might be missing it, and vice versa.
    all_entries = playlist_entries + history_entries
    overlap = len(playlist_entries) + len(history_entries) - len({e.video_id for e in all_entries})
    print(f"\n=== Total to import: {len(all_entries)} ===")
    print(f"  Playlist:  {len(playlist_entries)}")
    print(f"  History:   {len(history_entries)}")
    print(f"  Overlap:   {overlap} (COALESCE will merge)")

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"\n=== Import plan ({mode}) ===")
    expected_database_fingerprint = None
    planned_decisions = None
    if plan_data:
        current_plan = import_video_ids(
            all_entries,
            execute=False,
            db_path=effective_db_path,
        )
        if current_plan.database_fingerprint != plan_data.get("database_fingerprint"):
            parser.error("database state changed since the import plan")
        if _decision_signature(current_plan) != [
            (
                decision.get("video_id"),
                decision.get("decision"),
                decision.get("reason"),
            )
            for decision in plan_data.get("decisions", [])
        ]:
            parser.error("import decisions changed since the import plan")
        expected_database_fingerprint = plan_data["database_fingerprint"]
        planned_decisions = {
            decision.video_id: (decision.decision, decision.reason)
            for decision in current_plan.decisions
        }

    provenance_run_id = None
    if args.execute:
        playlist_context_count = len(playlist_entries)
        item_context = [
            {
                "source_path": str(playlist_path if index < playlist_context_count else history_path),
                "sequence_index": index,
            }
            for index, _entry in enumerate(all_entries)
        ]
        provenance_notes = {
            "source_description": "playlist-and-history-import",
            "input_fingerprint": input_fingerprint,
            "database_fingerprint": expected_database_fingerprint,
            "playlist_path": str(playlist_path),
            "history_path": str(history_path),
            "parse_stats": {
                "playlist": playlist_stats,
                "history": history_stats,
            },
        }
        provenance_run_id = record_video_import_run(
            all_entries,
            origin="scripts/import_video_ids.py",
            item_context=item_context,
            planned_decisions=planned_decisions,
            notes=provenance_notes,
        )

    try:
        result = import_video_ids(
            all_entries,
            execute=args.execute,
            db_path=effective_db_path,
            expected_database_fingerprint=expected_database_fingerprint,
        )
    except Exception:
        if provenance_run_id is not None:
            finish_playlist_import_run(provenance_run_id, status="failed")
        raise
    if provenance_run_id is not None:
        finish_playlist_import_run(provenance_run_id, status="completed")
    for decision, count in result.counts.items():
        if count:
            print(f"  {decision}: {count}")
    if not args.execute:
        print("  No status-row changes made; use --report PATH to create a plan for --execute.")
    if args.report:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode.lower().replace(" ", "_"),
            "playlist": str(playlist_path),
            "history": str(history_path),
            "db_path": str(effective_db_path),
            "input_fingerprint": input_fingerprint,
            "database_fingerprint": result.database_fingerprint,
            "plan_path": str(plan_path) if plan_path else None,
            "provenance_run_id": provenance_run_id,
            "input_count": len(all_entries),
            "parse_stats": {
                "playlist": playlist_stats,
                "history": history_stats,
            },
            "counts": result.counts,
            "decisions": [
                {
                    "video_id": decision.video_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                }
                for decision in result.decisions
            ],
        }
        write_decision_report(
            report_path,
            report,
            overwrite=args.overwrite_report,
        )
        print(f"  Decision report: {report_path}")
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
