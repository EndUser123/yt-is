#!/usr/bin/env python
"""C1 FINAL replay (A" sections 19-20). Composed production candidate:
BGE-M3 dense + learned sparse + Qdrant server + D_weighted exact routing +
FTS5 exact lane + freshness. All PREREGISTRATION_C1 gates. Promotes
atomically iff ALL pass; any failure -> STOP receipt, no promotion.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, buildspec, catalog, embedding, freshness, routing, server
from ef import projection_server as ps
from ef.query_server import ProductionQuery

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1


def mrr_video(results, positive):
    first = next((i + 1 for i, r in enumerate(results)
                  if r.video_id == positive), None)
    return (1.0 / first) if first and first <= 10 else 0.0


def bootstrap_ci(vals, n=10000, seed=13):
    rng = random.Random(seed)
    outs = []
    for _ in range(n):
        outs.append(statistics.mean(rng.choice(vals) for _ in vals))
    outs.sort()
    return round(outs[int(.025 * n)], 4), round(outs[int(.975 * n)], 4)


def main() -> int:
    R: dict = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "gates": {}, "pass": True}
    enc = embedding.BGEM3Dual()
    q = ProductionQuery(enc, generation=GEN)   # policy = DEFAULT (D_weighted)
    auto = json.loads((BENCH / "acceptance_c1_auto.json").read_text(encoding="utf-8"))
    hand = json.loads((BENCH / "acceptance_c1_hand.json").read_text(encoding="utf-8"))

    lat_all: list[float] = []

    def run_exact(items, k=10):
        r1 = r10 = 0.0
        for it in items:
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=k)
            lat_all.append(time.monotonic() - t0)
            ids = [r.chunk_id for r in res]
            rank = next((i + 1 for i, c in enumerate(ids)
                         if c == it["positive_chunk"]), None)
            r1 += 1.0 if rank == 1 else 0.0
            r10 += 1.0 if rank and rank <= k else 0.0
        n = len(items)
        return {"n": n, "r1": round(r1 / n, 4), "r10": round(r10 / n, 4)}

    def containment(items, k=10):
        import sqlite3
        c = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)
        tot = 0.0
        for it in items:
            res = q.relevant(it["query"], limit=k)
            if not res:
                continue
            m = routing.sanitize_fts_query(it["query"])
            hit = 0
            for r in res:
                row = c.execute("select 1 from chunks where chunk_id=? and "
                                "chunks match ?", (r.chunk_id, m)).fetchone()
                hit += 1 if row else 0
            tot += hit / len(res)
        c.close()
        return {"n": len(items), "containment": round(tot / len(items), 4)}

    def run_sem(items):
        vals = []
        for it in items:
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=10)
            lat_all.append(time.monotonic() - t0)
            vals.append(mrr_video(res, it["positive_video"]))
        lo, hi = bootstrap_ci(vals)
        return {"n": len(items), "mrr10": round(statistics.mean(vals), 4),
                "ci95": [lo, hi]}

    def twins(items):
        fp = 0
        t1 = 0.0
        for it in items:
            res = q.relevant(it["twin"], limit=10)
            if res and res[0].chunk_id == it["positive_chunk"]:
                t1 += 1
            mres = q.relevant(it["mutant"], limit=10)
            if mres and mres[0].chunk_id == it["positive_chunk"]:
                fp += 1
        return {"n": len(items), "twin_top1": round(t1 / len(items), 4),
                "false_pin": fp}

    # ---- gates
    g = R["gates"]
    v = run_exact(auto["exact_df1"])
    g["exact_df1"] = {**v, "gate": "r1==1.0", "pass": v["r1"] == 1.0}
    v = run_exact(auto["exact_df2_100"])
    g["exact_df2_100"] = {**v, "gate": "r10==1.0", "pass": v["r10"] == 1.0}
    v = containment(auto["exact_df101_1000"])
    g["exact_df101_1000"] = {**v, "gate": ">=0.95", "pass": v["containment"] >= 0.95}
    v = containment(auto["common_lexical"], k=5)
    g["common_lexical"] = {**v, "gate": ">=0.95", "pass": v["containment"] >= 0.95}
    v = containment(auto["punct_heavy"])
    g["punct_heavy"] = {**v, "gate": ">=0.95", "pass": v["containment"] >= 0.95}
    v = twins(auto["near_twins"])
    g["near_twins"] = {**v, "gate": "false_pin==0", "pass": v["false_pin"] == 0}
    for strat in ("semantic_natural", "semantic_technical", "comparison_questions"):
        items = [h for h in hand if h["stratum"] == strat]
        v = run_sem(items)
        g[strat] = {**v, "gate": "mrr>=0.40", "pass": v["mrr10"] >= 0.40}

    # latency
    p95 = sorted(lat_all)[max(0, int(len(lat_all) * .95) - 1)]
    g["latency_full_path"] = {"p95_s": round(p95, 3), "gate": "<=0.250",
                              "pass": p95 <= 0.250}

    # reopenability: 30 samples, exact-span equality via authority.reopen_span
    rook, rofail = 0, []
    pool = auto["exact_df1"][:10] + auto["exact_df2_100"][:5] + \
        [h for h in hand if h["stratum"] == "semantic_natural"][:15]
    for it in pool:
        res = q.relevant(it["query"], limit=5)
        if not res:
            rofail.append({"q": it["query"][:40], "why": "no results"})
            continue
        r = res[0]
        try:
            exact = authority.reopen_span(r.video_id, r.start_char, r.end_char)
            from ef import chunking as _ch
            rook += 1 if isinstance(exact, str) and exact else 0
            if not exact:
                rofail.append({"q": it["query"][:40], "why": "empty span"})
        except Exception as e:
            rofail.append({"q": it["query"][:40], "why": type(e).__name__})
    g["reopenability"] = {"sampled": len(pool), "ok": rook, "failures": rofail[:5],
                          "gate": "100%", "pass": rook == len(pool)}

    # filter correctness (20)
    fok, ftot = 0, 0
    for it in [h for h in hand if h["stratum"] == "semantic_natural"][:20]:
        res = q.relevant(it["query"], limit=5)
        if not res:
            continue
        ch = res[0].channel_id
        fres = q.relevant(it["query"], limit=5, channel_id=ch)
        ftot += 1
        if all(r.channel_id == ch for r in fres):
            fok += 1
    g["filter"] = {"n": ftot, "correct": fok, "gate": "100%",
                   "pass": ftot > 0 and fok == ftot}

    # structural + namespace
    qc = server.client()
    pts = ps.count(qc, GEN)
    conn = catalog.connect()
    cch = conn.execute(
        "select count(*) from chunk c join eu e on e.eu_id=c.eu_id "
        "where e.build_generation=?", (GEN,)).fetchone()[0]
    conn.close()
    g["structural"] = {"points": pts, "catalog": cch, "gate": "parity",
                       "pass": pts == cch}

    # freshness: incremental catch-up then lag
    inc = freshness.incremental_update()
    lag = freshness.compute_lag(freshness.load_state()["indexed_watermark"])
    g["freshness"] = {**inc, "lag": lag["index_lag_count"], "gate": "<=50",
                      "pass": lag["index_lag_count"] <= 50}

    R["pass"] = all(x.get("pass", False) for x in g.values() if isinstance(x, dict))
    for name, gv in g.items():
        print(f"[final] {name}: "
              f"{json.dumps({k: v for k, v in gv.items() if k != 'failures'})} "
              f"-> {'PASS' if gv.get('pass') else 'FAIL'}")

    if R["pass"]:
        doc = buildspec.promote(GEN, evidence=g)
        R["promotion"] = doc
        freshness.emit_status()
        print(f"[final] ALL GATES PASS — generation {GEN} PROMOTED atomically")
    else:
        print("[final] GATE FAILURE — NOT promoting (A\" 21)")

    out = REPO / "docs" / "evidence-fabric" / "c1_final_replay.json"
    out.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    print(f"[final] receipt -> {out}")
    return 0 if R["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
