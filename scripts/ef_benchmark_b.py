#!/usr/bin/env python
"""Phase B benchmark: model selection per PREREGISTRATION_B.md.

Subcommands:
  corpus      build the stratified benchmark corpus (chunks) -> JSON
  queries     build the automated DECISION query set -> JSON
  smoke-sample print 30 stratified chunk excerpts for hand authoring
  run         embed+index+evaluate one model on the corpus -> results JSON
  rules       apply preregistered decision rules to results -> verdict JSON

Determinism: video_id asc ordering everywhere; results carry corpus digest.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, chunking, embedding, projection  # noqa: E402
from ef.contracts import ChunkRecord  # noqa: E402

BENCH_DIR = REPO / "docs" / "evidence-fabric" / "benchmark"
CORPUS_N = 3000
QUERIES_PER_CATEGORY = 10

MODELS = {
    "minilm": {"st": "all-MiniLM-L6-v2", "query_prefix": ""},
    "bge-m3": {"st": "BAAI/bge-m3", "query_prefix": ""},
    "qwen3-4b": {"st": "Qwen/Qwen3-Embedding-4B", "query_prefix": "query: "},
}

TRANSCRIPTS_DB = authority.TRANSCRIPTS_DB
STATUS_DB = authority.STATUS_DB


def _bench_conn():
    conn = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{STATUS_DB}?mode=ro' as status")
    return conn


# ---------- corpus ----------

def build_corpus(out: Path):
    conn = _bench_conn()
    rows = conn.execute("""
        select t.video_id, t.transcript, a.title, a.description,
               a.channel_id, coalesce(nullif(cm.category,''),'Uncategorized') as category
        from transcript_cache t
        join status.analysis_status a on a.video_id = t.video_id
        left join status.channel_metadata cm on cm.channel_id = a.channel_id
        where length(t.transcript) >= 100
          and a.channel_id is not null and a.title is not null
        order by t.video_id asc
    """).fetchall()
    conn.close()

    # stratify: cap per category, keep video_id asc within category
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(dict(r))
    cap = max(CORPUS_N // max(1, len(by_cat)), 30)
    picked: list[dict] = []
    for cat in sorted(by_cat):
        picked.extend(by_cat[cat][:cap])
    picked.sort(key=lambda x: x["video_id"])
    picked = picked[:CORPUS_N]

    chunks: list[dict] = []
    for x in picked:
        for ch in chunking.chunk_transcript(f"{x['video_id']}:transcript",
                                            x["transcript"]):
            chunks.append({
                "chunk_id": ch.chunk_id, "video_id": x["video_id"],
                "category": x["category"], "title": x["title"],
                "text": ch.text,
            })
    digest = sha256(json.dumps(
        [[c["chunk_id"], c["category"]] for c in chunks]).encode()).hexdigest()[:16]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"digest": digest, "n_videos": len(picked), "n_chunks": len(chunks),
         "chunks": chunks}), encoding="utf-8")
    print(f"[corpus] videos={len(picked)} chunks={len(chunks)} digest={digest} -> {out}")


# ---------- queries ----------

def build_queries(corpus_path: Path, out: Path):
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    vids = {}
    for c in corpus["chunks"]:
        vids.setdefault(c["video_id"], {"category": c["category"], "title": c["title"]})
    by_cat: dict[str, list[str]] = {}
    for vid, meta in vids.items():
        by_cat.setdefault(meta["category"], []).append(vid)
    queries = []
    for cat in sorted(by_cat):
        for vid in by_cat[cat][:QUERIES_PER_CATEGORY]:
            meta = vids[vid]
            title = (meta["title"] or "").strip()
            if 15 <= len(title) <= 200:
                queries.append({
                    "tier": "decision", "kind": "title",
                    "query": title, "positive_video": vid, "category": cat,
                })
    out.write_text(json.dumps(queries, indent=1), encoding="utf-8")
    print(f"[queries] {len(queries)} decision queries across "
          f"{len(set(q['category'] for q in queries))} categories -> {out}")


def smoke_sample(corpus_path: Path, k: int = 30):
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    chunks = corpus["chunks"]
    # one chunk per category where possible, deterministic
    seen: set[str] = set()
    sample = []
    for c in chunks:
        if c["category"] not in seen and len(sample) < k:
            seen.add(c["category"])
            sample.append(c)
    for c in chunks[len(chunks) // 2:]:
        if len(sample) >= k:
            break
        if c["chunk_id"] not in [s["chunk_id"] for s in sample]:
            sample.append(c)
    for s in sample:
        print("=" * 70)
        print(f"chunk_id: {s['chunk_id']}  category: {s['category']}")
        print(f"title: {s['title']}")
        print(f"excerpt: {s['text'][:700]}")


# ---------- run one model ----------

def run_model(model_key: str, corpus_path: Path, queries_path: Path,
              smoke_path: Path | None, out: Path):
    from qdrant_client import QdrantClient, models
    from ef.embedding import BM25Encoder
    import tempfile, shutil

    cfg = MODELS[model_key]
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    chunks = corpus["chunks"]
    texts = [c["text"] for c in chunks]
    vid2cat = {c["video_id"]: c["category"] for c in chunks}

    res = {"model": model_key, "st": cfg["st"], "corpus_digest": corpus["digest"],
           "n_chunks": len(chunks), "ran_at": datetime.now(timezone.utc).isoformat(),
           "timings_s": {}, "metrics": {}}

    dense = embedding.DenseEmbedder(cfg["st"])
    res["device"] = dense.device
    t0 = time.monotonic()
    dvecs = dense.encode(texts)
    res["timings_s"]["corpus_dense_embed"] = time.monotonic() - t0
    res["chunks_per_s"] = len(texts) / res["timings_s"]["corpus_dense_embed"]

    bm25 = BM25Encoder().fit(texts)

    tmp = tempfile.mkdtemp(prefix=f"efbench_{model_key}_")
    try:
        qc = QdrantClient(path=tmp)
        coll = "bench"
        qc.create_collection(
            collection_name=coll,
            vectors_config={"dense": models.VectorParams(
                size=dense.dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"lex": models.SparseVectorParams()})
        svecs = [bm25.encode_document(t) for t in texts]
        from hashlib import md5
        pts = []
        for c, dv, (si, sv) in zip(chunks, dvecs, svecs):
            pts.append(models.PointStruct(
                id=int.from_bytes(md5(c["chunk_id"].encode()).digest()[:8], "big"),
                vector={"dense": dv,
                        "lex": models.SparseVector(indices=si, values=sv)},
                payload={"video_id": c["video_id"], "category": c["category"]}))
        for i in range(0, len(pts), 512):
            qc.upsert(coll, pts[i:i + 512])

        def evaluate(queries: list[dict], tier: str) -> dict:
            latencies = []
            per_q = []
            for q in queries:
                qtext = cfg["query_prefix"] + q["query"]
                dvec = dense.encode([qtext])[0]
                si, sv = bm25.encode_query(qtext)
                t0 = time.monotonic()
                r = qc.query_points(
                    collection_name=coll,
                    prefetch=[
                        models.Prefetch(query=dvec, using="dense", limit=100),
                        models.Prefetch(query=models.SparseVector(indices=si, values=sv),
                                        using="lex", limit=100),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=20, with_payload=True)
                latencies.append(time.monotonic() - t0)
                ranks = [p.payload["video_id"] for p in r.points]
                per_q.append(_per_query(q, ranks, vid2cat))
            return _aggregate(per_q, latencies, tier)

        decision = json.loads(queries_path.read_text(encoding="utf-8"))
        res["metrics"]["decision"] = evaluate(decision, "decision")
        if smoke_path and smoke_path.exists():
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
            res["metrics"]["smoke"] = evaluate(smoke, "smoke")
        qc.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    existing = [e for e in existing if e["model"] != model_key]
    existing.append(res)
    out.write_text(json.dumps(existing, indent=1), encoding="utf-8")
    print(f"[run] {model_key}: decision={res['metrics']['decision']} "
          f"({res['chunks_per_s']:.0f} ch/s on {res['device']})")


def _per_query(q: dict, ranks: list[str], vid2cat: dict) -> dict:
    pos = q["positive_video"]
    cat = q["category"]
    rels = []
    for vid in ranks:
        if vid == pos:
            rels.append(1.0)
        elif vid2cat.get(vid) == cat:
            rels.append(0.3)
        else:
            rels.append(0.0)
    rr = next((1.0 / (i + 1) for i, r in enumerate(rels) if r >= 1.0), 0.0)
    return {"rels": rels, "rr10": rr if any(rels[:10]) else rr,
            "rec5": 1.0 if any(rels[:5]) else 0.0,
            "rec20": 1.0 if any(rels) else 0.0}


def _aggregate(per_q: list[dict], latencies: list[float], tier: str) -> dict:
    n = len(per_q)
    dcg = lambda rels: sum(r / __import__("math").log2(i + 2)
                           for i, r in enumerate(rels[:10]))
    idcg = dcg(sorted([r for r in q["rels"] for r in [1.0] if q["rels"]], reverse=True))
    # proper nDCG: per-query ideal from its own rels
    ndcgs = []
    for q in per_q:
        ideal = sorted(q["rels"], reverse=True)
        d, i = dcg(q["rels"]), dcg(ideal)
        ndcgs.append(d / i if i > 0 else 1.0)
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    return {
        "n": n,
        "recall@5": round(sum(q["rec5"] for q in per_q) / n, 4),
        "recall@20": round(sum(q["rec20"] for q in per_q) / n, 4),
        "mrr@10": round(sum(q["rr10"] for q in per_q) / n, 4),
        "ndcg@10": round(statistics.mean(ndcgs), 4),
        "p95_latency_s": round(p95, 3),
    }


# ---------- rules ----------

def apply_rules(results_path: Path, out: Path):
    rs = {r["model"]: r for r in json.loads(results_path.read_text(encoding="utf-8"))}
    m = {k: v["metrics"]["decision"] for k, v in rs.items()}
    verdict = {"evaluated": sorted(rs), "rules": []}

    def rule(name, cond, detail):
        verdict["rules"].append({"rule": name, "pass": bool(cond), "detail": detail})

    if "minilm" in m and "bge-m3" in m:
        d_ndcg = m["bge-m3"]["ndcg@10"] - m["minilm"]["ndcg@10"]
        d_rec = m["bge-m3"]["recall@20"] - m["minilm"]["recall@20"]
        rule("R1 0.6B over baseline",
             d_ndcg >= 0.05 and d_rec >= 0.05,
             f"ΔnDCG={d_ndcg:+.4f} ΔRec@20={d_rec:+.4f} (need ≥+0.05 both)")
    if "bge-m3" in m and "qwen3-4b" in m:
        d_ndcg = m["qwen3-4b"]["ndcg@10"] - m["bge-m3"]["ndcg@10"]
        d_rec = m["qwen3-4b"]["recall@20"] - m["bge-m3"]["recall@20"]
        lat = rs["qwen3-4b"]["metrics"]["decision"]["p95_latency_s"]
        proj_h = 133000 / rs["qwen3-4b"]["chunks_per_s"] / 3600
        rule("R2 4B over 0.6B",
             d_ndcg >= 0.03 and d_rec >= 0.02 and lat <= 2.0 and proj_h <= 4,
             f"ΔnDCG={d_ndcg:+.4f} (≥+0.03) ΔRec@20={d_rec:+.4f} (≥+0.02) "
             f"p95={lat}s (≤2.0) proj={proj_h:.1f}h (≤4)")
    best = max(m, key=lambda k: (m[k]["ndcg@10"], m[k]["recall@20"]))
    rule("R3 reranker stage needed", m[best]["recall@20"] < 0.85,
         f"best {best} recall@20={m[best]['recall@20']}")
    verdict["winner_if_stopped_now"] = best
    out.write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    print(json.dumps(verdict, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus"); c.add_argument("--out", type=Path, default=BENCH_DIR / "corpus.json")
    q = sub.add_parser("queries"); q.add_argument("--corpus", type=Path, default=BENCH_DIR / "corpus.json")
    q.add_argument("--out", type=Path, default=BENCH_DIR / "decision_queries.json")
    s = sub.add_parser("smoke-sample"); s.add_argument("--corpus", type=Path, default=BENCH_DIR / "corpus.json")
    s.add_argument("--k", type=int, default=30)
    r = sub.add_parser("run"); r.add_argument("--model", choices=list(MODELS), required=True)
    r.add_argument("--corpus", type=Path, default=BENCH_DIR / "corpus.json")
    r.add_argument("--queries", type=Path, default=BENCH_DIR / "decision_queries.json")
    r.add_argument("--smoke", type=Path, default=BENCH_DIR / "smoke_queries.json")
    r.add_argument("--out", type=Path, default=BENCH_DIR / "results.json")
    v = sub.add_parser("rules"); v.add_argument("--results", type=Path, default=BENCH_DIR / "results.json")
    v.add_argument("--out", type=Path, default=BENCH_DIR / "verdict.json")
    args = ap.parse_args(argv)

    if args.cmd == "corpus":
        build_corpus(args.out)
    elif args.cmd == "queries":
        build_queries(args.corpus, args.out)
    elif args.cmd == "smoke-sample":
        smoke_sample(args.corpus, args.k)
    elif args.cmd == "run":
        run_model(args.model, args.corpus, args.queries,
                  args.smoke if args.smoke.exists() else None, args.out)
    elif args.cmd == "rules":
        apply_rules(args.results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
