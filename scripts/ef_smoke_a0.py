#!/usr/bin/env python
"""Phase A-0 smoke: end-to-end Evidence Fabric plumbing on real transcripts.

Proves, with receipts, the full chain (amendment §5):
  authority transcript -> EvidenceUnit -> catalog -> chunking ->
  dense+sparse embedding -> Qdrant local-mode projection -> hybrid query ->
  EvidenceResult -> reopen original transcript at char span.

Non-throwaway: every module invoked here is a production contract.
Exit 0 only if every check passes. Receipt JSON lands next to this doc tree.

Usage:
  python scripts/ef_smoke_a0.py [--n 200] [--receipt-dir docs/evidence-fabric]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, catalog, chunking, embedding, projection, query  # noqa: E402
from ef.contracts import SmokeReceipt  # noqa: E402

PROBE_QUERIES = [
    "semiconductor supply chain concentration risk",
    "how to backtest a trading strategy without overfitting",
    "why neural networks generalize despite overparameterization",
    "the history of the Roman Republic's decline",
    "cooking technique for perfect steak searing",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200,
                    help="number of transcripts (100-500 per amendment)")
    ap.add_argument("--receipt-dir", type=Path,
                    default=REPO / "docs" / "evidence-fabric")
    args = ap.parse_args(argv)
    if not (50 <= args.n <= 1000):
        print(f"[a0] --n must be 50..1000 (amendment window 100-500), got {args.n}")
        return 2

    r = SmokeReceipt(ran_at=datetime.now(timezone.utc).isoformat())
    r.config = {"n": args.n, "dense_model": "all-MiniLM-L6-v2",
                "target_chars": 1100, "overlap_chars": 150,
                "bm25": "client-side lucene k1=1.2 b=0.75"}

    t0 = time.monotonic()

    # 1. Authority read (read-only joins over live DBs)
    rows = authority.list_eligible_transcripts(limit=args.n)
    missing_meta = [x["video_id"] for x in rows
                    if not x["channel_id"] or not x["title"]]
    r.counts["authority_rows"] = len(rows)
    r.check("authority_join_complete", not missing_meta,
            f"{len(missing_meta)} rows missing channel/title")
    if not rows:
        print("[a0] no eligible transcripts"); return 1
    if missing_meta:
        print(f"[a0] join gaps: {missing_meta[:5]}"); return 1

    # 2. EU contract + catalog
    eus = [authority.build_eu(x) for x in rows]
    conn = catalog.connect()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    smoke_build = f"smoke/a0-{run_id}"
    catalog.claim_smoke_build(conn, smoke_build)
    # smoke writes to its own namespace: generation 0, never a production gen
    written = catalog.store_eus(conn, eus, generation=0, build_id=smoke_build)
    r.counts["eus"] = len(eus)
    r.check("catalog_wrote_all", written == len(eus), f"{written}/{len(eus)}")

    # 3. Chunking (char-offset provenance)
    all_chunks = []
    for eu in eus:
        row = next(x for x in rows if x["video_id"] == eu.video_id)
        all_chunks.extend(chunking.chunk_transcript(eu.eu_id, row["transcript"]))
    catalog.store_chunks(conn, all_chunks)
    cc = catalog.counts(conn)
    r.counts["chunks"] = len(all_chunks)
    r.check("catalog_counts_match", cc["eu"] >= len(eus) and cc["chunk"] >= len(all_chunks),
            f"catalog eu={cc['eu']} chunk={cc['chunk']}")
    conn.close()

    # 4. Dense embedding (GPU if free) + sparse BM25
    dense = embedding.DenseEmbedder()
    r.config["device"] = dense.device
    t = time.monotonic(); dvecs = dense.encode([c.text for c in all_chunks])
    r.timings_s["dense_embed"] = time.monotonic() - t
    bm25 = embedding.BM25Encoder().fit([c.text for c in all_chunks])
    t = time.monotonic()
    svecs = [bm25.encode_document(c.text) for c in all_chunks]
    r.timings_s["sparse_encode"] = time.monotonic() - t
    r.check("dense_dim_consistent",
            all(len(v) == dense.dim for v in dvecs), f"dim={dense.dim}")
    r.check("sparse_nonempty",
            all(len(i) > 0 for i, _ in svecs), "")

    # 5. Projection upsert (fresh local-mode dir for a clean smoke)
    projection.drop_all()
    qc = projection.connect()
    projection.ensure_collection(qc, dense_dim=dense.dim, recreate=True)
    eu_meta = {eu.eu_id: {"video_id": eu.video_id, "channel_id": eu.channel_id,
                          "channel_title": eu.channel_title, "title": eu.title}
               for eu in eus}
    t = time.monotonic(); projection.upsert_chunks(qc, all_chunks, dvecs, svecs, eu_meta)
    r.timings_s["upsert"] = time.monotonic() - t
    n_proj = projection.count(qc)
    r.counts["projected_points"] = n_proj
    r.check("projection_count_match", n_proj == len(all_chunks),
            f"{n_proj} vs {len(all_chunks)}")

    # 6. Hybrid queries (RELEVANT primitive) + honest path tagging
    hq = query.HybridQuery(qc, bm25, dense.encode)
    for qtext in PROBE_QUERIES:
        t = time.monotonic()
        results = hq.relevant(qtext, limit=5)
        dt = time.monotonic() - t
        r.queries.append({
            "query": qtext, "latency_s": round(dt, 3), "n_results": len(results),
            "top": [{"title": x.title[:70], "channel": x.channel_title[:40],
                     "paths": list(x.retrieval_paths),
                     "score": round(x.score, 4),
                     "span": [x.start_char, x.end_char]} for x in results[:3]],
        })
    r.check("all_queries_returned",
            all(q["n_results"] > 0 for q in r.queries), "")

    # 7. Round-trip: top hit's chunk text == authority slice at its span
    top = hq.relevant(PROBE_QUERIES[0], limit=3)[0]
    reopened = authority.reopen_span(top.video_id, top.start_char, top.end_char)
    chunk_row = next(c for c in all_chunks if c.chunk_id == top.chunk_id)
    r.check("roundtrip_span_exact", reopened == chunk_row.text,
            f"chunk {top.chunk_id} span [{top.start_char},{top.end_char})")
    r.check("url_format", top.url == f"https://youtu.be/{top.video_id}", top.url)
    qc.close()

    r.timings_s["total"] = time.monotonic() - t0
    r.ok = all(c["ok"] for c in r.checks)

    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    out = args.receipt_dir / "a0_smoke_receipt.json"
    out.write_text(json.dumps(r.__dict__, indent=2), encoding="utf-8")
    print(f"[a0] receipt -> {out}")
    print(f"[a0] EU={r.counts['eus']} chunks={r.counts['chunks']} "
          f"points={r.counts['projected_points']} "
          f"dense={r.timings_s['dense_embed']:.1f}s on {dense.device} "
          f"total={r.timings_s['total']:.1f}s")
    for c in r.checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
