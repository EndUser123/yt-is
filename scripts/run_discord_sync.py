"""Discord ingestion — read server messages into the yt-is knowledge base.

Requires a Discord bot token. To set up:
1. Go to https://discord.com/developers/applications
2. Create a New Application → Bot
3. Copy the bot token
4. Add to P:/.env:  DISCORD_BOT_TOKEN=your_token_here
5. Invite the bot to your server (with "Read Messages" permission)
6. Add channel IDs to track: DISCORD_CHANNELS=123456,789012

Usage:
    python scripts/run_discord_sync.py              # sync all tracked channels
    python scripts/run_discord_sync.py --add #channel-name  # track a channel
    python scripts/run_discord_sync.py --list       # list tracked channels
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
DISCORD_API = "https://discord.com/api/v10"
REQUEST_DELAY_S = 0.8  # Discord rate limit: 50 req/sec but be conservative

MESSAGES_PER_CHANNEL = 50


def _get_headers():
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("⚠️  No DISCORD_BOT_TOKEN set. Add it to P:/.env")
        print("   Get one at https://discord.com/developers/applications")
        sys.exit(1)
    return {
        "Authorization": f"Bot {token}",
        "User-Agent": "ytis-discord-sync/1.0",
        "Content-Type": "application/json",
    }


def _api_get(endpoint, params=None):
    """Make a GET request to the Discord API."""
    import urllib.parse
    url = f"{DISCORD_API}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers=_get_headers())
    time.sleep(REQUEST_DELAY_S)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Rate limited — respect the retry-after header
            retry_after = float(e.headers.get("Retry-After", 5))
            print(f"    Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        raise


def ensure_discord_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS discord_channels (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            guild_id TEXT,
            guild_name TEXT,
            added_at TEXT NOT NULL,
            last_synced TEXT,
            last_message_id TEXT,
            total_batches INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def get_tracked_channels():
    conn = sqlite3.connect(str(DB), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_discord_table(conn)
    rows = conn.execute("""
        SELECT channel_id, channel_name, guild_name FROM discord_channels
        ORDER BY added_at
    """).fetchall()
    conn.close()
    return rows


def add_channel(channel_id_or_name):
    """Add a Discord channel to track by ID or name."""
    # Resolve to channel ID
    channel_id = channel_id_or_name.strip().removeprefix("#").strip("<>")
    try:
        # If it's numeric, it's already an ID
        int(channel_id)
    except ValueError:
        # Try to find by name — needs at least one tracked guild
        print(f"  Looking up channel '{channel_id_or_name}'...")
        # For now, require the numeric ID
        print("  Please provide the channel ID (right-click channel → Copy Channel ID)")
        print("  Enable Developer Mode in Discord settings to see IDs")
        return

    # Get channel info from API
    try:
        info = _api_get(f"/channels/{channel_id}")
        channel_name = info.get("name", "")
        guild_id = info.get("guild_id", "")
        guild_name = ""
        if guild_id:
            try:
                guild = _api_get(f"/guilds/{guild_id}")
                guild_name = guild.get("name", "")
            except Exception:
                pass

        conn = sqlite3.connect(str(DB), timeout=30.0)
        conn.execute("PRAGMA busy_timeout=30000")
        ensure_discord_table(conn)
        conn.execute(
            """INSERT OR IGNORE INTO discord_channels
               (channel_id, channel_name, guild_id, guild_name, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (channel_id, channel_name, guild_id, guild_name,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        print(f"  Added #{channel_name} ({guild_name}) — ID: {channel_id}")
    except Exception as e:
        print(f"  Failed to add channel: {e}")


def fetch_channel_messages(channel_id, limit=MESSAGES_PER_CHANNEL, before=None):
    """Fetch recent messages from a channel."""
    params = {"limit": min(limit, 100)}
    if before:
        params["before"] = before

    data = _api_get(f"/channels/{channel_id}/messages", params)
    messages = []
    for m in data:
        # Skip bot messages and empty content
        if m.get("author", {}).get("bot"):
            continue
        content = m.get("content", "").strip()
        if not content:
            continue

        messages.append({
            "id": m["id"],
            "content": content,
            "author": m.get("author", {}).get("username", "unknown"),
            "author_id": m.get("author", {}).get("id", ""),
            "timestamp": m.get("timestamp", ""),
            "attachments": [
                a.get("url") for a in m.get("attachments", []) if a.get("url")
            ],
            "reactions": sum(
                r.get("count", 0) for r in m.get("reactions", [])
            ),
        })
    # Discord returns newest-first; reverse to chronological for transcripts
    messages.reverse()
    return messages


def messages_to_transcript(channel_name, guild_name, messages):
    """Convert a batch of messages to a transcript-like text."""
    parts = []
    parts.append(f"Channel: #{channel_name} ({guild_name})")
    parts.append(f"Messages: {len(messages)}")
    parts.append(f"Synced: {datetime.now(timezone.utc).isoformat()}")
    parts.append("")

    for m in messages:
        reactions = f" [{m['reactions']} reactions]" if m['reactions'] else ""
        parts.append(f"**{m['author']}**{reactions}:")
        parts.append(m["content"])
        for att in m.get("attachments", []):
            parts.append(f"  [attachment: {att}]")
        parts.append("")

    return "\n".join(parts)


def _retry_locked(fn, attempts=4, delay_s=5.0):
    """The transcript/status DBs have many concurrent writers (drain,
    backfill, indexer); queue and retry instead of crashing on a lock."""
    import time as _time
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts - 1:
                raise
            _time.sleep(delay_s)


def store_channel_batch(channel_id, channel_name, guild_name, messages):
    """Store a batch of messages, keyed by the batch's newest message.
    Returns True if a new batch was stored. Retries on DB lock."""
    return _retry_locked(lambda: _store_batch_once(channel_id, channel_name, guild_name, messages))


def _store_batch_once(channel_id, channel_name, guild_name, messages):
    if not messages:
        return False

    newest_id = messages[-1]["id"]
    cache_key = f"discord:{channel_id}:{newest_id}"
    batch_id = f"discord_{channel_id}_{newest_id}"

    transcript_text = messages_to_transcript(channel_name, guild_name, messages)

    tdb = sqlite3.connect(str(TDB), timeout=30.0)
    tdb.execute("PRAGMA busy_timeout=30000")

    existing = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()[0]
    if existing > 0:
        tdb.close()
        return False

    now = datetime.now(timezone.utc).isoformat()
    tdb.execute(
        """INSERT OR REPLACE INTO transcript_cache
           (cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id)
           VALUES (?, ?, 'en', 'discord', ?, ?, ?, 'discord')""",
        (
            cache_key,
            batch_id,
            transcript_text,
            json.dumps({
                "channel_id": channel_id,
                "channel_name": channel_name,
                "guild_name": guild_name,
                "message_count": len(messages),
                "newest_message_id": newest_id,
                "oldest_message_id": messages[0]["id"],
                "participants": list({m["author"] for m in messages}),
            }),
            now,
        ),
    )
    tdb.commit()
    tdb.close()
    return True


def sync_channel(channel_id, channel_name, guild_name, verbose=True):
    """Sync messages from one Discord channel."""
    if verbose:
        print(f"  Fetching #{channel_name} ({guild_name or 'unknown guild'})...")

    try:
        messages = fetch_channel_messages(channel_id, limit=MESSAGES_PER_CHANNEL)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"channel": channel_name, "error": "403 Missing Access (check bot permissions)", "new": 0, "total": 0}
        return {"channel": channel_name, "error": str(e)[:100], "new": 0, "total": 0}
    except Exception as e:
        return {"channel": channel_name, "error": str(e)[:100], "new": 0, "total": 0}

    # Nothing-new check: if the newest message is unchanged, skip storing
    conn = sqlite3.connect(str(DB), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_discord_table(conn)
    prev_newest = conn.execute(
        "SELECT last_message_id FROM discord_channels WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    prev_newest = prev_newest[0] if prev_newest else None

    newest_id = messages[-1]["id"] if messages else None
    stored = False
    if newest_id and newest_id != prev_newest:
        stored = store_channel_batch(channel_id, channel_name, guild_name, messages)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO discord_channels
             (channel_id, channel_name, guild_id, guild_name, added_at,
              last_synced, last_message_id, total_batches)
           VALUES (?, ?, '', ?, ?, ?, ?, ?)
           ON CONFLICT(channel_id) DO UPDATE SET
             last_synced = excluded.last_synced,
             last_message_id = COALESCE(excluded.last_message_id, last_message_id),
             total_batches = total_batches + excluded.total_batches""",
        (channel_id, channel_name, guild_name, now, now,
         newest_id, 1 if stored else 0),
    )
    conn.commit()
    conn.close()

    if verbose:
        status = "new batch" if stored else "no new messages"
        print(f"    {status} ({len(messages)} messages in window)")

    return {"channel": channel_name, "new": 1 if stored else 0, "total": len(messages), "error": None}


def main(argv=None):
    global MESSAGES_PER_CHANNEL
    load_workspace_env()

    parser = argparse.ArgumentParser(description="Sync Discord messages into yt-is")
    parser.add_argument("--add", default=None, help="Add a channel ID to track")
    parser.add_argument("--list", action="store_true", help="List tracked channels")
    parser.add_argument("--limit", type=int, default=MESSAGES_PER_CHANNEL)
    parser.add_argument("--all", action="store_true",
                        help="Fetch ALL channels from guilds the bot is in")
    args = parser.parse_args(argv)

    if args.add:
        add_channel(args.add)
        return 0

    if args.list:
        channels = get_tracked_channels()
        if not channels:
            print("No Discord channels tracked. Use --add <channel_id>")
        else:
            print("Tracked Discord channels:")
            for cid, cname, gname in channels:
                print(f"  #{cname or cid} ({gname or 'unknown guild'}) — {cid}")
        return 0

    # Get tracked channels (or discover all if --all)
    if args.all:
        print("Discovering all channels from bot's guilds...")
        # Get bot's guilds
        guilds = _api_get("/users/@me/guilds")
        for guild in guilds:
            guild_id = guild["id"]
            guild_name = guild["name"]
            try:
                channels = _api_get(f"/guilds/{guild_id}/channels")
                text_channels = [
                    ch for ch in channels
                    if ch.get("type") == 0  # type 0 = text channel
                ]
                for ch in text_channels[:5]:  # max 5 per guild
                    add_channel(ch["id"])
            except Exception as e:
                print(f"  ⚠️  Could not list channels for {guild_name}: {e}")

    tracked = get_tracked_channels()
    if not tracked:
        print("No Discord channels tracked.")
        print("Use --add <channel_id> or --all to discover channels")
        return 0

    MESSAGES_PER_CHANNEL = args.limit

    print(f"Syncing {len(tracked)} Discord channels...")
    print()

    total_new = 0
    total_errors = 0
    for cid, cname, gname in tracked:
        result = sync_channel(cid, cname, gname)
        total_new += result["new"]
        if result["error"]:
            total_errors += 1
            print(f"    ERROR: {result['error']}")

    print()
    print(f"Done: {total_new} new batches from {len(tracked)} channels")
    if total_errors:
        print(f"  ({total_errors} channels had errors)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
