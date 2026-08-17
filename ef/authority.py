"""Authority layer: read-only access to the authoritative transcript store.

Verified topology (2026-08-16, D006 — do not trust older session summaries):

  P:/.data/yt-is/transcripts.sqlite   transcript_cache(cache_key PK, video_id,
                                     lang, source, transcript TEXT, metadata_json,
                                     cached_at, terminal_id)   75,706 rows
  P:/.data/yt-is/batch_status.sqlite  analysis_status(video_id PK, status, ...,
                                     title, channel_id, published_at)  346,644
                                     channel_metadata(channel_url PK, ...,
                                     channel_id, channel_title, ...)

Both DBs are live (WAL) while the fetch pipeline runs; all connections use
mode=ro so this module can never write or lock the authority.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .contracts import EvidenceUnit, MEDIA_TRANSCRIPT

TRANSCRIPTS_DB = Path("P:/.data/yt-is/transcripts.sqlite")
STATUS_DB = Path("P:/.data/yt-is/batch_status.sqlite")

# Test fixtures and degenerate rows discovered during discovery (receipt in
# DECISIONS.md D005/D006 session notes): 1 test row, 48 rows <100 chars.
MIN_TRANSCRIPT_CHARS = 100


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_eligible_transcripts(limit: int | None = None,
                              min_chars: int = MIN_TRANSCRIPT_CHARS,
                              exclude_terminal_prefix: str = "test") -> list[dict]:
    """Return newest-first eligible transcripts joined with video metadata.

    Eligibility: real content length, not a test fixture. The join must
    resolve channel/title for the row to be eligible — an EvidenceUnit
    without provenance is a contract violation, so unjoined rows are
    reported by the caller via audit counts, not silently dropped here.
    """
    # Cross-DB join: attach the status DB read-only to the transcripts DB.
    # Attached tables are addressed attachname.tablename. analysis_status's
    # video_id is its PK (indexed); channel_metadata.channel_id is NOT
    # indexed — acceptable at smoke scale, revisit for full-corpus builds.
    # Eligibility requires resolvable provenance (title+channel): '--' IDs
    # and other metadata-less rows are excluded and counted in the audit.
    q = """
    select t.video_id, t.lang, t.source, t.cached_at, t.transcript,
           a.title, a.channel_id, a.published_at, a.duration,
           cm.channel_title
    from transcript_cache t
    left join status.analysis_status a on a.video_id = t.video_id
    left join status.channel_metadata cm on cm.channel_id = a.channel_id
    where length(t.transcript) >= ?
      and t.terminal_id not like ?
      and a.channel_id is not null
      and a.title is not null
    order by t.video_id asc
    """
    args: list = [min_chars, f"{exclude_terminal_prefix}%"]
    if limit:
        q += " limit ?"
        args.append(limit)

    conn = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"attach database 'file:{STATUS_DB}?mode=ro' as status")
        rows = [dict(r) for r in conn.execute(q, args)]
    finally:
        conn.close()
    return rows


def build_eu(row: dict) -> EvidenceUnit:
    transcript: str = row["transcript"]
    return EvidenceUnit(
        eu_id=f"{row['video_id']}:{MEDIA_TRANSCRIPT}",
        media_kind=MEDIA_TRANSCRIPT,
        video_id=row["video_id"],
        channel_id=row["channel_id"] or "",
        channel_title=(row["channel_title"] or "").strip(),
        title=(row["title"] or "").strip(),
        lang=row["lang"] or "en",
        source=row["source"] or "unknown",
        authority_ref=f"{row['video_id']}:{row['lang']}:{row['source']}",
        content_hash=hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        captured_at=row["cached_at"] or "",
        published_at=(row["published_at"] or "")[:10],
        duration_s=int(row["duration"] or 0),
        char_length=len(transcript),
    ).validate()


def reopen_span(video_id: str, start_char: int, end_char: int,
                context: int = 0, transcripts_db: Path = TRANSCRIPTS_DB) -> str:
    """Reopen the authority transcript and slice the provenance span.

    The 'reopen' half of the A-0 round-trip requirement: the result must be
    derived from the authority DB, never from projection-side copies.
    """
    conn = _ro(transcripts_db)
    try:
        row = conn.execute(
            "select transcript from transcript_cache where video_id = ? "
            "order by length(transcript) desc limit 1", (video_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise LookupError(f"no authority transcript for {video_id}")
    text = row["transcript"]
    lo = max(0, start_char - context)
    hi = min(len(text), end_char + context)
    return text[lo:hi]


def authority_stats() -> dict:
    """Cardinality + health snapshot of the authority layer (discovery aid)."""
    tc = _ro(TRANSCRIPTS_DB)
    try:
        stats = {
            "transcript_rows": tc.execute(
                "select count(*) from transcript_cache").fetchone()[0],
            "distinct_videos": tc.execute(
                "select count(distinct video_id) from transcript_cache").fetchone()[0],
            "by_source": dict(tc.execute(
                "select source, count(*) from transcript_cache group by source"
            ).fetchall()),
            "min_max_avg_chars": tc.execute(
                "select min(length(transcript)), cast(avg(length(transcript)) as int), "
                "max(length(transcript)) from transcript_cache").fetchone(),
        }
    finally:
        tc.close()

    bs = _ro(STATUS_DB)
    try:
        stats["analysis_by_status"] = dict(bs.execute(
            "select status, count(*) from analysis_status group by status").fetchall())
        stats["channels"] = bs.execute(
            "select count(*) from channel_metadata").fetchone()[0]
    finally:
        bs.close()

    # Provenance-gap audit: transcripts lacking title/channel (excluded
    # from EU building by contract; Phase A needs their cardinality).
    conn = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
    try:
        conn.execute(f"attach database 'file:{STATUS_DB}?mode=ro' as status")
        stats["provenance_gaps"] = conn.execute(
            """select count(*) from transcript_cache t
               left join status.analysis_status a on a.video_id = t.video_id
               where a.channel_id is null or a.title is null""").fetchone()[0]
    finally:
        conn.close()
    return stats
