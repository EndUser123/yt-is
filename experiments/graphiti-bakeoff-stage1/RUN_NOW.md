# RUN_NOW — Stage-1 execution the moment the endpoint is visible

agent: zcode. Context: execution home = P:/tmp/ytis-graphiti-bakeoff (pinned venvs
in arm_b1/.venv2). Frozen contract unchanged; no edits needed. This script is
mechanical; run it in ANY session whose shell can see FALKORDB_* (a ZCode session
started AFTER the operator set the variables — env snapshots at app start).

## Preconditions (one probe)

    python -c "import os; print({v: bool(os.environ.get(v)) for v in ('FALKORDB_HOST','FALKORDB_PORT','FALKORDB_USERNAME','FALKORDB_PASSWORD','PROXY_API_KEY')})"

All True (or FALKORDB_URL True + PROXY_API_KEY True) -> go. PROXY_API_KEY absent?
It also lives in C:/Users/brsth/.zcode/v2/config.json provider opencode-zen-free
(read at runtime; never copy into files).

## Sequence (from experiments/graphiti-bakeoff-stage1/)

    cd P:/tmp/ytis-graphiti-bakeoff
    python experiments/graphiti-bakeoff-stage1/arm_a/run_stage1.py          # Arm A repro (no endpoint needed)
    cd experiments/graphiti-bakeoff-stage1/arm_b1
    .venv2/Scripts/python.exe run_b1.py --run 1
    .venv2/Scripts/python.exe run_b1.py --run 2
    .venv2/Scripts/python.exe run_b1.py --run 3
    cd ../er_stress
    ../arm_b1/.venv2/Scripts/python.exe run_b1_er_stress.py --run-number 1

Each `run_b1.py --run N` purges its own group (b1_runN) first: clean graph per
run. Aggregate repeatability:

    cd ../arm_b1 && .venv2/Scripts/python.exe run_b1.py --aggregate

Outputs: arm_b1/results_run{1,2,3}.json + results.json (per-case agreement),
er_stress/results_b1_run1.json. LLM call counts + token usage embedded per run.

## Notes

- redis pinned 7.4.1 in .venv2 (architect amendment: avoid 8.1.0 on this path);
  lock.txt refreshed.
- Do not edit PREREGISTRATION.md / fixture.json / freeze-hashes.txt.
- Expected duration: ~5-15 LLM calls per EU per run (15 EUs), free-tier latency.
- After results: apply frozen decision rule (PREREGISTRATION.md), fresh-context
  review, brokered commit, push.
