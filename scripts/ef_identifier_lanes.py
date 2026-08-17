#!/usr/bin/env python
"""C-gate item 6: exact-token lane comparison on identifier dev cases.

Builds ≥50 dev identifier queries spread across distinct benchmark-corpus
videos (one token per video — no concentration), then compares lanes:
  L1 BGE-M3 learned sparse
  L2 Qdrant server BM25 (Document query; falls back to fastembed Bm25 client vector)
  L3 FTS5
  L2+L3, L1+L3 combos (justified combinations)
Positive = the chunk containing the token. Metrics: chunk-level
Recall@1/@5/@10, MRR@10. Receipt -> benchmark/identifier_lanes_dev.json
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
CACHE = Path("P:/tmp/ef_lane_cache")
QDRANT_BIN = Path("P:/.data/yt-is/ef/tools/qdrant.exe")
PORT = 6390
TOPK = 10

IDENT = re.compile(
    r"\b(?:"
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}"          # snake_case
    r"|[a-z]+(?:\.[a-z0-9]+){1,}"                # dotted.name
    r"|[A-Za-z]*[a-z][A-Z][A-Za-z]*"              # camelCase
    r"|[A-Z]{3,}[0-9]*"                           # ACRONYM / error code
    r"|[a-zA-Z]+[0-9]+[a-zA-Z0-9.-]*"             # alnum versions
    r")\b")


def build_dev_queries(n=55):
    corpus = json.loads((BENCH / "corpus.json").read_text(encoding="utf-8"))
    chunks = corpus["chunks"]
    # document frequency to exclude ubiquitous tokens
    df: dict[str, int] = {}
    tok_chunks = []
    for ci, c in enumerate(chunks):
        toks = set(m.group(0) for m in IDENT.finditer(c["text"]))
        tok_chunks.append(toks)
        for t in toks:
            df[t] = df.get(t, 0) + 1
    rare_cut = max(3, len(chunks) // 100)
    queries, used_videos = [], set()
    # deterministic order, one token per video
    order = sorted(range(len(chunks)), key=lambda i: chunks[i]["chunk_id"])
    for ci in order:
        c = chunks[ci]
        if c["video_id"] in used_videos:
            continue
        cands = sorted(t for t in tok_chunks[ci]
                       if 4 <= len(t) <= 40 and 1 <= df.get(t, 0) <= rare_cut)
        if not cands:
            continue
        tok = cands[len(used_videos) % len(cands)]
        queries.append({"tier": "identifier_dev", "query": tok,
                        "positive_video": c["video_id"],
                        "positive_chunk": c["chunk_id"]})
        used_videos.add(c["video_id"])
        if len(queries) >= n:
            break
    (BENCH / "identifier_dev_queries.json").write_text(
        json.dumps(queries, indent=1), encoding="utf-8")
    print(f"[lanes] {len(queries)} dev queries across {len(used_videos)} videos")
    return queries, chunks


def main() -> int:
    from FlagEmbedding import BGEM3FlagModel
    from qdrant_client import QdrantClient, models

    CACHE.mkdir(parents=True, exist_ok=True)
    queries, chunks = build_dev_queries()
    texts = [c["text"] for c in chunks]
    qtoks = [q["query"] for q in queries]

    # BGE-M3 dense + learned sparse (cached)
    m3p, lsp = CACHE / "m3.npy", CACHE / "lex.npy"
    if m3p.exists() and lsp.exists():
        dense = np.load(m3p)
        import pickle
        lex = pickle.load(open(lsp, "rb"))
    else:
        m3 = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
        o = m3.encode(texts, batch_size=16, max_length=512,
                      return_dense=True, return_sparse=True,
                      return_colbert_vecs=False)
        dense = np.asarray(o["dense_vecs"], dtype="float32")
        dense /= (np.linalg.norm(dense, axis=1, keepdims=True) + 1e-12)
        lex = o["lexical_weights"]
        np.save(m3p, dense)
        import pickle
        pickle.dump(lex, open(lsp, "wb"))
    qm3p, qlsp = CACHE / "qm3.npy", CACHE / "qlex.pkl"
    if qm3p.exists():
        qdense = np.load(qm3p)
        import pickle
        qlex = pickle.load(open(qlsp, "rb"))
    else:
        m3q = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
        oq = m3q.encode(qtoks, batch_size=16, max_length=512,
                        return_dense=True, return_sparse=True)
        qdense = np.asarray(oq["dense_vecs"], dtype="float32")
        qdense /= (np.linalg.norm(qdense, axis=1, keepdims=True) + 1e-12)
        qlex = oq["lexical_weights"]
        np.save(qm3p, qdense)
        import pickle
        pickle.dump(qlex, open(qlsp, "wb"))

    # FTS5 (cached)
    fts = CACHE / "fts5.sqlite"
    if not fts.exists():
        conn = sqlite3.connect(str(fts))
        conn.execute("create virtual table chunks using fts5(text)")
        conn.executemany("insert into chunks(rowid, text) values (?, ?)",
                         list(enumerate(texts)))
        conn.commit()
    else:
        conn = sqlite3.connect(str(fts))

    # learned-sparse postings
    postings: dict[int, list[tuple[int, float]]] = {}
    for di, d in enumerate(lex):
        for t, v in d.items():
            postings.setdefault(int(t), []).append((di, float(v)))

    def lane_learned(qi, top):
        acc = {}
        for t, w in qlex[qi].items():
            for i, v in postings.get(int(t), ()):
                acc[i] = acc.get(i, 0.0) + float(w) * v
        return [chunks[i]["chunk_id"]
                for i, _ in sorted(acc.items(), key=lambda kv: -kv[1])[:top]]

    def lane_fts(qi, top):
        tok = qtoks[qi]
        rows = conn.execute(
            "select rowid from chunks where chunks match ? "
            "order by bm25(chunks) limit ?", (f'"{tok}"', top)).fetchall()
        return [chunks[r[0]]["chunk_id"] for r in rows]

    # ---- Qdrant server on dedicated port (PID-owned)
    cfg = CACHE / "qdrant_config.yaml"
    storage = CACHE / "qdrant_storage"
    cfg.write_text(
        f"storage:\n  storage_path: {storage.as_posix()}\n"
        f"service:\n  http_port: {PORT}\n  grpc_port: {PORT + 1}\n"
        f"telemetry: false\n", encoding="utf-8")
    import shutil
    if storage.exists():
        shutil.rmtree(storage)
    proc = subprocess.Popen([str(QDRANT_BIN), "--config-path", str(cfg)],
                            cwd=str(QDRANT_BIN.parent),
                            stdout=open(CACHE / "qdrant.log", "w"),
                            stderr=subprocess.STDOUT)
    import atexit
    atexit.register(lambda: proc.poll() is None and proc.kill())
    qc = QdrantClient(url=f"http://127.0.0.1:{PORT}", timeout=120)
    for _ in range(120):
        try:
            qc.get_collections(); break
        except Exception:
            time.sleep(0.5)

    from hashlib import md5
    def pid_(s): return int.from_bytes(md5(s.encode()).digest()[:8], "big")

    from fastembed import SparseTextEmbedding
    bm25enc = SparseTextEmbedding(model_name="Qdrant/bm25")
    bm25_vecs = list(bm25enc.embed(texts))   # hashed vocab, own token space

    qc.create_collection(
        "lanes",
        vectors_config={"dense": models.VectorParams(size=dense.shape[1],
                       distance=models.Distance.COSINE)},
        sparse_vectors_config={"lex": models.SparseVectorParams(),
                               "bm25": models.SparseVectorParams()})
    from qdrant_client import models as M
    pts = []
    for i, c in enumerate(chunks):
        l = lex[i]
        bv = bm25_vecs[i]
        pts.append(M.PointStruct(
            id=pid_(c["chunk_id"]),
            vector={"dense": dense[i].tolist(),
                    "lex": M.SparseVector(indices=[int(t) for t in l.keys()],
                                          values=[float(v) for v in l.values()]),
                    "bm25": M.SparseVector(
                        indices=[int(x) for x in bv.indices],
                        values=[float(x) for x in bv.values])},
            payload={"text": c["text"][:2000], "chunk_id": c["chunk_id"]}))
    for i in range(0, len(pts), 500):
        qc.upsert("lanes", pts[i:i + 500], wait=True)

    def lane_qdrant_bm25(qi, top):
        # Qdrant BM25 lane: fastembed Qdrant/bm25 client-encoded vector
        # against the identically-encoded corpus field (server-side
        # Document/bm25 inference requires the same fastembed model anyway).
        qv = list(bm25enc.embed([qtoks[qi]]))[0]
        r = qc.query_points("lanes",
                            query=M.SparseVector(
                                indices=[int(i) for i in qv.indices],
                                values=[float(v) for v in qv.values]),
                            using="bm25", limit=top, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    # ---- evaluate lanes
    def chunk_ids_to_rank(lane_results, top):
        return lane_results

    def metrics(ranked_chunk_ids, positive_chunk):
        r = ranked_chunk_ids
        first = next((i + 1 for i, c in enumerate(r) if c == positive_chunk), None)
        return {"r1": 1.0 if first == 1 else 0.0,
                "r5": 1.0 if first and first <= 5 else 0.0,
                "r10": 1.0 if first and first <= 10 else 0.0,
                "mrr10": (1.0 / first) if first and first <= 10 else 0.0}

    def run_lane(name, fn, top=TOPK):
        per = []
        for qi, q in enumerate(queries):
            ranked = fn(qi, top)
            per.append(metrics(ranked, q["positive_chunk"]))
        agg = {k: round(statistics.mean(p[k] for p in per), 4) for k in per[0]}
        print(f"[lanes] {name}: {agg}")
        return agg

    def rrf_merge(*lanes_fn, top=TOPK):
        def fused(qi, t):
            score = {}
            for fn in lanes_fn:
                for rk, cid in enumerate(fn(qi, t)):
                    score[cid] = score.get(cid, 0.0) + 1.0 / (60 + rk + 1)
            return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:top]]
        return fused

    results = {}
    results["L1_bge_learned_sparse"] = run_lane("L1 learned", lane_learned)
    results["L2_qdrant_bm25"] = run_lane("L2 qdrant bm25", lane_qdrant_bm25)
    results["L3_fts5"] = run_lane("L3 fts5", lane_fts)
    results["L1+L3"] = run_lane("L1+L3", rrf_merge(lane_learned, lane_fts))
    results["L2+L3"] = run_lane("L2+L3", rrf_merge(lane_qdrant_bm25, lane_fts))
    results["L1+L2"] = run_lane("L1+L2", rrf_merge(lane_learned, lane_qdrant_bm25))

    best = max(results, key=lambda k: (results[k]["r1"], results[k]["r5"],
                                       results[k]["mrr10"]))
    receipt = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "n_dev": len(queries), "lanes": results,
               "best_by_r1": best}
    (BENCH / "identifier_lanes_dev.json").write_text(json.dumps(receipt, indent=1),
                                                     encoding="utf-8")
    print(f"[lanes] BEST by Recall@1: {best} -> {results[best]}")
    conn.close()
    qc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
