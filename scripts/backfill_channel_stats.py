#!/usr/bin/env python3
"""Backfill channel-shape stats: shorts and playlist counts.

Neither count exists in the YouTube Data API channel object; both are free
via yt-dlp channel tabs (one innertube request each, no key, no quota).
Counts are first-page caps (default 30) — "30" means "30 or more".

Targets channels where shorts_count IS NULL, so the run is resumable and
idempotent. Pace keeps YouTube comfortable (~0.3s between channels, two
requests each).
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

from csf.batch_status import upsert_channel
from csf.paths import get_batch_db_path, load_workspace_env


def _fetch_tab_count(channel_url: str, tab: str, cap: int = 30, timeout: float = 30.0) -> int | None:
    """Count entries on a channel tab's first page; None = tab unavailable."""
    import yt_dlp

    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlist_items": f"1-{cap}",
        "socket_timeout": timeout,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url.rstrip("/") + f"/{tab}", download=False)
    except Exception as exc:
        # "This channel does not have a <tab> tab" is a real answer (zero),
        # not a fetch failure — otherwise the channel re-queues forever.
        if "does not have" in str(exc).lower():
            return 0
        return None
    entries = (info or {}).get("entries") or []
    return len([e for e in entries if e])


def backfill(db_path: Path, pace_s: float = 0.3, limit: int | None = None) -> dict[str, object]:
    from csf.batch_status import _BatchStatusStorage

    _BatchStatusStorage(db_path=db_path)  # runs column migrations on the real DB
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT channel_url FROM channel_metadata WHERE shorts_count IS NULL"
        " ORDER BY channel_url"
    ).fetchall()
    conn.close()
    urls = [r[0] for r in rows]
    if limit:
        urls = urls[:limit]

    updated = 0
    failed: list[str] = []
    for url in urls:
        shorts = _fetch_tab_count(url, "shorts")
        playlists = _fetch_tab_count(url, "playlists")
        if shorts is None and playlists is None:
            failed.append(url)
        else:
            kwargs: dict[str, int] = {}
            if shorts is not None:
                kwargs["shorts_count"] = shorts
            if playlists is not None:
                kwargs["playlists_count"] = playlists
            upsert_channel(url, db_path=db_path, **kwargs)
            updated += 1
        time.sleep(pace_s)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "targeted": len(urls),
        "updated": updated,
        "unresolved": len(failed),
        "unresolved_sample": failed[:5],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--pace", type=float, default=0.3)
    parser.add_argument("--limit", type=int, default=None, help="Cap channels for this run (testing/preview).")
    args = parser.parse_args(argv)

    load_workspace_env()
    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    receipt = backfill(db_path, pace_s=args.pace, limit=args.limit)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
