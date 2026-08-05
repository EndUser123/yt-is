#!/usr/bin/env python3
"""Build a deterministic exact-video manifest from local analysis_status data."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.paths import get_batch_db_path
from csf.video_selection_manifest import (
    load_video_selection_manifest,
    write_video_selection_manifest,
)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_candidates(
    db_path: Path,
    *,
    status: str,
    source: str | None,
    order_by: str,
) -> list[dict[str, object | None]]:
    if not db_path.exists():
        raise FileNotFoundError(f"batch status database not found: {db_path}")
    order_sql = "updated_at ASC, video_id ASC" if order_by == "updated_at" else "video_id ASC"
    clauses: list[str] = []
    params: list[str] = []
    if status != "all":
        clauses.append("status = ?")
        params.append(status)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT video_id, status, source, updated_at FROM analysis_status"
            f"{where} ORDER BY {order_sql}",
            params,
        ).fetchall()
    return [
        {
            "video_id": row[0],
            "status": row[1],
            "source": row[2],
            "updated_at": row[3],
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection-name", required=True)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--status", choices=("pending", "complete", "failed", "all"), default="pending")
    parser.add_argument("--source", default=None, help="Exact local analysis_status.source filter")
    parser.add_argument("--order-by", choices=("video_id", "updated_at"), default="video_id")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")

    db_path = Path(args.db_path or get_batch_db_path()).resolve()
    output = args.output.resolve()
    if output == db_path:
        parser.error("--output must not replace the active batch status database")

    candidates = _read_candidates(
        db_path,
        status=args.status,
        source=args.source,
        order_by=args.order_by,
    )
    selected = candidates if args.limit is None else candidates[:args.limit]
    criteria = {
        "status": args.status,
        "source": args.source,
        "order_by": args.order_by,
        "limit": args.limit,
    }
    payload = {
        "manifest_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_name": args.selection_name,
        "selection_criteria": criteria,
        "input_database_fingerprint": _fingerprint(candidates),
        "videos": [
            {
                "video_id": str(row["video_id"]),
                "source_note": f"analysis_status:{row['status']}" +
                (f" source={row['source']}" if row.get("source") else ""),
            }
            for row in selected
        ],
    }
    write_video_selection_manifest(output, payload, overwrite=args.overwrite)
    loaded = load_video_selection_manifest(output)
    print(json.dumps({
        "manifest_path": str(output),
        "manifest_fingerprint": loaded.fingerprint,
        "input_database_fingerprint": loaded.input_database_fingerprint,
        "candidate_count": len(candidates),
        "selected_count": len(loaded.items),
        "selection_criteria": criteria,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
