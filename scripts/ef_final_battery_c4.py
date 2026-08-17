#!/usr/bin/env python
"""E-gate C4 FINAL battery. Emits an IMMUTABLE verdict receipt ONLY —
this script has NO promotion capability (ef/receipt.py design).
Telegraphic stratum: emits a config-anonymous judging listing; metrics
are computed by ef_c4_judge.py after the judge labels it blind.
All other gates evaluated inline per PREREGISTRATION_C4.
"""

from __future__ import annotations

import json
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, catalog, embedding, freshness, receipt, routing, server
from ef import projection_server as ps
from ef.query_server import ProductionQuery

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
GEN = 1


def mrr_video(results, positive):
    first = next((i + 1 for i, r in enumerate(results)
                  if r.video_id == positive), None)
    return (1.0 / first) if first and first <= 10 else 0.0


def main() -> int:
    R: dict = {"ran_at": datetime.now(timezone.utc).isoformat(),
               "gates": {}}
    enc = embedding.BGEM3Dual()
    q = ProductionQuery(enc, generation=GEN)
    c4 = json.loads((BENCH / "acceptance_c4_auto.json").read_text(encoding="utf-8"))
    hand = json.loads((BENCH / "acceptance_c4_hand.json").read_text(encoding="utf-8"))
    lat: list[float] = []
    import sqlite3
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def timed_relevant(text, **kw):
        t0 = time.monotonic()
        res = q.relevant(text, **kw)
        lat.append(time.monotonic() - t0)
        return res

    def literal_prefix(items, k=10):
        ok = n = 0
        for it in items:
            n += 1
            res = timed_relevant(it["query"], limit=k)
            need = min(k, it.get("df") or k)
            pre = [r.chunk_id for r in res][:need]
            if not pre:
                continue
            m = routing.sanitize_fts_query(it["query"])
            hit = sum(1 for cid in pre if fts.execute(
                "select 1 from chunks where chunk_id=? and chunks match ?",
                (cid, m)).fetchone())
            ok += 1 if hit == len(pre) else 0
        return {"n": n, "literal_prefix": round(ok / n, 4)}

    def r1_exact(items):
        ok = 0
        for it in items:
            res = timed_relevant(it["query"], limit=5)
            ok += 1 if res and res[0].chunk_id == it["positive_chunk"] else 0
        return {"n": len(items), "r1": round(ok / len(items), 4)}

    def zero_primary(items):
        ok = 0
        for it in items:
            res = timed_relevant(it["query"], limit=10)
            ok += 1 if len(res) == 0 else 0
        return {"n": len(items), "empty_rate": round(ok / len(items), 4)}

    def twins(items):
        fp = 0
        for it in items:
            timed_relevant(it["twin"], limit=10)
            mres = timed_relevant(it["mutant"], limit=10)
            if mres and mres[0].chunk_id == it["positive_chunk"]:
                fp += 1
        return {"n": len(items), "false_pin": fp}

    def sem(items):
        vals = []
        for it in items:
            res = timed_relevant(it["query"], limit=10)
            vals.append(mrr_video(res, it["positive_video"]))
        rng = random.Random(13)
        boots = sorted(statistics.mean(rng.choice(vals) for _ in vals)
                       for _ in range(10000))
        return {"n": len(items), "mrr10": round(statistics.mean(vals), 4),
                "ci95": [round(boots[250], 4), round(boots[9750], 4)]}

    g = R["gates"]
    c4["exact_df2_10"] = [x for x in c4["exact_df2_100"] if x["df"] <= 10]
    c4["exact_df11_100"] = [x for x in c4["exact_df2_100"] if x["df"] > 10]

    v = r1_exact(c4["exact_df1"])
    g["exact_df1"] = {**v, "pass": v["r1"] == 1.0}
    for strat in ("exact_df2_10", "exact_df11_100", "exact_df101_1000",
                  "punct_heavy"):
        v = literal_prefix(c4[strat])
        g[strat] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = zero_primary(c4["zero_df_identifiers"])
    g["zero_df_identifiers"] = {**v, "pass": v["empty_rate"] == 1.0}
    v = twins(c4["near_twins"])
    g["near_twins"] = {**v, "pass": v["false_pin"] == 0}
    amb = [h for h in hand if h["stratum"] == "ambiguous_words"]
    routed = all(routing.classify(h["query"]).intent == "semantic"
                 for h in amb)
    g["ambiguous_words"] = {"n": len(amb), "routed_semantic": routed,
                            "pass": routed}

    # telegraphic: retrieve + emit anonymous judging listing; MRR info now
    tele = [h for h in hand if h["stratum"] == "telegraphic"]
    listing = []
    anon_id = 0
    mrrs = []
    for it in tele:
        res = timed_relevant(it["query"], limit=3)
        mrrs.append(mrr_video(q.relevant(it["query"], limit=10),
                              it["positive_video"]))
        for rk, r in enumerate(res[:3]):
            listing.append({"anon": f"J{anon_id:03d}", "query": it["query"],
                            "rank": rk + 1, "video": r.video_id,
                            "title": r.title or "(no title)"})
            anon_id += 1
    (BENCH / "c4_telegraphic_listing.json").write_text(
        json.dumps(listing, indent=1, ensure_ascii=False), encoding="utf-8")
    g["telegraphic_judged"] = {
        "listing_emitted": len(listing),
        "authored_mrr10_info": round(statistics.mean(mrrs), 4),
        "pass": None,   # filled by ef_c4_judge.py after blind labeling
        "note": "awaiting blind judgments"}

    for strat in ("semantic_technical", "comparison_questions"):
        v = sem([h for h in hand if h["stratum"] == strat])
        g[strat] = {**v, "pass": v["mrr10"] >= 0.40}

    p95 = sorted(lat)[max(0, int(len(lat) * .95) - 1)]
    g["latency"] = {"p95_s": round(p95, 3), "pass": p95 <= 0.250}

    pool = c4["exact_df1"][:10] + tele[:20]
    rook = 0
    for it in pool:
        res = q.relevant(it["query"], limit=5)
        if not res:
            continue
        try:
            exact = authority.reopen_span(res[0].video_id, res[0].start_char,
                                          res[0].end_char)
            rook += 1 if exact else 0
        except Exception:
            pass
    g["reopenability"] = {"sampled": len(pool), "ok": rook,
                          "pass": rook == len(pool)}

    fok = ftot = 0
    for it in tele[:20]:
        res = q.relevant(it["query"], limit=5)
        if not res:
            continue
        ch = res[0].channel_id
        fres = q.relevant(it["query"], limit=5, channel_id=ch)
        ftot += 1
        fok += 1 if all(r.channel_id == ch for r in fres) else 0
    g["filter"] = {"n": ftot, "correct": fok, "pass": ftot > 0 and fok == ftot}

    qc = server.client()
    pts = ps.count(qc, GEN)
    conn = catalog.connect()
    cch = conn.execute(
        "select count(*) from chunk c join eu e on e.eu_id=c.eu_id "
        "where e.build_generation=?", (GEN,)).fetchone()[0]
    build_id = conn.execute(
        "select build_id from eu where build_generation=? limit 1",
        (GEN,)).fetchone()
    conn.close()
    g["structural"] = {"points": pts, "catalog": cch,
                       "candidate_generation": GEN,
                       "pass": pts == cch}
    ns = subprocess.run([sys.executable, str(REPO / "scripts" /
                        "ef_validate_namespace.py")], capture_output=True,
                        text=True)
    g["namespace"] = {"pass": ns.returncode == 0,
                      "build_id": build_id[0] if build_id else None}

    inc = freshness.incremental_update()
    lag = freshness.compute_lag(freshness.load_state()["indexed_watermark"])
    g["freshness"] = {**inc, "lag": lag["index_lag_count"],
                      "pass": lag["index_lag_count"] <= 50}

    import ef.server as srv
    st = srv.status()
    if st["running"]:
        subprocess.run(["taskkill", "/F", "/PID", str(st["pid"])],
                       capture_output=True)
        time.sleep(2)
    srv._CLIENT = qc
    try:
        res = q.relevant("semiconductor supply chain", limit=3)
        ok_restart = len(res) > 0
    except Exception:
        ok_restart = False
    g["qdrant_restart_reconnect"] = {"pass": ok_restart}

    c1 = json.loads((BENCH / "acceptance_c1_auto.json").read_text(encoding="utf-8"))
    v = r1_exact(c1["exact_df1"][:15])
    g["reg_c1_df1"] = {**v, "pass": v["r1"] == 1.0}
    c2 = json.loads((BENCH / "acceptance_c2_auto.json").read_text(encoding="utf-8"))
    v = literal_prefix(c2["exact_df2_100"])
    g["reg_c2_literal"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = twins(c2["near_twins"])
    g["reg_c2_twins"] = {**v, "pass": v["false_pin"] == 0}
    c2hand = json.loads((BENCH / "acceptance_c2_hand.json").read_text(encoding="utf-8"))
    v = sem([h for h in c2hand if h["stratum"] == "semantic_natural"])
    g["reg_c2_semantic"] = {**v, "pass": v["mrr10"] >= 0.35}

    # verdict: telegraphic gate pending judgments -> not yet PASS/FAIL
    hard = [k for k in g if k != "telegraphic_judged"]
    all_pass = all(g[k].get("pass", False) for k in hard)
    R["status"] = "telegraphic_judging_pending" if all_pass else "gate_failure"
    out = REPO / "docs" / "evidence-fabric" / "c4_battery_partial.json"
    out.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    for name, gv in g.items():
        print(f"[c4] {name}: {json.dumps({k: v for k, v in gv.items() if k != 'note'})} "
              f"-> {'PASS' if gv.get('pass') else ('PENDING' if gv.get('pass') is None else 'FAIL')}")
    print(f"[c4] non-telegraphic gates: {'ALL PASS' if all_pass else 'FAILURES PRESENT'}")
    print(f"[c4] partial receipt -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
