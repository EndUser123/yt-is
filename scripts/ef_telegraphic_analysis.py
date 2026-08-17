#!/usr/bin/env python
"""E-gate item 2: telegraphic-query development analysis on C3 data.

Prints query | config | rank | video | title for the union of top-3 of:
  prod (server RRF) / dense-only / sparse-only / sparse-heavy (w=2 client)
so relevance judgments (per query+video, config-independent) can be made
once, then computes MRR@10 (authored), judged Recall@10, judged nDCG@10
per config from the judgment file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import catalog, embedding, server
from ef import projection_server as ps
from qdrant_client import models

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1


def main() -> int:
    hand = json.loads((BENCH / "acceptance_c3_hand.json").read_text(encoding="utf-8"))
    tele = [h for h in hand if h["stratum"] in ("short_natural",
                                                "comparison_questions")]
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)
    conn = catalog.connect()
    c2v = {r[0]: r[1] for r in conn.execute(
        "select c.chunk_id, e.video_id from chunk c join eu e "
        "on e.eu_id=c.eu_id").fetchall()}
    conn.close()

    listing = []
    for q in tele:
        dense, lex = enc.encode([q["query"]])
        qv, lw = dense[0], lex[0]
        idxs = sorted(lw.keys())
        legs = {}
        legs["dense_only"] = [p.payload["chunk_id"] for p in qc.query_points(
            coll, query=[float(x) for x in qv], using=ps.DENSE_NAME,
            limit=10, with_payload=True).points]
        legs["sparse_only"] = [p.payload["chunk_id"] for p in qc.query_points(
            coll, query=models.SparseVector(
                indices=[int(t) for t in idxs],
                values=[float(lw[t]) for t in idxs]),
            using=ps.LEX_NAME, limit=10, with_payload=True).points]
        prod = [p.payload["chunk_id"] for p in qc.query_points(
            coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=10, with_payload=True).points]
        legs["prod_rrf"] = prod
        # sparse-heavy: client weighted RRF (dense w=1, sparse w=2)
        score = {}
        for name, w in (("dense_only", 1.0), ("sparse_only", 2.0)):
            for rk, cid in enumerate(legs[name][:100]):
                score[cid] = score.get(cid, 0.0) + w / (60 + rk + 1)
        legs["sparse_heavy"] = [c for c, _ in sorted(score.items(),
                                                     key=lambda kv: -kv[1])][:10]

        seen = set()
        for cfg in ("prod_rrf", "dense_only", "sparse_only", "sparse_heavy"):
            for rk, cid in enumerate(legs[cfg][:3]):
                if (cfg, cid) in seen or rk >= 3:
                    continue
                seen.add((cfg, cid))
                listing.append({"query": q["query"], "config": cfg,
                                "rank": rk + 1, "video": c2v.get(cid),
                                "authored_positive": q["positive_video"]})
    out = Path("P:/tmp/telegraphic_listing.json")
    out.write_text(json.dumps(listing, indent=1), encoding="utf-8")
    for it in listing:
        mark = " <AUTHORED-POS>" if it["video"] == it["authored_positive"] else ""
        print(f"{it['query'][:42]:<43}| {it['config'][:11]:<12}| r{it['rank']} | {it['video']}{mark}")
    print(f"# {len(listing)} listing rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
