# RUNBOOK B1 — ENTITY_RESOLUTION_STRESS_DIAGNOSTIC (Arm B1 / Graphiti add_episode)

`label: NON_DECISION_DIAGNOSTIC`
This runbook is fully mechanical. Execute it verbatim; it requires no design
decisions. It drives `er_stress/fixture_er_stress.json` through the SAME pinned
Graphiti semantic pipeline as Arm B1 (`add_episode` extraction/resolution via
the local proxy LLM + deterministic fastembed + local BGE reranker), and dumps
Graphiti's entity-resolution output to disk. Scoring rules are embedded in the
driver and mirror DIAGNOSTIC.md rubric v1, so results are directly comparable
with `er_stress/results_arm_a.json`.

Delta-review note (D5 fixed): this runbook previously suggested graphiti's
default OpenAI cloud clients with `arm_b/.venv`; that would have run a
DIFFERENT LLM configuration than Arm B1. The committed driver now imports
`arm_b1/run_b1.py::build_graphiti`, i.e. the exact pinned stack from
`arm_b1/CONFIG.md`.

Target output: `er_stress/results_b1_run{N}.json`

## 0. Preconditions

1. A reachable FalkorDB server (>= 1.1.2), NOT FalkorDBLite.
2. The pinned venv: `P:/tmp/ytis-graphiti-bakeoff/experiments/graphiti-bakeoff-stage1/arm_b1/.venv2`
   (pins in `../arm_b1/lock.txt`; includes fastembed + sentence-transformers).
3. Proxy API key for the pinned LLM route (runtime env only; never committed).

## 1. Environment variables (exact names)

| variable          | required | example        | meaning                                  |
|-------------------|----------|----------------|------------------------------------------|
| FALKORDB_HOST     | yes      | 127.0.0.1      | FalkorDB host                            |
| FALKORDB_PORT     | yes      | 6379           | FalkorDB port                            |
| FALKORDB_USERNAME | no*      | default        | omit if server has no auth               |
| FALKORDB_PASSWORD | no*      | secret         | omit if server has no auth               |
| PROXY_API_KEY     | yes      | (proxy key)    | go-llm-proxy key for nemotron extraction |

*Set both or neither.

bash (Git Bash):

```bash
cd /p/tmp/ytis-graphiti-bakeoff/experiments/graphiti-bakeoff-stage1/er_stress
export FALKORDB_HOST=127.0.0.1
export FALKORDB_PORT=6379
export FALKORDB_USERNAME=''    # empty string treated as not provided
export FALKORDB_PASSWORD=''
export PROXY_API_KEY='<key>'
../arm_b1/.venv2/Scripts/python.exe run_b1_er_stress.py --run-number 1
```

PowerShell:

```powershell
cd P:\tmp\ytis-graphiti-bakeoff\experiments\graphiti-bakeoff-stage1\er_stress
$env:FALKORDB_HOST = "127.0.0.1"; $env:FALKORDB_PORT = "6379"
$env:FALKORDB_USERNAME = ""; $env:FALKORDB_PASSWORD = ""
$env:PROXY_API_KEY = "<key>"
& ..\arm_b1\.venv2\Scripts\python.exe run_b1_er_stress.py --run-number 1
```

`--run-number N` selects `results_b1_run{N}.json` and the isolated FalkorDB
graph name `er_stress_run{N}`. Re-runs increment N; never reuse an N whose
results you want to keep.

Success prints `WROTE .../er_stress/results_b1_run{N}.json`.
Exit codes: 0 success, 3 FalkorDB unreachable, 4 missing config, 5 safety guard.

## 2. Driver

Committed at `er_stress/run_b1_er_stress.py` (execute verbatim, edit nothing).
It builds the graph via `arm_b1.run_b1.build_graphiti` — the SAME client stack,
temperature-0 structured output, embedder and reranker pins as the X1..X14 arm.

## 3. What gets extracted (resolution-output contract)

1. `per_episode_ingest` — per EU: node uuid/name pairs add_episode emitted at
   ingest time (pre-resolution view) plus edge facts.
2. `final_nodes_after_resolution` — every Entity node after Graphiti's
   dedupe/merge pass.
3. `final_edges_after_resolution` — surviving relations incl. `invalid_at` /
   `expired_at` (merge-superseded edges visible).
4. `probe_index` — mechanical mapping of each stress ref (same normalize
   function semantics as the Arm A side) to matching node uuid(s).
5. `resolved_clusters` — uuid/name/summary index over final nodes.

Plus `llm_config` (the effective pin descriptions) for auditability.

## 4. Interpretation rule (fixed, no decisions)

- A case expecting `MERGE_TO_ONE` passes iff all its refs' `matched_node_uuids`
  intersect exactly ONE final-node uuid carrying the expected canonical surface.
- A case expecting `DISTINCT` fails iff two refs with different declared targets
  share any `matched_node_uuid` (collapse); both-refs-unmatched recorded
  separately as PARTIAL-style failure.
- Ref matched to a node of another case's expected entity = WRONG_ENTITY.

Compare against `results_arm_a.json` only for architectural interpretation. Per
the Stage-1 preregistration this diagnostic CANNOT change the X1..X14 decision.

## 5. Cleanup (optional, explicit operator instruction only)

```
GRAPH.ERASE er_stress_run1
```
