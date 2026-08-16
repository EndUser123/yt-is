#!/usr/bin/env python3
"""Detect terminated/deleted channels and mark them durably.

Motivation: channels terminated for copyright claims (operator example:
alexhollings52) return no description, no video titles, and no classification
evidence — they sit as permanent "unresolvable" stragglers, get refetched by
every evidence pass, and confuse the review page. yt-dlp's error text names
the cause ("account has been terminated...", "channel does not exist").

For each probed channel that is dead:
- channel_metadata.channel_status = 'terminated' | 'deleted' (+ detected_at)
- the channel is soft-blocked (existing blocklist machinery) so fetch/sync
  skip it immediately; unblock still works if YouTube resurrects it.

Targets by default: channels with no evidence (empty description) or
unclassified/Other, which is where the dead ones hide. --all probes every
channel. Resumable: only probes channel_status IS NULL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import block_channel, upsert_channel
from csf.paths import get_batch_db_path, load_workspace_env

DEAD_SIGNATURES = {
    "terminated": (
        "account has been terminated",
        "terminated because we received multiple",
    ),
    "deleted": (
        "channel does not exist",
        "this channel is not available",
        "404: not found",
    ),
}


def _classify_error(error_text: str) -> str | None:
    lowered = (error_text or "").lower()
    for status, signatures in DEAD_SIGNATURES.items():
        if any(sig in lowered for sig in signatures):
            return status
    return None


def _probe_channel(channel_url: str, timeout: float = 30.0) -> tuple[str | None, str]:
    """Return (dead_status|None, evidence_text) via yt-dlp about extraction."""
    import yt_dlp

    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": timeout,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(channel_url.rstrip("/") + "/about", download=False)
        return None, "alive"
    except Exception as exc:
        status = _classify_error(str(exc))
        return status, str(exc)[:300]


def detect(
    db_path: Path, *, probe_all: bool, pace_s: float, limit: int | None
) -> dict[str, object]:
    from csf.batch_status import _BatchStatusStorage

    _BatchStatusStorage(db_path=db_path)  # run column migrations
    conn = sqlite3.connect(db_path)
    if probe_all:
        rows = conn.execute(
            "SELECT channel_url FROM channel_metadata WHERE channel_status IS NULL"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT channel_url FROM channel_metadata WHERE channel_status IS NULL "
            "AND ((description IS NULL OR TRIM(description)='') "
            "  OR category IS NULL OR category='Other')"
        ).fetchall()
    conn.close()
    urls = [r[0] for r in rows]
    if limit:
        urls = urls[:limit]

    marked: dict[str, list] = {"terminated": [], "deleted": []}
    alive = 0
    inconclusive = 0
    for url in urls:
        status, evidence = _probe_channel(url)
        if status is None:
            if evidence == "alive":
                alive += 1
            else:
                inconclusive += 1
            time.sleep(pace_s)
            continue
        upsert_channel(
            url,
            db_path=db_path,
            channel_status=status,
            channel_status_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            block_channel(url, db_path=db_path, reason="dead")
        except Exception:
            pass
        marked[status].append(url)
        time.sleep(pace_s)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "probed": len(urls),
        "alive": alive,
        "inconclusive": inconclusive,
        "terminated": len(marked["terminated"]),
        "deleted": len(marked["deleted"]),
        "terminated_sample": marked["terminated"][:5],
        "deleted_sample": marked["deleted"][:5],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--all", action="store_true", help="Probe every channel, not just evidence-less ones.")
    parser.add_argument("--pace", type=float, default=0.3)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    load_workspace_env()
    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    receipt = detect(db_path, probe_all=args.all, pace_s=args.pace, limit=args.limit)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
