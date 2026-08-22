"""Ingest Discord History Tracker (DHT) archives into yt-is.

Canonical archive location (operator 2026-08-22): G:/backups/dht — the
cold originals. Fresh captures may still appear in Documents/Downloads
or P:/.data/dht before being copied there; discovery covers all of them.

Each archive is a SQLite file. This script introspects the schema,
converts messages into 100-message transcript windows (source='discord',
terminal_id='dht'), and hands them to transcript_cache. Ingestion is
idempotent per window (cache_key = dht:channel:firstid:lastid).

Scale handling (2026-08-22, 110 GB / 18 archives):
- STREAMING: rows are consumed with fetchmany() in rowid order and
  windowed per channel in bounded buffers. The previous fetchall()
  design exhausted memory on multi-GB archives (bear trap = 56 GB).
- FINGERPRINT SKIP: (path, size, mtime) recorded per archive in
  dht_archive_state; unchanged archives are skipped in milliseconds so
  the daily 06:00 sync never rescans the frozen originals.

Usage:
    python scripts/run_dht_ingest.py                # all discovered archives
    python scripts/run_dht_ingest.py --archive "G:/backups/dht"
    python scripts/run_dht_ingest.py --archive "G:/backups/dht/X.dht"
    python scripts/run_dht_ingest.py --reprocess    # ignore fingerprints
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

TDB = Path("P:/.data/yt-is/transcripts.sqlite")
SDB = Path("P:/.data/yt-is/batch_status.sqlite")

# First hit wins for same-named files; G: is the canonical cold store.
CANDIDATE_DIRS = [
    Path("G:/backups/dht"),
    Path("P:/.data/dht"),
    Path("P:/.data/yt-is/dht"),
    Path.home() / "Documents",
    Path.home() / "Downloads",
]

WINDOW = 100          # messages per stored document
MIN_WINDOW_CHARS = 100
FETCH_MANY = 10_000   # streaming batch size
HEARTBEAT_EVERY = 2_000_000  # messages between progress lines


def _looks_like_dht(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        return any("message" in t.lower() for t in tables)
    except sqlite3.Error:
        return False


def discover_archives() -> list[Path]:
    """All DHT archives across candidate dirs, deduped by name, smallest
    first (fast wins early; the 56 GB one lands last)."""
    by_name: dict[str, Path] = {}
    for d in CANDIDATE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.dht")):
            by_name.setdefault(p.name, p)
    files = []
    for name, p in by_name.items():
        if _looks_like_dht(p):
            files.append(p)
    return sorted(files, key=lambda p: p.stat().st_size)


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


def _fingerprint_state() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SDB), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""CREATE TABLE IF NOT EXISTS dht_archive_state (
        archive TEXT PRIMARY KEY,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        processed_at TEXT NOT NULL,
        messages_seen INTEGER,
        new_batches INTEGER)""")
    conn.commit()
    return conn


def ingest_archive(archive: Path, tdb: sqlite3.Connection) -> dict:
    """Stream one archive: bounded per-channel window buffers, no
    fetchall of the full table."""
    t0 = time.time()
    try:
        src = sqlite3.connect(f"file:{archive}?mode=ro", uri=True, timeout=30.0)
        src.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error as e:
        return {"ok": False, "error": f"open failed: {e}"}
    try:
        found = introspect_messages_table(src)
        if not found:
            tables = [r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            return {"ok": False,
                    "error": f"no recognizable messages table (tables: {tables[:8]})"}
        table, roles = found

        sel_parts = []
        for role in ("id", "content", "timestamp"):
            col = roles.get(role)
            if col:
                sel_parts.append(f'"{col}" AS {role}')
        channel_col = roles.get("channel")
        if channel_col:
            sel_parts.append(f'"{channel_col}" AS channel')
        author_col = roles.get("author")
        if author_col:
            sel_parts.append(f'"{author_col}" AS author_raw')
        sql = f'SELECT {", ".join(sel_parts)} FROM "{table}"'

        # id -> name maps (bounded: users/channels/servers tables are small
        # relative to messages; guarded for schemas without them)
        src_tables = {r[0].lower() for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        users: dict = {}
        channels: dict = {}
        server_of: dict = {}
        try:
            if "users" in src_tables:
                users = dict(src.execute(
                    "SELECT id, name FROM users").fetchall())
            if "channels" in src_tables:
                channels = dict(src.execute(
                    "SELECT c.id, c.name FROM channels c").fetchall())
                servers: dict = {}
                if "servers" in src_tables:
                    servers = dict(src.execute(
                        "SELECT s.id, s.name FROM servers s").fetchall())
                for cid, sid in src.execute(
                        "SELECT id, server FROM channels").fetchall():
                    server_of[cid] = servers.get(sid, "")
        except sqlite3.Error:
            pass  # names are cosmetic; ids still disambiguate

        cur = src.execute(sql)
        cur.arraysize = FETCH_MANY

        buffers: dict = {}      # channel -> list of messages (<= WINDOW)
        new_batches = 0
        seen = 0
        window_check = tdb.execute  # local alias
        now = datetime.now(timezone.utc).isoformat()

        def flush(channel_key: str) -> None:
            nonlocal new_batches
            buf = buffers.get(channel_key)
            if not buf:
                return
            ch_name = channels.get(channel_key, channel_key)
            guild = server_of.get(channel_key, "")
            for i in range(0, len(buf), WINDOW):
                window = buf[i:i + WINDOW]
                first_id, last_id = window[0][0], window[-1][0]
                cache_key = f"dht:{channel_key}:{first_id}:{last_id}"
                if window_check(
                        "SELECT 1 FROM transcript_cache WHERE cache_key = ?",
                        (cache_key,)).fetchone():
                    continue
                lines, ts_min, ts_max = [], None, None
                for mid, content, ts, _ck, author in window:
                    ts_s = str(ts or "")
                    if ts_s:
                        ts_min = ts_s if ts_min is None or ts_s < ts_min else ts_min
                        ts_max = ts_s if ts_max is None or ts_s > ts_max else ts_max
                    author_name = users.get(str(author),
                                            str(author or "unknown"))
                    lines.append(f"[{ts_s[:19]}] {author_name}: {content}")
                if sum(len(l) for l in lines) < MIN_WINDOW_CHARS:
                    continue
                doc_id = f"dht_{channel_key}_{first_id}_{last_id}"[:120]
                tdb.execute(
                    """INSERT OR REPLACE INTO transcript_cache
                       (cache_key, video_id, lang, source, transcript,
                        metadata_json, cached_at, terminal_id)
                       VALUES (?, ?, 'en', 'discord', ?, ?, ?, 'dht')""",
                    (cache_key, doc_id, "\n".join(lines),
                     json.dumps({"channel_id": str(channel_key),
                                 "channel_name": ch_name,
                                 "guild_name": guild,
                                 "message_count": len(window),
                                 "first_ts": ts_min, "last_ts": ts_max,
                                 "archive": str(archive)}),
                     now))
                new_batches += 1
            buffers[channel_key] = []

        while True:
            rows = cur.fetchmany()
            if not rows:
                break
            for row in rows:
                r = list(row) + [None] * (5 - len(row))
                _id, content, _ts, channel, author = r
                if not content:
                    continue
                ck = str(channel) if channel is not None else "unknown"
                buf = buffers.get(ck)
                if buf is None:
                    buf = buffers[ck] = []
                buf.append((str(_id), content, _ts, ck, author))
                if len(buf) >= WINDOW:
                    flush(ck)
            seen += len(rows)
            if seen // HEARTBEAT_EVERY != (seen - len(rows)) // HEARTBEAT_EVERY:
                print(f"  … {archive.name}: {seen:,} messages, "
                      f"{new_batches:,} new docs, {time.time()-t0:.0f}s",
                      flush=True)

        for ck in list(buffers):
            flush(ck)
        tdb.commit()
        return {"ok": True, "messages_seen": seen, "channels": len(buffers),
                "new_batches": new_batches,
                "seconds": round(time.time() - t0, 1)}
    except sqlite3.Error as e:
        return {"ok": False, "error": f"sqlite: {e}"}
    finally:
        src.close()


def main(argv=None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=None,
                        help="specific .dht file or directory of them")
    parser.add_argument("--reprocess", action="store_true",
                        help="ignore fingerprints and reprocess (idempotent)")
    args = parser.parse_args(argv)

    if args.archive:
        target = Path(args.archive)
        files = sorted(target.glob("*.dht")) if target.is_dir() else [target]
    else:
        files = discover_archives()
    if not files:
        print("no DHT archives found — canonical location is G:/backups/dht")
        return 0

    state = _fingerprint_state()
    tdb = sqlite3.connect(str(TDB), timeout=30.0)
    tdb.execute("PRAGMA busy_timeout=30000")
    tdb.execute("PRAGMA journal_mode=WAL")

    ok = True
    now = datetime.now(timezone.utc).isoformat()
    for f in files:
        st = f.stat()
        if not args.reprocess:
            row = state.execute(
                "SELECT 1 FROM dht_archive_state WHERE archive = ? "
                "AND size = ? AND mtime = ?",
                (str(f), st.st_size, st.st_mtime)).fetchone()
            if row:
                print(f"{f.name}: fingerprint match — skipped", flush=True)
                continue
        print(f"{f.name}: ingesting ({st.st_size / 1e9:.1f} GB)…", flush=True)
        out = ingest_archive(f, tdb)
        print(f"{f.name}: {json.dumps(out)}", flush=True)
        ok = ok and out.get("ok", False)
        if out.get("ok"):
            state.execute(
                """INSERT OR REPLACE INTO dht_archive_state
                   (archive, size, mtime, processed_at, messages_seen, new_batches)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(f), st.st_size, st.st_mtime, now,
                 out.get("messages_seen"), out.get("new_batches")))
            state.commit()

    state.close()
    tdb.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
