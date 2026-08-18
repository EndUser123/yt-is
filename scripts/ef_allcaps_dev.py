#!/usr/bin/env python
"""I-gate rule 3: ALLCAPS ambiguity development experiment on EXPOSED data.

Set: conventional acronyms (VPN API GPU JSON HTTP) + opaque ALLCAPS
tokens mined from C1-C7 strata (pure ALLCAPS, no digit/underscore).
Policies: A ALLCAPS->identifier (pre-change) | B existing dual lane
(no pin) | C dual lane + df=1 singleton pin (current) | D stronger
literal weight (w=6) if C insufficient.
Opaque metrics: literal R@1, Recall@10. Conventional: judged any@3,
literal discoverability (any literal in top-10).
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import embedding, routing, server
from ef import projection_server as ps
from qdrant_client import models

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1
CONV = ["VPN", "API", "GPU", "JSON", "HTTP"]


def build_opaque():
    acro = re.compile(r"^[A-Z]{2,5}$")
    out, seen = [], set()
    for c in ("c1", "c2", "c3", "c4", "c5", "c6", "shard01"):
        p = BENCH / f"{'acceptance_' + c if c.startswith('c') else c}_auto.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for it in data.get("exact_df1", []) + data.get("exact_df2_100", []):
            tok = it["query"]
            if tok in seen or not acro.match(tok):
                continue
            seen.add(tok)
            out.append({"query": tok, "positive_chunk": it["positive_chunk"],
                        "df": it.get("df")})
    return out[:20]


def main() -> int:
    opaque = build_opaque()
    print(f"[ac] opaque ALLCAPS: {len(opaque)} "
          f"(df=1: {sum(1 for o in opaque if o['df']==1)})")
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def sem(text):
        d, l = enc.encode([text])
        qv, lw = d[0], l[0]
        idxs = sorted(lw.keys())
        r = qc.query_points(coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=100, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    def lit(text):
        m = routing.sanitize_fts_query(text)
        return [r[0] for r in fts.execute(
            "select chunk_id from chunks where chunks match ? "
            "order by bm25(chunks) limit 100", (m,)).fetchall()] if m else []

    def policy(policy, text, k=10):
        L, S = lit(text), sem(text)
        if policy == "A_identifier":
            ids = routing.fuse_identifier_priority(L, S, k)
            ls = set(L)
            return [(c, c in ls) for c in ids]
        if policy == "B_dual":
            # no-pin variant: emulate by bypassing singleton branch
            sem_rank = {c: i for i, c in enumerate(S)}
            sub = sorted(L, key=lambda c: (sem_rank.get(c, 1 << 30), L.index(c)))
            score, lset = {}, set(L)
            for rk, c in enumerate(sub):
                score[c] = score.get(c, 0.0) + 3.0 / (60 + rk + 1)
            for rk, c in enumerate(S):
                if c in lset:
                    continue
                score[c] = score.get(c, 0.0) + 1.0 / (60 + rk + 1)
            ranked = sorted(score.items(), key=lambda kv: -kv[1])[:k]
            return [(c, c in lset) for c, _ in ranked]
        if policy == "C_pin":
            return routing.fuse_ambiguous_subgroup(L, S, k)
        if policy == "D_heavy":
            L2, S2 = lit(text), sem(text)
            sem_rank = {c: i for i, c in enumerate(S2)}
            sub = sorted(L2, key=lambda c: (sem_rank.get(c, 1 << 30), L2.index(c)))
            score, lset = {}, set(L2)
            for rk, c in enumerate(sub):
                score[c] = score.get(c, 0.0) + 6.0 / (60 + rk + 1)
            for rk, c in enumerate(S2):
                if c in lset:
                    continue
                score[c] = score.get(c, 0.0) + 1.0 / (60 + rk + 1)
            ranked = sorted(score.items(), key=lambda kv: -kv[1])[:k]
            return [(c, c in lset) for c, _ in ranked]

    res = {}
    for pol in ("A_identifier", "B_dual", "C_pin", "D_heavy"):
        r1 = rec = 0.0
        for it in opaque:
            ranked = policy(pol, it["query"], 10)
            ids = [c for c, _ in ranked]
            rank = next((i + 1 for i, c in enumerate(ids)
                         if c == it["positive_chunk"]), None)
            r1 += 1.0 if rank == 1 else 0.0
            rec += 1.0 if rank else 0.0
        # conventional: literal discoverability in top-10 + any semantic hit
        disc = 0.0
        for t in CONV:
            ranked = policy(pol, t, 10)
            disc += 1.0 if any(l for _c, l in ranked) else 0.0
        res[pol] = {"opaque_r1": round(r1 / len(opaque), 4),
                    "opaque_rec@10": round(rec / len(opaque), 4),
                    "conv_literal_disc@10": round(disc / len(CONV), 4)}
        print(f"[ac] {pol}: {res[pol]}", flush=True)
    (BENCH / "allcaps_dev_results.json").write_text(
        json.dumps({"opaque_n": len(opaque), "conventional": CONV,
                    "results": res}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
