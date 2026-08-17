#!/usr/bin/env python
"""Phase C full-corpus backfill (C-gate decision 4).

Builds generation 1 over the ENTIRE eligible corpus (including the 7,109
incomplete-metadata Case-A rows; test fixture quarantined):
  - catalog rows (eu + chunks) in P:/.data/yt-is/ef/catalog.sqlite
  - Qdrant server collection evidence_chunks__gen1 (dense + learned sparse)
  - FTS5 exact-lane index P:/.data/yt-is/ef/fts5.sqlite
Resumable: videos already present in catalog for this generation are
skipped. Runs entirely read-only against the authority DBs (off the fetch
critical path). Receipt -> docs/evidence-fabric/c_backfill_receipt.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, buildspec, catalog, chunking, embedding, server
from ef import projection_server as ps

FTS_DB = Path("P:/.data/yt-is/ef/fts5.sqlite")
GEN = 1
VIDEO_BATCH = 200          # transcripts per embed batch-group


def production_build_id(spec) -> str:
    return f"generation/gen{spec['generation']}-{buildspec.spec_digest(spec)}"


def main() -> int:
    spec = buildspec.GEN1
    assert spec["generation"] == GEN
    buildspec.write_spec(spec)
    print(f"[backfill] buildspec gen{GEN} digest={buildspec.spec_digest(spec)}")

    receipt = {"started_at": datetime.now(timezone.utc).isoformat(),
               "generation": GEN, "spec_digest": buildspec.spec_digest(spec),
               "counts": {}, "timings_s": {}}

    t0 = time.monotonic()
    rows = authority.list_eligible_transcripts(include_incomplete=True)
    receipt["counts"]["eligible_transcripts"] = len(rows)
    print(f"[backfill] eligible transcripts: {len(rows):,} "
          f"(includes incomplete-metadata Case A)")

    conn = catalog.connect()
    build_id = production_build_id(spec)
    catalog.claim_production_generation(conn, GEN, build_id,
                                        buildspec.spec_digest(spec))
    done = {r[0] for r in conn.execute(
        "select distinct video_id from eu where build_generation=? "
        "and media_kind='transcript'", (GEN,)).fetchall()}
    todo = [r for r in rows if r["video_id"] not in done]
    receipt["counts"]["already_done"] = len(done)
    receipt["counts"]["to_build"] = len(todo)
    print(f"[backfill] resume: {len(done):,} done, {len(todo):,} to build")
    if not todo:
        conn.close()
        return 0

    qc = server.client()
    ps.ensure_collection(GEN, dense_dim=spec["encoder"]["dense_dim"],
                         hnsw_m=spec["projection"]["hnsw_m"])

    # FTS5 index (append-mode: chunk_id -> text)
    fts = sqlite3.connect(str(FTS_DB))
    fts.execute("create virtual table if not exists chunks using "
                "fts5(text, chunk_id UNINDEXED)")
    fts.commit()

    enc = embedding.BGEM3Dual()
    n_eu = n_chunk = n_pts = 0
    incomplete = 0
    checkpoint = time.monotonic()
    errors = []

    for gi in range(0, len(todo), VIDEO_BATCH):
        group = todo[gi:gi + VIDEO_BATCH]
        eus, chunks = [], []
        for row in group:
            try:
                eu = authority.build_eu(row)
            except Exception as e:
                errors.append({"video_id": row.get("video_id"),
                               "stage": "eu", "error": str(e)[:120]})
                continue
            if not eu.title:
                incomplete += 1
            eus.append(eu)
            chunks.extend(chunking.chunk_transcript(eu.eu_id, row["transcript"]))
        if not chunks:
            continue

        try:
            dense, lex = enc.encode([c.text for c in chunks])
        except Exception as e:
            # OOM guard: halve and retry once per half
            try:
                mid = len(chunks) // 2
                d1, l1 = enc.encode([c.text for c in chunks[:mid]])
                d2, l2 = enc.encode([c.text for c in chunks[mid:]])
                import numpy as np
                dense = np.vstack([d1, d2])
                lex = l1 + l2
            except Exception as e2:
                errors.append({"group": gi, "stage": "encode",
                               "error": str(e2)[:120]})
                continue

        eu_meta = {eu.eu_id: {
            "video_id": eu.video_id, "channel_id": eu.channel_id,
            "channel_title": eu.channel_title, "title": eu.title,
            "metadata_state": "incomplete" if not eu.title else "complete"}
            for eu in eus}
        n_pts += ps.upsert_chunks(qc, chunks,
                                  [d.tolist() for d in dense], lex,
                                  eu_meta, GEN)
        catalog.store_eus(conn, eus, generation=GEN, build_id=build_id)
        catalog.store_chunks(conn, chunks)
        fts.executemany("insert into chunks(text, chunk_id) values (?, ?)",
                        [(c.text, c.chunk_id) for c in chunks])
        fts.commit()
        n_eu += len(eus)
        n_chunk += len(chunks)

        if gi % (VIDEO_BATCH * 10) == 0:
            el = time.monotonic() - checkpoint
            checkpoint = time.monotonic()
            print(f"[backfill] {gi + len(group):,}/{len(todo):,} videos "
                  f"({n_chunk:,} chunks, {n_pts:,} pts) "
                  f"[{el:.0f}s/2000]", flush=True)

    receipt["counts"].update({"eu_built": n_eu, "chunks_built": n_chunk,
                              "points_upserted": n_pts,
                              "incomplete_metadata_eus": incomplete,
                              "errors": len(errors)})
    receipt["timings_s"]["total"] = round(time.monotonic() - t0, 1)
    receipt["errors_sample"] = errors[:10]
    receipt["final_counts"] = {
        "collection_points": ps.count(qc, GEN),
        "catalog": catalog.counts(conn),
    }
    conn.close()
    fts.close()

    out = REPO / "docs" / "evidence-fabric" / "c_backfill_receipt.json"
    out.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    print(f"[backfill] DONE {json.dumps(receipt['counts'])}")
    print(f"[backfill] receipt -> {out}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
