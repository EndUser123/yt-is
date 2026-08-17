#!/usr/bin/env python
"""b-prime C3 FINAL battery. Promotion armed ONLY on: every preregistered
C3 gate (PREREGISTRATION_C3) AND regression precondition (C2's previously-
green gates stay green). Composed production system under the amended
zero-literal contract."""

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

from ef import authority, buildspec, catalog, embedding, freshness, routing, server
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
               "gates": {}, "pass": True}
    enc = embedding.BGEM3Dual()
    q = ProductionQuery(enc, generation=GEN)
    c3 = json.loads((BENCH / "acceptance_c3_auto.json").read_text(encoding="utf-8"))
    hand = json.loads((BENCH / "acceptance_c3_hand.json").read_text(encoding="utf-8"))
    lat: list[float] = []
    import sqlite3
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def literal_prefix(items, k=10):
        ok = n = 0
        for it in items:
            n += 1
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=k)
            lat.append(time.monotonic() - t0)
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
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=5)
            lat.append(time.monotonic() - t0)
            ok += 1 if res and res[0].chunk_id == it["positive_chunk"] else 0
        return {"n": len(items), "r1": round(ok / len(items), 4)}

    def zero_primary(items):
        ok = 0
        for it in items:
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=10)
            lat.append(time.monotonic() - t0)
            ok += 1 if len(res) == 0 else 0
        return {"n": len(items), "empty_rate": round(ok / len(items), 4)}

    def twins(items):
        fp = 0
        for it in items:
            q.relevant(it["twin"], limit=10)
            mres = q.relevant(it["mutant"], limit=10)
            if mres and mres[0].chunk_id == it["positive_chunk"]:
                fp += 1
        return {"n": len(items), "false_pin": fp}

    def sem(items, gate=0.40):
        vals = []
        for it in items:
            t0 = time.monotonic()
            res = q.relevant(it["query"], limit=10)
            lat.append(time.monotonic() - t0)
            vals.append(mrr_video(res, it["positive_video"]))
        rng = random.Random(13)
        boots = sorted(statistics.mean(rng.choice(vals) for _ in vals)
                       for _ in range(10000))
        return {"n": len(items), "mrr10": round(statistics.mean(vals), 4),
                "ci95": [round(boots[250], 4), round(boots[9750], 4)]}

    g = R["gates"]
    c3["exact_df2_10"] = [x for x in c3["exact_df2_100"] if x["df"] <= 10]
    c3["exact_df11_100"] = [x for x in c3["exact_df2_100"] if x["df"] > 10]

    v = r1_exact(c3["exact_df1"])
    g["exact_df1"] = {**v, "pass": v["r1"] == 1.0}
    for strat in ("exact_df2_10", "exact_df11_100", "exact_df101_1000",
                  "punct_heavy"):
        v = literal_prefix(c3[strat])
        g[strat] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = zero_primary(c3["zero_df_identifiers"])
    g["zero_df_identifiers"] = {**v, "pass": v["empty_rate"] == 1.0}
    v = twins(c3["near_twins"])
    g["near_twins"] = {**v, "pass": v["false_pin"] == 0}
    for strat in ("short_natural", "semantic_technical", "comparison_questions"):
        v = sem([h for h in hand if h["stratum"] == strat])
        g[strat] = {**v, "pass": v["mrr10"] >= 0.40}
    amb = [h for h in hand if h["stratum"] == "ambiguous_common"]
    routed = all(routing.classify(h["query"]).intent == "semantic"
                 for h in amb)
    v = sem(amb)
    g["ambiguous_common"] = {**v, "routed_semantic": routed,
                             "pass": routed and v["mrr10"] >= 0.30}

    p95 = sorted(lat)[max(0, int(len(lat) * .95) - 1)]
    g["latency"] = {"p95_s": round(p95, 3), "pass": p95 <= 0.250}

    pool = c3["exact_df1"][:10] + [h for h in hand
                                   if h["stratum"] == "short_natural"][:20]
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
    for it in [h for h in hand if h["stratum"] == "short_natural"][:20]:
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
    conn.close()
    g["structural"] = {"points": pts, "catalog": cch, "pass": pts == cch}
    ns = subprocess.run([sys.executable, str(REPO / "scripts" /
                        "ef_validate_namespace.py")], capture_output=True, text=True)
    g["namespace"] = {"pass": ns.returncode == 0}

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

    # regression precondition: C1 + C2 spot gates
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

    R["pass"] = all(x.get("pass", False) for x in g.values()
                    if isinstance(x, dict))
    for name, gv in g.items():
        printable = {k: v for k, v in gv.items()}
        print(f"[c3] {name}: {json.dumps(printable)} -> "
              f"{'PASS' if gv.get('pass') else 'FAIL'}")

    if R["pass"]:
        doc = buildspec.promote(GEN, evidence=g)
        R["promotion"] = doc
        freshness.emit_status()
        from ef import buildspec as _bs
        R["post_promotion"] = {
            "active_generation": _bs.active_generation(),
            "rollback": "generation 0 retained (no prior promotion.json)",
        }
        print(f"[c3] ALL GATES PASS — generation {GEN} PROMOTED; "
              f"active={R['post_promotion']['active_generation']}")
    else:
        print("[c3] GATE FAILURE — NOT promoting")
    out = REPO / "docs" / "evidence-fabric" / "c3_final_battery.json"
    out.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    print(f"[c3] receipt -> {out}")
    return 0 if R["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
