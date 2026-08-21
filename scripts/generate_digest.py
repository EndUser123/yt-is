"""Generate a daily digest — what your sources taught you today.

Produces a markdown summary of new content, notable topics, and highlights.
Can be sent to Discord, email, or saved as a file.

Usage:
    python scripts/generate_digest.py              # generate and print
    python scripts/generate_digest.py --save      # save to .data/yt-is/digests/
    python scripts/generate_digest.py --discord   # post to Discord webhook
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
DIGEST_DIR = Path("P:/.data/yt-is/digests")


def _ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    return conn


def get_new_transcripts(hours=24):
    """All new YouTube transcripts in the window, grouped per channel.

    Unbounded by design: every active channel appears with its exact count
    and its 2 latest titles (window function — no arbitrary sampling that
    would bias toward whichever channels happened to finish last). At
    ~28K transcripts/day the complete list belongs in the saved digest
    file; the console view renders the busiest channels.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _ro(DB)
    rows = conn.execute("""
        WITH ranked AS (
            SELECT a.video_id, a.title, a.updated_at, a.source,
                   cm.channel_title,
                   ROW_NUMBER() OVER (PARTITION BY a.source
                                      ORDER BY a.updated_at DESC) AS rn,
                   COUNT(*) OVER (PARTITION BY a.source) AS ch_total
            FROM analysis_status a
            LEFT JOIN channel_metadata cm ON cm.channel_id = a.channel_id
            WHERE a.status = 'complete' AND a.updated_at >= ?
              AND a.source NOT LIKE '%://reddit.com%'
              AND a.source NOT LIKE '%://news.ycombinator.com%'
              AND a.source NOT LIKE '%://discord.com%'
        )
        SELECT video_id, title, updated_at, source, channel_title, ch_total
        FROM ranked WHERE rn <= 2
        ORDER BY ch_total DESC, updated_at DESC
    """, (cutoff,)).fetchall()
    conn.close()

    channels: dict[str, dict] = {}
    for vid, title, ts, source, ch_title, ch_total in rows:
        ch = channels.setdefault(source, {
            "name": ch_title or source, "count": ch_total, "latest": []})
        if len(ch["latest"]) < 2:
            ch["latest"].append({
                "video_id": vid,
                "title": title or vid,
                "url": f"https://youtube.com/watch?v={vid}",
                "timestamp": ts,
            })
    ordered = sorted(channels.values(), key=lambda c: (-c["count"], c["name"]))
    return {"total": sum(c["count"] for c in ordered), "channels": ordered}


def get_new_reddit(hours=24):
    """New Reddit posts in the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    tdb = _ro(TDB)
    rows = tdb.execute("""
        SELECT video_id, metadata_json FROM transcript_cache
        WHERE source = 'reddit' AND cached_at >= ?
        ORDER BY cached_at DESC
    """, (cutoff,)).fetchall()
    tdb.close()

    posts = []
    for vid, meta in rows:
        try:
            m = json.loads(meta) if meta else {}
            posts.append({
                "id": vid,
                "title": m.get("title", vid),
                "subreddit": m.get("subreddit", ""),
                "score": m.get("score", 0),
                "url": m.get("permalink", f"https://reddit.com/comments/{vid[3:]}"),
            })
        except (json.JSONDecodeError, TypeError):
            continue
    return posts


def get_new_artifacts(hours=24):
    """New code artifacts extracted in the last N hours."""
    cutoff_epoch = datetime.now(timezone.utc).timestamp() - hours * 3600
    visual_root = Path("P:/.data/yt-is/visual")

    artifacts = []
    if not visual_root.is_dir():
        return artifacts

    conn = _ro(DB)
    for d in visual_root.iterdir():
        art = d / "artifacts.md"
        if not art.exists():
            continue
        try:
            if art.stat().st_mtime < cutoff_epoch:
                continue
            title_row = conn.execute(
                "SELECT title FROM analysis_status WHERE video_id = ?", (d.name,)
            ).fetchone()
            artifacts.append({
                "video_id": d.name,
                "title": title_row[0] if title_row else d.name,
                "path": str(art),
                "url": f"https://youtube.com/watch?v={d.name}",
                "size_kb": art.stat().st_size // 1024,
            })
        except OSError:
            continue
    conn.close()
    return artifacts


def get_stats():
    conn = _ro(DB)
    complete = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='complete'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='pending'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='failed'").fetchone()[0]
    channels = conn.execute("SELECT COUNT(*) FROM channel_metadata").fetchone()[0]
    conn.close()
    return {"complete": complete, "pending": pending, "failed": failed, "channels": channels}


CONSOLE_CHANNELS = 15     # console shows the busiest; --save writes ALL


def _render_channel_section(channels, total, limit=None):
    """limit=None renders every channel (saved file); otherwise the
    busiest `limit` channels with an honest remainder line."""
    lines = []
    shown = channels if limit is None else channels[:limit]
    for ch in shown:
        lines.append(f"### {ch['name']} — {ch['count']} new")
        for v in ch["latest"]:
            lines.append(f"- [{v['title']}]({v['url']})")
    if limit is not None and len(channels) > limit:
        rest = len(channels) - limit
        rest_total = sum(c["count"] for c in channels[limit:])
        lines.append(f"- … plus {rest} more channels "
                     f"({rest_total:,} transcripts) — all in the saved digest")
    return lines


def generate_digest(hours=24, full=False):
    """Generate the digest as markdown. full=True renders every active
    channel (used for the saved file); the console shows the busiest."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    transcripts = get_new_transcripts(hours)
    reddit = get_new_reddit(hours)
    artifacts = get_new_artifacts(hours)
    stats = get_stats()
    # Trend alerts (today's threshold-crossing topics). Imported here
    # rather than at module top so this module can still load on systems
    # where compute_trend_alerts has never been touched.
    alerts: list[dict] = []
    try:
        from scripts.compute_trend_alerts import get_today_alerts
        alerts = get_today_alerts()
    except Exception:
        alerts = []
    # URL-encode the click-through so spaces in topic labels don't break
    # the /query?q=... link in clients that don't auto-encode.
    from urllib.parse import quote_plus

    lines = []
    lines.append(f"# Daily Digest — {date_str}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    if transcripts["total"]:
        n_ch = len(transcripts["channels"])
        lines.append(f"- **{transcripts['total']:,}** new transcripts "
                     f"across **{n_ch}** channels")
    if reddit:
        lines.append(f"- **{len(reddit)}** new Reddit posts")
    if artifacts:
        lines.append(f"- **{len(artifacts)}** new code artifacts extracted")
    if alerts:
        lines.append(f"- **{len(alerts)}** trend alert(s) — see Alerts below")
    lines.append(f"- **{stats['complete']:,}** total transcripts in knowledge base")
    lines.append("")

    # Trend alerts — only when non-empty. Sits above New Videos so a
    # threshold-crossing topic is the first thing the operator sees.
    if alerts:
        lines.append("## Alerts")
        lines.append("")
        for a in alerts:
            pct_str = (f"+{a['pct']}%" if a["pct"] != 999 else "new arrival")
            q = quote_plus(a["topic"])
            lines.append(
                f"- **[{a['topic']}](/query?q={q})** "
                f"({a['window']}, {pct_str}, "
                f"{a['new_chunks']:,} chunks vs {a['prev_chunks']:,})"
            )
        lines.append("")

    # New content
    if transcripts["channels"]:
        lines.append("## New Videos Transcribed" +
                     ("" if full else " (busiest channels)"))
        lines.append("")
        limit = None if full else CONSOLE_CHANNELS
        lines.extend(_render_channel_section(
            transcripts["channels"], transcripts["total"], limit))
        lines.append("")

    # Reddit highlights
    if reddit:
        lines.append("## New Reddit Posts")
        lines.append("")
        shown = reddit if full else reddit[:10]
        for p in shown:
            lines.append(f"- [{p['title']}]( {p['url']} ) (r/{p['subreddit']}, {p['score']} pts)")
        if not full and len(reddit) > 10:
            lines.append(f"- … plus {len(reddit) - 10} more")
        lines.append("")

    # Code artifacts
    if artifacts:
        lines.append("## New Code Artifacts")
        lines.append("")
        shown = artifacts if full else artifacts[:10]
        for a in shown:
            art_url = Path(a["path"]).as_uri()
            lines.append(f"- **[{a['title']}]({a['url']})** ({a['size_kb']}KB)")
            lines.append(f"  - Extracted from video — view [artifacts.md]({art_url})")
        if not full and len(artifacts) > 10:
            lines.append(f"- … plus {len(artifacts) - 10} more")
        lines.append("")

    # Pipeline status
    lines.append("## System Status")
    lines.append("")
    lines.append(f"- Pipeline: {'✅ healthy' if stats['pending'] > 0 or stats['complete'] > 0 else '⚠️ check needed'}")
    lines.append(f"- Channels: {stats['channels']:,}")
    lines.append(f"- Pending: {stats['pending']:,} | Failed: {stats['failed']:,}")
    lines.append("")

    return "\n".join(lines)


def post_to_discord(webhook_url, content):
    """Post the digest to a Discord webhook."""
    # Discord has a 2000 char limit per message — split if needed
    chunks = []
    current = ""
    for line in content.split("\n"):
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        data = json.dumps({"content": chunk}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)


def main(argv=None):
    load_workspace_env()

    parser = argparse.ArgumentParser(description="Generate daily digest")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--discord", action="store_true")
    args = parser.parse_args(argv)

    digest = generate_digest(args.hours)            # compact console view
    print(digest)

    if args.save:
        DIGEST_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = DIGEST_DIR / f"digest-{date_str}.md"
        path.write_text(generate_digest(args.hours, full=True),
                        encoding="utf-8")
        print(f"\nSaved to: {path} (complete — all channels)")

    if args.discord:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not webhook:
            print("\n⚠️  No DISCORD_WEBHOOK_URL set in environment")
            print("   Add it to P:/.env to post digests to Discord")
            return 1
        post_to_discord(webhook, digest)
        print("\nPosted to Discord")

    return 0


if __name__ == "__main__":
    sys.exit(main())
