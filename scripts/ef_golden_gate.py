#!/usr/bin/env python
"""ef_golden_gate — behavioral drift gate for the Evidence Fabric (design doc §6, D3).

Two gates, explicitly named (red-team D3):
  DRIFT gate (this script): compares current top-N results for a fixed query
    set against a version-stamped baseline. Detects CHANGE — encoder-stack
    drift, qdrant upgrades, index corruption. Cannot detect wrongness.
  QUALITY gate (separate follow-on): sealed-shard relevance scoring.

Modes:
  --stamp  : run fixture queries, record baseline (requires quiescent index —
             runbook step 0: pipeline stopped, index_lag_count == 0)
  --check  : run fixture queries, compare vs baseline; exit 0 pass / 1 fail
Tolerances (starting values, calibrate after first deliberate encoder
upgrade per §9-Q1 resolution): result-key Jaccard >= 0.9 per query AND
mean-score delta <= 0.02 overall.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import buildspec, embedding, server
from ef.query_server import ProductionQuery

FIXTURE = REPO / "tests" / "fixtures" / "golden_queries.json"
BASELINE = Path("P:/.data/yt-is/ef/golden-baseline.json")
FAILURES = Path("P:/.data/yt-is/ef/golden-gate-failures.jsonl")
TOP_N = 8
JACCARD_FLOOR = 0.9
SCORE_DELTA_MAX = 0.02


def _encoder_versions() -> dict:
    import subprocess
    out = {}
    for pkg in ("sentence-transformers", "transformers", "torch", "qdrant-client"):
        r = subprocess.run([sys.executable, "-m", "pip", "show", pkg],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith("Version:"):
                out[pkg] = line.split(":", 1)[1].strip()
    return out


def _run_queries() -> dict:
    gen = buildspec.active_generation()
    enc = embedding.BGEM3Dual(device="cuda")
    q = ProductionQuery(enc, generation=gen)
    out = {}
    errors = []
    for spec in json.loads(FIXTURE.read_text(encoding="utf-8"))["queries"]:
        try:
            res = q.relevant(spec["q"], limit=TOP_N,
                             channel_id=spec.get("channel_id"),
                             exact=True if spec.get("lane") == "identifier" else None)
            out[spec["q"]] = [
                {"video_id": r.video_id, "chunk_id": r.chunk_id, "score": r.score}
                for r in res
            ]
        except Exception as e:
            # data integrity issues (missing authority rows etc.) are gate
            # FINDINGS, not crashes — record and continue
            errors.append({"query": spec["q"], "error": str(e)[:200]})
            out[spec["q"]] = []
    if errors:
        print(f"DATA INTEGRITY: {len(errors)} queries errored:", file=sys.stderr)
        for e in errors:
            print(f"  {e['query'][:40]}: {e['error'][:120]}", file=sys.stderr)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    st = server.status()
    if not st["running"]:
        server.start()

    results = _run_queries()

    if a.stamp:
        BASELINE.write_text(json.dumps({
            "stamped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "versions": _encoder_versions(),
            "jaccard_floor": JACCARD_FLOOR,
            "score_delta_max": SCORE_DELTA_MAX,
            "results": results,
        }, indent=1), encoding="utf-8")
        print(f"stamped {len(results)} queries at {time.strftime('%H:%M:%S')}")
        return 0

    if a.check:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        now_v, base_v = _encoder_versions(), base.get("versions", {})
        version_note = ("VERSIONS MATCH" if now_v == base_v else
                        f"VERSION DRIFT: {base_v} -> {now_v}")
        fails, jaccs = [], []
        for q, base_rows in base["results"].items():
            now_rows = results.get(q, [])
            j = _jaccard({(r["video_id"], r["chunk_id"]) for r in base_rows},
                         {(r["video_id"], r["chunk_id"]) for r in now_rows})
            jaccs.append(j)
            if j < JACCARD_FLOOR:
                fails.append({"query": q, "jaccard": round(j, 4)})
        mean_base = sum(r["score"] for rows in base["results"].values() for r in rows) / \
            max(1, sum(len(v) for v in base["results"].values()))
        mean_now = sum(r["score"] for rows in results.values() for r in rows) / \
            max(1, sum(len(v) for v in results.values()))
        score_delta = abs(mean_now - mean_base)
        ok = not fails and score_delta <= SCORE_DELTA_MAX
        print(f"{version_note}; min-Jaccard {min(jaccs):.3f}; "
              f"score-delta {score_delta:.4f} (max {SCORE_DELTA_MAX}) -> "
              f"{'PASS' if ok else 'FAIL'}")
        if fails:
            print("failing queries:", json.dumps(fails[:5]))
            with FAILURES.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "versions_before": base_v, "versions_after": now_v,
                    "score_delta": round(score_delta, 5), "fails": fails,
                }) + "\n")
        return 0 if ok else 1

    print("use --stamp or --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
