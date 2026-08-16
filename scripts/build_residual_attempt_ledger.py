#!/usr/bin/env python3
"""Seed a residual-attempt ledger from exact historical requeue receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.residual_attempt_ledger import (
    LEDGER_VERSION,
    file_fingerprint,
    write_residual_attempt_ledger,
)


def _receipt_entry(path: Path, payload: dict[str, object]) -> dict[str, object] | None:
    if payload.get("receipt_version") != 1 or not payload.get("apply"):
        return None
    required = ("run_id", "created_at", "db_path", "manifest_path", "manifest_fingerprint", "video_ids")
    if any(key not in payload for key in required):
        return None
    video_ids = payload["video_ids"]
    if not isinstance(video_ids, list) or not video_ids or any(not isinstance(item, str) for item in video_ids):
        raise ValueError(f"invalid video_ids in receipt: {path}")
    attempt_id = str(payload["run_id"])
    return {
        "attempt_id": attempt_id,
        "created_at": str(payload["created_at"]),
        "status": str(payload.get("status") or "historical_applied"),
        "db_path": str(payload["db_path"]),
        "manifest_path": str(payload["manifest_path"]),
        "manifest_fingerprint": str(payload["manifest_fingerprint"]),
        "video_ids": list(video_ids),
        "expected_failure_reason": payload.get("expected_failure_reason"),
        "expected_failure_class": payload.get("expected_failure_class"),
        "mechanism_id": "legacy-receipt",
        "hypothesis": "Historical receipt predates explicit residual-attempt hypothesis capture.",
        "account_scope": "legacy-unknown",
        "decision_packet_path": None,
        "decision_packet_fingerprint": None,
        "receipt_path": str(path.resolve()),
        "receipt_fingerprint": file_fingerprint(path),
        "legacy": True,
    }


def build_residual_attempt_ledger(*, roots: tuple[Path, ...], output: Path, overwrite: bool = False) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    seen_attempt_ids: set[str] = set()
    seen_receipts: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"receipt root not found: {root}")
        for path in sorted(root.rglob("*.json")):
            if "requeue" not in path.name.lower():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            entry = _receipt_entry(path, payload)
            if entry is None:
                continue
            if entry["attempt_id"] in seen_attempt_ids:
                raise ValueError(f"duplicate historical attempt_id: {entry['attempt_id']}")
            seen_attempt_ids.add(str(entry["attempt_id"]))
            seen_receipts.add(path.resolve())
            entries.append(entry)
    entries.sort(key=lambda item: (str(item["created_at"]), str(item["attempt_id"])))
    ledger: dict[str, object] = {
        "ledger_version": LEDGER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_roots": [str(Path(root).resolve()) for root in roots],
        "source_receipt_count": len(seen_receipts),
        "attempts": entries,
    }
    output = output.resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"ledger exists: {output}")
    write_residual_attempt_ledger(output, ledger)
    return {
        "status": "built",
        "output": str(output),
        "attempt_count": len(entries),
        "source_receipt_count": len(seen_receipts),
        "database_mutated": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = build_residual_attempt_ledger(
        roots=tuple(args.root),
        output=args.output,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
