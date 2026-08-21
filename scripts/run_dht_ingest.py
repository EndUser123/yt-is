"""Ingest a Discord History Tracker (DHT) archive into yt-is.

The DHT desktop app (installed at P:/tools/dht/DiscordHistoryTracker.exe)
captures messages from your logged-in Discord session into a SQLite
archive. This script finds that archive (or takes --archive PATH),
introspects its schema, converts new messages into transcript batches
(source='discord'), and hands them to the standard connector ingestion.

Runs daily in the 06:00 sync; silently skips when no archive exists yet.

Usage:
    python scripts/run_dht_ingest.py                # auto-discover archive
    python scripts/run_dht_ingest.py --archive "P:/path/tracker.dht"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
CANDIDATE_GLOBS = [
    Path.home() / "Documents" / "*.dht",
    Path.home() / "Documents" / "*.db",
    Path.home() / "Downloads" / "*.dht",
    Path("P:/.data/yt-is") / "dht" / "*.dht",
    Path("P:/.data/yt-is") / "dht" / "*.sqlite",
]


def discover_archive() -> Path | None:
    seen = set()
    for pattern in CANDIDATE_GLOBS:
        parent = pattern.parent
        if not parent.exists():
            continue
        for p in parent.glob(pattern.name):
            if p in seen:
                continue
            seen.add(p)
            try:
                conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True,
                                       timeout=5.0)
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                conn.close()
                if any("message" in t.lower() for t in tables):
                    return p
            except sqlite3.Error:
                continue
    return None


def introspect_messages_table(conn) -> tuple[str, dict] | None:
    """Find the messages table and its column roles. DHT's schema has
    evolved; adapt by name heuristics instead of hardcoding."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if "message" not in table.lower():
            continue
        cols = {r[1].lower(): r[1] for r in conn.execute(
            f'PRAGMA table_info("{table}")')}
        if not cols:
            continue
        # `taken` prevents a column already assigned to an earlier role
        # (e.g. `id` -> "messageid") from being re-picked by a later role's
        # looser substring match (e.g. `content` -> "message" in "messageid").
        taken: set[str] = set()

        def find(*names):
            for n in names:
                for c in cols:
                    if c in taken:
                        continue
                    if n == c or c.endswith("_" + n) or c.startswith(n + "_"):
                        return cols[c]
                for c in cols:
                    if c in taken:
                        continue
                    if n in c:
                        return cols[c]
            return None
        roles: dict = {}
        for role, names in (
            ("id", ("id", "messageid")),
            ("content", ("content", "text", "message", "body")),
            ("author", ("author", "user", "username")),
            ("timestamp", ("timestamp", "time", "date")),
            ("channel", ("channel", "channelid")),
            ("server", ("server", "guild", "guildid")),
        ):
            v = find(*names)
            roles[role] = v
            if v:
                taken.add(v.lower())
        if roles["content"] and roles["id"]:
            return table, roles
    return None


def ingest_archive(archive: Path, limit: int = 5000) -> dict:
    src = sqlite3.connect(f"file:{archive}?mode=ro", uri=True, timeout=10.0)
    found = introspect_messages_table(src)
    if not found:
        tables = [r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        src.close()
        return {"ok": False,
                "error": f"no recognizable messages table (tables: {tables[:8]})"}
    table, roles = found

    sel_parts, sel_cols = [], []
    for role in ("id", "content", "timestamp"):
        col = roles.get(role)
        if col:
            sel_parts.append(f'"{col}" AS {role}')
            sel_cols.append(role)
    channel_col = roles.get("channel")

    # readable names via users/channels/servers joins where available
    sql = f'SELECT {", ".join(sel_parts)}'
    params = []
    if channel_col:
        sql += f', "{channel_col}" AS channel'
        sel_cols.append("channel")
    author_col = roles.get("author")
    if author_col:
        sql += f', "{author_col}" AS author_raw'
        sel_cols.append("author_raw")
    sql += f' FROM "{table}"'
    rows = src.execute(sql).fetchall()

    # resolve id -> name maps from companion tables
    src_tables = {r[0].lower() for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if 'users' in src_tables:
        users = dict(src.execute("SELECT id, name FROM users").fetchall())
    else:
        users = {}
    channels = dict(src.execute(
        "SELECT c.id, c.name FROM channels c").fetchall())
    servers = dict(src.execute(
        "SELECT s.id, s.name FROM servers s").fetchall())
    server_of = {}
    for cid, sid in src.execute("SELECT id, server FROM channels").fetchall():
        server_of[cid] = servers.get(sid, "")
    src.close()

    msgs = [dict(zip(sel_cols, r)) for r in rows]
    by_channel: dict = {}
    for m in msgs:
        if not m.get("content"):
            continue
        by_channel.setdefault(str(m.get("channel") or "unknown"), []).append(m)

    BATCH = 100  # messages per stored document
    tdb = sqlite3.connect(str(TDB), timeout=30.0)
    tdb.execute("PRAGMA busy_timeout=30000")
    new_batches = 0
    now = datetime.now(timezone.utc).isoformat()
    for ch, messages in by_channel.items():
        # Sort by id numerically when possible. Discord snowflakes are
        # 18-digit numbers stored as TEXT; the previous lexicographic sort
        # only works because their lengths are uniform. Any archive with
        # mixed-length ids (test fixtures, older schemas) would produce
        # the wrong window boundaries.
        def _id_key(m):
            raw = m.get("id")
            try:
                return (0, int(raw))
            except (TypeError, ValueError):
                return (1, str(raw))
        messages.sort(key=_id_key)
        ch_name = channels.get(ch, ch)
        guild = server_of.get(ch, "")
        for i in range(0, len(messages), BATCH):
            window = messages[i:i + BATCH]
            first_id, last_id = str(window[0]["id"]), str(window[-1]["id"])
            cache_key = f"dht:{ch}:{first_id}:{last_id}"
            if tdb.execute(
                    "SELECT 1 FROM transcript_cache WHERE cache_key = ?",
                    (cache_key,)).fetchone():
                continue
            lines, ts_min, ts_max = [], None, None
            for m in window:
                ts = str(m.get("timestamp") or "")
                if ts:
                    ts_min = ts if ts_min is None or ts < ts_min else ts_min
                    ts_max = ts if ts_max is None or ts > ts_max else ts_max
                author = users.get(str(m.get("author_raw")),
                                   str(m.get("author_raw") or "unknown"))
                lines.append(f"[{ts[:19]}] {author}: {m['content']}")
            if sum(len(l) for l in lines) < 100:
                continue
            doc_id = f"dht_{ch}_{first_id}_{last_id}"[:120]
            tdb.execute(
                """INSERT OR REPLACE INTO transcript_cache
                   (cache_key, video_id, lang, source, transcript,
                    metadata_json, cached_at, terminal_id)
                   VALUES (?, ?, 'en', 'discord', ?, ?, ?, 'dht')""",
                (cache_key, doc_id, "\n".join(lines),
                 json.dumps({"channel_id": str(ch), "channel_name": ch_name,
                             "guild_name": guild,
                             "message_count": len(window),
                             "first_ts": ts_min, "last_ts": ts_max,
                             "archive": str(archive)}),
                 now))
            new_batches += 1
        tdb.commit()
    tdb.close()
    total_msgs = sum(len(v) for v in by_channel.values())
    return {"ok": True, "messages_seen": total_msgs,
            "channels": len(by_channel), "new_batches": new_batches}


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=None)
    args = parser.parse_args(argv)

    archive = Path(args.archive) if args.archive else discover_archive()
    if not archive:
        print("no DHT archive found — run the tracker "
              "(P:/tools/dht/DiscordHistoryTracker.exe) and capture "
              "a channel first")
        return 0

    files = sorted(archive.glob("*.dht")) if archive.is_dir() else [archive]
    if not files:
        print(f"no .dht files in {archive}")
        return 0
    ok = True
    for f in files:
        out = ingest_archive(f)
        print(f"{f.name}: {json.dumps(out)}", flush=True)
        ok = ok and out.get("ok", False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
