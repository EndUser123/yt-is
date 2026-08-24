"""judge_bakeoff — judge-input emission + score summary for the graph bake-off.

Scoring runs via a spawned ZCode General Agent (GLM-5.3, operator directive
2026-08-22: fleet lanes + spawn agents only), not pi/third-party judges.

Flow:
  python judge_bakeoff.py emit     -> judge-input-<arm>.json per arm (items:
        qid, question, gold_fact, context) into P:/.data/scout/graph-bakeoff/
  [spawn agent: read those files, apply rubric, write judge-scores.json as
        {"<arm>/<qid>": 0|1|2, ...} for ALL items in every input file]
  python judge_bakeoff.py summarize -> merges scores + prints metrics table

Rubric (given to the agent verbatim):
  2 = the context contains the substance of the gold fact (wording may differ)
  1 = the context is on-topic for the question but lacks the specific fact
  0 = the fact is absent, or the context is empty/irrelevant
Empty contexts are scored 0 locally (emitted with context "" and auto-zeroed
at summarize time so the agent is not asked to judge empties).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path("P:/.data/scout/graph-bakeoff")
RES = BASE / "comparison-results.jsonl"
QUERIES = json.loads(
    Path("P:/packages/yt-is/scripts/graph_bakeoff/queries.json").read_text()
)["queries"]
GOLD = {q["id"]: q for q in QUERIES}
ARMS = ("fts_raw", "fts_kw", "cte_expand", "lightrag")
CTX_LIMIT = 6000


def emit():
    rows = [json.loads(l) for l in RES.read_text(encoding="utf-8").splitlines()]
    for arm in ARMS:
        items = []
        for r in rows:
            if r["arm"] != arm:
                continue
            q = GOLD[r["qid"]]
            ctx = r["context"] if not r.get("error") else ""
            items.append({"qid": r["qid"], "question": q["question"],
                          "gold_fact": q["gold_fact"],
                          "context": ctx[:CTX_LIMIT]})
        out = BASE / f"judge-input-{arm}.json"
        out.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"{out.name}: {len(items)} items")


def summarize():
    rows = [json.loads(l) for l in RES.read_text(encoding="utf-8").splitlines()]
    scores_raw = json.loads((BASE / "judge-scores.json").read_text())
    judged = []
    for r in rows:
        key = f"{r['arm']}/{r['qid']}"
        if not r["context"].strip() or r.get("error"):
            score = 0
        else:
            score = scores_raw.get(key)
            if score is None:
                print(f"WARN: no judge score for {key}")
        judged.append({**r, "judge_score": score})
    (BASE / "judged-results.jsonl").write_text(
        "\n".join(json.dumps(j, ensure_ascii=False) for j in judged),
        encoding="utf-8")

    print(f"{'arm':12s} {'gold@5':>7s} {'avg_judge':>9s} {'j2':>3s} {'j1':>3s} {'j0':>3s}")
    for arm in ARMS:
        ar = [r for r in judged if r["arm"] == arm]
        hit = sum(r["gold_hit"] for r in ar)
        js = [r["judge_score"] for r in ar if r["judge_score"] is not None]
        avg = sum(js) / len(js) if js else 0
        print(f"{arm:12s} {hit:>3d}/{len(ar):<3d} {avg:>9.2f} "
              f"{sum(1 for s in js if s == 2):>3d} "
              f"{sum(1 for s in js if s == 1):>3d} "
              f"{sum(1 for s in js if s == 0):>3d}")
    for cat in ("vocab", "hop", "direct"):
        line = f"  [{cat:6s}] "
        for arm in ARMS:
            ar = [r for r in judged if r["arm"] == arm and r["category"] == cat]
            js = [r["judge_score"] for r in ar if r["judge_score"] is not None]
            avg = sum(js) / len(js) if js else 0
            line += f"{arm}={avg:.2f}  "
        print(line)


if __name__ == "__main__":
    {"emit": emit, "summarize": summarize}[sys.argv[1]]()
