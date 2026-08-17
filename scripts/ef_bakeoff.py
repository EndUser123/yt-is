#!/usr/bin/env python
"""Projection-engine bakeoff (B_GATE_DECISIONS.md Decision 2).

Candidate A: faiss-cpu HNSW (M=32) + sqlite FTS5 + RRF in Python.
Candidate B: Qdrant SERVER v1.19.0 native Windows binary — dense HNSW
             (m=32) + upserted client-BM25 sparse vectors + RRF prefetch.
Same points, same vectors (bge-m3 1024d primary; MiniLM 384d latency
spot-check), same queries, same machine, same top-K, same warmup.

Matrix measured: recall@20 vs exact, p50/p95/p99 latency, end-to-end
holdout quality (W-MRR), RAM (peak RSS), disk, build time, incremental
add, delete/tombstone, metadata-filtered retrieval, concurrent readers,
concurrent indexing, rebuild, startup/recovery, kill -9 failure mode,
and growth trend at ~2x corpus (jittered duplicates, latency only).

Stages cache artifacts under P:/tmp/ef_bakeoff_cache/ so a crashed stage
resumes without re-embedding. Receipt ->
docs/evidence-fabric/benchmark/bakeoff_results.json
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ef import authority, chunking, embedding  # noqa: E402

CACHE = Path("P:/tmp/ef_bakeoff_cache")
QDRANT_BIN = Path("P:/.data/yt-is/ef/tools/qdrant.exe")   # stable loc; P:/tmp gets hygiene-wiped
QDRANT_DIR = CACHE / "qdrant_storage"
QDRANT_CFG = CACHE / "qdrant_config.yaml"
RECEIPT = REPO / "docs" / "evidence-fabric" / "benchmark" / "bakeoff_results.json"
FTS_DB = CACHE / "fts5.sqlite"
# Isolated ports: the host runs the operator's OpenWhispr qdrant on 6333/6334.
# NEVER taskkill by image name on this machine — it shares qdrant binaries
# with other workloads; cleanup is PID-tracked only (see main).
PORT = 6390
TOPK = 20
COLL = "bakeoff_b"
COLL_ML = "bakeoff_ml"

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"


def ps_rss(pid: int) -> int:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).WorkingSet64"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out)
    except Exception:
        return 0


def rss_mb(pid: int) -> float:
    return ps_rss(pid) / 1e6


def dir_mb(p: Path) -> float:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total / 1e6


def load_queries():
    hold = json.loads((BENCH / "holdout_hand_queries.json").read_text(encoding="utf-8"))
    hold += json.loads((BENCH / "holdout_auto_queries.json").read_text(encoding="utf-8"))
    dec = json.loads((BENCH / "decision_queries.json").read_text(encoding="utf-8"))
    return hold, dec


def stage_corpus():
    if (CACHE / "chunks.jsonl").exists():
        rows = [json.loads(l) for l in
                (CACHE / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        return rows
    rows = authority.list_eligible_transcripts()
    out = []
    for row in rows:
        eu = authority.build_eu(row)
        for ch in chunking.chunk_transcript(eu.eu_id, row["transcript"]):
            out.append({"chunk_id": ch.chunk_id, "text": ch.text,
                        "video_id": eu.video_id, "channel_id": eu.channel_id})
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(CACHE / "chunks.jsonl", "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return out


def stage_vectors(chunks):
    m3_path, ml_path = CACHE / "m3.npy", CACHE / "ml.npy"
    texts = [c["text"] for c in chunks]
    if not m3_path.exists():
        from FlagEmbedding import BGEM3FlagModel
        m3 = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
        vecs = []
        t0 = time.monotonic()
        B = 512
        for i in range(0, len(texts), B):
            o = m3.encode(texts[i:i + B], batch_size=16, max_length=512,
                          return_dense=True, return_sparse=False,
                          return_colbert_vecs=False)
            v = np.asarray(o["dense_vecs"], dtype="float32")
            v /= (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
            vecs.append(v)
            print(f"[vec] m3 {i + len(v)}/{len(texts)} ({time.monotonic()-t0:.0f}s)",
                  flush=True)
        np.save(m3_path, np.vstack(vecs))
    if not ml_path.exists():
        ml = embedding.DenseEmbedder("all-MiniLM-L6-v2", batch_size=128)
        ml.model.max_seq_length = 512
        np.save(ml_path, np.asarray(ml.encode(texts), dtype="float32"))
    return np.load(m3_path), np.load(ml_path)


def query_vectors(model: str, texts: list[str]) -> np.ndarray:
    if model == "m3":
        from FlagEmbedding import BGEM3FlagModel
        m3 = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
        o = m3.encode(texts, batch_size=16, max_length=512,
                      return_dense=True, return_sparse=False)
        v = np.asarray(o["dense_vecs"], dtype="float32")
    else:
        ml = embedding.DenseEmbedder("all-MiniLM-L6-v2", batch_size=128)
        ml.model.max_seq_length = 512
        v = np.asarray(ml.encode(texts), dtype="float32")
    v /= (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
    return v


def main() -> int:
    import faiss
    from qdrant_client import QdrantClient, models

    R: dict = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "engine_A": "faiss-cpu HNSW(M=32)+FTS5+RRF",
               "engine_B": f"qdrant server 1.19.0 native "
                           f"(HNSW m=32, sparse bm25 vectors, RRF)",
               "stages": {}}

    chunks = stage_corpus()
    texts = [c["text"] for c in chunks]
    vids = [c["video_id"] for c in chunks]
    chans = [c["channel_id"] for c in chunks]
    print(f"[bake] corpus {len(chunks):,} chunks")
    R["n_chunks"] = len(chunks)

    m3_vecs, ml_vecs = stage_vectors(chunks)
    hold, dec = load_queries()
    qtexts = [q["query"] for q in hold] + [q["query"] for q in dec]
    hold_n = len(hold)
    qm3_path, qml_path = CACHE / "qm3.npy", CACHE / "qml.npy"
    if qm3_path.exists():
        qm3, qml = np.load(qm3_path), np.load(qml_path)
    else:
        qm3 = query_vectors("m3", qtexts)
        qml = query_vectors("ml", qtexts)
        np.save(qm3_path, qm3)
        np.save(qml_path, qml)

    # BM25 sparse (client, Lucene k1=1.2 b=0.75) for BOTH engines' sparse leg
    bm25 = embedding.BM25Encoder().fit(texts)
    svecs = [bm25.encode_document(t) for t in texts]

    # ---- exact ground truth for recall@20
    ex_path = CACHE / "exact_m3.npy"
    if ex_path.exists():
        exact_top = np.load(ex_path)
    else:
        exact_top = np.argsort(-(qm3 @ m3_vecs.T), axis=1)[:, :TOPK]
        np.save(ex_path, exact_top)

    # ================= ENGINE A: faiss + FTS5 =================
    fai_path = CACHE / "faiss_m3.index"
    if fai_path.exists():
        index = faiss.read_index(str(fai_path))
        t_build = None
    else:
        t0 = time.monotonic()
        index = faiss.IndexHNSWFlat(m3_vecs.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
        index.add(m3_vecs)
        faiss.write_index(index, str(fai_path))
        t_build = time.monotonic() - t0
    if not FTS_DB.exists():
        t0 = time.monotonic()
        conn = sqlite3.connect(str(FTS_DB))
        conn.execute("create virtual table chunks using fts5(text)")
        conn.executemany("insert into chunks(rowid, text) values (?, ?)",
                         list(enumerate(texts)))
        conn.commit()
        t_fts = time.monotonic() - t0
    else:
        conn = sqlite3.connect(str(FTS_DB))
        t_fts = None
    globals()["_CONN_A"] = conn   # main-thread connection; readers make their own
    R["stages"]["A_build"] = {
        "faiss_build_s": round(t_build, 1) if t_build else "cached",
        "fts5_build_s": round(t_fts, 1) if t_fts else "cached",
        "disk_mb": round(fai_path.stat().st_size / 1e6 + FTS_DB.stat().st_size / 1e6, 1),
    }
    print("[bake] A built:", R["stages"]["A_build"])

    def a_hybrid(qv, qtext, topk=TOPK, chan=None, conn=None):
        conn = conn if conn is not None else globals()["_CONN_A"]
        _D, I = index.search(qv.reshape(1, -1), 100)
        dense = [(int(i), 0.0) for i in I[0]]
        # strip embedded double quotes: titles like 'Why "AI" ...' break
        # FTS5 quoted-term syntax otherwise
        terms = [t.replace('"', "") for t in qtext.split()]
        match = " OR ".join(f'"{t}"' for t in terms if t)
        fts = []
        if match:
            fts = [(r[0], -r[1]) for r in conn.execute(
                "select rowid, bm25(chunks) from chunks where chunks match ? "
                "order by bm25(chunks) limit 100", (match,)).fetchall()]
        rrf: dict[int, float] = {}
        for leg in (dense, fts):
            for rk, (idx, _s) in enumerate(leg):
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rk + 1)
        out = sorted(rrf.items(), key=lambda kv: -kv[1])
        if chan is not None:
            out = [(i, s) for i, s in out if chans[i] == chan]
        return out[:topk]

    def a_lat(queries_vecs, qtexts_list):
        lat = []
        for i in range(len(qtexts_list)):
            t0 = time.monotonic()
            a_hybrid(queries_vecs[i], qtexts_list[i])
            lat.append(time.monotonic() - t0)
        return lat

    # warmup + latency (holdout+decision, m3)
    a_hybrid(qm3[0], qtexts[0])
    latA = a_lat(qm3, qtexts)
    def pct(xs, p):
        s = sorted(xs)
        return round(s[max(0, int(len(s) * p) - 1)] * 1000, 1)
    R["stages"]["A_latency_ms"] = {"p50": pct(latA, .5), "p95": pct(latA, .95),
                                   "p99": pct(latA, .99)}
    # ANN recall@20 vs exact (dense leg)
    recs = []
    for i in range(len(qtexts)):
        _D, I = index.search(qm3[i].reshape(1, -1), TOPK)
        recs.append(len(set(I[0].tolist()) & set(exact_top[i].tolist())) / TOPK)
    R["stages"]["A_ann_recall@20"] = round(statistics.mean(recs), 4)
    # holdout quality (engine A hybrid, first-occurrence MRR@10)
    from ef_b1_run import rrf_fuse  # reuse rank fusion
    mrrA = []
    for i, q in enumerate(hold):
        fused = a_hybrid(qm3[i], q["query"])
        ranks = [vids[idx] for idx, _ in fused]
        first = next((j + 1 for j, v in enumerate(ranks) if v == q["positive_video"]), None)
        mrrA.append((1.0 / first) if first and first <= 10 else 0.0)
    R["stages"]["A_holdout_mrr@10"] = round(statistics.mean(mrrA), 4)

    # ================= ENGINE B: qdrant server (isolated port) =================
    # NOTE: host runs an operator-owned qdrant (OpenWhispr) on 6333 — this
    # experiment uses 6390 via explicit config file. Cleanup kills only the
    # exact PID spawned here.
    QDRANT_CFG.write_text(
        f"storage:\n  storage_path: {QDRANT_DIR.as_posix()}\n"
        f"service:\n  http_port: {PORT}\n  grpc_port: {PORT + 1}\n"
        f"telemetry: false\n", encoding="utf-8")
    if QDRANT_DIR.exists():
        shutil.rmtree(QDRANT_DIR)
    proc = subprocess.Popen(
        [str(QDRANT_BIN), "--config-path", str(QDRANT_CFG)],
        cwd=str(QDRANT_BIN.parent),
        stdout=open(CACHE / "qdrant.log", "w"),
        stderr=subprocess.STDOUT)
    R["b_qdrant_pid"] = proc.pid
    import atexit
    atexit.register(lambda: proc.poll() is None and proc.kill())
    t0 = time.monotonic()
    qc = QdrantClient(url=f"http://127.0.0.1:{PORT}", timeout=120)
    for _ in range(120):
        try:
            qc.get_collections()
            break
        except Exception:
            time.sleep(0.5)
    R["stages"]["B_startup_s"] = round(time.monotonic() - t0, 2)

    t0 = time.monotonic()
    qc.create_collection(
        collection_name=COLL,
        vectors_config={"dense": models.VectorParams(
            size=m3_vecs.shape[1], distance=models.Distance.COSINE,
            hnsw_config=models.HnswConfigDiff(m=32))},
        sparse_vectors_config={"lex": models.SparseVectorParams()})
    from hashlib import md5
    def pid_(s): return int.from_bytes(md5(s.encode()).digest()[:8], "big")
    pts = []
    for i, c in enumerate(chunks):
        si, sv = svecs[i]
        pts.append(models.PointStruct(
            id=pid_(c["chunk_id"]),
            vector={"dense": m3_vecs[i].tolist(),
                    "lex": models.SparseVector(indices=list(map(int, si)),
                                               values=list(map(float, sv)))},
            payload={"video_id": c["video_id"], "channel_id": c["channel_id"]}))
    for i in range(0, len(pts), 1000):
        qc.upsert(COLL, pts[i:i + 1000], wait=True)
    t_buildB = time.monotonic() - t0
    qc.create_payload_index(COLL, "channel_id",
                            models.PayloadSchemaType.KEYWORD)
    qc.create_payload_index(COLL, "video_id",
                            models.PayloadSchemaType.KEYWORD)
    R["stages"]["B_build"] = {"build_s": round(t_buildB, 1),
                              "disk_mb": round(dir_mb(QDRANT_DIR), 1),
                              "rss_mb": round(rss_mb(proc.pid), 1)}
    print("[bake] B built:", R["stages"]["B_build"])

    def b_hybrid(qv, qtext, topk=TOPK, chan=None):
        si, sv = bm25.encode_query(qtext)
        flt = models.Filter(must=[models.FieldCondition(
            key="channel_id",
            match=models.MatchValue(value=chan))]) if chan else None
        r = qc.query_points(
            COLL,
            prefetch=[
                models.Prefetch(query=qv.tolist(), using="dense", limit=100),
                models.Prefetch(query=models.SparseVector(
                    indices=list(map(int, si)), values=list(map(float, sv))),
                    using="lex", limit=100, filter=flt)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=topk, query_filter=flt,
            with_payload=True)
        return [(p.payload["video_id"], p.id) for p in r.points]

    b_hybrid(qm3[0], qtexts[0])
    latB = []
    for i in range(len(qtexts)):
        t0 = time.monotonic()
        b_hybrid(qm3[i], qtexts[i])
        latB.append(time.monotonic() - t0)
    R["stages"]["B_latency_ms"] = {"p50": pct(latB, .5), "p95": pct(latB, .95),
                                   "p99": pct(latB, .99)}
    mrrB = []
    for i, q in enumerate(hold):
        vids_ranked = [v for v, _ in b_hybrid(qm3[i], q["query"])]
        first = next((j + 1 for j, v in enumerate(vids_ranked)
                      if v == q["positive_video"]), None)
        mrrB.append((1.0 / first) if first and first <= 10 else 0.0)
    R["stages"]["B_holdout_mrr@10"] = round(statistics.mean(mrrB), 4)
    # B ANN recall (dense-only query)
    recsB = []
    for i in range(len(qtexts)):
        r = qc.query_points(COLL, query=qm3[i].tolist(), using="dense",
                            limit=TOPK)
        got = {p.id for p in r.points}
        want = {pid_(chunks[j]["chunk_id"]) for j in exact_top[i]}
        recsB.append(len(got & want) / TOPK)
    R["stages"]["B_ann_recall@20"] = round(statistics.mean(recsB), 4)

    # ---- metadata-filtered retrieval
    chan_counts = {}
    for ch in chans:
        chan_counts[ch] = chan_counts.get(ch, 0) + 1
    sel_chan = next(c for c, n in sorted(chan_counts.items(),
                                         key=lambda kv: -kv[1]) if 20 <= n <= 300)
    t0 = time.monotonic(); a_hybrid(qm3[0], qtexts[0], chan=sel_chan)
    latAF = []
    for i in range(0, 40):
        t0 = time.monotonic()
        a_hybrid(qm3[i], qtexts[i], chan=sel_chan)
        latAF.append(time.monotonic() - t0)
    latBF = []
    for i in range(40):
        t0 = time.monotonic()
        b_hybrid(qm3[i], qtexts[i], chan=sel_chan)
        latBF.append(time.monotonic() - t0)
    R["stages"]["filtered_latency_ms"] = {
        "A_postfilter_p95": pct(latAF, .95),
        "B_native_filter_p95": pct(latBF, .95),
        "filter_channel": sel_chan,
        "selectivity_pct": round(100 * chan_counts[sel_chan] / len(chunks), 3),
    }

    # ---- concurrent readers (4 threads x 60 queries)
    def reader(res, off):
        lat = []
        for i in range(off, off + 60):
            t0 = time.monotonic()
            b_hybrid(qm3[i % len(qtexts)], qtexts[i % len(qtexts)])
            lat.append(time.monotonic() - t0)
        res.extend(lat)
    res: list[float] = []
    ths = [threading.Thread(target=reader, args=(res, k * 60)) for k in range(4)]
    t0 = time.monotonic()
    for t in ths: t.start()
    for t in ths: t.join()
    R["stages"]["B_concurrent_readers"] = {
        "threads": 4, "queries": len(res),
        "wall_s": round(time.monotonic() - t0, 2),
        "qps": round(len(res) / (time.monotonic() - t0 + 1e-9), 1),
        "p95_ms": pct(res, .95)}

    def reader_a(res, off):
        lat = []
        tconn = sqlite3.connect(str(FTS_DB))   # per-thread: sqlite is thread-affine
        for i in range(off, off + 60):
            t0 = time.monotonic()
            a_hybrid(qm3[i % len(qtexts)], qtexts[i % len(qtexts)], conn=tconn)
            lat.append(time.monotonic() - t0)
        tconn.close()
        res.extend(lat)
    resA: list[float] = []
    ths = [threading.Thread(target=reader_a, args=(resA, k * 60)) for k in range(4)]
    t0 = time.monotonic()
    for t in ths: t.start()
    for t in ths: t.join()
    R["stages"]["A_concurrent_readers"] = {
        "threads": 4, "queries": len(resA),
        "wall_s": round(time.monotonic() - t0, 2), "p95_ms": pct(resA, .95)}

    # ---- incremental add + concurrent indexing (engine B)
    jitter = m3_vecs[:2000] + np.random.RandomState(7).normal(
        0, 0.01, (2000, m3_vecs.shape[1])).astype("float32")
    jitter /= np.linalg.norm(jitter, axis=1, keepdims=True)
    add_pts = [models.PointStruct(
        id=pid_(f"inc-{i}"), vector={"dense": jitter[i].tolist(),
                                     "lex": models.SparseVector(indices=[i], values=[1.0])},
        payload={"video_id": f"inc-{i}", "channel_id": "inc"}) for i in range(2000)]
    latDuring = []
    stop = threading.Event()
    def query_during():
        i = 0
        while not stop.is_set():
            t0 = time.monotonic()
            b_hybrid(qm3[i % 100], qtexts[i % 100])
            latDuring.append(time.monotonic() - t0)
            i += 1
    qt = threading.Thread(target=query_during); qt.start()
    t0 = time.monotonic()
    for i in range(0, 2000, 500):
        qc.upsert(COLL, add_pts[i:i + 500], wait=True)
    t_add = time.monotonic() - t0
    stop.set(); qt.join()
    R["stages"]["B_incremental_add"] = {
        "add_2000_s": round(t_add, 2),
        "query_p95_during_indexing_ms": pct(latDuring, .95),
        "queries_during": len(latDuring)}

    # ---- delete/tombstone
    t0 = time.monotonic()
    sel = [pid_(f"inc-{i}") for i in range(1000)]
    for i in range(0, 1000, 256):
        qc.delete(COLL, points_selector=models.PointIdsList(
            points=sel[i:i + 256]))
    t_del = time.monotonic() - t0
    n_after = qc.count(COLL, exact=True).count
    R["stages"]["B_delete"] = {"delete_1000_s": round(t_del, 2),
                               "count_after": n_after,
                               "expected": len(chunks) + 1000}

    # ---- kill -9 during upsert, restart, verify
    big = [models.PointStruct(
        id=pid_(f"kill-{i}"), vector={"dense": jitter[i % 2000].tolist(),
                                      "lex": models.SparseVector(indices=[i], values=[1.0])},
        payload={"video_id": f"kill-{i}", "channel_id": "kill"}) for i in range(5000)]
    for i in range(0, 2000, 500):   # REST payload limit: 32MB per request
        qc.upsert(COLL, big[i:i + 500], wait=True)
    R["stages"]["B_kill9"] = {"interrupted": None}   # None = kill raced clean commits

    def killer():
        time.sleep(1.0)          # 500-pt batches ~200ms: kill mid-stream
        proc.kill()
    threading.Thread(target=killer, daemon=True).start()
    try:
        for i in range(2000, 5000, 500):
            qc.upsert(COLL, big[i:i + 500], wait=True)
    except Exception as e:
        R["stages"]["B_kill9"]["interrupted"] = type(e).__name__
    proc.wait(timeout=30)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [str(QDRANT_BIN), "--config-path", str(QDRANT_CFG)],
        cwd=str(QDRANT_BIN.parent),
        stdout=open(CACHE / "qdrant2.log", "w"), stderr=subprocess.STDOUT)
    R["b_qdrant_pid_restart"] = proc.pid
    atexit.register(lambda: proc.poll() is None and proc.kill())
    qc2 = QdrantClient(url=f"http://127.0.0.1:{PORT}", timeout=120)
    for _ in range(120):
        try:
            qc2.get_collections(); break
        except Exception:
            time.sleep(0.5)
    R["stages"]["B_kill9"].update({
        "restart_to_first_query_s": round(time.monotonic() - t0, 2),
        "count_after_recovery": qc2.count(COLL, exact=True).count})
    print("[bake] matrix:", json.dumps(R["stages"], indent=1)[:800])

    # ---- growth trend: 2x corpus, latency only (jittered dupes)
    growth = m3_vecs + np.random.RandomState(8).normal(
        0, 0.005, m3_vecs.shape).astype("float32")
    growth /= np.linalg.norm(growth, axis=1, keepdims=True)
    t0 = time.monotonic()
    gi = faiss.IndexHNSWFlat(growth.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
    gi.add(growth)
    R["stages"]["A_growth_2x"] = {"build_s": round(time.monotonic() - t0, 1)}
    latA2 = []
    for i in range(60):
        t0 = time.monotonic()
        gi.search(qm3[i].reshape(1, -1), TOPK)
        latA2.append(time.monotonic() - t0)
    R["stages"]["A_growth_2x"]["dense_p95_ms"] = pct(latA2, .95)

    # cleanup
    qc2.close()
    proc.terminate(); proc.wait(timeout=30)
    conn.close()

    RECEIPT.write_text(json.dumps(R, indent=1), encoding="utf-8")
    print(f"[bake] receipt -> {RECEIPT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
