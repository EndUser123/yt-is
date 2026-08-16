#!/usr/bin/env python3
"""Classify failed yt-is backlog rows without mutating the database.

The authoritative status database can contain failures from several layers.
This audit keeps those layers separate so a full-backlog decision cannot turn
all failures into one blind retry pool.  It emits both a machine-readable
packet and a short Markdown review artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


CLASSIFICATION_VERSION = "unattended-residuals-v4"


def classify_failure(*, failure_reason: object, unavailable_reason: object) -> dict[str, object]:
    """Return a conservative disposition for one failed database row."""
    reason = str(failure_reason or "").strip()
    unavailable = str(unavailable_reason or "").strip()
    normalized = f"{reason} {unavailable}".lower().replace("_", " ")
    if "source add failed" in normalized or "could not add url source" in normalized:
        return {
            "failure_class": "source_add",
            "disposition": "bounded_fallback_candidate",
            "requires_decision_packet": True,
        }
    if "sourcenotfounderror" in normalized or "source not found" in normalized:
        return {
            "failure_class": "source_addressability",
            "disposition": "bounded_fallback_candidate",
            "requires_decision_packet": True,
        }
    if "below threshold" in normalized or "content_below_threshold" in normalized:
        return {
            "failure_class": "content_threshold",
            "disposition": "bounded_quality_retry_candidate",
            "requires_decision_packet": True,
        }
    if "fallback quality" in normalized or "fallback output below" in normalized:
        return {
            "failure_class": "fallback_quality",
            "disposition": "bounded_quality_retry_candidate",
            "requires_decision_packet": True,
        }
    if (
        "cookies are no longer valid" in normalized
        or ("age restricted" in normalized and "audio download failed" in normalized)
    ):
        return {
            "failure_class": "cookie_source",
            "disposition": "blocked_external_cookie_state",
            "requires_decision_packet": True,
        }
    if "subtitles disabled" in normalized:
        return {
            "failure_class": "no_transcript",
            "disposition": "terminal_no_retry",
            "requires_decision_packet": False,
        }
    if "whisper produced empty transcript" in normalized:
        return {
            "failure_class": "empty_transcript",
            "disposition": "terminal_no_retry",
            "requires_decision_packet": False,
        }
    if "whisper transcription timed out" in normalized or (
        normalized.startswith("timeout:") and "whisper" in normalized
    ):
        return {
            "failure_class": "whisper_timeout",
            "disposition": "bounded_quality_retry_candidate",
            "requires_decision_packet": True,
        }
    if "command failed" in normalized:
        return {
            "failure_class": "command",
            "disposition": "bounded_industrial_fallback_candidate",
            "requires_decision_packet": True,
        }
    if "unavailable" in normalized or "private" in normalized or "deleted" in normalized:
        return {
            "failure_class": "unavailable",
            "disposition": "terminal_no_retry",
            "requires_decision_packet": False,
        }
    if normalized.startswith("unknown:") or normalized == "unknown":
        return {
            "failure_class": "unknown",
            "disposition": "blocked_unclassified",
            "requires_decision_packet": True,
        }
    if not reason and not unavailable:
        return {
            "failure_class": "unknown",
            "disposition": "blocked_missing_failure_reason",
            "requires_decision_packet": True,
        }
    return {
        "failure_class": "other",
        "disposition": "blocked_unclassified",
        "requires_decision_packet": True,
    }


def _read_failed_rows(db_path: Path) -> tuple[list[dict[str, object]], str]:
    if not db_path.is_file():
        raise FileNotFoundError(f"batch status database not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        rows = conn.execute(
            "SELECT video_id, status, updated_at, source, has_captions, last_stage, "
            "failure_reason, unavailable_reason FROM analysis_status "
            "WHERE status = 'failed' ORDER BY updated_at, video_id"
        ).fetchall()
    result: list[dict[str, object]] = []
    for video_id, status, updated_at, source, has_captions, last_stage, failure_reason, unavailable_reason in rows:
        classification = classify_failure(
            failure_reason=failure_reason,
            unavailable_reason=unavailable_reason,
        )
        result.append(
            {
                "video_id": str(video_id),
                "status": str(status),
                "updated_at": updated_at,
                "source": source,
                "has_captions": has_captions,
                "last_stage": last_stage,
                "failure_reason": failure_reason,
                "unavailable_reason": unavailable_reason,
                **classification,
            }
        )
    return result, integrity


def build_packet(db_path: Path) -> dict[str, object]:
    rows, integrity = _read_failed_rows(db_path)
    by_class = Counter(str(row["failure_class"]) for row in rows)
    by_disposition = Counter(str(row["disposition"]) for row in rows)
    return {
        "packet_version": 1,
        "classification_version": CLASSIFICATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path.resolve()),
        "integrity_check": integrity,
        "failed_count": len(rows),
        "failure_class_counts": dict(sorted(by_class.items())),
        "disposition_counts": dict(sorted(by_disposition.items())),
        "requires_decision_packet_count": sum(
            1 for row in rows if row["requires_decision_packet"]
        ),
        "rows": rows,
    }


def render_markdown(packet: dict[str, object]) -> str:
    rows = packet["rows"]
    assert isinstance(rows, list)
    lines = [
        "# Unattended Residual Audit",
        "",
        f"Generated: `{packet['generated_at']}`",
        f"Database: `{packet['db_path']}`",
        f"Integrity check: `{packet['integrity_check']}`",
        f"Classification version: `{packet['classification_version']}`",
        "",
        "## Decision",
        "",
        "This is a read-only classification packet. It authorizes no retry and "
        "does not change database state.",
        "",
        f"Failed rows: **{packet['failed_count']}**",
        f"Rows requiring a decision packet: **{packet['requires_decision_packet_count']}**",
        "",
        "## Counts",
        "",
        "| Failure class | Count | Disposition |",
        "|---|---:|---|",
    ]
    disposition_by_class: dict[str, Counter[str]] = {}
    for row in rows:
        failure_class = str(row["failure_class"])
        disposition_by_class.setdefault(failure_class, Counter())[str(row["disposition"])] += 1
    for failure_class in sorted(disposition_by_class):
        dispositions = disposition_by_class[failure_class]
        lines.append(
            f"| `{failure_class}` | {sum(dispositions.values())} | "
            + ", ".join(f"`{key}` ({value})" for key, value in sorted(dispositions.items()))
            + " |"
        )
    lines.extend([
        "",
        "## Exact Rows",
        "",
        "| Video ID | Class | Disposition | Last stage | Failure reason |",
        "|---|---|---|---|---|",
    ])
    for row in rows:
        reason = str(row.get("failure_reason") or row.get("unavailable_reason") or "")
        reason = reason.replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(
            f"| `{row['video_id']}` | `{row['failure_class']}` | "
            f"`{row['disposition']}` | `{row.get('last_stage') or ''}` | {reason} |"
        )
    lines.extend([
        "",
        "## Gate",
        "",
        "Before full-backlog authorization, every non-terminal class must have "
        "a reviewed packet, exact manifest, falsifier, bounded retry policy, "
        "and postcondition. Unknown or unclassified rows remain blocked.",
        "",
    ])
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = build_packet(args.db_path)
    _write_text(args.json_output, json.dumps(packet, indent=2, sort_keys=True) + "\n")
    _write_text(args.markdown_output, render_markdown(packet))
    print(json.dumps({
        "status": "audited",
        "failed_count": packet["failed_count"],
        "failure_class_counts": packet["failure_class_counts"],
        "disposition_counts": packet["disposition_counts"],
        "json_output": str(args.json_output.resolve()),
        "markdown_output": str(args.markdown_output.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
