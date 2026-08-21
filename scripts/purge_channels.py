"""Purge channels from the yt-is knowledge base — explicit, receipted,
destructive.

Purge is NEVER tied to the review-page block toggle. It runs only when
specifically invoked, always produces a dry-run receipt first, and
requires --confirm to execute. Optionally blacklists purged channels so
future scans cannot re-add them.

A purge removes a channel's footprint from every store:
  analysis_status (video rows) · transcript_cache (transcripts)
  EF catalog (eu + chunk + chunk_clusters) · Qdrant (vectors)
  FTS5 index (text rows) · visual artifacts (frame/OCR dirs)
  channel_metadata (the channel itself) · + blocklist row if requested

Usage:
    python scripts/purge_channels.py --missing-description            # dry run
    python scripts/purge_channels.py --missing-description --confirm  # execute
    python scripts/purge_channels.py --urls-file channels.txt         # explicit list
    python scripts/purge_channels.py --missing-description --confirm --and-blacklist
"""

from __future__ import annotations

import argparse
import json
import shutil
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
CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
FTS = Path("P:/.data/yt-is/ef/fts5.sqlite")
VISUAL_ROOT = Path("P:/.data/yt-is/visual")
RECEIPT_DIR = Path("P:/packages/yt-is/.logs/purge")
QDRANT_BATCH = 500


def _rw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _count_in(conn, table_where: str, ids: list[str]) -> int:
    """COUNT(*) with an IN clause, batched for the SQLite variable limit."""
    total = 0
    for i in range(0, len(ids), 900):
        batch = ids[i:i + 900]
        ph = ",".join("?" for _ in batch)
        total += conn.execute(
            f"SELECT COUNT(*) FROM {table_where.format(ph=ph)}",
            batch).fetchone()[0]
    return total


def select_channels(args) -> list[dict]:
    """Resolve the purge set from criteria or an explicit URL/ID file."""
    conn = _rw(DB)
    if args.urls_file:
        wanted = [l.strip() for l in
                  Path(args.urls_file).read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
        ph = ",".join("?" for _ in wanted)
        rows = conn.execute(f"""
            SELECT channel_url, channel_id, channel_title FROM channel_metadata
            WHERE channel_url IN ({ph}) OR channel_id IN ({ph})
        """, [*wanted, *wanted]).fetchall()
    else:
        cond = []
        if args.missing_description:
            cond.append("(description IS NULL OR description = '')")
        if args.missing_thumbnail:
            cond.append("(thumbnail_url IS NULL OR thumbnail_url = '')")
        if args.dead:
            cond.append("(channel_status IS NOT NULL AND channel_status != '')")
        if args.no_completion:
            cond.append("""channel_id NOT IN (
                SELECT DISTINCT channel_id FROM analysis_status
                WHERE status = 'complete' AND channel_id IS NOT NULL)""")
        if args.stale_days is not None:
            cond.append(f"""channel_id IN (
                SELECT channel_id FROM analysis_status
                WHERE channel_id IS NOT NULL
                GROUP BY channel_id
                HAVING MAX(COALESCE(published_at, '')) <
                datetime('now', '-{int(args.stale_days)} days'))""")
        where = " AND ".join(cond) if args.require_all else " OR ".join(cond)
        rows = conn.execute(f"""
            SELECT channel_url, channel_id, channel_title FROM channel_metadata
            WHERE {where}
        """).fetchall()
    conn.close()
    return [{"url": r[0], "id": r[1], "title": r[2] or r[0]} for r in rows]


def build_plan(channels: list[dict]) -> dict:
    """Everything that WOULD be deleted, counted and sampled."""
    conn = _rw(DB)
    ids = [c["id"] for c in channels if c["id"]]
    ph = ",".join("?" for _ in ids)
    plan = {"channels": channels, "videos": 0, "transcripts": 0,
            "chunks": 0, "visual_dirs": 0, "per_channel": []}
    if not ids:
        conn.close()
        return plan

    video_counts = dict(conn.execute(f"""
        SELECT channel_id, COUNT(*) FROM analysis_status
        WHERE channel_id IN ({ph}) GROUP BY channel_id
    """, ids).fetchall())
    complete_counts = dict(conn.execute(f"""
        SELECT channel_id, COUNT(*) FROM analysis_status
        WHERE channel_id IN ({ph}) AND status = 'complete' GROUP BY channel_id
    """, ids).fetchall())
    video_ids = [r[0] for r in conn.execute(f"""
        SELECT video_id FROM analysis_status WHERE channel_id IN ({ph})
    """, ids).fetchall()]
    conn.close()

    if video_ids:
        tdb = _rw(TDB)
        plan["transcripts"] = _count_in(
            tdb, "transcript_cache WHERE video_id IN ({ph})", video_ids)
        tdb.close()

        cat = _rw(CATALOG)
        plan["chunks"] = _count_in(
            cat,
            "chunk WHERE eu_id IN "
            "(SELECT eu_id FROM eu WHERE video_id IN ({ph}))",
            video_ids)
        cat.close()

    for c in channels:
        n = video_counts.get(c["id"], 0)
        done = complete_counts.get(c["id"], 0)
        plan["videos"] += n
        plan["per_channel"].append(
            {"url": c["url"], "title": c["title"], "videos": n,
             "complete": done})
    for vid in video_ids:
        if (VISUAL_ROOT / vid).is_dir():
            plan["visual_dirs"] += 1
    plan["video_ids"] = video_ids
    return plan


def execute_purge(plan: dict, blacklist: bool) -> dict:
    """Run the deletions across all stores. Receipted, batched, best-effort
    ordering so a failure mid-way leaves a smaller corpus, not a bigger one."""
    import hashlib

    from qdrant_client import models

    from ef import server
    from ef import projection_server as ps
    from ef import buildspec

    video_ids = plan["video_ids"]
    channel_ids = [c["id"] for c in plan["channels"] if c["id"]]
    channel_urls = [c["url"] for c in plan["channels"]]
    done: dict[str, int] = {}

    conn = _rw(DB)
    ph = ",".join("?" for _ in channel_ids)
    done["analysis_status"] = conn.execute(
        f"DELETE FROM analysis_status WHERE channel_id IN ({ph})",
        channel_ids).rowcount
    done["channel_metadata"] = conn.execute(
        f"DELETE FROM channel_metadata WHERE channel_id IN ({ph})",
        channel_ids).rowcount
    if blacklist:
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO channel_blocklist (channel_url, reason) "
            "VALUES (?, ?)",
            [(u, f"purged:{now}") for u in channel_urls])
        done["blocklist"] = len(channel_urls)
    conn.commit()
    conn.close()

    if video_ids:
        def batched(conn, sql_fmt: str, ids: list[str]) -> int:
            n = 0
            for i in range(0, len(ids), 900):
                batch = ids[i:i + 900]
                ph = ",".join("?" for _ in batch)
                n += conn.execute(sql_fmt.format(ph=ph), batch).rowcount
            return n

        tdb = _rw(TDB)
        done["transcript_cache"] = batched(
            tdb, "DELETE FROM transcript_cache WHERE video_id IN ({ph})",
            video_ids)
        tdb.commit()
        tdb.close()

        cat = _rw(CATALOG)
        chunk_ids = []
        for i in range(0, len(video_ids), 900):
            batch = video_ids[i:i + 900]
            ph = ",".join("?" for _ in batch)
            chunk_ids.extend(r[0] for r in cat.execute(
                f"SELECT c.chunk_id FROM chunk c "
                f"JOIN eu ON eu.eu_id = c.eu_id "
                f"WHERE eu.video_id IN ({ph})", batch).fetchall())
        done["chunks"] = batched(
            cat, "DELETE FROM chunk WHERE chunk_id IN ({ph})", chunk_ids)
        batched(cat, "DELETE FROM eu WHERE video_id IN ({ph})", video_ids)
        done["chunk_clusters"] = batched(
            cat, "DELETE FROM chunk_clusters WHERE video_id IN ({ph})",
            video_ids)
        cat.commit()
        cat.close()

        if chunk_ids:
            fts = _rw(FTS)
            for i in range(0, len(chunk_ids), 1000):
                batch = chunk_ids[i:i + 1000]
                fts.execute(
                    f"DELETE FROM chunks WHERE chunk_id IN "
                    f"({','.join('?' for _ in batch)})", batch)
            fts.commit()
            fts.close()

            def pid(chunk_id: str) -> int:
                return int.from_bytes(
                    hashlib.md5(chunk_id.encode("utf-8")).digest()[:8], "big")
            qc = server.client()
            collection = ps.collection_name(
                buildspec.load_spec()["generation"])
            for i in range(0, len(chunk_ids), QDRANT_BATCH):
                batch = [pid(c) for c in chunk_ids[i:i + QDRANT_BATCH]]
                qc.delete(collection_name=collection,
                          points_selector=models.PointIdsList(points=batch))
                time.sleep(0.2)
            done["qdrant_points"] = len(chunk_ids)

        removed_dirs = 0
        for vid in video_ids:
            d = VISUAL_ROOT / vid
            if d.is_dir():
                shutil.rmtree(d)
                removed_dirs += 1
        done["visual_dirs"] = removed_dirs

    return done


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser(
        description="Purge channels (explicit, destructive, receipted)")
    parser.add_argument("--urls-file",
                        help="text file: one channel URL or ID per line")
    parser.add_argument("--missing-description", action="store_true",
                        help="channels with no description stored")
    parser.add_argument("--missing-thumbnail", action="store_true",
                        help="channels with no thumbnail stored")
    parser.add_argument("--dead", action="store_true",
                        help="channels flagged dead by the scan "
                             "(channel_status set)")
    parser.add_argument("--no-completion", action="store_true",
                        help="channels with zero completed transcripts")
    parser.add_argument("--stale-days", type=int, default=None,
                        help="channels whose newest video is older than "
                             "N days")
    parser.add_argument("--require-all", action="store_true",
                        help="criteria must ALL match (default: any)")
    parser.add_argument("--confirm", action="store_true",
                        help="execute (default: dry-run receipt only)")
    parser.add_argument("--and-blacklist", action="store_true",
                        help="after purging, blocklist the channels so "
                             "future scans never re-add them")
    args = parser.parse_args(argv)

    if not (args.urls_file or args.missing_description or args.missing_thumbnail
            or args.dead or args.no_completion or args.stale_days is not None):
        parser.error("select channels with --urls-file, criteria flags "
                     "(--missing-description/--missing-thumbnail/--dead/"
                     "--no-completion/--stale-days), or combinations")

    channels = select_channels(args)
    if not channels:
        print("no channels match the selection")
        return 0

    plan = build_plan(channels)

    print(f"purge plan ({'EXECUTE' if args.confirm else 'DRY RUN'}):")
    print(f"  channels:       {len(plan['channels'])}")
    complete_total = sum(c["complete"] for c in plan["per_channel"])
    print(f"  videos:         {plan['videos']:,} "
          f"({complete_total:,} with completed transcripts)")
    print(f"  transcripts:    {plan['transcripts']:,}")
    print(f"  search chunks:  {plan['chunks']:,}")
    print(f"  visual dirs:    {plan['visual_dirs']}")
    biggest = sorted(plan["per_channel"], key=lambda c: -c["videos"])[:5]
    print("  largest channels by video count:")
    for c in biggest:
        print(f"    {c['videos']:>5} videos ({c['complete']} complete) — "
              f"{c['title'][:60]}")

    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_path = RECEIPT_DIR / f"purge-{stamp}.json"
    receipt_path.write_text(json.dumps(
        {"mode": "execute" if args.confirm else "dry-run",
         "blacklist": args.and_blacklist,
         "plan": {k: v for k, v in plan.items() if k != "video_ids"},
         "video_ids_count": len(plan.get("video_ids", []))},
        indent=1), encoding="utf-8")
    print(f"  receipt: {receipt_path}")

    if not args.confirm:
        print("\nDRY RUN — re-run with --confirm to execute")
        return 0

    done = execute_purge(plan, blacklist=args.and_blacklist)
    receipt_path.write_text(receipt_path.read_text(encoding="utf-8")
                            .rstrip()[:-1]
                            + ",\n \"deleted\": " + json.dumps(done) + "}\n",
                            encoding="utf-8")
    print("deleted:", done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
