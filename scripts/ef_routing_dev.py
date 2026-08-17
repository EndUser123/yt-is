#!/usr/bin/env python
"""A" sections 8-9: routing-development benchmark + policy comparison.

PRE-REGISTERED SELECTION RULE (fixed before running):
  A policy qualifies iff ALL of:
    (a) low-df exact stratum R@10 >= 0.95 (target prior, A" 12);
    (b) moderate-df exact stratum R@10 >= 0.85;
    (c) near-twin mutant false-pin rate == 0 (no non-literal pinning at R@1);
    (d) natural+semantic strata MRR@10 >= policy-A value - 0.01
        (non-regression; semantic path is policy-invariant by construction —
        this is the wiring check);
    (e) common-term containment@5 >= 0.90 (top-5 literally contain token).
  Among qualifiers: highest low-df R@1; tie -> policy order B, C, D.
  If none qualify: exit 1, STOP per A" 21.

Strata (dev-only data; the failed sealed set remains a regression suite):
  low-df ids (df<=100) / moderate-df ids (100<df<=1000) / common terms /
  short natural / semantic technical / near-twins / punctuation-heavy.
Receipt -> docs/evidence-fabric/benchmark/routing_dev_results.json
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import catalog, embedding, routing, server
from ef import projection_server as ps
from qdrant_client import models

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
FTS_DB = routing.FTS_DB
GEN = 1
N_PER_STRATUM = 30
COMMON_TERMS = ["YouTube", "Google", "Python", "ChatGPT", "iPhone", "NVIDIA",
                "JavaScript", "Windows"]
PUNCT_RE = re.compile(r"\b[a-zA-Z0-9]+(?:[./:_-][a-zA-Z0-9]+){2,}\b")
# unanchored identifier finder for scanning chunk TEXT (the routing regex
# is whole-query-anchored by design and cannot find tokens inside text)
IDENT_SCAN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+"
    r"|[a-z]+(?:_[a-z0-9]+)+"
    r"|[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*"
    r"|[A-Za-z]+-[0-9][A-Za-z0-9-]*"
    r"|[A-Z]{2,}[A-Za-z0-9]*"
    r"|[A-Za-z]+[0-9][A-Za-z0-9]*)\b")


def fts_conn():
    conn = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def df_of(conn, tok):
    m = routing.sanitize_fts_query(tok)
    if not m:
        return 0
    return conn.execute("select count(*) from chunks where chunks match ?",
                        (m,)).fetchone()[0]


def sample_ids(conn, lo, hi, punct=False):
    """identifier tokens with prod-scale df in (lo, hi], one per chunk."""
    out, seen = [], set()
    rows = conn.execute("select chunk_id, text from chunks order by chunk_id "
                        "limit 60000").fetchall()
    for r in rows:
        if len(out) >= N_PER_STRATUM:
            break
        rx = PUNCT_RE if punct else IDENT_SCAN
        for m in re.finditer(rx, r["text"]):
            tok = m.group(0)
            if punct and not (PUNCT_RE.fullmatch(tok)):
                continue
            if tok.lower() in seen or not (4 <= len(tok) <= 40):
                continue
            d = df_of(conn, tok)
            if lo < d <= hi:
                out.append({"query": tok, "positive_chunk": r["chunk_id"],
                            "df": d})
                seen.add(tok.lower())
                break
    return out


def build_strata():
    conn = fts_conn()
    # low-df: prefer the existing dev tokens with valid prod df
    dev = json.loads((BENCH / "identifier_dev_queries.json").read_text(encoding="utf-8"))
    lowdf = []
    for q in dev:
        d = df_of(conn, q["query"])
        if 0 < d <= 100:
            lowdf.append({"query": q["query"], "positive_chunk": q["positive_chunk"],
                          "df": d})
    # top up if the dev set shrank at prod scale
    lowdf.extend(sample_ids(conn, 0, 100)[:max(0, N_PER_STRATUM - len(lowdf))])
    lowdf = lowdf[:N_PER_STRATUM]
    moderate = sample_ids(conn, 100, 1000)
    punct = sample_ids(conn, 0, 5000, punct=True)
    common = [{"query": t, "df": df_of(conn, t)} for t in COMMON_TERMS]
    hold = json.loads((BENCH / "holdout_hand_queries.json").read_text(encoding="utf-8"))
    natural = [q for q in hold if q["stratum"] == "ytis_natural"
               and len(q["query"].split()) <= 5][:N_PER_STRATUM]
    semantic = [q for q in hold if q["stratum"] in ("wiki_evidence",
                                                    "review_arch")][:N_PER_STRATUM]
    twins = []
    for q in lowdf[:12]:
        tok = q["query"]
        mutate = tok[:-1] + ("3" if tok[-1] != "3" else "7")
        twins.append({"twin": tok, "mutant": mutate,
                      "positive_chunk": q["positive_chunk"],
                      "mutant_df": df_of(conn, mutate)})
    conn.close()
    strata = {"lowdf_exact": lowdf, "moderate_df_exact": moderate,
              "common_terms": common, "short_natural": natural,
              "semantic_technical": semantic, "near_twins": twins,
              "punct_heavy": punct}
    for k, v in strata.items():
        print(f"[rdev] stratum {k}: {len(v)}")
    (BENCH / "routing_dev_strata.json").write_text(
        json.dumps(strata, indent=1), encoding="utf-8")
    return strata


def main() -> int:
    strata = build_strata()
    enc = embedding.BGEM3Dual()
    qc = server.client()
    coll = ps.collection_name(GEN)

    # chunk->video map from catalog
    conn = catalog.connect()
    c2v = {r[0]: r[1] for r in conn.execute(
        "select c.chunk_id, e.video_id from chunk c join eu e "
        "on e.eu_id=c.eu_id").fetchall()}
    conn.close()

    def semantic_ids(text, flt=None):
        dense, lex = enc.encode([text])
        qv, lw = dense[0], lex[0]
        idxs = sorted(lw.keys())
        res = qc.query_points(
            collection_name=coll,
            prefetch=[models.Prefetch(query=[float(x) for x in qv],
                                      using=ps.DENSE_NAME, limit=100),
                      models.Prefetch(query=models.SparseVector(
                          indices=[int(t) for t in idxs],
                          values=[float(lw[t]) for t in idxs]),
                          using=ps.LEX_NAME, limit=100)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=100, query_filter=flt, with_payload=True)
        return [p.payload["chunk_id"] for p in res.points]

    def fts_ids(text, top=100):
        c = fts_conn()
        try:
            m = routing.sanitize_fts_query(text)
            if not m:
                return []
            return [r[0] for r in c.execute(
                "select chunk_id from chunks where chunks match ? "
                "order by bm25(chunks) limit ?", (m, top)).fetchall()]
        finally:
            c.close()

    results: dict[str, dict] = {}

    def eval_exact(stratum_items, policy):
        r1 = r10 = mrr = 0.0
        for it in stratum_items:
            route = routing.classify(it["query"])
            sem = semantic_ids(it["query"]) if route.intent == "exact" else None
            fts = fts_ids(it["query"])
            legs = [sem[:100] if sem is not None else [], fts]
            fused = routing.POLICIES[policy](legs, 10, exact_leg_idx=-1)
            rank = next((i + 1 for i, c in enumerate(fused)
                         if c == it["positive_chunk"]), None)
            r1 += 1.0 if rank == 1 else 0.0
            r10 += 1.0 if rank and rank <= 10 else 0.0
            mrr += (1.0 / rank) if rank and rank <= 10 else 0.0
        n = len(stratum_items)
        return {"r1": round(r1 / n, 4), "r10": round(r10 / n, 4),
                "mrr10": round(mrr / n, 4)} if n else {}

    def eval_semantic(items, policy):
        # semantic path is policy-invariant; run once per policy for the
        # wiring check anyway
        mrr = 0.0
        for q in items:
            ids = semantic_ids(q["query"])
            vids = [c2v.get(c) for c in ids[:10]]
            first = next((i + 1 for i, v in enumerate(vids)
                          if v == q["positive_video"]), None)
            mrr += (1.0 / first) if first and first <= 10 else 0.0
        return {"mrr10": round(mrr / len(items), 4)}

    def eval_common(items, policy):
        cont = 0.0
        for it in items:
            sem = semantic_ids(it["query"])
            fts = fts_ids(it["query"])
            legs = [sem[:100], fts]
            fused = routing.POLICIES[policy](legs, 5, exact_leg_idx=-1)
            c = fts_conn()
            hit = 0
            for cid in fused:
                row = c.execute("select 1 from chunks where chunk_id=? and "
                                "chunks match ?", (cid,
                                routing.sanitize_fts_query(it["query"]))).fetchone()
                hit += 1 if row else 0
            c.close()
            cont += hit / max(1, len(fused))
        return {"containment@5": round(cont / len(items), 4)}

    def eval_twins(items, policy):
        twin_found, false_pin = 0, 0
        for it in items:
            fts = fts_ids(it["twin"])
            sem = semantic_ids(it["twin"])
            fused = routing.POLICIES[policy]([sem[:100], fts], 10,
                                             exact_leg_idx=-1)
            if fused and fused[0] == it["positive_chunk"]:
                twin_found += 1
            mfts = fts_ids(it["mutant"])
            if not mfts:  # mutant has no literal hits
                msem = semantic_ids(it["mutant"])
                mfused = routing.POLICIES[policy]([msem[:100], mfts], 10,
                                                  exact_leg_idx=-1)
                if mfused and mfused[0] == it["positive_chunk"]:
                    false_pin += 1
        n = len(items)
        return {"twin_top1": round(twin_found / n, 4),
                "mutant_false_pin": false_pin}

    t0 = time.monotonic()
    for policy in routing.POLICIES:
        results[policy] = {
            "lowdf_exact": eval_exact(strata["lowdf_exact"], policy),
            "moderate_df_exact": eval_exact(strata["moderate_df_exact"], policy),
            "punct_heavy": eval_exact(strata["punct_heavy"], policy),
            "short_natural": eval_semantic(strata["short_natural"], policy),
            "semantic_technical": eval_semantic(strata["semantic_technical"], policy),
            "common_terms": eval_common(strata["common_terms"], policy),
            "near_twins": eval_twins(strata["near_twins"], policy),
        }
        print(f"[rdev] {policy}: lowdf={results[policy]['lowdf_exact']} "
              f"({time.monotonic()-t0:.0f}s)", flush=True)

    # pre-registered selection rule
    a_sem = (results["A_equal_rrf"]["short_natural"]["mrr10"]
             + results["A_equal_rrf"]["semantic_technical"]["mrr10"])
    verdict = []
    for pol, r in results.items():
        qual = (r["lowdf_exact"].get("r10", 0) >= 0.95
                and r["moderate_df_exact"].get("r10", 0) >= 0.85
                and r["near_twins"]["mutant_false_pin"] == 0
                and (r["short_natural"]["mrr10"]
                     + r["semantic_technical"]["mrr10"]) >= a_sem - 0.01
                and r["common_terms"]["containment@5"] >= 0.90)
        verdict.append({"policy": pol, "qualifies": qual,
                        "lowdf_r1": r["lowdf_exact"].get("r1", 0)})
    qualifiers = [v for v in verdict if v["qualifies"]]
    order = {"B_exact_only": 0, "C_containment_priority": 1, "D_weighted": 2,
             "A_equal_rrf": 3}
    winner = None
    if qualifiers:
        winner = sorted(qualifiers,
                        key=lambda v: (-v["lowdf_r1"], order[v["policy"]]))[0]["policy"]
    receipt = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "strata_counts": {k: len(v) for k, v in strata.items()},
               "results": results, "verdict": verdict, "winner": winner,
               "selection_rule": "pre-registered in module docstring"}
    (BENCH / "routing_dev_results.json").write_text(json.dumps(receipt, indent=1),
                                                    encoding="utf-8")
    print(f"[rdev] VERDICT: {json.dumps(verdict, indent=1)}")
    print(f"[rdev] WINNER: {winner}")
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
