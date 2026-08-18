#!/usr/bin/env python
"""F-gate C5 FINAL battery, phase 1 (non-judged gates + listings).
Receipt-only; NO promotion capability. Phase 2 (ef_c5_finisher.py)
computes judged gates and writes the immutable verdict receipt.
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

from ef import authority, catalog, embedding, freshness, routing, server
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
    c5 = json.loads((BENCH / "shard02_auto.json").read_text(encoding="utf-8"))
    hand = json.loads((BENCH / "shard02_hand.json").read_text(encoding="utf-8"))
    lat: list[float] = []
    import sqlite3
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)

    def timed(text, **kw):
        t0 = time.monotonic()
        res = q.relevant(text, **kw)
        lat.append(time.monotonic() - t0)
        return res

    def literal_prefix(items, k=10):
        ok = n = 0
        for it in items:
            n += 1
            res = timed(it["query"], limit=k)
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
            res = timed(it["query"], limit=5)
            ok += 1 if res and res[0].chunk_id == it["positive_chunk"] else 0
        return {"n": len(items), "r1": round(ok / len(items), 4)}

    def weak_top3_literal(items):
        ok = 0
        pref = []
        for it in items:
            res = timed(it["query"], limit=10)
            m = routing.sanitize_fts_query(it["query"])
            lit3 = sum(1 for r in res[:3] if fts.execute(
                "select 1 from chunks where chunk_id=? and chunks match ?",
                (r.chunk_id, m)).fetchone())
            ok += 1 if lit3 >= 1 else 0
            pre = [r.chunk_id for r in res][:min(10, it.get("df") or 10)]
            hit = sum(1 for cid in pre if fts.execute(
                "select 1 from chunks where chunk_id=? and chunks match ?",
                (cid, m)).fetchone())
            pref.append(1.0 if pre and hit == len(pre) else 0.0)
        return {"n": len(items),
                "top3_literal": round(ok / len(items), 4),
                "literal_prefix": round(statistics.mean(pref), 4)}

    def zero_primary(items):
        ok = 0
        for it in items:
            res = timed(it["query"], limit=10)
            ok += 1 if len(res) == 0 else 0
        return {"n": len(items), "empty_rate": round(ok / len(items), 4)}

    def twins(items):
        fp = 0
        for it in items:
            timed(it["twin"], limit=10)
            mres = timed(it["mutant"], limit=10)
            if mres and mres[0].chunk_id == it["positive_chunk"]:
                fp += 1
        return {"n": len(items), "false_pin": fp}

    g = R["gates"]
    strong2 = [x for x in c5["exact_df2_100_strong"] if x["df"] <= 10]
    strong11 = [x for x in c5["exact_df2_100_strong"] if x["df"] > 10]

    v = r1_exact(c5["exact_df1_strong"])
    g["exact_df1_strong"] = {**v, "pass": v["r1"] == 1.0}
    v = literal_prefix(strong2)
    g["exact_df2_10_strong"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = literal_prefix(strong11)
    g["exact_df11_100_strong"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = literal_prefix(c5["exact_df101_1000"])
    g["exact_df101_1000"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = literal_prefix(c5["punct_heavy"])
    g["punct_heavy"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = weak_top3_literal(c5["weak_df1"])
    g["weak_df1"] = {**v, "pass": v["top3_literal"] == 1.0
                     and v["literal_prefix"] >= 0.95}
    v = zero_primary(c5["zero_df_identifiers"])
    g["zero_df_identifiers"] = {**v, "pass": v["empty_rate"] == 1.0}
    v = twins(c5["near_twins"])
    g["near_twins"] = {**v, "pass": v["false_pin"] == 0}
    # v1.1: ambiguous_allcaps behavioral gates
    acro = [x for x in c5.get("ambiguous_allcaps_df1", [])] if False else None
    import json as _j
    auto2 = _j.loads((BENCH / "shard02_auto.json").read_text(encoding="utf-8"))
    acaps = auto2.get("ambiguous_allcaps_df1", [])
    v = r1_exact(acaps)
    g["ambiguous_allcaps_df1"] = {**v, "pass": v["r1"] == 1.0}
    import sqlite3 as _s
    conv_disc = 0.0
    for t in ("VPN", "API", "GPU"):
        res = q.relevant(t, limit=10)
        m = routing.sanitize_fts_query(t)
        f = _s.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)
        disc = any(f.execute("select 1 from chunks where chunk_id=? and "
                             "chunks match ?", (r.chunk_id, m)).fetchone()
                   for r in res)
        f.close()
        conv_disc += 1.0 if disc else 0.0
    g["ambiguous_allcaps_conventional"] = {"disc@10": round(conv_disc / 3, 4),
                                          "pass": conv_disc == 3.0}
    weak_common = ["TikTok", "YouTube", "OpenAI"]
    routed = all(routing.classify(t).intent == "ambiguous"
                 for t in weak_common)
    g["weak_common_routed"] = {"terms": weak_common, "routed_ambiguous": routed,
                               "pass": routed}
    plain = [h for h in hand if h["stratum"] == "ambiguous_common"]
    routed_s = all(routing.classify(h["query"]).intent in ("semantic",
                                                           "ambiguous")
                   for h in plain)  # v1.1 behavioral gate
    g["ambiguous_plain_routed"] = {"routed_semantic": routed_s,
                                   "pass": routed_s}

    # judged strata: emit anonymous listings + authored-MRR info
    listings = {}
    authored = {}
    for strat in ("descriptive_semantic", "telegraphic", "technical",
                  "comparison"):
        items = [h for h in hand if h["stratum"] == strat]
        if strat == "comparison":
            # G-gate: authored MRR tripwire >= 0.25 on the comparison lane
            trip = statistics.mean(mrrs) >= 0.25
        rows = []
        mrrs = []
        for it in items:
            res = timed(it["query"], limit=3)
            full = q.relevant(it["query"], limit=10)
            mrrs.append(mrr_video(full, it["positive_video"]))
            for rk, r in enumerate(res[:3]):
                rows.append({"stratum": strat, "query": it["query"],
                             "rank": rk + 1, "video": r.video_id,
                             "title": r.title or "(no title)"})
        listings[strat] = rows
        authored[strat] = round(statistics.mean(mrrs), 4)
    # weak_common judged listing
    rows = []
    for t in weak_common:
        for rk, r in enumerate(q.relevant(t, limit=3)):
            rows.append({"stratum": "weak_common", "query": t,
                         "rank": rk + 1, "video": r.video_id,
                         "title": r.title or "(no title)"})
    listings["weak_common"] = rows
    (BENCH / "c8_judging_listing.json").write_text(
        json.dumps(listings, indent=1, ensure_ascii=False), encoding="utf-8")
    for strat in ("descriptive_semantic", "telegraphic", "technical", "comparison"):
        g[f"{strat}_judged"] = {"listing": len(listings[strat]),
                                "authored_mrr_info": authored[strat],
                                "pass": None, "note": "awaiting judgments"}
    g["weak_common_judged"] = {"listing": len(listings["weak_common"]),
                               "pass": None, "note": "awaiting judgments"}

    p95 = sorted(lat)[max(0, int(len(lat) * .95) - 1)]
    g["latency"] = {"p95_s": round(p95, 3), "pass": p95 <= 0.250}

    pool = c5["exact_df1_strong"][:10] + [h for h in hand
                                          if h["stratum"] == "telegraphic"][:20]
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
    for it in [h for h in hand if h["stratum"] == "telegraphic"][:20]:
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
                        "ef_validate_namespace.py")], capture_output=True,
                        text=True)
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

    # regression precondition (strong-only per F-gate)
    c1 = json.loads((BENCH / "acceptance_c1_auto.json").read_text(encoding="utf-8"))
    c1s = [x for x in c1["exact_df1"]
           if routing.classify(x["query"]).intent == "identifier"]
    v = r1_exact(c1s[:15])
    g["reg_c1_df1_strong"] = {**v, "pass": v["r1"] == 1.0}
    c2 = json.loads((BENCH / "acceptance_c2_auto.json").read_text(encoding="utf-8"))
    c2s = [x for x in c2["exact_df2_100"]
           if routing.classify(x["query"]).intent == "identifier"]
    v = literal_prefix(c2s)
    g["reg_c2_literal_strong"] = {**v, "pass": v["literal_prefix"] == 1.0}
    v = twins(c2["near_twins"])
    g["reg_c2_twins"] = {**v, "pass": v["false_pin"] == 0}
    c2hand = json.loads((BENCH / "acceptance_c2_hand.json").read_text(encoding="utf-8"))
    mrrs = [mrr_video(q.relevant(h["query"], limit=10), h["positive_video"])
            for h in c2hand if h["stratum"] == "semantic_natural"]
    g["reg_c2_semantic"] = {"mrr10": round(statistics.mean(mrrs), 4),
                            "pass": statistics.mean(mrrs) >= 0.35}

    R["status"] = "judging_pending"
    out = REPO / "docs" / "evidence-fabric" / "c8_battery_phase1.json"
    out.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    for name, gv in g.items():
        pr = gv.get("pass")
        print(f"[c8] {name}: "
              f"{json.dumps({k: v for k, v in gv.items() if k != 'note'})} -> "
              f"{'PASS' if pr else ('PENDING' if pr is None else 'FAIL')}")
    print(f"[c8] phase-1 receipt -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
