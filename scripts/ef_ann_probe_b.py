"""Phase B: embedded-ANN + FTS5 latency probe at full corpus scale.

Rule 5 failed for Qdrant local mode (p95 9.7s @ 154,719 pts, brute force).
This probe measures the two Windows-native embedded alternatives at the
SAME scale so the C-redesign decision is evidence-based:
  1. faiss-cpu HNSW over MiniLM dense vectors (ANN)
  2. sqlite FTS5 BM25 over chunk texts (lexical)
  3. RRF fusion of both in Python (the hybrid path)
Receipt -> docs/evidence-fabric/benchmark/ann_fts5_probe.json
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import faiss  # noqa: E402
import numpy as np  # noqa: E402

from ef import authority, chunking, embedding  # noqa: E402

PROBE_QUERIES = [
    "semiconductor supply chain concentration risk",
    "how to backtest a trading strategy without overfitting",
    "why neural networks generalize despite overparameterization",
    "the history of the Roman Republic's decline",
    "cooking technique for perfect steak searing",
    "journaling habits that reduce anxiety",
    "solar panel payback period calculation",
    "how submarines navigate underwater",
]
DB = Path("P:/tmp/ef_ann_probe.sqlite")


def main() -> int:
    receipt = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "model": "all-MiniLM-L6-v2", "timings_s": {}, "latency": {}}

    t0 = time.monotonic()
    rows = authority.list_eligible_transcripts()
    receipt["n_transcripts"] = len(rows)
    texts, video_ids = [], []
    for row in rows:
        eu = authority.build_eu(row)
        for ch in chunking.chunk_transcript(eu.eu_id, row["transcript"]):
            texts.append(ch.text)
            video_ids.append(row["video_id"])
    receipt["n_chunks"] = len(texts)
    receipt["timings_s"]["read_chunk"] = time.monotonic() - t0
    print(f"[probe] {len(texts):,} chunks")

    dense = embedding.DenseEmbedder(batch_size=128)
    t0 = time.monotonic()
    vecs = np.asarray(dense.encode(texts), dtype="float32")
    receipt["timings_s"]["dense_embed"] = time.monotonic() - t0

    # 1. faiss HNSW
    t0 = time.monotonic()
    d = vecs.shape[1]
    index = faiss.IndexHNSWFlat(d, 32, faiss.METRIC_INNER_PRODUCT)
    index.add(vecs)
    receipt["timings_s"]["faiss_build"] = time.monotonic() - t0

    # 2. FTS5 BM25
    t0 = time.monotonic()
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(str(DB))
    conn.execute("create virtual table chunks using fts5(text)")
    conn.executemany("insert into chunks(rowid, text) values (?, ?)",
                     list(enumerate(texts)))
    conn.commit()
    receipt["timings_s"]["fts5_build"] = time.monotonic() - t0
    print(f"[probe] faiss build {receipt['timings_s']['faiss_build']:.0f}s, "
          f"fts5 build {receipt['timings_s']['fts5_build']:.0f}s")

    # 3. hybrid latency probes (dense ANN + FTS5, RRF in Python)
    ann_lat, fts_lat, hyb_lat = [], [], []
    for warm in (True, False):
        for q in PROBE_QUERIES:
            qv = np.asarray(dense.encode([q])[0], dtype="float32").reshape(1, -1)
            t0 = time.monotonic()
            _D, ann_I = index.search(qv, 50)
            dt = time.monotonic() - t0
            t0 = time.monotonic()
            match = " OR ".join(f'"{t}"' for t in q.split())
            fts_hits = conn.execute(
                "select rowid, bm25(chunks) from chunks "
                "where chunks match ? order by bm25(chunks) limit 50",
                (match,)).fetchall()
            dt2 = time.monotonic() - t0
            if warm:
                continue
            ann_lat.append(dt)
            fts_lat.append(dt2)
            t0 = time.monotonic()
            rrf = {}
            for rank, i in enumerate(ann_I[0]):
                rrf[int(i)] = rrf.get(int(i), 0.0) + 1.0 / (rank + 1)
            for rank, (rid, _s) in enumerate(fts_hits):
                rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (rank + 1)
            top = sorted(rrf.items(), key=lambda kv: -kv[1])[:10]
            _ = [texts[i] for i, _s in top]
            hyb_lat.append(time.monotonic() - t0)

    def p(name, xs):
        receipt["latency"][name] = {
            "p50": round(statistics.median(xs) * 1000, 1),
            "p95": round(sorted(xs)[max(0, int(len(xs) * 0.95) - 1)] * 1000, 1),
        }
        print(f"[probe] {name}: p50={receipt['latency'][name]['p50']}ms "
              f"p95={receipt['latency'][name]['p95']}ms")

    p("faiss_ann", ann_lat)
    p("fts5_bm25", fts_lat)
    p("hybrid_rrf_python", hyb_lat)
    total_hybrid = [a + f + h for a, f, h in zip(ann_lat, fts_lat, hyb_lat)]
    p("end_to_end_hybrid", total_hybrid)

    conn.close()
    DB.unlink(missing_ok=True)
    out = REPO / "docs" / "evidence-fabric" / "benchmark" / "ann_fts5_probe.json"
    out.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    print(f"[probe] receipt -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
