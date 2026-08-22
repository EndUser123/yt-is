"""Ingest connector-source batches (reddit/hn/discord) into the Evidence Fabric.

The authority build and the freshness delta loop both join
status.analysis_status (YouTube-specific) with `a.channel_id is not null`,
so connector batches stored in transcript_cache never enter the index —
they are invisible to ef-query and the warm query service. This module is
their path in.

Per batch (one transcript_cache row): build an EU (media_kind=transcript,
source=reddit/hn/discord distinguishes the connector) → chunk → BGE-M3
encode → Qdrant upsert + FTS5 row + catalog eu/chunk rows — the same
per-row sequence as freshness.incremental_update().

Idempotent: an EU whose content_hash is unchanged is skipped and only
advances the watermark. The watermark lives at
state.json:connector_indexed_watermark (separate from the YouTube
indexed_watermark, which the freshness loop owns).

Usage:
    python -m ef.ingest_connectors                    # all sources, incremental
    python -m ef.ingest_connectors --source reddit
    python -m ef.ingest_connectors --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from . import authority, buildspec, catalog, chunking, server
from . import projection_server as ps
from .contracts import EvidenceUnit, MEDIA_TRANSCRIPT
from .freshness import EF_DATA, load_state, save_state

TRANSCRIPTS_DB = authority.TRANSCRIPTS_DB
FTS_DB = EF_DATA / "fts5.sqlite"
# CLI name -> source value stored in transcript_cache by the sync scripts
SOURCE_ALIASES = {"reddit": "reddit", "hn": "hackernews",
                  "discord": "discord", "rss": "rss", "github": "github",
                  "dht-artifact": "dht-artifact"}
SOURCES = tuple(SOURCE_ALIASES)  # CLI-facing names
WATERMARK_KEY = "connector_indexed_watermark"
MIN_CHARS = 100  # same floor as the freshness loop


def list_batches(sources: tuple[str, ...], since: str,
                 limit: int | None = None) -> list[dict]:
    """Connector batches newer than the watermark, oldest first."""
    db_sources = tuple(SOURCE_ALIASES[s] for s in sources)
    if not db_sources:
        return []
    ph = ",".join("?" for _ in db_sources)
    conn = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        q = f"""
            select cache_key, video_id, source, cached_at, transcript,
                   metadata_json, lang
            from transcript_cache
            where source in ({ph}) and cached_at > ?
              and length(transcript) >= ?
            order by cached_at asc
        """
        args = [*db_sources, since, MIN_CHARS]
        if limit:
            q += " limit ?"
            args.append(limit)
        return [dict(r) for r in conn.execute(q, args)]
    finally:
        conn.close()


def _date_only(v) -> str:
    """Metadata timestamps are mixed epoch-float / ISO / RFC822 (RSS)."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
    s = str(v).strip()
    from datetime import datetime, timezone
    for parser in (
            datetime.fromisoformat,
            lambda x: datetime.strptime(x, "%a, %d %b %Y %H:%M:%S %z"),
            lambda x: datetime.strptime(x, "%a, %d %b %Y %H:%M:%S %Z"),
    ):
        try:
            dt = parser(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            continue
    return s[:10]


def build_connector_eu(row: dict) -> EvidenceUnit:
    """EU for a connector batch; authority_ref MUST be the cache_key so
    query_server._reopen can slice the transcript for snippets."""
    try:
        meta = json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        meta = {}

    src = row["source"]
    if src == "reddit":
        channel_id = f"r/{meta.get('subreddit', 'unknown')}"
        channel_title = channel_id
        title = (meta.get("title") or "")[:300]
        published = _date_only(meta.get("created_utc"))
    elif src == "hackernews":
        channel_id = "hn"
        channel_title = "Hacker News"
        title = (meta.get("title") or "")[:300]
        published = _date_only(meta.get("created_at"))
    elif src == "discord":
        channel_id = meta.get("channel_id", "")
        channel_title = meta.get("guild_name") or "Discord"
        title = f"#{meta.get('channel_name', 'channel')} ({channel_title})"
        published = ""
    elif src == "podcast":
        channel_id = f"podcast:{meta.get('feed', 'unknown')}"[:80]
        channel_title = meta.get("feed", "Podcast")
        title = (meta.get("title") or "")[:300]
        published = _date_only(meta.get("published"))
    elif src == "github":
        channel_id = f"github:{meta.get('repo', 'unknown')}"[:80]
        channel_title = meta.get("repo", "GitHub")
        title = (meta.get("title") or "")[:300]
        published = _date_only(meta.get("published"))
    elif src == "rss":
        channel_id = f"rss:{meta.get('feed', 'unknown')}"[:80]
        channel_title = meta.get("feed", "RSS")
        title = (meta.get("title") or "")[:300]
        published = _date_only(meta.get("published"))
    elif src == "dht-artifact":
        # Two-layer markdown artifact from a Discord attachment (handoff
        # 2026-08-21: scripts/extract_dht_artifacts.py). The metadata is set
        # by the extractor's upsert_transcript_cache_row: archive, message_id,
        # attachment_id, name, url, size_bytes, content_hash, source_kind.
        archive = (meta.get("archive") or "dht-artifact").replace("_", " ")
        name = meta.get("name") or ""
        att_id = meta.get("attachment_id")
        channel_id = f"dht-artifact:{meta.get('archive', 'unknown')}"[:80]
        channel_title = f"Discord archive ({archive})"
        # Title: "<archive> :: <attachment name>"; the OCR + vision layers
        # follow as the transcript body for snippet / search.
        title = f"{archive} :: {name}"[:300] if name else archive[:300]
        published = ""
    else:
        channel_id = src
        channel_title = src
        title = ""
        published = ""

    transcript = row["transcript"]
    return EvidenceUnit(
        eu_id=f"{row['video_id']}:{MEDIA_TRANSCRIPT}",
        media_kind=MEDIA_TRANSCRIPT,
        video_id=row["video_id"],
        channel_id=channel_id,
        channel_title=channel_title,
        title=title,
        lang=row.get("lang") or "en",
        source=src,
        authority_ref=row["cache_key"],
        content_hash=hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        captured_at=row["cached_at"] or "",
        published_at=published,
        duration_s=0,
        char_length=len(transcript),
    ).validate()


def ingest(sources: tuple[str, ...] = SOURCES, limit: int | None = None,
           dry_run: bool = False) -> dict:
    st = load_state()
    since = st.get(WATERMARK_KEY, "")
    rows = list_batches(sources, since, limit)

    counts = {"candidates": len(rows), "added": 0, "skipped": 0,
              "chunks": 0, "by_source": {}}
    if dry_run:
        for r in rows:
            counts["by_source"][r["source"]] = \
                counts["by_source"].get(r["source"], 0) + 1
        return counts

    if not rows:
        return counts

    spec = buildspec.load_spec()
    gen = spec["generation"]
    build_id = f"generation/gen{gen}-{buildspec.spec_digest(spec)}"

    cat = catalog.connect()
    qc = server.client()
    fts = sqlite3.connect(str(FTS_DB), timeout=30.0)
    fts.execute("PRAGMA busy_timeout=30000")
    enc = None  # lazy: load BGE-M3 only when there is work for it
    new_wm = since
    try:
        for row in rows:
            eu = build_connector_eu(row)
            prior = cat.execute(
                "select content_hash from eu where eu_id=?",
                (eu.eu_id,)).fetchone()
            if prior and prior[0] == eu.content_hash:
                counts["skipped"] += 1
                new_wm = row["cached_at"]
                continue

            if enc is None:
                from . import embedding
                enc = embedding.BGEM3Dual()

            chunks = chunking.chunk_transcript(eu.eu_id, row["transcript"])
            if chunks:
                dense, lex = enc.encode([c.text for c in chunks])
                meta = {"video_id": eu.video_id, "channel_id": eu.channel_id,
                        "channel_title": eu.channel_title, "title": eu.title,
                        "metadata_state": "complete" if eu.title else "incomplete"}
                ps.upsert_chunks(qc, chunks, [d.tolist() for d in dense],
                                 lex, {eu.eu_id: meta}, gen)
                fts.executemany(
                    "insert or replace into chunks(text, chunk_id) values (?, ?)",
                    [(c.text, c.chunk_id) for c in chunks])
                fts.commit()
            catalog.store_eus(cat, [eu], generation=gen, build_id=build_id)
            catalog.store_chunks(cat, chunks)

            counts["added"] += 1
            counts["chunks"] += len(chunks)
            counts["by_source"][row["source"]] = \
                counts["by_source"].get(row["source"], 0) + 1
            new_wm = row["cached_at"]
    finally:
        fts.close()
        cat.close()

    st = load_state()  # re-read: freshness loop may have advanced its own keys
    st[WATERMARK_KEY] = new_wm
    save_state(st)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ingest connector batches (reddit/hn/discord) into EF")
    parser.add_argument("--source", default=",".join(SOURCES),
                        help="comma-separated subset of: reddit,hn,discord")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sources = tuple(s.strip() for s in args.source.split(",") if s.strip())
    bad = [s for s in sources if s not in SOURCES]
    if bad:
        parser.error(f"unknown source(s) {bad}; valid: {SOURCES}")

    counts = ingest(sources, limit=args.limit, dry_run=args.dry_run)
    if args.dry_run:
        print(f"candidates past watermark: {counts['candidates']}")
        for src, n in sorted(counts["by_source"].items()):
            print(f"  {src}: {n} batches")
        return 0

    print(f"connector ingest: {counts['added']} batches "
          f"({counts['chunks']} chunks) added, {counts['skipped']} unchanged")
    for src, n in sorted(counts["by_source"].items()):
        print(f"  {src}: {n} batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
