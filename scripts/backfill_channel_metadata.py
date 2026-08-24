#!/usr/bin/env python3
"""Backfill missing channel metadata (thumbnail, description, etc.) via
the YouTube Data API — quota-efficient by design.

Root cause: some channel-add paths write metadata with thumbnail_url=None
or without description, and the RSS scan path never backfills channel
metadata. channels.list costs 1 quota unit per call and accepts up to 50
channel IDs, so refreshing every affected channel is ~32 units for the
whole 2,865-channel universe — negligible against the 10,000/day default.

Self-healing: only channels still missing fields are queried, and updates
fill ONLY missing fields (never overwrite existing values), so re-runs
cost nothing once complete. Wired into the daily 06:00 sync; /status
surfaces the remaining gap count.

Credentials come from the environment, never from source. (An earlier
revision of this script embedded a credential inline; that value also
exists in git history — rotate it if still active.)

Usage:
    python scripts/backfill_channel_metadata.py           # up to 60 calls
    python scripts/backfill_channel_metadata.py --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
PER_CALL = 50          # channels.list accepts up to 50 IDs per call
PAUSE_S = 1.0
MAX_CALLS = 60         # bounded per invocation (covers ~3000 channels)


def affected_channels(conn) -> list[str]:
    rows = conn.execute("""
        SELECT channel_id FROM channel_metadata
        WHERE channel_id LIKE 'UC%'
          AND ((thumbnail_url IS NULL OR thumbnail_url = '')
               OR (description IS NULL OR description = ''))
        ORDER BY channel_url
    """).fetchall()
    return [r[0] for r in rows]


def fetch_metadata(channel_ids: list[str]) -> list[dict]:
    """One channels.list call (1 quota unit, up to 50 IDs)."""
    from csf.source_enumerator import _api_request
    resp = _api_request("channels", {
        "part": "snippet,statistics",
        "id": ",".join(channel_ids),
    }, unit_cost=1)
    if resp is None:
        return []
    out = []
    for item in resp.get("items", []):
        snippet = item.get("snippet", {})
        thumbs = snippet.get("thumbnails", {})
        thumb = (thumbs.get("high") or thumbs.get("medium")
                 or thumbs.get("default") or {}).get("url", "")
        out.append({
            "channel_id": item["id"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "thumbnail_url": thumb,
            "subscriber_count": (item.get("statistics", {})
                                 .get("subscriberCount")),
            "custom_url": snippet.get("customUrl", ""),
            "country": snippet.get("country", ""),
            "published_at": snippet.get("publishedAt", ""),
        })
    return out


def apply_metadata(conn, items: list[dict]) -> int:
    """Fill ONLY missing fields — never overwrite existing values."""
    n = 0
    for m in items:
        cur = conn.execute(
            """UPDATE channel_metadata SET
                 thumbnail_url = COALESCE(NULLIF(thumbnail_url, ''), ?),
                 description = COALESCE(NULLIF(description, ''), ?),
                 channel_title = COALESCE(NULLIF(channel_title, ''), ?),
                 subscriber_count = COALESCE(subscriber_count, ?),
                 custom_url = COALESCE(NULLIF(custom_url, ''), ?),
                 country = COALESCE(NULLIF(country, ''), ?),
                 published_at = COALESCE(NULLIF(published_at, ''), ?)
               WHERE channel_id = ?""",
            (m["thumbnail_url"], m["description"], m["title"],
             m["subscriber_count"], m["custom_url"], m["country"],
             m["published_at"], m["channel_id"]))
        n += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return n


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from csf.source_enumerator import set_spend_authorized, spend_used
    set_spend_authorized(True)

    conn = sqlite3.connect(str(DB), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    ids = affected_channels(conn)
    print(f"channels missing metadata: {len(ids)}")

    if args.dry_run:
        conn.close()
        print(f"would use ~{-(-len(ids) // PER_CALL)} quota units")
        return 0

    calls = updated = 0
    for i in range(0, len(ids), PER_CALL):
        if calls >= MAX_CALLS:
            print(f"  call cap reached ({MAX_CALLS}); re-run to continue")
            break
        batch = ids[i:i + PER_CALL]
        try:
            items = fetch_metadata(batch)
        except Exception as e:
            print(f"  batch error: {e}")
            break
        updated += apply_metadata(conn, items)
        calls += 1
        print(f"  call {calls}: {len(items)} channels returned "
              f"({updated} rows updated so far)", flush=True)
        time.sleep(PAUSE_S)

    remaining = len(affected_channels(conn))
    conn.close()
    print(f"done: {updated} channels updated, {remaining} still missing, "
          f"{calls} quota units used (session spend: {spend_used()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
