#!/usr/bin/env python
"""D-gate final battery: C2 acceptance gates (PREREGISTRATION_C2) + C1
regression + restart/reconnect robustness + freshness + latency.
Promotes generation 1 atomically iff ALL hard gates pass."""

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
    c2 = json.loads((BENCH / "acceptance_c2_auto.json").read_text(encoding="utf-8"))
    hand = json.loads((BENCH / "acceptance_c2_hand.json").read_text(encoding="utf-8"))
    c1 = json.loads((BENCH / "acceptance_c1_auto.json").read_text(encoding="utf-8"))
    lat: list[float] = []
    import sqlite3
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def literal_prefix(items, k=10, stratum=None):
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

    def twins(items):
        fp = 0
        for it in items:
            q.relevant(it["twin"], limit=10)
            mres = q.relevant(it["mutant"], limit=10)
            if mres and mres[0].chunk_id == it["positive_chunk"]:
                fp += 1
        return {"n": len(items), "false_pin": fp}

    def sem(items):
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
    # split df strata per prereg
    c2["exact_df2_10"] = [x for x in c2["exact_df2_100"] if x["df"] <= 10]
    c2["exact_df11_100"] = [x for x in c2["exact_df2_100"] if x["df"] > 10]

    v = r1_exact(c2["exact_df1"])
    g["exact_df1"] = {**v, "pass": v["r1"] == 1.0}
    for strat, gate in (("exact_df2_10", 1.0), ("exact_df11_100", 1.0),
                        ("exact_df101_1000", 1.0), ("punct_heavy", 1.0)):
        v = literal_prefix(c2[strat])
        g[strat] = {**v, "pass": v["literal_prefix"] == gate}
    v = twins(c2["near_twins"])
    g["near_twins"] = {**v, "pass": v["false_pin"] == 0}
    for strat in ("semantic_natural", "semantic_technical",
                  "comparison_questions"):
        items = [h for h in hand if h["stratum"] == strat]
        v = sem(items)
        g[strat] = {**v, "pass": v["mrr10"] >= 0.40}
    # common: informational
    com = literal_prefix(c2["common_lexical"], k=5)
    g["common_lexical_info"] = {**com, "pass": True,
                                "note": "informational (semantic route)"}

    # latency
    p95 = sorted(lat)[max(0, int(len(lat) * .95) - 1)]
    g["latency"] = {"p95_s": round(p95, 3), "pass": p95 <= 0.250}

    # reopen 30 exact-span
    pool = c2["exact_df1"][:10] + [h for h in hand
                                   if h["stratum"] == "semantic_natural"][:20]
    rook = 0
    for it in pool:
        res = q.relevant(it["query"], limit=5)
        if not res:
            continue
        r = res[0]
        try:
            exact = authority.reopen_span(r.video_id, r.start_char, r.end_char)
            rook += 1 if exact else 0
        except Exception:
            pass
    g["reopenability"] = {"sampled": len(pool), "ok": rook,
                          "pass": rook == len(pool)}

    # filter 20
    fok = ftot = 0
    for it in [h for h in hand if h["stratum"] == "semantic_natural"][:20]:
        res = q.relevant(it["query"], limit=5)
        if not res:
            continue
        ch = res[0].channel_id
        fres = q.relevant(it["query"], limit=5, channel_id=ch)
        ftot += 1
        fok += 1 if all(r.channel_id == ch for r in fres) else 0
    g["filter"] = {"n": ftot, "correct": fok, "pass": ftot > 0 and fok == ftot}

    # structural + namespace
    qc = server.client()
    pts = ps.count(qc, GEN)
    conn = catalog.connect()
    cch = conn.execute(
        "select count(*) from chunk c join eu e on e.eu_id=c.eu_id "
        "where e.build_generation=?", (GEN,)).fetchone()[0]
    conn.close()
    g["structural"] = {"points": pts, "catalog": cch, "pass": pts == cch}
    ns = subprocess.run([sys.executable, str(REPO / "scripts" /
                        "ef_validate_namespace.py")], capture_output=True,
                        text=True)
    g["namespace"] = {"pass": ns.returncode == 0,
                      "tail": ns.stdout.strip().splitlines()[-1] if ns.stdout else ""}

    # freshness catch-up
    inc = freshness.incremental_update()
    lag = freshness.compute_lag(freshness.load_state()["indexed_watermark"])
    g["freshness"] = {**inc, "lag": lag["index_lag_count"],
                      "pass": lag["index_lag_count"] <= 50}

    # qdrant restart/reconnect robustness (D-gate)
    import ef.server as srv
    st = srv.status()
    if st["running"]:
        subprocess.run(["taskkill", "/F", "/PID", str(st["pid"])],
                       capture_output=True)
        time.sleep(2)
    # cached client must recover on next use
    srv._CLIENT = qc           # stale cached client
    try:
        res = q.relevant("semiconductor supply chain", limit=3)
        ok_restart = len(res) > 0
    except Exception:
        ok_restart = False
    g["qdrant_restart_reconnect"] = {"pass": ok_restart}

    # C1 regression (informational + still-valid gates)
    v = r1_exact(c1["exact_df1"][:15])
    g["c1_regression_df1"] = {**v, "pass": v["r1"] == 1.0}
    c1hand = json.loads((BENCH / "acceptance_c1_hand.json").read_text(encoding="utf-8"))
    v = sem([h for h in c1hand if h["stratum"] == "semantic_natural"])
    g["c1_regression_semantic"] = {**v, "pass": v["mrr10"] >= 0.35,
                                   "note": "0.05 regression allowance (C1 passed 0.4162)"}

    R["pass"] = all(x.get("pass", False) for x in g.values()
                    if isinstance(x, dict))
    for name, gv in g.items():
        printable = {k: v for k, v in gv.items() if k not in ("note", "tail")}
        print(f"[c2] {name}: {json.dumps(printable)} -> "
              f"{'PASS' if gv.get('pass') else 'FAIL'}")

    if R["pass"]:
        doc = buildspec.promote(GEN, evidence=g)
        R["promotion"] = doc
        freshness.emit_status()
        print(f"[c2] ALL GATES PASS — generation {GEN} PROMOTED")
    else:
        print("[c2] GATE FAILURE — NOT promoting")
    out = REPO / "docs" / "evidence-fabric" / "c2_final_battery.json"
    out.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    print(f"[c2] receipt -> {out}")
    return 0 if R["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
