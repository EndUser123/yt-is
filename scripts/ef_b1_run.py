#!/usr/bin/env python
"""B.1 confirmatory run per PREREGISTRATION_B1.md.

Configs A/B/C/D over the holdout, exact dense search + FTS5 + learned
sparse, RRF fusion, stratified paired bootstrap for R-B1.1(b).
Receipt -> docs/evidence-fabric/benchmark/b1_results.json
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ef import chunking, embedding  # noqa: E402
from ef_holdout_build import fresh_rows  # noqa: E402

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
FTS_DB = Path("P:/tmp/ef_b1_fts5.sqlite")
RRF_K = 60
TOPLEG = 100

WEIGHTS = {
    "ytis_natural": 0.30, "wiki_evidence": 0.20, "www_prior": 0.15,
    "wiki_contradiction": 0.10, "review_arch": 0.10, "title_entity": 0.10,
    "exact_identifiers": 0.05,
}


def load_corpus():
    frozen = json.loads((BENCH / "corpus.json").read_text(encoding="utf-8"))
    chunks = [dict(c) for c in frozen["chunks"]]
    fresh_ids = set()
    for x in fresh_rows():
        for ch in chunking.chunk_transcript(f"{x['video_id']}:transcript",
                                            x["transcript"]):
            chunks.append({"chunk_id": ch.chunk_id, "video_id": x["video_id"],
                           "category": x["category"], "title": x["title"],
                           "text": ch.text})
            fresh_ids.add(x["video_id"])
    return chunks, fresh_ids, frozen["digest"]


def fts_rank(conn, query, top):
    match = " OR ".join(f'"{t}"' for t in query.split() if t)
    if not match:
        return []
    rows = conn.execute(
        "select rowid, bm25(chunks) from chunks where chunks match ? "
        "order by bm25(chunks) limit ?", (match, top)).fetchall()
    return [(r[0], -r[1]) for r in rows]


def rrf_fuse(legs, top=20):
    scores = {}
    for leg in legs:
        for rank, (idx, _s) in enumerate(leg):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])[:top]


def per_query_metrics(query, fused, video_of, positive):
    """First-occurrence relevance: only the FIRST chunk of the positive
    video in the ranking is relevant. Without this, multiple chunks of the
    positive video inflate nDCG above 1.0 (harness defect found and fixed
    per R-B1.4 on first run; fix recorded in b1_results defect_log)."""
    ranks = [video_of[i] for i, _ in fused]
    first = next((i + 1 for i, v in enumerate(ranks) if v == positive), None)
    rels = []
    seen = False
    for v in ranks[:10]:
        if v == positive and not seen:
            rels.append(1.0)
            seen = True
        else:
            rels.append(0.0)
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    return {
        "mrr10": (1.0 / first) if first and first <= 10 else 0.0,
        "rec10": 1.0 if first and first <= 10 else 0.0,
        "ndcg10": dcg,   # binary single-relevant ideal = 1.0
    }


def aggregate(per_q):
    out = {}
    for s in WEIGHTS:
        rows = [q for q in per_q if q["stratum"] == s]
        if not rows:
            continue
        out[s] = {
            "n": len(rows),
            "mrr10": round(sum(r["mrr10"] for r in rows) / len(rows), 4),
            "rec10": round(sum(r["rec10"] for r in rows) / len(rows), 4),
            "ndcg10": round(sum(r["ndcg10"] for r in rows) / len(rows), 4),
        }
    def wmean(metric):
        return sum(WEIGHTS[s] * out[s][metric] for s in out)
    return out, {"W_mrr10": round(wmean("mrr10"), 4),
                 "W_rec10": round(wmean("rec10"), 4),
                 "W_ndcg10": round(wmean("ndcg10"), 4)}


def bootstrap_delta(per_q_b, per_q_a, n=10000, seed=42):
    """Stratified paired bootstrap of weighted mean ΔMRR@10 (B minus A)."""
    rng = random.Random(seed)
    strata = WEIGHTS.keys()
    by_s = {s: [(a["mrr10"], b["mrr10"]) for a, b in zip(per_q_a, per_q_b)
                if a["stratum"] == s and b["stratum"] == s] for s in strata}
    deltas = []
    for _ in range(n):
        wsum = 0.0
        for s, pairs in by_s.items():
            if not pairs:
                continue
            k = len(pairs)
            sample = [pairs[rng.randrange(k)] for _ in range(k)]
            wsum += WEIGHTS[s] * sum(b - a for a, b in sample) / k
        deltas.append(wsum)
    deltas.sort()
    lo, hi = deltas[int(0.025 * n)], deltas[int(0.975 * n)]
    point = sum(deltas) / n
    return point, lo, hi


def main() -> int:
    queries = (json.loads((BENCH / "holdout_hand_queries.json").read_text(encoding="utf-8"))
               + json.loads((BENCH / "holdout_auto_queries.json").read_text(encoding="utf-8")))
    print(f"[b1] {len(queries)} queries")
    chunks, fresh_ids, frozen_digest = load_corpus()
    texts = [c["text"] for c in chunks]
    video_of = [c["video_id"] for c in chunks]
    print(f"[b1] corpus: {len(chunks)} chunks ({len(fresh_ids)} fresh videos)")

    receipt = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "n_queries": len(queries), "n_chunks": len(chunks),
               "frozen_digest": frozen_digest, "configs": {}, "rule_R_B1_1": {},
               "defect_log": [
                   "run 1 (2026-08-16): nDCG@10 exceeded 1.0 for configs B/C/D "
                   "because every chunk of the positive video counted as "
                   "relevant while IDCG assumed one. Fix: first-occurrence "
                   "relevance only. MRR@10 and Recall@10 were unaffected by "
                   "the defect. Full re-run per R-B1.4; this receipt is run 2."
               ]}

    # FTS5 index
    if FTS_DB.exists():
        FTS_DB.unlink()
    conn = sqlite3.connect(str(FTS_DB))
    conn.execute("create virtual table chunks using fts5(text)")
    conn.executemany("insert into chunks(rowid, text) values (?, ?)",
                     list(enumerate(texts)))
    conn.commit()

    # MiniLM dense (config A)
    t0 = time.monotonic()
    ml = embedding.DenseEmbedder("all-MiniLM-L6-v2", batch_size=128)
    ml.model.max_seq_length = 512
    ml_corpus = np.asarray(ml.encode(texts), dtype="float32")
    ml_q = np.asarray(ml.encode([q["query"] for q in queries]), dtype="float32")
    print(f"[b1] minilm embedded in {time.monotonic()-t0:.0f}s")

    # BGE-M3 dense + learned sparse (configs B/C/D)
    from FlagEmbedding import BGEM3FlagModel
    t0 = time.monotonic()
    m3 = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
    m3_out = m3.encode(texts, batch_size=16, max_length=512,
                       return_dense=True, return_sparse=True,
                       return_colbert_vecs=False)
    m3_corpus = np.asarray(m3_out["dense_vecs"], dtype="float32")
    # normalize dense (FP16 model returns unnormalized)
    m3_corpus /= (np.linalg.norm(m3_corpus, axis=1, keepdims=True) + 1e-12)
    m3_q_out = m3.encode([q["query"] for q in queries], batch_size=16,
                         max_length=512, return_dense=True, return_sparse=True)
    m3_q = np.asarray(m3_q_out["dense_vecs"], dtype="float32")
    m3_q /= (np.linalg.norm(m3_q, axis=1, keepdims=True) + 1e-12)
    print(f"[b1] bge-m3 embedded in {time.monotonic()-t0:.0f}s")

    # learned-sparse postings (term id -> weight), aligned to corpus order
    global _POSTINGS
    _POSTINGS = {}
    lw = m3_out["lexical_weights"]
    idx_lists = [[int(t) for t in d.keys()] for d in lw]
    val_lists = [[float(v) for v in d.values()] for d in lw]
    for di, (idxs, vals) in enumerate(zip(idx_lists, val_lists)):
        for t, v in zip(idxs, vals):
            _POSTINGS.setdefault(t, []).append((di, v))
    q_lw = m3_q_out["lexical_weights"]

    def sparse_leg_q(qi):
        acc = {}
        for t, w in q_lw[qi].items():
            for i, v in _POSTINGS.get(int(t), ()):
                acc[i] = acc.get(i, 0.0) + float(w) * v
        return sorted(acc.items(), key=lambda kv: -kv[1])[:TOPLEG]

    # configs
    def run_config(name, corpus_dense, q_dense_arr, use_fts, use_sparse):
        per_q = []
        for qi, q in enumerate(queries):
            legs = []
            sims = corpus_dense @ q_dense_arr[qi]
            legs.append([(int(i), float(sims[i]))
                         for i in np.argsort(-sims)[:TOPLEG]])
            if use_fts:
                legs.append(fts_rank(conn, q["query"], TOPLEG))
            if use_sparse:
                legs.append(sparse_leg_q(qi))
            fused = rrf_fuse(legs)
            m = per_query_metrics(q, fused, video_of, q["positive_video"])
            per_q.append({"stratum": q["stratum"], **m})
        strat, weighted = aggregate(per_q)
        receipt["configs"][name] = {"strata": strat, "weighted": weighted}
        print(f"[b1] {name}: {weighted}")
        return per_q

    pa = run_config("A_minilm_fts5", ml_corpus, ml_q, True, False)
    pb = run_config("B_bgem3_fts5", m3_corpus, m3_q, True, False)
    pc = run_config("C_bgem3_learned_sparse", m3_corpus, m3_q, False, True)
    pd = run_config("D_bgem3_both", m3_corpus, m3_q, True, True)

    # R-B1.1
    point, lo, hi = bootstrap_delta(pb, pa)
    wA = receipt["configs"]["A_minilm_fts5"]["weighted"]
    wB = receipt["configs"]["B_bgem3_fts5"]["weighted"]
    crit_ok = (receipt["configs"]["B_bgem3_fts5"]["strata"]["exact_identifiers"]["rec10"]
               >= receipt["configs"]["A_minilm_fts5"]["strata"]["exact_identifiers"]["rec10"] - 0.02
               and
               receipt["configs"]["B_bgem3_fts5"]["strata"]["title_entity"]["rec10"]
               >= receipt["configs"]["A_minilm_fts5"]["strata"]["title_entity"]["rec10"] - 0.05)
    promote = (wB["W_mrr10"] - wA["W_mrr10"] >= 0.03 and lo > 0 and crit_ok
               and wB["W_rec10"] >= wA["W_rec10"] - 0.01)
    receipt["rule_R_B1_1"] = {
        "delta_W_mrr10": round(wB["W_mrr10"] - wA["W_mrr10"], 4),
        "bootstrap_point": round(point, 4), "ci95": [round(lo, 4), round(hi, 4)],
        "critical_strata_ok": crit_ok, "PROMOTE_BGE_M3": promote,
    }
    # R-B1.2
    wD = receipt["configs"]["D_bgem3_both"]["weighted"]
    ident_ok = (receipt["configs"]["D_bgem3_both"]["strata"]["exact_identifiers"]["rec10"]
                >= receipt["configs"]["B_bgem3_fts5"]["strata"]["exact_identifiers"]["rec10"] - 0.02)
    receipt["rule_R_B1_2"] = {
        "delta_W_ndcg10_D_vs_B": round(wD["W_ndcg10"] - wB["W_ndcg10"], 4),
        "identifiers_ok": ident_ok,
        "ADOPT_LEARNED_SPARSE": (wD["W_ndcg10"] - wB["W_ndcg10"] >= 0.02 and ident_ok),
    }
    print("[b1] R-B1.1:", receipt["rule_R_B1_1"])
    print("[b1] R-B1.2:", receipt["rule_R_B1_2"])

    conn.close()
    FTS_DB.unlink(missing_ok=True)
    out = BENCH / "b1_results.json"
    out.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    print(f"[b1] receipt -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
