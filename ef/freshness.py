"""Delta freshness (A" section 17) + operational status surface (section 18).

Watermark model:
  authority_watermark  max(cached_at) observed in the transcript authority
  indexed_watermark    max(cached_at) fully processed into the active build
Incremental update processes inserts/updates (cached_at > indexed watermark)
and reconciles deletions (catalog EUs absent from authority). Idempotent
(content-hash aware), resumable, read-only on the authority, single-writer
on the projection. A Qdrant failure never touches the fetch pipeline.

Status is emitted to P:/.data/yt-is/ef/operational-status.json for the
external operational monitor (read-only consumer; not required for
Evidence Fabric correctness).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import authority, buildspec, catalog, chunking, embedding, server
from . import projection_server as ps

EF_DATA = Path("P:/.data/yt-is/ef")
STATE_PATH = EF_DATA / "state.json"
STATUS_PATH = EF_DATA / "operational-status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def authority_watermark() -> str:
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    try:
        return conn.execute(
            "select max(cached_at) from transcript_cache").fetchone()[0] or ""
    finally:
        conn.close()


def compute_lag(indexed_wm: str) -> dict:
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    try:
        n, oldest = conn.execute(
            "select count(*), min(cached_at) from transcript_cache "
            "where cached_at > ?", (indexed_wm,)).fetchone()
    finally:
        conn.close()
    age_s = None
    if oldest:
        try:
            dt = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        except ValueError:
            pass
    return {"index_lag_count": n or 0, "oldest_unindexed_at": oldest,
            "oldest_unindexed_age_s": age_s}


def emit_status(index_error: str | None = None) -> dict:
    """Durable operational status for the external monitor."""
    st = load_state()
    aw = authority_watermark()
    iw = st.get("indexed_watermark", "")
    lag = compute_lag(iw) if iw else {"index_lag_count": -1,
                                      "oldest_unindexed_at": None,
                                      "oldest_unindexed_age_s": None}
    qdrant_ok, points = False, None
    try:
        gen = buildspec.active_generation()
        if gen:
            points = ps.count(server.client(), gen)
            qdrant_ok = True
        else:
            qdrant_ok = server.status()["running"]
    except Exception:
        qdrant_ok = False
    status = {
        "emitted_at": _now(),
        "active_generation": buildspec.active_generation(),
        "build_id": st.get("build_id"),
        "build_state": st.get("build_state"),
        "authority_watermark": aw,
        "indexed_watermark": iw or None,
        **lag,
        "last_index_success": st.get("last_index_success"),
        "qdrant": {"reachable": qdrant_ok,
                   "url": server.URL,
                   "active_points": points},
        "last_promotion": (json.loads(
            (EF_DATA / "promotion.json").read_text(encoding="utf-8"))
            if (EF_DATA / "promotion.json").exists() else None),
        "last_indexing_error": index_error or st.get("last_indexing_error"),
    }
    STATUS_PATH.write_text(json.dumps(status, indent=1), encoding="utf-8")
    return status


def incremental_update(batch_limit: int = 2000) -> dict:
    """Process authority changes past the indexed watermark into the ACTIVE
    generation (or gen1 pre-promotion when explicitly the current build).
    Idempotent + resumable; source-revision aware via content_hash."""
    spec = buildspec.load_spec()
    gen = spec["generation"]
    digest = buildspec.spec_digest(spec)
    build_id = f"generation/gen{gen}-{digest}"
    st = load_state()
    iw = st.get("indexed_watermark")
    if not iw:
        raise RuntimeError("no indexed_watermark recorded; run bulk build "
                           "state bootstrap first (set to build snapshot "
                           "watermark)")
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{authority.STATUS_DB}?mode=ro' as status")
    rows = [dict(r) for r in conn.execute("""
        select t.video_id, t.lang, t.source, t.cached_at, t.transcript,
               a.title, a.channel_id, a.published_at, a.duration,
               cm.channel_title
        from transcript_cache t
        left join status.analysis_status a on a.video_id = t.video_id
        left join status.channel_metadata cm on cm.channel_id = a.channel_id
        where t.cached_at > ? and length(t.transcript) >= 100
          and t.terminal_id not like 'test%'
          and a.channel_id is not null
        order by t.cached_at asc limit ?
    """, (iw, batch_limit)).fetchall()]
    conn.close()
    rows = [r for r in rows if r["video_id"] not in authority.QUARANTINED_VIDEO_IDS]

    cat = catalog.connect()
    enc = embedding.BGEM3Dual()
    qc = server.client()
    fts = sqlite3.connect(str(EF_DATA / "fts5.sqlite"))
    added = updated = deleted = 0
    new_wm = iw
    try:
        for row in rows:
            eu = authority.build_eu(row)
            prior = cat.execute(
                "select content_hash from eu where eu_id=?", (eu.eu_id,)).fetchone()
            if prior and prior[0] == eu.content_hash:
                new_wm = row["cached_at"]
                continue
            if prior:
                # source revision: drop stale chunk points, rewrite
                old = [r[0] for r in cat.execute(
                    "select chunk_id from chunk where eu_id=?",
                    (eu.eu_id,)).fetchall()]
                if old:
                    qc.delete(ps.collection_name(gen),
                              points_selector=models_ids(old))
                    for cid in old:
                        fts.execute("delete from chunks where chunk_id=?", (cid,))
                    cat.execute("delete from chunk where eu_id=?", (eu.eu_id,))
                updated += 1
            else:
                added += 1
            chunks = chunking.chunk_transcript(eu.eu_id, row["transcript"])
            if chunks:
                dense, lex = enc.encode([c.text for c in chunks])
                meta = {"video_id": eu.video_id, "channel_id": eu.channel_id,
                        "channel_title": eu.channel_title, "title": eu.title,
                        "metadata_state": "incomplete" if not eu.title else "complete"}
                ps.upsert_chunks(qc, chunks, [d.tolist() for d in dense], lex,
                                 {eu.eu_id: meta}, gen)
                fts.executemany("insert or replace into chunks(text, chunk_id) "
                                "values (?, ?)",
                                [(c.text, c.chunk_id) for c in chunks])
            catalog.store_eus(cat, [eu], generation=gen, build_id=build_id)
            catalog.store_chunks(cat, chunks)
            fts.commit()
            new_wm = row["cached_at"]

        # deletion reconciliation: catalog EUs missing from authority
        gone = [r[0] for r in cat.execute(
            "select eu_id from eu where build_generation=?", (gen,)).fetchall()
            if _eu_missing_from_authority(r[0])]
        for eu_id in gone:
            old = [r[0] for r in cat.execute(
                "select chunk_id from chunk where eu_id=?", (eu_id,)).fetchall()]
            if old:
                qc.delete(ps.collection_name(gen),
                          points_selector=models_ids(old))
                for cid in old:
                    fts.execute("delete from chunks where chunk_id=?", (cid,))
            cat.execute("delete from chunk where eu_id=?", (eu_id,))
            cat.execute("delete from eu where eu_id=?", (eu_id,))
            deleted += 1
        cat.commit()
        st.update({"indexed_watermark": new_wm, "build_id": build_id,
                   "build_state": "incremental",
                   "last_index_success": _now(),
                   "last_indexing_error": None})
        save_state(st)
        out = {"processed": len(rows), "added": added, "updated": updated,
               "deleted": deleted, "indexed_watermark": new_wm}
        emit_status()
        return out
    except Exception as e:
        st["last_indexing_error"] = f"{type(e).__name__}: {e}"[:500]
        save_state(st)
        emit_status()
        raise
    finally:
        fts.close()
        cat.close()


def models_ids(chunk_ids: list[str]):
    from qdrant_client import models
    return models.PointsIdsList(points=[ps.point_id(c) for c in chunk_ids])


def _eu_missing_from_authority(eu_id: str) -> bool:
    vid = eu_id.split(":")[0]
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    try:
        return conn.execute(
            "select 1 from transcript_cache where video_id=? limit 1",
            (vid,)).fetchone() is None
    finally:
        conn.close()


def bootstrap_watermark_from_build() -> str:
    """Set indexed_watermark to the authority max at bulk-build completion
    (call once, after the bulk build finishes)."""
    aw = authority_watermark()
    st = load_state()
    st.setdefault("indexed_watermark", aw)
    st["authority_watermark_at_build"] = aw
    save_state(st)
    emit_status()
    return aw


if __name__ == "__main__":
    print(json.dumps(emit_status(), indent=1))
