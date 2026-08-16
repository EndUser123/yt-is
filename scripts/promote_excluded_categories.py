#!/usr/bin/env python3
"""Promote channels in excluded categories to the blocklist before sync.

``csf-source categorize`` assigns each tracked channel a category, but no
downstream path consults it: ``add``, ``check``, and ``check-all`` enforce
only the ``channel_blocklist`` table.  This script closes that gap in the
discover → classify → exclude → sync workflow: it selects channels whose
``channel_metadata.category`` is in the operator's exclusion set and, in
``--apply`` mode, blocks them through the existing soft-block API so every
enforcement point naturally skips them.

Contract:
- Dry-run is the default; ``--apply`` is required for any mutation.
- Unknown category names fail closed (exit 2) — a typo must never look
  like "nothing to exclude".
- Only channels with a matching category are blocked; uncategorized
  channels are never blocked, only counted in the receipt so the gap is
  visible.
- Blocking is soft (audit-preserving); ``csf-source unblock`` reverses it.
- Idempotent: already-blocked channels are counted, not re-blocked.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import block_channel, is_channel_blocked
from csf.categorize import CATEGORIES
from csf.paths import get_batch_db_path


def load_promotion_candidates(
    db_path: Path, excluded_categories: frozenset[str]
) -> tuple[list[dict[str, object]], int, int]:
    """Return (matching channels, uncategorized count, exempted count)."""
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" * len(excluded_categories))
        rows = conn.execute(
            "SELECT channel_url, channel_id, channel_title, category "
            f"FROM channel_metadata WHERE category IN ({placeholders}) "
            "AND COALESCE(exempt_from_exclusion, 0) != 1 "
            "ORDER BY category, channel_url",
            tuple(sorted(excluded_categories)),
        ).fetchall()
        exempt_count = conn.execute(
            f"SELECT COUNT(*) FROM channel_metadata WHERE category IN ({placeholders}) "
            "AND COALESCE(exempt_from_exclusion, 0) = 1",
            tuple(sorted(excluded_categories)),
        ).fetchone()[0]
        uncategorized = conn.execute(
            "SELECT COUNT(*) FROM channel_metadata WHERE category IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    candidates = [
        {
            "channel_url": row[0],
            "channel_id": row[1],
            "channel_title": row[2],
            "category": row[3],
        }
        for row in rows
    ]
    return candidates, int(uncategorized), int(exempt_count)


def promote(
    *,
    db_path: Path,
    excluded_categories: frozenset[str],
    apply: bool,
) -> dict[str, object]:
    candidates, uncategorized, exempt_count = load_promotion_candidates(db_path, excluded_categories)
    promoted: list[str] = []
    already_blocked = 0
    errors: list[dict[str, str]] = []
    if apply:
        for row in candidates:
            url = str(row["channel_url"])
            try:
                if is_channel_blocked(url, db_path=db_path):
                    already_blocked += 1
                    continue
                block_channel(url, db_path=db_path, reason=f"category:{row['category']}")
                promoted.append(url)
            except Exception as exc:  # keep going; report every failure
                errors.append({"channel_url": url, "error": f"{type(exc).__name__}: {exc}"})
    receipt: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "excluded_categories": sorted(excluded_categories),
        "candidates": len(candidates),
        "promoted": len(promoted),
        "already_blocked": already_blocked,
        "uncategorized_channels": uncategorized,
        "exempted_channels": exempt_count,
        "promoted_channel_urls": promoted,
        "errors": errors,
    }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Blocklist channels whose category is excluded from sync.",
    )
    parser.add_argument(
        "--exclude",
        required=True,
        help="Comma-separated category names to exclude from sync (e.g. 'News,Entertainment').",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Batch status DB (default: canonical yt-is batch DB).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually blocklist matching channels (default is dry-run).",
    )
    parser.add_argument(
        "--receipt-path",
        type=Path,
        default=None,
        help="Optional path to also write the JSON receipt.",
    )
    args = parser.parse_args(argv)

    raw_names = [name.strip() for name in args.exclude.split(",") if name.strip()]
    if not raw_names:
        print("error: --exclude contained no category names", file=sys.stderr)
        return 2
    known = set(CATEGORIES)
    unknown = [name for name in raw_names if name not in known]
    if unknown:
        print(
            f"error: unknown categories {unknown}; valid categories: {sorted(known)}",
            file=sys.stderr,
        )
        return 2

    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    if not db_path.exists():
        print(f"error: batch DB not found: {db_path}", file=sys.stderr)
        return 2

    receipt = promote(
        db_path=db_path,
        excluded_categories=frozenset(raw_names),
        apply=args.apply,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if args.receipt_path is not None:
        args.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
        )
    if receipt["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
