#!/usr/bin/env python
"""Fix-up: rebuild catalog chunks missing from the gen1 collection.

The A-0 smoke cataloged 200 videos with build_generation=1 before the
server collection existed; ef_backfill_c.py resumed over them, leaving
their chunks absent from evidence_chunks__gen1 and the FTS5 lane. This
script finds catalog gen1 chunk_ids absent from the collection and
rebuilds exactly those (encode + upsert + FTS5 insert).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, catalog, chunking, embedding, server
from ef import projection_server as ps

GEN = 1
FTS_DB = Path("P:/.data/yt-is/ef/fts5.sqlite")


def main() -> int:
    conn = catalog.connect()
    # The missing set is analytic: eu rows cataloged BEFORE the backfill
    # ran (A-0 smoke wrote 200 at ~2026-08-17T00:4x UTC = 18:40 MDT on the
    # 16th; backfill wrote at 07:1x UTC). Cutoff sits between, UTC.
    stale_eus = conn.execute(
        "select eu_id, video_id, channel_id, channel_title, title from eu "
        "where build_generation = ? and built_at < '2026-08-17T02:00'",
        (GEN,)).fetchall()
    print(f"[fixup] stale (pre-backfill) gen1 eus: {len(stale_eus)}")
    if not stale_eus:
        conn.close()
        return 0
    eu_meta = {r[0]: {"video_id": r[1], "channel_id": r[2],
                      "channel_title": r[3], "title": r[4],
                      "metadata_state": "incomplete" if not r[4] else "complete"}
               for r in stale_eus}
    want = {r[1] for r in stale_eus}

    rows = [r for r in authority.list_eligible_transcripts(include_incomplete=True)
            if r["video_id"] in want]
    print(f"[fixup] re-reading {len(rows)} transcripts")
    enc = embedding.BGEM3Dual()
    fts = sqlite3.connect(str(FTS_DB))
    qc = server.client()
    built = 0
    for row in rows:
        eu = authority.build_eu(row)
        chunks = chunking.chunk_transcript(eu.eu_id, row["transcript"])
        if not chunks:
            continue
        dense, lex = enc.encode([c.text for c in chunks])
        built += ps.upsert_chunks(qc, chunks, [d.tolist() for d in dense], lex,
                                  eu_meta, GEN)
        fts.executemany("insert into chunks(text, chunk_id) values (?, ?)",
                        [(c.text, c.chunk_id) for c in chunks])
        fts.commit()
    fts.close()
    conn.close()
    print(f"[fixup] rebuilt {built} points; collection now "
          f"{ps.count(qc, GEN):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
