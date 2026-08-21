#!/usr/bin/env python3
"""Enforce the channel-exclusion policy: every channel whose category is in
the operator's exclusion set, and is not explicitly exempted, must be on the
blocklist. Symmetrically, every category-reason block whose category is no
longer excluded (or whose channel is now exempt) must be removed.

This is the single chokepoint the operator expects to be the source of
truth for "exclusion means blocked." It is called automatically from:

  - ``apply_channel_review.py`` after every review export is applied, so
    the next sync's enforcement table matches the operator's latest
    decision set.
  - ``run_all_syncs.py`` at the start of the daily task, as a guardrail
    against direct DB edits, missed apply runs, or out-of-band config
    changes.

Contract:
  - Idempotent: re-running with the same ``excluded_categories`` is a no-op
    (returns zeros for adds/removes).
  - Never blocks a channel that is in the exemption set
    (``channel_metadata.exempt_from_exclusion = 1``). The ★ button on the
    review page is the only path to exemption; respecting it here is the
    "Gmail DLP" pattern (rule no longer applies -> unblock).
  - Only touches category-reason blocks (``category:<X>``). Operator
    blocks (reason="operator") are not removed by this script; the
    operator unblocks them.
  - Returns a receipt identical in shape to
    ``promote_excluded_categories.promote`` so callers that already
    consume that receipt keep working.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import block_channel, is_channel_blocked  # noqa: E402
from csf.categorize import CATEGORIES  # noqa: E402
from csf.paths import get_batch_db_path  # noqa: E402

CATEGORY_BLOCK_PREFIX = "category:"


def _enforce_connection(db_path: Path):
    """Open a connection to the batch DB. Caller closes."""
    return sqlite3.connect(str(db_path))


def _candidate_channels(
    conn: sqlite3.Connection, excluded_categories: frozenset[str]
) -> list[dict[str, str]]:
    """Channels that should be blocked because their category is excluded.

    A channel is a candidate iff its category is in the excluded set AND
    it is not exempt. We project the full row we need to either block or
    keep, plus a few diagnostic fields.
    """
    if not excluded_categories:
        return []
    placeholders = ",".join("?" * len(excluded_categories))
    rows = conn.execute(
        f"SELECT channel_url, channel_id, channel_title, category "
        f"FROM channel_metadata "
        f"WHERE category IN ({placeholders}) "
        f"  AND COALESCE(exempt_from_exclusion, 0) != 1 "
        f"ORDER BY category, channel_url",
        tuple(sorted(excluded_categories)),
    ).fetchall()
    return [
        {
            "channel_url": r[0],
            "channel_id": r[1],
            "channel_title": r[2],
            "category": r[3],
        }
        for r in rows
    ]


def _category_reason_blocks(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every (channel_url, reason) pair on the blocklist whose reason
    starts with ``category:`` — the only blocks this script owns."""
    return [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT channel_url, reason FROM channel_blocklist "
            "WHERE reason LIKE 'category:%'"
        ).fetchall()
    ]


def enforce(
    *,
    db_path: Path,
    excluded_categories: frozenset[str],
) -> dict[str, object]:
    """Reconcile ``channel_blocklist`` to match the exclusion policy.

    Returns a receipt with the same shape as
    ``promote_excluded_categories.promote`` so callers that already
    consume that receipt keep working (adds in ``promoted`` / ``promoted_channel_urls``,
    removals in ``reconciled`` / ``reconciled_channel_urls``).
    """
    db_path = Path(db_path)
    promoted: list[str] = []
    reconciled: list[str] = []
    errors: list[dict[str, str]] = []
    promoted_details: list[dict[str, str]] = []

    conn = _enforce_connection(db_path)
    try:
        # Phase 1: add category-reason blocks for new candidates.
        # Idempotent: skip channels already on the blocklist for any reason.
        candidates = _candidate_channels(conn, excluded_categories)
        candidate_urls = {c["channel_url"] for c in candidates}
        already_blocked = 0
        for c in candidates:
            url = c["channel_url"]
            try:
                if is_channel_blocked(url, db_path=db_path):
                    already_blocked += 1
                    continue
                block_channel(
                    url, db_path=db_path,
                    reason=f"{CATEGORY_BLOCK_PREFIX}{c['category']}",
                )
                promoted.append(url)
                promoted_details.append({
                    "channel_url": url,
                    "category": c["category"],
                })
            except Exception as exc:  # keep going; report every failure
                errors.append({
                    "channel_url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # Phase 2: remove category-reason blocks that no longer apply.
        # A category-reason block is stale when:
        #   (a) the channel's current category is not in the excluded set
        #       (operator removed the category from exclusions), OR
        #   (b) the channel is now exempt (operator clicked ★ on review).
        # We only remove blocks with reason starting with "category:";
        # operator blocks are out of scope.
        for url, reason in _category_reason_blocks(conn):
            cat = reason[len(CATEGORY_BLOCK_PREFIX):] if reason.startswith(
                CATEGORY_BLOCK_PREFIX
            ) else ""
            row = conn.execute(
                "SELECT category, COALESCE(exempt_from_exclusion, 0) "
                "FROM channel_metadata WHERE channel_url = ?",
                (url,),
            ).fetchone()
            if row is None:
                # Channel was deleted from metadata entirely; the
                # category-reason block no longer applies.
                _delete_category_block(conn, url, reason)
                reconciled.append(url)
                continue
            current_category, exempt = row[0], row[1]
            if cat not in excluded_categories or exempt:
                _delete_category_block(conn, url, reason)
                reconciled.append(url)

        # Diagnostic counts (mirrors promote_excluded_categories' shape).
        exempt_count = _exempt_count(conn, excluded_categories)
        uncategorized = conn.execute(
            "SELECT COUNT(*) FROM channel_metadata WHERE category IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "mode": "apply",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "excluded_categories": sorted(excluded_categories),
        "candidates": len(candidates),
        "promoted": len(promoted),
        "promoted_channel_urls": promoted,
        "promoted_details": promoted_details,
        "already_blocked": already_blocked,
        "reconciled": len(reconciled),
        "reconciled_channel_urls": reconciled,
        "uncategorized_channels": uncategorized,
        "exempted_channels": exempt_count,
        "errors": errors,
    }


def _delete_category_block(
    conn: sqlite3.Connection, url: str, reason: str
) -> None:
    """Delete a single category-reason block, scoped to the exact reason
    so we never accidentally clear an operator block on the same URL."""
    conn.execute(
        "DELETE FROM channel_blocklist "
        "WHERE channel_url = ? AND reason = ?",
        (url, reason),
    )
    conn.commit()


def _exempt_count(
    conn: sqlite3.Connection, excluded_categories: frozenset[str]
) -> int:
    if not excluded_categories:
        return 0
    placeholders = ",".join("?" * len(excluded_categories))
    return conn.execute(
        f"SELECT COUNT(*) FROM channel_metadata "
        f"WHERE category IN ({placeholders}) "
        f"  AND COALESCE(exempt_from_exclusion, 0) = 1",
        tuple(sorted(excluded_categories)),
    ).fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile channel_blocklist to match the exclusion policy.",
    )
    parser.add_argument(
        "--exclude",
        required=True,
        help="Comma-separated category names to exclude from sync "
             "(e.g. 'News,Entertainment').",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Batch status DB (default: canonical yt-is batch DB).",
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

    receipt = enforce(
        db_path=db_path,
        excluded_categories=frozenset(raw_names),
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
