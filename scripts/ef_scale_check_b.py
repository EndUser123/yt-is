#!/usr/bin/env python
"""Phase B rule 5: at-scale Qdrant local-mode latency checkpoint.

Builds a THROWAWAY generation-0 index over the full eligible corpus with the
baseline model (MiniLM), then measures hybrid RRF query latency at scale.
NOT the canonical build (Phase C); collection is deleted afterwards unless
--keep. Exit 1 if p95 > 500 ms (preregistered rule 5).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, chunking, embedding, projection  # noqa: E402
from ef.contracts import EvidenceUnit  # noqa: E402
from qdrant_client import models  # noqa: E402

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="keep the gen0 index (demo purposes)")
    ap.add_argument("--receipt", type=Path,
                    default=REPO / "docs" / "evidence-fabric" / "benchmark"
                    / "scale_check.json")
    args = ap.parse_args(argv)

    receipt = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "model": "all-MiniLM-L6-v2", "timings_s": {}, "checks": []}

    t0 = time.monotonic()
    rows = authority.list_eligible_transcripts()   # full eligible corpus
    receipt["n_transcripts"] = len(rows)
    eus, chunks = [], []
    for row in rows:
        eu = authority.build_eu(row)
        eus.append(eu)
        chunks.extend(chunking.chunk_transcript(eu.eu_id, row["transcript"]))
    receipt["n_chunks"] = len(chunks)
    receipt["timings_s"]["read_and_chunk"] = time.monotonic() - t0
    print(f"[scale] {len(rows):,} transcripts -> {len(chunks):,} chunks "
          f"({receipt['timings_s']['read_and_chunk']:.0f}s)")

    texts = [c.text for c in chunks]
    dense = embedding.DenseEmbedder(batch_size=128)
    receipt["device"] = dense.device
    t0 = time.monotonic()
    dvecs = dense.encode(texts)
    receipt["timings_s"]["dense_embed"] = time.monotonic() - t0
    bm25 = embedding.BM25Encoder().fit(texts)
    t0 = time.monotonic()
    svecs = [bm25.encode_document(t) for t in texts]
    receipt["timings_s"]["sparse_encode"] = time.monotonic() - t0
    print(f"[scale] dense {receipt['timings_s']['dense_embed']:.0f}s "
          f"sparse {receipt['timings_s']['sparse_encode']:.0f}s")

    projection.drop_all()
    qc = projection.connect()
    projection.ensure_collection(qc, dense_dim=dense.dim, recreate=True)
    eu_meta = {eu.eu_id: {"video_id": eu.video_id, "channel_id": eu.channel_id,
                          "channel_title": eu.channel_title, "title": eu.title}
               for eu in eus}
    t0 = time.monotonic()
    for i in range(0, len(chunks), 1000):
        projection.upsert_chunks(qc, chunks[i:i + 1000],
                                 dvecs[i:i + 1000], svecs[i:i + 1000], eu_meta)
    receipt["timings_s"]["upsert"] = time.monotonic() - t0
    receipt["n_points"] = projection.count(qc)
    print(f"[scale] upsert {receipt['timings_s']['upsert']:.0f}s -> "
          f"{receipt['n_points']:,} points")

    # latency probes (exclude first warmup)
    lat = []
    for qi, q in enumerate(PROBE_QUERIES):
        dv = dense.encode([q])[0]
        si, sv = bm25.encode_query(q)
        qc.query_points(collection_name=projection.COLLECTION,
                        prefetch=[models.Prefetch(query=dv, using="dense", limit=50),
                                  models.Prefetch(query=models.SparseVector(indices=si, values=sv),
                                                  using="lex", limit=50)],
                        query=models.FusionQuery(fusion=models.Fusion.RRF),
                        limit=10, with_payload=True)
        t0 = time.monotonic()
        qc.query_points(collection_name=projection.COLLECTION,
                        prefetch=[models.Prefetch(query=dv, using="dense", limit=50),
                                  models.Prefetch(query=models.SparseVector(indices=si, values=sv),
                                                  using="lex", limit=50)],
                        query=models.FusionQuery(fusion=models.Fusion.RRF),
                        limit=10, with_payload=True)
        lat.append(time.monotonic() - t0)
    p50 = statistics.median(lat)
    p95 = sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]
    receipt["query_latency_s"] = {"p50": round(p50, 3), "p95": round(p95, 3),
                                  "n_probes": len(lat)}
    ok = p95 <= 0.5
    receipt["checks"].append({"name": "rule5_p95_le_500ms", "ok": ok,
                              "detail": f"p95={p95:.3f}s at {receipt['n_points']:,} pts"})
    print(f"[scale] query p50={p50:.3f}s p95={p95:.3f}s -> "
          f"{'PASS' if ok else 'FAIL'} (rule 5: <=0.5s)")

    if not args.keep:
        qc.close()
        projection.drop_all()
        print("[scale] gen0 index dropped (throwaway)")
    else:
        qc.close()

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
