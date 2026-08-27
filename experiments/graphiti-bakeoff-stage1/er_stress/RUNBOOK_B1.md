# RUNBOOK B1 — ENTITY_RESOLUTION_STRESS_DIAGNOSTIC (Arm B / Graphiti add_episode)

`label: NON_DECISION_DIAGNOSTIC`
This runbook is fully mechanical. Execute it verbatim; it requires no design
decisions. It drives `er_stress/fixture_er_stress.json` through the Graphiti
add_episode extraction/resolution pipeline (the LLM path that Arm B's stage-1
LLM-free adapter deliberately bypassed) and dumps Graphiti's entity-resolution
output to disk. Scoring rules are embedded in the driver and mirror the rubric
in DIAGNOSTIC.md, so results are directly comparable with
`er_stress/results_arm_a.json`.

Target output file: `P:/tmp/ytis-graphiti-bakeoff/experiments/graphiti-bakeoff-stage1/er_stress/results_b1_run{N}.json`

## 0. Preconditions

1. A reachable FalkorDB server (>= 1.1.2), NOT FalkorDBLite.
2. Python with `graphiti-core==0.29.3`. The pinned venv from the Stage-1 bakeoff
   already has it:
   - Windows: `P:/tmp/ytis-graphiti-bakeoff/experiments/graphiti-bakeoff-stage1/arm_b/.venv/Scripts/python.exe`
   - pins: see `../arm_b/lock.txt`
3. An LLM API key for the default OpenAI extraction/embedding clients used by
   graphiti-core (`OPENAI_API_KEY`). If your endpoint differs, also set
   `OPENAI_BASE_URL`; consult graphiti-core 0.29.3 defaults for model env names.
   No other credentials are read.

## 1. Environment variables (exact names, set before running)

| variable            | required | example        | meaning                                |
|---------------------|----------|----------------|----------------------------------------|
| FALKORDB_HOST       | yes      | 127.0.0.1      | FalkorDB host                          |
| FALKORDB_PORT       | yes      | 6379           | FalkorDB port                          |
| FALKORDB_USERNAME   | no*      | default        | omit if server has no auth             |
| FALKORDB_PASSWORD   | no*      | secret         | omit if server has no auth             |
| OPENAI_API_KEY      | yes      | sk-...         | used by add_episode extraction+embedding |

*Set both or neither.

PowerShell:

```powershell
cd P:\tmp\ytis-graphiti-bakeoff\experiments\graphiti-bakeoff-stage1\er_stress
$env:FALKORDB_HOST = "127.0.0.1"
$env:FALKORDB_PORT = "6379"
$env:FALKORDB_USERNAME = ""          # empty string treated as not provided
$env:FALKORDB_PASSWORD = ""
$env:OPENAI_API_KEY  = "<key>"
& ..\arm_b\.venv\Scripts\python.exe run_b1_er_stress.py --run-number 1
```

bash (Git Bash):

```bash
cd /p/tmp/ytis-graphiti-bakeoff/experiments/graphiti-bakeoff-stage1/er_stress
export FALKORDB_HOST=127.0.0.1
export FALKORDB_PORT=6379
export FALKORDB_USERNAME=''    # empty string treated as not provided
export FALKORDB_PASSWORD=''
export OPENAI_API_KEY='<key>'
../arm_b/.venv/Scripts/python.exe run_b1_er_stress.py --run-number 1
```

`--run-number N` selects the `{N}` in `results_b1_run{N}.json` and the isolated
FalkorDB graph name `er_stress_run{N}`. Re-runs should increment N; never reuse
an N that produced results you want to keep.

On success the script prints `WROTE P:/.../er_stress/results_b1_run1.json`.
Exit codes: 0 success, 3 FalkorDB unreachable, 4 missing config, 5 safety guard.

## 2. Driver script (save EXACTLY this as er_stress/run_b1_er_stress.py)

```python
"""B1 ER-stress driver. NON_DECISION_DIAGNOSTIC. Mechanical: edit nothing."""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture_er_stress.json"

STAGE1 = HERE.parent
if str(STAGE1) not in sys.path:
    sys.path.insert(0, str(STAGE1))


def local_normalize(alias: str) -> str:
    """Byte-identical semantics to ef.concept_registry.normalize_alias
    (casefold; hyphen/underscore between word chars -> space; collapse ws;
    strip surrounding punctuation). Replicated here so the driver runs
    without importing the yt-is package."""
    s = re.sub(r"(?<=[\w])[-_](?=[\w])", " ", alias.casefold())
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^[^\w]+|[^\w]+$", "", re.sub(r"\s+", " ", s)).strip()


def build_driver(group_suffix: str):
    import os
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    db = f"er_stress_{group_suffix}"
    if not db.startswith("er_stress_"):
        raise SystemExit("SAFETY: graph name must start with er_stress_")
    user = os.environ.get("FALKORDB_USERNAME") or None
    pw = os.environ.get("FALKORDB_PASSWORD") or None
    return db, FalkorDriver(
        host=os.environ.get("FALKORDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=user, password=pw, database=db,
    )


def rows_of(res):
    """Normalize execute_query result shapes into list[dict] (defensive)."""
    if res is None:
        return []
    seq = res if isinstance(res, (list, tuple)) else getattr(res, "records", [res])
    out = []
    for row in seq:
        if isinstance(row, dict):
            out.append(row)
        elif hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            out.append({"value": str(row)})
    return out


async def main() -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-number", type=int, required=True)
    args = ap.parse_args()
    n = args.run_number

    for var in ("FALKORDB_HOST", "FALKORDB_PORT"):
        if not os.environ.get(var):
            print(f"MISSING_ENV {var}")
            return 4
    if not os.environ.get("OPENAI_API_KEY"):
        print("MISSING_ENV OPENAI_API_KEY (needed by add_episode resolution)")
        return 4

    from graphiti_core.graphiti import Graphiti
    from graphiti_core.nodes import EpisodeType

    db, driver = build_driver(f"run{n}")
    g = Graphiti(graph_driver=driver)  # defaults: OpenAI llm + embedder clients
    try:
        await g.driver.health_check()
    except Exception as e:
        print(f"FALKORDB_UNAVAILABLE {os.environ.get('FALKORDB_HOST')}:{os.environ.get('FALKORDB_PORT')}: {type(e).__name__}: {e}")
        return 3

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    await g.build_indices_and_constraints()

    per_episode = []
    for eu in sorted(fixture["evidence_units"], key=lambda u: u["t"]):
        ref_t = datetime.fromisoformat(eu["t"].replace("Z", "+00:00"))
        res = await g.add_episode(
            name=eu["eu_id"],
            episode_body=eu["text"],
            source_description=fixture["sources"][
                next(i for i, s in enumerate(fixture["sources"])
                     if s["source_id"] == eu["source_id"])]["channel"],
            source=EpisodeType.message,
            reference_time=ref_t,
            group_id=db,
        )
        per_episode.append({
            "eu_id": eu["eu_id"],
            "t": eu["t"],
            "source_hint": eu["source_id"],
            "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
            "episode_nodes": [
                {"uuid": nd.uuid, "name": nd.name} for nd in (getattr(res, "nodes", []) or [])],
            "episode_edges": [
                {"uuid": ed.uuid, "fact": getattr(ed, "fact", None)}
                for ed in (getattr(res, "edges", []) or [])],
        })

    gid = db
    q_nodes = ("MATCH (n:Entity) WHERE n.group_id=$gid RETURN n.uuid AS uuid,"
               " n.name AS name, n.summary AS summary, n.created_at AS created_at"
               " ORDER BY n.uuid")
    q_edges = ("MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE a.group_id=$gid"
               " RETURN r.uuid AS uuid, r.fact AS fact, r.created_at AS created_at,"
               " coalesce(r.invalid_at,'') AS invalid_at, coalesce(r.expired_at,'')"
               " AS expired_at, a.uuid AS source_uuid, b.uuid AS target_uuid"
               " ORDER BY r.uuid")
    nodes = rows_of(await g.driver.execute_query(q_nodes, gid=gid))
    edges = rows_of(await g.driver.execute_query(q_edges, gid=gid))

    # Mechanical probe index: which extracted node(s) carry each stress surface.
    probe_index = []
    for case in fixture["er_cases"]:
        entries = []
        for inp in case["inputs"]:
            rn = local_normalize(inp["ref"])
            matched = []
            for nd in nodes:
                name_n = local_normalize(nd.get("name") or "")
                summ_n = local_normalize(nd.get("summary") or "")
                if rn == name_n or (rn in summ_n and len(rn) >= 3):
                    matched.append(nd.get("uuid"))
            entries.append({
                "ref": inp["ref"], "source_hint": inp.get("source_hint"),
                "context_hint": inp.get("context_hint"),
                "normalized_ref": rn, "matched_node_uuids": matched})
        probe_index.append({"case_id": case["case_id"], "inputs": entries})

    cluster_index = [{"uuid": nd.get("uuid"), "name": nd.get("name"),
                      "summary_excerpt": (nd.get("summary") or "")[:400]}
                     for nd in sorted(nodes, key=lambda d: d.get("uuid") or "")]

    out_path = HERE / f"results_b1_run{n}.json"
    payload = {
        "schema": "er-stress-results-b1-v1",
        "label": "NON_DECISION_DIAGNOSTIC",
        "diagnostic_name": "ENTITY_RESOLUTION_STRESS_DIAGNOSTIC",
        "arm": "B1",
        "run_number": n,
        "falkordb_graph": db,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "fixture_version": fixture["fixture_version"],
        "per_episode_ingest": per_episode,
        "final_nodes_after_resolution": nodes,
        "final_edges_after_resolution": edges,
        "probe_index": probe_index,
        "resolved_clusters": cluster_index,
        "scoring_rule_note": (
            "Apply DIAGNOSTIC.md rubric v1 to probe_index + final_nodes: MERGE_TO_ONE "
            "PASS iff all refs of the case match nodes belonging to ONE canonical "
            "cluster containing the expected entity's canonical surface; DISTINCT "
            "FAIL iff refs whose targets differ map to one node uuid. Wrong-entity "
            "assignment = resolved to a node belonging to another case's cluster."),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"WROTE {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

## 3. What gets extracted (resolution-output contract)

`results_b1_run{N}.json` contains exactly:

1. `per_episode_ingest` — per EU: the node uuids/name pairs add_episode emitted
   at ingest time (pre-resolution view), plus edge facts.
2. `final_nodes_after_resolution` — every Entity node remaining after Graphiti's
   dedupe/merge pass (merged duplicates disappear as separate uuids).
3. `final_edges_after_resolution` — surviving relations incl. `invalid_at` /
   `expired_at` so merge-superseded edges are visible.
4. `probe_index` — mechanical mapping of each fixture stress ref (exact same
   normalize function as Arm A side) to matching node uuid(s).
5. `resolved_clusters` — uuid/name/summary index over final nodes (the
   "name clusters" view: one line per post-resolution identity).

## 4. Interpretation rule (fixed, no decisions)

- A case expecting `MERGE_TO_ONE` passes iff all its refs' `matched_node_uuids`
  intersect exactly ONE final-node uuid and that node's name/summary carries the
  expected canonical surface.
- A case expecting `DISTINCT` fails iff two refs with different declared targets
  share any `matched_node_uuid` (collapse). Both-refs-unmatched is PARTIAL-style
  failure (no evidence either way), recorded separately.
- Ref matched to a node belonging to another case's expected entity =
  WRONG_ENTITY failure.

Compare against `results_arm_a.json` scorecard only for architectural
interpretation. Per the Stage-1 preregistration this diagnostic CANNOT change
the X1..X14 decision.

## 5. Cleanup (optional, only on explicit operator instruction)

Graphs are isolated per run (`er_stress_run{N}`); deleting them touches nothing
else on the endpoint:
```
GRAPH.ERASE er_stress_run1
```
