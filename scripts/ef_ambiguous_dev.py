#!/usr/bin/env python
"""F-gate item 4: weak-token development experiment.

Dev set: human/common weak tokens (+ orthographic variants) and opaque
weak identifiers mined from C1-C4 exact strata (weak-shaped only).
Policies: A weak->semantic | B weak->identifier | C boost | D subgroup.
Metrics: opaque -> literal Recall@10 / MRR@10; human -> judged nDCG@10 /
Recall@10 (judge labels union of top-10 across policies, blind to policy).
PRE-REGISTERED SELECTION (fixed before running):
  qualify iff opaque literal Recall@10 >= 0.95 AND human judged
  Recall@10 >= 0.50 AND no policy violates the unique-literal invariant
  (for opaque df<=2 tokens, the literal must appear in top-3 for >= 0.95
  of them). Winner: highest (opaque MRR@10 + human nDCG@10); tie -> C.
Receipt -> benchmark/ambiguous_dev_results.json (+ judge listing file).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import embedding, routing, server
from ef import projection_server as ps
from qdrant_client import models

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1

HUMAN = ["TikTok", "Tik Tok", "YouTube", "You Tube", "Google", "Python",
         "GitHub", "Git Hub", "OpenAI"]


def build_dev_set():
    import re
    weak_re = re.compile(r"^[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*$")
    opaque = []
    seen = set()
    for c in ("c1", "c2", "c3", "c4"):
        p = BENCH / f"acceptance_{c}_auto.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for it in data.get("exact_df1", []) + data.get("exact_df2_100", []):
            tok = it["query"]
            if tok in seen or not weak_re.match(tok):
                continue
            seen.add(tok)
            opaque.append({"query": tok, "positive_chunk": it["positive_chunk"],
                           "df": it.get("df")})
    opaque = [o for o in opaque if o["df"] and o["df"] <= 100][:24]
    return opaque


def main() -> int:
    opaque = build_dev_set()
    print(f"[amb] opaque weak identifiers: {len(opaque)}")
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)
    conn = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def semantic_leg(text):
        dense, lex = enc.encode([text])
        qv, lw = dense[0], lex[0]
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

    def literal_leg(text):
        m = routing.sanitize_fts_query(text)
        if not m:
            return []
        return [r[0] for r in conn.execute(
            "select chunk_id from chunks where chunks match ? "
            "order by bm25(chunks) limit 100", (m,)).fetchall()]

    def policy_results(policy, text, k=10):
        """Returns list[(chunk_id, is_literal)]"""
        if policy == "A_weak_semantic":
            return [(c, False) for c in semantic_leg(text)[:k]]
        lit = literal_leg(text)
        sem = semantic_leg(text)
        if policy == "B_weak_identifier":
            ids = routing.fuse_identifier_priority(lit, sem, k)
            ls = set(lit)
            return [(c, c in ls) for c in ids]
        f = routing.AMBIGUOUS_POLICIES[policy]
        return f(lit, sem, k)

    # ---- opaque metrics (literal gold known)
    results = {}
    for pol in routing.AMBIGUOUS_POLICIES:
        rec = mrr = uniq = 0.0
        n_uniq = 0
        for it in opaque:
            ranked = policy_results(pol, it["query"], 10)
            ids = [c for c, _ in ranked]
            rank = next((i + 1 for i, c in enumerate(ids)
                         if c == it["positive_chunk"]), None)
            rec += 1.0 if rank else 0.0
            mrr += (1.0 / rank) if rank else 0.0
            if it["df"] <= 2:
                n_uniq += 1
                any_lit_top3 = any(l for c, l in ranked[:3])
                uniq += 1.0 if any_lit_top3 else 0.0
        results[pol] = {"opaque_recall@10": round(rec / len(opaque), 4),
                        "opaque_mrr@10": round(mrr / len(opaque), 4),
                        "unique_literal_top3": round(uniq / n_uniq, 4)}
        print(f"[amb] {pol}: {results[pol]}", flush=True)

    # ---- human tokens: emit union-of-top10 judging listing (policy-blind)
    listing = []
    i = 0
    for term in HUMAN:
        union = {}
        for pol in routing.AMBIGUOUS_POLICIES:
            for c, _l in policy_results(pol, term, 10):
                union.setdefault(c, None)
        for c in union:
            row = conn.execute("select chunk_id from chunks where chunk_id=?",
                               (c,)).fetchone()
            listing.append({"anon": f"H{i:03d}", "term": term, "chunk": c})
            i += 1
    (BENCH / "ambiguous_human_listing.json").write_text(
        json.dumps(listing, indent=1), encoding="utf-8")
    print(f"[amb] human listing: {len(listing)} rows -> "
          f"ambiguous_human_listing.json (awaiting blind judgments)")
    conn.close()

    receipt = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "opaque_n": len(opaque), "human_terms": HUMAN,
               "policies_opaque": results,
               "selection_rule": "pre-registered in docstring"}
    (BENCH / "ambiguous_dev_results.json").write_text(
        json.dumps(receipt, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
