"""Arm B1 orchestrator. agent: zcode

Usage (run with arm_b1/.venv2/Scripts/python.exe):
  python run_b1.py --purge-group b1_run1          # wipe one run partition
  python run_b1.py --run 1                        # full run 1 -> results_run1.json
  python run_b1.py --aggregate                    # 3 runs -> results.json summary
  python run_b1.py --run 2 --llm-mode json_object # structured-output fallback mode

Sequence per run: purge group -> sequential add_episode ingest in t order ->
evaluator X1..X14 (read-only cases first, then removals X7/X8, concurrency X14)
-> results_run{N}.json with per-case actuals, wall times, LLM call counts and
token usage captured via CountingLLMClient around the real client.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import b1_clients as bc
import evaluate as ev
from ingest import ingest_fixture, load_fixture

HERE = Path(__file__).resolve().parent


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_tokens(tracker) -> dict[str, Any]:
    usage = tracker.get_usage()
    total = tracker.get_total_usage()
    return {
        "total": {"input": total.input_tokens, "output": total.output_tokens,
                  "total": total.total_tokens},
        "per_prompt": {
            k: {"calls": v.call_count, "input": v.total_input_tokens,
                "output": v.total_output_tokens}
            for k, v in sorted(usage.items())
        },
    }


async def cmd_purge_group(group: str) -> dict:
    driver = bc.build_driver(database=group)
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data

    await clear_data(driver, group_ids=[group])
    # verify emptiness (read-only)
    res = await driver.execute_query(
        "MATCH (n) WHERE n.group_id = $g RETURN count(n) AS n", g=group)
    remaining = res[0][0]["n"] if res else "?"
    await driver.close()
    return {"purged_group": group, "nodes_remaining_in_group": remaining,
            "timestamp": utcnow()}


async def build_graphiti(database: str, llm_mode: str | None):
    """Graphiti over the REAL semantic pipeline: proxy LLM + local fastembed +
    local BGE reranker; all extraction/resolution/invalidation is Graphiti's.
    Driver is built and health-checked FIRST so an absent endpoint fails fast,
    before any heavy local model loads."""
    from graphiti_core.graphiti import Graphiti

    driver = bc.build_driver(database)
    await driver.health_check()  # surfaces connection-refused immediately
    llm, llm_desc = bc.build_llm_client(llm_mode)
    embedder = bc.build_embedder()
    reranker, reranker_desc = bc.build_reranker(lambda: bc.build_llm_client(llm_mode)[0])
    g = Graphiti(graph_driver=driver, llm_client=llm, embedder=embedder,
                 cross_encoder=reranker)
    await g.build_indices_and_constraints()
    return g, driver, llm_desc, reranker_desc


async def execute_run(run: int, llm_mode: str | None) -> dict:
    group = bc.group_id_for_run(run)
    out: dict[str, Any] = {"arm": "B1", "run": run, "group_id": group,
                           "started_at": utcnow()}
    try:
        purge = await cmd_purge_group(group)
        out["purge"] = purge

        fixture = load_fixture()
        graphiti, driver, llm_desc, reranker_desc = await build_graphiti(group, llm_mode)
        out["llm_client"] = {**llm_desc,
                             "structured_mode_effective":
                                 getattr(graphiti.llm_client._inner,
                                         "structured_output_mode", llm_desc.get("structured_output_mode"))}
        out["reranker"] = reranker_desc

        t0 = time.perf_counter()
        report = await ingest_fixture(graphiti, fixture, group, run)
        report.total_wall_s = time.perf_counter() - t0
        out["ingest"] = report.summary()
        out["llm_calls"] = {
            "total_calls": len(graphiti.llm_client.calls),
            "per_prompt_name": {},
        }
        for c in graphiti.llm_client.calls:
            key = c["prompt_name"] or "unknown"
            out["llm_calls"]["per_prompt_name"].setdefault(key, 0)
            out["llm_calls"]["per_prompt_name"][key] += 1
        calls_per_episode = (len(graphiti.llm_client.calls) / max(1, len(report.records)))
        out["llm_calls"]["avg_calls_per_episode_observed"] = round(calls_per_episode, 3)
        out["token_usage"] = serialize_tokens(graphiti.llm_client.token_tracker)

        evaluator = ev.B1Evaluator(graphiti, driver, fixture, group)

        async def second_session_add(name: str, episode_body: str,
                                     reference_time) -> Any:
            """X14 Session B: a second DRIVER CONNECTION running the same full
            pipeline against the run's group."""
            g2, _, _, _ = await build_graphiti(group, llm_mode)
            return await g2.add_episode(
                name=name,
                episode_body=episode_body,
                source_description="Starline Weekly",
                reference_time=reference_time,
                group_id=group,
            )

        evaluator.second_session_add = second_session_add

        snap = await evaluator.fresh_snapshot()
        cases: list[dict[str, Any]] = []

        read_only = [
            ("X1", lambda: evaluator.x1_launch_asof(snap)),
            ("X2", lambda: evaluator.x2_partners_asof(snap)),
            ("X3", lambda: evaluator.x3_launch_current(snap)),
            ("X4", lambda: evaluator.x4_budget_coexist(snap)),
            ("X5", lambda: evaluator.x5_identity(snap)),
        ]

        x6_result_holder: dict[str, dict] = {}

        async def do_x6():
            r = await evaluator.x6_bridge(snap)
            x6_result_holder["r"] = r
            return r

        read_only += [
            ("X6", do_x6),
            ("X9", lambda: evaluator.x9_leads_now(snap)),
            ("X10", lambda: evaluator.x10_leads_asof_inclusive(snap)),
            ("X11", lambda: evaluator.x11_replay_checkpoints(snap)),
            ("X12", lambda: evaluator.x12_provenance(snap)),
            ("X13", lambda: evaluator.x13_why_surfaced(x6_result_holder.get("r", {}))),
        ]
        mutations = [
            ("X7", lambda: evaluator.x7_remove_eu08(snap)),
            ("X8", lambda: evaluator.x8_remove_eu11()),
            ("X14", lambda: evaluator.x14_concurrency()),
        ]
        for cid, fn in read_only + mutations:
            tc = time.perf_counter()
            try:
                res = await fn()
            except Exception as e:  # noqa: BLE001 — record, never hide
                trace = traceback.format_exc()
                errstr = f"{type(e).__name__}: {e}"
                blocked = _is_unreachable_error(errstr)
                res = ev.case_result(
                    cid,
                    {"error": errstr, "trace": trace[-4000:]},
                    ok=None if blocked else False,
                    failure_class="F-endpoint-unavailable" if blocked else "F-exec",
                )
            res["wall_s"] = round(time.perf_counter() - tc, 3)
            cases.append(res)

        out["cases"] = cases
        n_pass = sum(1 for c in cases if c["status"] == "PASS")
        n_fail = sum(1 for c in cases if c["status"] == "FAIL")
        n_untest = sum(1 for c in cases if c["status"] == "UNTESTABLE")
        out["summary"] = {"pass": n_pass, "fail": n_fail, "untestable": n_untest}
        out["completed_at"] = utcnow()
    except Exception as e:  # noqa: BLE001 — whole-run blocker (e.g. no endpoint)
        out["run_status"] = "blocked"
        out["error"] = f"{type(e).__name__}: {e}"
        out["error_trace"] = traceback.format_exc()[-8000:]
    else:
        out["run_status"] = "completed"
    finally:
        try:
            await graphiti.close()  # type: ignore[name-defined]
        except Exception:
            pass

    path = HERE / f"results_run{run}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return {"written": str(path), "status": out.get("run_status"),
            "summary": out.get("summary")}


def _is_unreachable_error(err: str) -> bool:
    markers = (
        "ConnectionError", "connect", "refused", "Max retries exceeded",
        "APIConnectionError", "AuthenticationError", "401",
        "NameOrServiceNotKnown", "getaddrinfo failed", "TimeoutError",
    )
    return any(m.lower() in err.lower() for m in markers)


def aggregate(runs=(1, 2, 3)) -> dict:
    """Per-case agreement across runs on (status, normalized actual payload)."""
    per_case: dict[str, Any] = {}
    runs_data: dict[int, dict] = {}
    for r in runs:
        p = HERE / f"results_run{r}.json"
        if not p.exists():
            continue
        runs_data[r] = json.loads(p.read_text(encoding="utf-8"))
    for cid in [f"X{i}" for i in range(1, 15)]:
        entries = {}
        statuses = []
        actual_hashes = []
        for r, data in sorted(runs_data.items()):
            case = next((c for c in data.get("cases", []) if c.get("case") == cid), None)
            if case is None:
                entries[f"run{r}"] = {"present": False}
                continue
            statuses.append(case["status"])
            h = hashlib.sha256(
                json.dumps(case.get("actual"), sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            actual_hashes.append(h)
            entries[f"run{r}"] = {"status": case["status"],
                                  "failure_class": case.get("failure_class"),
                                  "actual_hash": h}
        agrees = (len(set(statuses)) == 1) if statuses else False
        payload_stable = (len(set(actual_hashes)) <= 1) if actual_hashes else False
        per_case[cid] = {"runs_present": list(entries), "entries": entries,
                         "status_agreement": agrees,
                         "actual_payload_stable": payload_stable,
                         "repeatable": bool(agrees and payload_stable)}
    out = {
        "arm": "B1",
        "generated_at": utcnow(),
        "runs_aggregated": sorted(runs_data),
        "repeatability_per_case": per_case,
        "llm_calls_per_episode_by_run": {
            f"run{r}": d.get("ingest", {}).get("episodes_attempted") and
            d.get("llm_calls", {}).get("avg_calls_per_episode_observed")
            for r, d in runs_data.items()
        },
    }
    (HERE / "results.json").write_text(json.dumps(out, indent=2, default=str),
                                       encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Arm B1 orchestrator")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=int, choices=[1, 2, 3])
    g.add_argument("--purge-group", metavar="GROUP")
    g.add_argument("--aggregate", action="store_true")
    ap.add_argument("--llm-mode", choices=["json_schema", "json_object"], default=None,
                    help="structured output request mode (default pinned json_schema)")
    args = ap.parse_args()

    if args.purge_group:
        print(json.dumps(asyncio.run(cmd_purge_group(args.purge_group)), indent=2))
        return 0
    if args.aggregate:
        print(json.dumps(aggregate(), indent=2, default=str)[:2000])
        return 0
    res = asyncio.run(execute_run(args.run, args.llm_mode))
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") != "blocked" else 3


if __name__ == "__main__":
    raise SystemExit(main())
