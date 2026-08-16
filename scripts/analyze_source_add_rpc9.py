#!/usr/bin/env python3
"""Offline reducer for multi-account RPC9 source-add attempt artifacts.

This module only reads JSONL and, when requested, a local SQLite database in
read-only mode.  It never retries IDs, contacts a service, or writes an
artifact.  Counts are deliberately split between attempt rows and distinct
videos: a retry is an additional attempt, not an additional video.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable

ATTEMPT_ACTIONS = {
    "nlm_batch_source_add_attempt_started",
    "nlm_batch_source_add_attempt_completed",
}
MISSING = "<missing>"
ASSOCIATION_LABEL = "association_only"
CAUSAL_LABEL = "causal_proof_not_established"
_RPC_CODE_RE = re.compile(r"(?:rpc[_ ]?code|code)\s*[=:]\s*(\d+)", re.IGNORECASE)
_DIMENSION_FIELDS = {
    "account": "account_profile",
    "worker": "worker_id",
    "notebook": "nb_id",
    "position": "source_position",
    "subbatch": "subbatch_index",
    "channel": "channel_id",
}


def _as_text(value: Any) -> str:
    if value is None or value == "":
        return MISSING
    return str(value)


def _sorted_counts(
    counter: Counter[str],
    video_counts: dict[str, set[str]],
    row_denominator: int,
    video_denominator: int,
) -> list[dict[str, Any]]:
    return [
        {
            "value": value,
            "count": counter[value],
            "video_count": len(video_counts.get(value, set())),
            "row_denominator": row_denominator,
            "video_denominator": video_denominator,
            "row_share": round(counter[value] / row_denominator, 6) if row_denominator else None,
            "video_share": round(len(video_counts.get(value, set())) / video_denominator, 6) if video_denominator else None,
        }
        for value in sorted(counter)
    ]


def _distribution_rows(
    records: Iterable[dict[str, Any]],
    dimensions: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    """Summarize one completion cohort with explicit row/video denominators."""
    by_dimension: dict[str, Counter[str]] = {name: Counter() for name in dimensions}
    videos_by_dimension: dict[str, dict[str, set[str]]] = {
        name: {} for name in dimensions
    }
    records = list(records)
    video_ids = {
        str((record.get("data") or {}).get("video_id"))
        for record in records
        if isinstance(record.get("data"), dict)
        and (record.get("data") or {}).get("video_id") not in (None, "")
    }
    for record in records:
        data = record.get("data")
        if not isinstance(data, dict):
            data = record
        video_id = data.get("video_id")
        for dimension in dimensions:
            value = _as_text(data.get(_DIMENSION_FIELDS[dimension]))
            by_dimension[dimension][value] += 1
            if video_id not in (None, ""):
                videos_by_dimension[dimension].setdefault(value, set()).add(str(video_id))
    return {
        name: _sorted_counts(
            by_dimension[name],
            videos_by_dimension[name],
            len(records),
            len(video_ids),
        )
        for name in dimensions
    }


def _outcome(data: dict[str, Any]) -> str:
    status = str(data.get("status", "")).strip().lower()
    return status or MISSING


def _rpc_code(data: dict[str, Any]) -> str:
    for value in (data.get("rpc_code"), data.get("error"), data.get("failure_reason")):
        if value in (None, ""):
            continue
        match = _RPC_CODE_RE.search(str(value))
        if match:
            return match.group(1)
    return MISSING


def _expand_inputs(inputs: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    for supplied in inputs:
        path = supplied.resolve()
        if path.is_dir():
            paths.update(p.resolve() for p in path.rglob("*.jsonl") if p.is_file())
        elif path.is_file():
            paths.add(path)
    return sorted(paths, key=lambda p: p.as_posix().casefold())


def _iter_records(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    stats = {"jsonl_files": 0, "lines": 0, "blank_lines": 0, "invalid_json": 0, "non_object": 0}
    for path in paths:
        stats["jsonl_files"] += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            stats["invalid_json"] += 1
            continue
        for line_number, line in enumerate(lines, 1):
            stats["lines"] += 1
            if not line.strip():
                stats["blank_lines"] += 1
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                stats["invalid_json"] += 1
                continue
            if not isinstance(value, dict):
                stats["non_object"] += 1
                continue
            value = dict(value)
            value["raw_artifact_path"] = str(path)
            value["raw_artifact_line"] = line_number
            records.append(value)
    return records, stats


def _join_sqlite(db_path: Path, video_ids: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not video_ids:
        return {}, {"joined": 0, "missing": 0, "schema": "not_used"}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(analysis_status)")}
            if "video_id" not in columns:
                return {}, {"joined": 0, "missing": len(video_ids), "schema": "missing_video_id"}
            selected = ["video_id"]
            for name in ("status", "source", "channel_id", "channel_title", "updated_at"):
                if name in columns:
                    selected.append(name)
            rows_by_id: dict[str, dict[str, Any]] = {}
            ids = sorted(video_ids)
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT {', '.join(selected)} FROM analysis_status WHERE video_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    rows_by_id[str(row[0])] = dict(zip(selected, row))
    except (OSError, sqlite3.Error) as exc:
        return {}, {"joined": 0, "missing": len(video_ids), "schema": "unavailable", "error": str(exc)}
    return rows_by_id, {
        "joined": len(rows_by_id),
        "missing": len(video_ids) - len(rows_by_id),
        "schema": "analysis_status",
        "columns": sorted(columns),
        "path": str(db_path.resolve()),
    }


def analyze_source_add_attempts(inputs: Iterable[Path], *, db_path: Path | None = None) -> dict[str, Any]:
    """Return a deterministic, non-authorizing report for source-add JSONL."""
    paths = _expand_inputs(inputs)
    all_records, parse_stats = _iter_records(paths)
    attempts = [r for r in all_records if r.get("action") in ATTEMPT_ACTIONS]
    videos = {str((r.get("data") or {}).get("video_id")) for r in attempts if isinstance(r.get("data"), dict) and (r.get("data") or {}).get("video_id") not in (None, "")}
    joined, join_stats = _join_sqlite(db_path, videos) if db_path else ({}, {"joined": 0, "missing": 0, "schema": "not_requested"})

    dimensions = ("account", "worker", "notebook", "position", "subbatch", "channel")
    by_dimension: dict[str, Counter[str]] = {name: Counter() for name in dimensions}
    videos_by_dimension: dict[str, dict[str, set[str]]] = {name: {} for name in dimensions}
    missing_fields: Counter[str] = Counter()
    for record in attempts:
        data = record.get("data")
        if not isinstance(data, dict):
            data = record
        video_id = data.get("video_id")
        db_row = joined.get(str(video_id)) if video_id not in (None, "") else None
        values = {
            dimension: data.get(field)
            for dimension, field in _DIMENSION_FIELDS.items()
        }
        values["channel"] = values["channel"] or (db_row or {}).get("channel_id")
        for dimension, raw in values.items():
            if raw in (None, ""):
                missing_fields[dimension] += 1
            value = _as_text(raw)
            by_dimension[dimension][value] += 1
            if video_id not in (None, ""):
                videos_by_dimension[dimension].setdefault(value, set()).add(str(video_id))

    attempt_video_ids = {str((r.get("data") or {}).get("video_id")) for r in attempts if isinstance(r.get("data"), dict) and (r.get("data") or {}).get("video_id") not in (None, "")}
    started = sum(r.get("action") == "nlm_batch_source_add_attempt_started" for r in attempts)
    completed = sum(r.get("action") == "nlm_batch_source_add_attempt_completed" for r in attempts)
    completed_records = [
        record for record in attempts
        if record.get("action") == "nlm_batch_source_add_attempt_completed"
    ]
    completed_by_outcome: dict[str, list[dict[str, Any]]] = {}
    outcome_counts: Counter[str] = Counter()
    outcome_videos: dict[str, set[str]] = {}
    rpc_code_counts: Counter[str] = Counter()
    rpc_code_videos: dict[str, set[str]] = {}
    for record in completed_records:
        data = record.get("data")
        if not isinstance(data, dict):
            data = record
        outcome = _outcome(data)
        outcome_counts[outcome] += 1
        video_id = data.get("video_id")
        if video_id not in (None, ""):
            outcome_videos.setdefault(outcome, set()).add(str(video_id))
        code = _rpc_code(data)
        if code != MISSING:
            rpc_code_counts[code] += 1
            if video_id not in (None, ""):
                rpc_code_videos.setdefault(code, set()).add(str(video_id))
    for outcome in sorted(outcome_counts):
        cohort = [
            record for record in completed_records
            if _outcome(record.get("data") if isinstance(record.get("data"), dict) else record) == outcome
        ]
        completed_by_outcome[outcome] = _distribution_rows(cohort, dimensions)
    completed_video_ids = {
        str((record.get("data") or {}).get("video_id"))
        for record in completed_records
        if isinstance(record.get("data"), dict)
        and (record.get("data") or {}).get("video_id") not in (None, "")
    }
    return {
        "schema_version": 1,
        "analysis": "offline_source_add_rpc9",
        "non_authorizing": True,
        "live_work_authorized": False,
        "external_calls": False,
        "database_mutated": False,
        "evidence_labels": {"distributions": ASSOCIATION_LABEL, "causal_claim": CAUSAL_LABEL},
        "input_artifacts": [str(path) for path in paths],
        "raw_artifacts": [
            {"path": str(path), "preserved": True}
            for path in paths
        ],
        "parse": parse_stats,
        "attempts": {
            "total": len(attempts),
            "started": started,
            "completed": completed,
            "distinct_videos": len(attempt_video_ids),
            "duplicate_attempt_rows_over_videos": len(attempts) - len(attempt_video_ids),
        },
        "completed_outcomes": {
            "rows": completed,
            "distinct_videos": len(completed_video_ids),
            "by_status": [
                {
                    "value": value,
                    "count": outcome_counts[value],
                    "video_count": len(outcome_videos.get(value, set())),
                    "row_share": round(outcome_counts[value] / completed, 6) if completed else None,
                    "video_share": round(len(outcome_videos.get(value, set())) / len(completed_video_ids), 6) if completed_video_ids else None,
                }
                for value in sorted(outcome_counts)
            ],
            "by_rpc_code": [
                {
                    "value": value,
                    "count": rpc_code_counts[value],
                    "video_count": len(rpc_code_videos.get(value, set())),
                    "row_share": round(rpc_code_counts[value] / completed, 6) if completed else None,
                    "video_share": round(len(rpc_code_videos.get(value, set())) / len(completed_video_ids), 6) if completed_video_ids else None,
                }
                for value in sorted(rpc_code_counts)
            ],
            "distributions_by_status": completed_by_outcome,
        },
        "missing_fields": dict(sorted(missing_fields.items())),
        "denominator_checks": {
            "attempt_rows_equal_dimension_total": all(sum(c.values()) == len(attempts) for c in by_dimension.values()),
            "video_ids_with_attempts": len(attempt_video_ids),
            "video_ids_joined": join_stats.get("joined", 0),
        },
        "distributions": {
            name: _sorted_counts(counter, videos_by_dimension[name], len(attempts), len(attempt_video_ids))
            for name, counter in sorted(by_dimension.items())
        },
        "sqlite_join": join_stats,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise deterministic human-readable packet."""
    lines = ["# Offline RPC9 source-add analysis", "", f"- Attempts: {report['attempts']['total']}", f"- Distinct videos: {report['attempts']['distinct_videos']}", "- Evidence: distributions are association only; causal proof is not established.", "", "## Distributions"]
    for name, rows in report["distributions"].items():
        lines.extend([f"### {name}", "| value | count | row share | video share |", "|---|---:|---:|---:|"])
        lines.extend(f"| {row['value']} | {row['count']} | {row['row_share']} | {row['video_share']} |" for row in rows)
        lines.append("")
    outcomes = report["completed_outcomes"]
    lines.extend(["## Completed outcomes", "", f"- Completion rows: {outcomes['rows']}", f"- Distinct completed videos: {outcomes['distinct_videos']}", "", "### Status", "| value | count | row share | video share |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['value']} | {row['count']} | {row['row_share']} | {row['video_share']} |" for row in outcomes["by_status"])
    lines.extend(["", "### RPC code", "| value | count | row share | video share |", "|---|---:|---:|---:|"])
    lines.extend(f"| {row['value']} | {row['count']} | {row['row_share']} | {row['video_share']} |" for row in outcomes["by_rpc_code"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="+", type=Path, help="JSONL file(s) or directories")
    parser.add_argument("--db-path", type=Path, help="Optional authoritative batch_status SQLite database")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)
    report = analyze_source_add_attempts(args.input, db_path=args.db_path)
    payload = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if args.json_output:
        args.json_output.write_text(payload, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.write_text(render_markdown(report) + "\n", encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
