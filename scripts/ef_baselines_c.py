#!/usr/bin/env python
"""A" section 11: same-corpus component baselines + section 15 stage latency.

On the CURRENT production corpus (166K points, gen1 collection), same
queries, same machine, same depth, measure:
  BGE dense only | BGE learned sparse only | dense+sparse RRF | FTS5 only |
  final routing candidate (winning policy)
Stage-level latency decomposition over 30 warm queries:
  encode -> qdrant dense -> qdrant sparse -> fusion -> fts5 -> hydration.
Receipt -> docs/evidence-fabric/benchmark/same_corpus_baselines.json
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import catalog, embedding, routing, server
from ef import projection_server as ps
from ef.query_server import ProductionQuery
from qdrant_client import models

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1


def main() -> int:
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)

    conn = catalog.connect()
    c2v = {r[0]: r[1] for r in conn.execute(
        "select c.chunk_id, e.video_id from chunk c join eu e "
        "on e.eu_id=c.eu_id").fetchall()}
    conn.close()

    hold = json.loads((BENCH / "holdout_hand_queries.json").read_text(encoding="utf-8"))
    hold += json.loads((BENCH / "holdout_auto_queries.json").read_text(encoding="utf-8"))
    queries = hold  # 114 semantic/mixed with video positives

    def encode(text):
        d, l = enc.encode([text])
        return d[0], l[0]

    def leg_dense(qv):
        r = qc.query_points(coll, query=[float(x) for x in qv],
                            using=ps.DENSE_NAME, limit=10, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    def leg_sparse(lw):
        idxs = sorted(lw.keys())
        r = qc.query_points(coll, query=models.SparseVector(
            indices=[int(t) for t in idxs],
            values=[float(lw[t]) for t in idxs]),
            using=ps.LEX_NAME, limit=10, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    def leg_hybrid(qv, lw):
        idxs = sorted(lw.keys())
        r = qc.query_points(coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=10, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    def leg_fts(text):
        c = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)
        try:
            m = routing.sanitize_fts_query(text)
            if not m:
                return []
            return [r[0] for r in c.execute(
                "select chunk_id from chunks where chunks match ? "
                "order by bm25(chunks) limit 10", (m,)).fetchall()]
        finally:
            c.close()

    def mrr(ids, positive_video):
        vids = [c2v.get(c) for c in ids[:10]]
        first = next((i + 1 for i, v in enumerate(vids)
                      if v == positive_video), None)
        return (1.0 / first) if first and first <= 10 else 0.0

    def rec(ids, positive_video):
        return 1.0 if any(c2v.get(c) == positive_video for c in ids[:10]) else 0.0

    results = {}
    cfgs = {}
    for name in ("dense_only", "sparse_only", "hybrid_rrf", "fts5_only"):
        cfgs[name] = {"mrr": [], "rec": []}
    pq = None  # final candidate lazily (needs policy winner)
    for q in queries:
        qv, lw = encode(q["query"])
        for name, ids in (("dense_only", leg_dense(qv)),
                          ("sparse_only", leg_sparse(lw)),
                          ("hybrid_rrf", leg_hybrid(qv, lw)),
                          ("fts5_only", leg_fts(q["query"]))):
            cfgs[name]["mrr"].append(mrr(ids, q["positive_video"]))
            cfgs[name]["rec"].append(rec(ids, q["positive_video"]))
    for name, v in cfgs.items():
        results[name] = {"mrr@10": round(statistics.mean(v["mrr"]), 4),
                         "recall@10": round(statistics.mean(v["rec"]), 4)}
        print(f"[base] {name}: {results[name]}")

    # final candidate with the policy winner (from routing_dev_results)
    winner = json.loads((BENCH / "routing_dev_results.json")
                        .read_text(encoding="utf-8")).get("winner")
    if winner:
        pq = ProductionQuery(enc, generation=GEN, policy=winner)
        mrrs, recs = [], []
        for q in queries:
            res = pq.relevant(q["query"], limit=10)
            first = next((i + 1 for i, r in enumerate(res)
                          if r.video_id == q["positive_video"]), None)
            mrrs.append((1.0 / first) if first and first <= 10 else 0.0)
            recs.append(1.0 if first and first <= 10 else 0.0)
        results["final_candidate"] = {"policy": winner,
                                      "mrr@10": round(statistics.mean(mrrs), 4),
                                      "recall@10": round(statistics.mean(recs), 4)}
        print(f"[base] final_candidate: {results['final_candidate']}")

    # ---- stage latency decomposition (30 warm queries)
    stages = {k: [] for k in ("encode_ms", "qdrant_dense_ms", "qdrant_sparse_ms",
                              "fusion_rrf_ms", "fts5_ms", "hydration_reopen_ms")}
    sample = [q["query"] for q in queries[:30]]
    for text in sample:   # warm
        encode(text); leg_hybrid(*encode(text))
    for text in sample:
        t0 = time.monotonic(); qv, lw = encode(text)
        stages["encode_ms"].append((time.monotonic() - t0) * 1000)
        t0 = time.monotonic(); leg_dense(qv)
        stages["qdrant_dense_ms"].append((time.monotonic() - t0) * 1000)
        t0 = time.monotonic(); leg_sparse(lw)
        stages["qdrant_sparse_ms"].append((time.monotonic() - t0) * 1000)
        idxs = sorted(lw.keys())
        t0 = time.monotonic()
        qc.query_points(coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=10,
            with_payload=True)
        stages["fusion_rrf_ms"].append((time.monotonic() - t0) * 1000)
        t0 = time.monotonic(); leg_fts(text)
        stages["fts5_ms"].append((time.monotonic() - t0) * 1000)
        t0 = time.monotonic()
        from ef.authority import reopen_span
        res = pq.relevant(text, limit=5) if pq else []
        if res:
            reopen_span(res[0].video_id, res[0].start_char, res[0].end_char)
        stages["hydration_reopen_ms"].append((time.monotonic() - t0) * 1000)
    lat = {k: round(statistics.median(v), 1) for k, v in stages.items()}
    e2e = {  # warm hybrid p95 (server query only) and full-path p95
        "hybrid_server_only_ms_p95": round(sorted(stages["fusion_rrf_ms"])[28], 1),
        "full_path_ms_p95": round(sorted(a + b + c for a, b, c in zip(
            stages["encode_ms"], stages["fusion_rrf_ms"],
            stages["hydration_reopen_ms"]))[28], 1),
    }
    print(f"[base] stage latency (median ms): {lat}")
    print(f"[base] end-to-end: {e2e}")

    receipt = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "n_queries": len(queries), "corpus_points":
                   ps.count(qc, GEN), "baselines": results,
               "stage_latency_ms": lat, "end_to_end": e2e}
    (BENCH / "same_corpus_baselines.json").write_text(
        json.dumps(receipt, indent=1), encoding="utf-8")
    print("[base] receipt -> same_corpus_baselines.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
