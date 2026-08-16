#!/usr/bin/env python3
"""Backfill channel descriptions — free yt-dlp path first, Data API fallback.

The playlist imports store titles but not descriptions, so classification
and review worked from titles alone — the direct cause of the "Other"
wave (100% of Other channels had empty descriptions).

Two tiers, cheapest first:
  1. yt-dlp channel /about extraction (innertube) — keyless, zero API
     quota, ~1s per channel. Verified against live channels.
  2. YouTube Data API channels.list in batches of 50 (1 quota unit per
     batch) — only for channels tier 1 could not fetch, and only when
     --allow-spend is given (per-run authorization, dies with the process).

Also stores fresh subscriber counts when the API tier supplies them.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import upsert_channel
from csf.paths import get_batch_db_path, load_workspace_env


def _targets(db_path: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT channel_url, channel_id FROM channel_metadata "
        "WHERE description IS NULL OR TRIM(description)='' "
        "   OR channel_title IS NULL OR TRIM(channel_title)='' "
        "ORDER BY channel_url"
    ).fetchall()
    conn.close()
    return [(url, cid) for url, cid in rows if cid]


def _fetch_via_ytdlp(channel_url: str) -> dict[str, str] | None:
    """Return {title, description} (either may be absent) or None on failure."""
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "extract_flat": True, "socket_timeout": 30}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url.rstrip("/") + "/about", download=False)
        info = info or {}
        title = (info.get("channel") or info.get("title") or "").strip()
        description = (info.get("description") or "").strip()
        if not title and not description:
            return None
        result: dict[str, str] = {}
        if title:
            result["channel_title"] = title
        if description:
            result["description"] = description
        return result
    except Exception:
        return None


def _api_keys() -> list[str]:
    names = (
        "YOUTUBE_API_KEY", "YOUTUBE_API_KEY_2", "YOUTUBE_API_KEY_3",
        "YOUTUBE_API_KEY_4", "YOUTUBE_API_KEY_5",
        "YT_API_KEY_1", "YT_API_KEY_2", "YT_API_KEY_3", "YT_API_KEY_4", "YT_API_KEY_5",
    )
    return [k for n in names if (k := os.environ.get(n, "").strip())]


def _channels_list(key: str, channel_ids: list[str]) -> list[dict]:
    query = urllib.parse.urlencode({
        "part": "snippet,statistics",
        "id": ",".join(channel_ids),
        "key": key,
    })
    url = "https://www.googleapis.com/youtube/v3/channels?" + query
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
        return json.loads(response.read()).get("items", [])


def backfill(
    db_path: Path, *, allow_spend: bool, ytdlp_pace_s: float = 0.3, batch_size: int = 50
) -> dict[str, object]:
    targets = _targets(db_path)
    updated_ytdlp = 0
    failed_ytdlp: list[str] = []
    for url, _cid in targets:
        fields = _fetch_via_ytdlp(url)
        if fields:
            upsert_channel(url, db_path=db_path, **fields)
            updated_ytdlp += 1
        else:
            failed_ytdlp.append(url)
        time.sleep(ytdlp_pace_s)

    updated_api = 0
    api_errors: list[str] = []
    remaining_with_ids = [
        (url, cid) for url, cid in targets if url in set(failed_ytdlp)
    ]
    if remaining_with_ids and allow_spend:
        keys = _api_keys()
        if not keys:
            api_errors.append("no YouTube API keys configured for fallback")
        else:
            key_index = 0
            for start in range(0, len(remaining_with_ids), batch_size):
                batch = remaining_with_ids[start:start + batch_size]
                items = None
                last_error = ""
                for _attempt in range(len(keys) * 2):
                    key = keys[key_index % len(keys)]
                    key_index += 1
                    try:
                        items = _channels_list(key, [cid for _, cid in batch])
                        break
                    except Exception as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                if items is None:
                    api_errors.append(f"batch {start//batch_size}: {last_error}")
                    continue
                found = {item["id"]: item for item in items}
                for url, cid in batch:
                    item = found.get(cid)
                    if not item:
                        continue
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    description = (snippet.get("description") or "").strip()
                    title = (snippet.get("title") or "").strip()
                    kwargs: dict[str, str | int] = {}
                    if description:
                        kwargs["description"] = description
                    if title:
                        kwargs["channel_title"] = title
                    subscribers = stats.get("subscriberCount")
                    if subscribers and subscribers.isdigit():
                        kwargs["subscriber_count"] = int(subscribers)
                    if kwargs:
                        upsert_channel(url, db_path=db_path, **kwargs)
                        updated_api += 1

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "channels_targeted": len(targets),
        "updated_via_ytdlp": updated_ytdlp,
        "updated_via_api": updated_api,
        "unresolved": len(targets) - updated_ytdlp - updated_api,
        "api_errors": api_errors[:5],
        "api_used": bool(remaining_with_ids and allow_spend),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--allow-spend", action="store_true",
        help="Authorize the Data API fallback tier for channels yt-dlp could not fetch.",
    )
    parser.add_argument("--pace", type=float, default=0.3, help="Seconds between yt-dlp fetches.")
    args = parser.parse_args(argv)

    load_workspace_env()
    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    receipt = backfill(db_path, allow_spend=args.allow_spend, ytdlp_pace_s=args.pace)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not receipt["api_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
