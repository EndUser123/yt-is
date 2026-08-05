#!/usr/bin/env python3
"""List and reconcile append-only video-import provenance runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.playlist_imports import (
    VideoImportReconciliationUnavailable,
    get_playlist_import_db_path,
    list_video_import_runs,
    reconcile_video_import_run,
)
from csf.paths import get_batch_db_path


def _write_json(path: Path, payload: object, *, overwrite: bool = False) -> None:
    path = path.resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Reconcile one video-import run")
    parser.add_argument(
        "--status",
        action="append",
        choices=("running", "failed", "completed"),
        help="Run status to list; repeat for multiple statuses (default: running, failed)",
    )
    parser.add_argument("--playlist-db", type=Path, default=None)
    parser.add_argument("--batch-db", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    statuses = tuple(args.status or ("running", "failed"))
    playlist_db = Path(args.playlist_db or get_playlist_import_db_path()).resolve()
    batch_db = Path(args.batch_db or get_batch_db_path()).resolve()

    try:
        if args.run_id:
            payload = reconcile_video_import_run(
                args.run_id,
                batch_status_db_path=args.batch_db,
                playlist_import_db_path=playlist_db,
            )
        else:
            payload = {
                "operation": "list_video_import_runs",
                "playlist_import_db_path": str(playlist_db),
                "statuses": list(statuses),
                "runs": list_video_import_runs(statuses=statuses, db_path=playlist_db),
            }
    except VideoImportReconciliationUnavailable as exc:
        parser.error(str(exc))

    if args.output:
        output = args.output.resolve()
        protected = {playlist_db, batch_db}
        recorded_batch_db = payload.get("batch_status_db_path") if isinstance(payload, dict) else None
        if isinstance(recorded_batch_db, str) and recorded_batch_db.strip():
            protected.add(Path(recorded_batch_db).resolve())
        if output in protected:
            parser.error("--output must not replace a playlist or batch status database")

    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(rendered)
    if args.output:
        _write_json(args.output, payload, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
