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
    get_playlist_import_db_path,
    list_video_import_runs,
    reconcile_video_import_run,
)


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
    if args.run_id:
        payload = reconcile_video_import_run(
            args.run_id,
            batch_status_db_path=args.batch_db,
            playlist_import_db_path=args.playlist_db,
        )
    else:
        payload = {
            "operation": "list_video_import_runs",
            "playlist_import_db_path": str(Path(args.playlist_db or get_playlist_import_db_path())),
            "statuses": list(statuses),
            "runs": list_video_import_runs(statuses=statuses, db_path=args.playlist_db),
        }

    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(rendered)
    if args.output:
        _write_json(args.output, payload, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
