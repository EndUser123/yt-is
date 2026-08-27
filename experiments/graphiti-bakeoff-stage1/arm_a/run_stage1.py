"""Arm A stage-1 run: ingest fixture, execute X1..X14, measure performance,
write results.json + code-surface accounting. HARNESS code (not semantic).

Usage:  python arm_a/run_stage1.py
Stdout: compact PASS/FAIL table; artifacts land next to this script.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))   # worktree root for ef.*
sys.path.insert(0, str(HERE))                 # arm_a package

from cases import FAILURE_CLASS as CASE_FAILURE_CLASS  # noqa: E402

CASES = {
    "X1": "as-of 2026-01-15: E2 launch_year == 2031 ASSERTED-ONLY",
    "X2": "as-of 2026-01-19(now incl 18th): partners_with SUPPORTED emerged 01-18",
    "X3": "now: launch_year 2033 SUPPORTED; 2031 historical < 01-20",
    "X4": "now: budget 2M and 5M coexist, each single-source",
    "X5": "alias resolution incl 'Alphard Minor' distinct from Alphard",
    "X6": "bridge T1-T2 via B1, aggregate-supported, emerged 01-24, novel mediator",
    "X7": "remove EU08 -> partners downgrades to ASSERTED-ONLY; no over-cascade",
    "X8": "remove EU11 -> bridge disappears; B1 enables T1 remains",
    "X9": "now: P1 leads E1 SUPPORTED emerged 2026-02-02",
    "X10": "as-of 01-26 inclusive: leads ASSERTED-ONLY (EU15 must not leak)",
    "X11": "replay to 4 checkpoints incl leakage probe at each",
    "X12": "provenance exactness for two claims",
    "X13": "why_surfaced completeness for the bridge answer",
    "X14": "concurrency: stale generation write must fail, replay stays consistent",
}


def _load_fixture():
    fixture_path = HERE.parent / "fixture.json"
    raw = fixture_path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _perf(fn, repeats=3):
    """Mean of N runs with time.perf_counter (ms)."""
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {"mean_ms": round(statistics.fmean(samples), 3),
            "samples_ms": [round(s, 3) for s in samples]}


def _loc(path: Path) -> int:
    """Non-blank, non-comment-only physical lines."""
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def main():
    from store import ArmAStore

    fixture, fixture_sha = _load_fixture()
    db_path = HERE / "arm_a.sqlite"
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    # -- ingest (main DB) ---------------------------------------------------
    store = ArmAStore(db_path)
    t0 = time.perf_counter()
    ingest_summary = store.ingest_fixture(fixture)
    ingest_main_ms = (time.perf_counter() - t0) * 1000.0
    ingest_loads_ms = [ingest_main_ms]
    for _ in range(2):  # median-of-3 across fresh loads
        scratch = ArmAStore(None)
        ts = time.perf_counter()
        scratch.ingest_fixture(fixture)
        ingest_loads_ms.append((time.perf_counter() - ts) * 1000.0)
        scratch.close()

    # -- per-query latency (mean of 3), representative query types ----------
    per_query = {}
    e1c = store.cid_of("E1")
    del e1c  # cid indirection handled inside the API via entity ids
    per_query["as_of_now_attribute"] = _perf(
        lambda: store.as_of_query(subject="E2", predicate="launch_year"))
    per_query["as_of_historical"] = _perf(
        lambda: store.as_of_query(subject="P1", predicate="leads",
                                  as_of="2026-01-26T00:00:00Z"))
    per_query["provenance"] = _perf(
        lambda: store.provenance("E1", "partners_with", object_="O1"))
    per_query["bridge_discovery"] = _perf(lambda: store.find_bridges("T1", "T2"))
    per_query["alias_resolution"] = _perf(
        lambda: store.resolve_name("ALPHARD initiative"))

    # -- replay latency (4 checkpoints) --------------------------------------
    replay_latency = {}
    for cp in ["2026-01-05T00:00:00Z", "2026-01-18T00:00:00Z",
               "2026-01-20T00:00:00Z", "2026-02-02T00:00:00Z"]:
        t0 = time.perf_counter()
        rep, meta = store.replay(cp)
        dt = (time.perf_counter() - t0) * 1000.0
        replay_latency[cp] = round(dt, 3)
        rep.close()

    # -- case execution -------------------------------------------------------
    import cases as C

    results = []
    dispatch = {
        "X7": lambda: C.case_x7(store, fixture),
        "X8": lambda: C.case_x8(store, fixture),
        "X14": lambda: C.case_x14(store, fixture),
    }
    read_only = {
        k: (lambda k=k: getattr(C, f"case_{k.lower()}")(store))
        for k in ["X1", "X2", "X3", "X4", "X5", "X6", "X9", "X10", "X11", "X12", "X13"]
    }
    all_calls = {**read_only, **dispatch}
    for case_id in sorted(all_calls):
        passed, actual = all_calls[case_id]()
        results.append({
            "id": case_id,
            "query": CASES[case_id],
            "pass": bool(passed),
            "failure_class": None if passed else CASE_FAILURE_CLASS[case_id],
            "actual": actual,
        })

    # -- accounting -----------------------------------------------------------
    added_loc = _loc(HERE / "store.py")
    harness_loc = sum(_loc(p) for p in [HERE / "cases.py", HERE / "run_stage1.py"])
    reused = [
        {"mechanism": "deterministic concept identity (concept_identity_id/upsert_concept)",
         "file": "ef/concept_registry.py"},
        {"mechanism": "exact-normalized alias identity (normalize_alias/add_alias/resolve_alias)",
         "file": "ef/concept_registry.py"},
        {"mechanism": "observations with evidence_ref provenance (record_observation)",
         "file": "ef/concept_registry.py"},
        {"mechanism": "relations with evidence payload (record_concept_relation)",
         "file": "ef/concept_registry.py"},
        {"mechanism": "idempotent registry schema (ensure_schema) + deterministic digest ids (_short_digest)",
         "file": "ef/concept_registry.py"},
        {"mechanism": "content-hash identity pattern + tmp-DB test isolation precedent",
         "file": "ef/personal_graph.py"},
    ]

    out = {
        "arm": "A",
        "architecture": "existing yt-is relational substrate (ef/concept_registry.py)"
                        " + minimum adapter additions (arm_a/store.py); isolated SQLite DB",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixture_version": fixture.get("fixture_version"),
        "fixture_sha256_freeze_recorded": (
            (HERE.parent / "freeze-hashes.txt").read_text().split()[0]),
        "fixture_sha256_this_run": fixture_sha,
        "db_path": str(db_path),
        "generation_after_ingest": store.generation,
        "ingest_summary": ingest_summary,
        "semantics": results,
        "semantics_score": {
            "passed": sum(1 for r in results if r["pass"]),
            "total": len(results),
        },
        "performance_ms": {
            "ingest_full_fixture_load": {
                "main_db_ms": round(ingest_main_ms, 3),
                "median_of_3_fresh_loads_ms": round(statistics.median(ingest_loads_ms), 3),
            },
            "per_query_mean_of_3": per_query,
            "replay_checkpoints": replay_latency,
        },
        "accounting": {
            "split_rule": "semantic = arm_a/store.py wholesale (every capability was "
                          "absent pre-bakeoff per stage-0 inventory); harness = cases.py "
                          "+ run_stage1.py (evaluation/orchestration). LOC = non-blank, "
                          "non-comment lines.",
            "added_semantic_loc": added_loc,
            "harness_loc": harness_loc,
            "reused_mechanisms": reused,
        },
    }

    out_path = HERE / "results.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Arm A | {out['fixture_sha256_this_run'][:12]}... | "
          f"{out['semantics_score']['passed']}/{len(results)} PASS")
    for r in results:
        mark = "PASS" if r["pass"] else f"FAIL ({r['failure_class']})"
        print(f"  {r['id']:>4}  {mark}")
    print(f"ingest {out['performance_ms']['ingest_full_fixture_load']['median_of_3_fresh_loads_ms']} ms | "
          f"replay total {round(sum(replay_latency.values()), 3)} ms | "
          f"semantic LOC {added_loc} (+{harness_loc} harness)")
    print(f"results: {out_path}")
    store.close()
    return 0 if out["semantics_score"]["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
