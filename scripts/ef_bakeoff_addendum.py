#!/usr/bin/env python
"""Bakeoff fairness addendum: engine A re-measured at efSearch=64.

The main bakeoff ran FAISS HNSW at its default efSearch (=16), which is not
an equivalent retrieval configuration to Qdrant's auto-scaled search width
(operator's Decision 2 requires equivalence). This re-measures engine A's
recall, latency, and holdout MRR at efSearch=64 using the cached artifacts.
Receipt -> benchmark/bakeoff_addendum.json
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import faiss
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CACHE = Path("P:/tmp/ef_bakeoff_cache")
BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
TOPK = 20


def main() -> int:
    chunks = [json.loads(l) for l in
              (CACHE / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    vids = [c["video_id"] for c in chunks]
    m3 = np.load(CACHE / "m3.npy")
    qm3 = np.load(CACHE / "qm3.npy")
    exact_top = np.load(CACHE / "exact_m3.npy")

    hold = json.loads((BENCH / "holdout_hand_queries.json").read_text(encoding="utf-8"))
    hold += json.loads((BENCH / "holdout_auto_queries.json").read_text(encoding="utf-8"))
    dec = json.loads((BENCH / "decision_queries.json").read_text(encoding="utf-8"))
    qtexts = [q["query"] for q in hold] + [q["query"] for q in dec]

    index = faiss.read_index(str(CACHE / "faiss_m3.index"))
    faiss.ParameterSpace().set_index_parameter(index, "efSearch", 64)
    conn = sqlite3.connect(str(CACHE / "fts5.sqlite"))

    out: dict = {"efSearch": 64, "n_chunks": len(chunks)}

    # recall@20
    recs = []
    for i in range(len(qtexts)):
        _D, I = index.search(qm3[i].reshape(1, -1), TOPK)
        recs.append(len(set(I[0].tolist()) & set(exact_top[i].tolist())) / TOPK)
    out["A_ann_recall@20"] = round(statistics.mean(recs), 4)

    # hybrid latency
    def a_hybrid(qv, qtext):
        _D, I = index.search(qv.reshape(1, -1), 100)
        dense = [(int(i), 0.0) for i in I[0]]
        terms = [t.replace('"', "") for t in qtext.split()]
        match = " OR ".join(f'"{t}"' for t in terms if t)
        fts = [(r[0], -r[1]) for r in conn.execute(
            "select rowid, bm25(chunks) from chunks where chunks match ? "
            "order by bm25(chunks) limit 100", (match,)).fetchall()] if match else []
        rrf: dict[int, float] = {}
        for leg in (dense, fts):
            for rk, (idx, _s) in enumerate(leg):
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rk + 1)
        return sorted(rrf.items(), key=lambda kv: -kv[1])[:TOPK]

    a_hybrid(qm3[0], qtexts[0])
    lat = []
    for i in range(len(qtexts)):
        t0 = time.monotonic()
        a_hybrid(qm3[i], qtexts[i])
        lat.append(time.monotonic() - t0)
    s = sorted(lat)
    out["A_latency_ms"] = {
        "p50": round(s[len(s) // 2] * 1000, 1),
        "p95": round(s[max(0, int(len(s) * .95) - 1)] * 1000, 1),
        "p99": round(s[max(0, int(len(s) * .99) - 1)] * 1000, 1)}

    # holdout MRR@10
    mrr = []
    for i, q in enumerate(hold):
        fused = a_hybrid(qm3[i], q["query"])
        ranks = [vids[idx] for idx, _ in fused]
        first = next((j + 1 for j, v in enumerate(ranks)
                      if v == q["positive_video"]), None)
        mrr.append((1.0 / first) if first and first <= 10 else 0.0)
    out["A_holdout_mrr@10"] = round(statistics.mean(mrr), 4)
    conn.close()

    (BENCH / "bakeoff_addendum.json").write_text(json.dumps(out, indent=1),
                                                 encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
