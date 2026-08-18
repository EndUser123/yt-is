#!/usr/bin/env python
"""G-gate items 2-4: comparison-query retrieval-policy development.

Dev set: 30 exposed comparison cases (C3/C4/C5). Policies:
  A production unchanged
  B normalized (comparison framing removed -> "X Y")
  C decomposition (original + X + Y subqueries, RRF union)
  D decomposition + compact "X Y" query (4-way RRF union)
  S sparse-heavier fusion (class-specific; original query)
X/Y parsed from the connective (vs/versus/compared/difference/or).
Emits a policy-anonymous judging listing (union of top-3 per policy);
metrics computed by ef_comparison_metrics.py after blind judgments.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import embedding, routing, server
from ef import projection_server as ps
from qdrant_client import models

GEN = 1
CONNECT = re.compile(
    r"\s+(?:vs\.?|versus|compared\s+(?:to|with)|or)\s+", re.I)


def parse_xy(query: str):
    m = CONNECT.search(query)
    if not m:
        # "difference between X and Y"
        m2 = re.match(r"difference\s+between\s+(.+?)\s+and\s+(.+)$", query, re.I)
        if m2:
            return m2.group(1).strip(), m2.group(2).strip()
        return None, None
    pre, post = query[:m.start()].strip(), query[m.end():].strip()
    # trim trailing context after Y (e.g. "for homelab") heuristically:
    # keep Y as-is; subqueries tolerate noise.
    return pre, post


def normalize(query: str):
    x, y = parse_xy(query)
    if x is None:
        return query
    return f"{x} {y}"


STOP = {"the", "a", "an", "for", "on", "in", "with", "and"}


def main() -> int:
    cases = json.load(open("P:/tmp/comp_dev.json", encoding="utf-8"))
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)

    def hybrid(text, k=10, dense_w=None):
        d, l = enc.encode([text])
        qv, lw = d[0], l[0]
        idxs = sorted(lw.keys())
        if dense_w:
            # class-specific sparse-heavier: two explicit legs, weighted
            r1 = qc.query_points(coll, query=[float(x) for x in qv],
                                 using=ps.DENSE_NAME, limit=100,
                                 with_payload=True).points
            r2 = qc.query_points(coll, query=models.SparseVector(
                indices=[int(t) for t in idxs],
                values=[float(lw[t]) for t in idxs]),
                using=ps.LEX_NAME, limit=100, with_payload=True).points
            score = {}
            for rk, p in enumerate(r1):
                score[p.payload["chunk_id"]] = score.get(p.payload["chunk_id"], 0) + 1.0 / (60 + rk + 1)
            for rk, p in enumerate(r2):
                score[p.payload["chunk_id"]] = score.get(p.payload["chunk_id"], 0) + dense_w / (60 + rk + 1)
            return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])][:k]
        r = qc.query_points(coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=k, with_payload=True)
        return [p.payload["chunk_id"] for p in r.points]

    def rrf_union(lists, k=10, weights=None):
        weights = weights or [1.0] * len(lists)
        score = {}
        for lst, w in zip(lists, weights):
            for rk, cid in enumerate(lst[:100]):
                score[cid] = score.get(cid, 0.0) + w / (60 + rk + 1)
        return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])][:k]

    rankings = {}   # (policy, query) -> [chunk_id]
    for case in cases:
        q = case["query"]
        x, y = parse_xy(q)
        rankings[("A", q)] = hybrid(q)
        rankings[("B", q)] = hybrid(normalize(q))
        if x and y:
            rankings[("C", q)] = rrf_union([hybrid(q), hybrid(x), hybrid(y)])
            rankings[("D", q)] = rrf_union([hybrid(q), hybrid(x), hybrid(y),
                                            hybrid(f"{x} {y}")])
        rankings[("S", q)] = hybrid(q, dense_w=2.0)  # sparse-heavier

    # judge listing: unique videos per query across policies (top-3 each)
    import sqlite3
    cat = sqlite3.connect(r"P:/.data/yt-is/ef/catalog.sqlite")
    c2v = {r[0]: r[1] for r in cat.execute(
        "select c.chunk_id, e.video_id from chunk c join eu e "
        "on e.eu_id=c.eu_id").fetchall()}
    v2t = {}
    for r in cat.execute("select distinct video_id, title from eu").fetchall():
        v2t.setdefault(r[0], r[1] or "(no title)")
    listing = []
    for case in cases:
        q = case["query"]
        seen = set()
        for pol in ("A", "B", "C", "D", "S"):
            for cid in rankings.get((pol, q), [])[:3]:
                v = c2v.get(cid)
                if (q, v) in seen:
                    continue
                seen.add((q, v))
                listing.append({"query": q, "video": v,
                                "title": v2t.get(v, "?")})
    out = Path(r"P:\packages\yt-is\docs\evidence-fabric\benchmark")
    (out / "comparison_dev_listing.json").write_text(
        json.dumps({"rankings": {f"{p}||{q}": v for (p, q), v in rankings.items()},
                    "listing": listing}, indent=1), encoding="utf-8")
    print(f"[cmp] {len(cases)} cases, {len(listing)} judge rows, "
          f"xy parsed for {sum(1 for c in cases if parse_xy(c['query'])[0])}/30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
