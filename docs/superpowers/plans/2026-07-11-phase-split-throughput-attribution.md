# Phase-Split Throughput Attribution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the new per-attempt phase timings to identify the next evidence-backed `yt-is` throughput mechanism, implement only a discriminating fix, and validate it without benchmark churn.

**Architecture:** Extend the existing Candidate 6 analyzer into a tested, machine-readable reducer before collecting new data. Run one separately authorized telemetry smoke against the established `3+3` shape, classify slow iterations by auth, reap, content subprocess, or unexplained residual time, and branch to the smallest justified mechanism. A throughput candidate is allowed only after the telemetry result survives adversarial review and a fresh decision packet.

**Tech Stack:** Python 3, JSONL event logs, `pytest`, `csf-sharded-lane-series`, Markdown/JSON decision packets.

## Global Constraints

- Read `CLAUDE.md`, `AGENTS.md`, `HANDOFF.md`, the throughput contract, current plan, registry, observability checklist, and artifact audit before acting.
- `3788.53` VPH is the current observed leader, not a proven optimum. `5572.04` is historical context, not the current control.
- Do not fetch external metadata, mutate prior run artifacts, or rerun a closed threshold/projection branch.
- A telemetry-validation smoke is not a throughput candidate. Its VPH is a regression/health signal only.
- Do not launch any live run until a new completed decision packet explicitly authorizes that exact run.
- Every causal conclusion must be based on raw JSONL plus the production measurement path. Analyzer output alone is not sufficient.
- Keep auth, pre-reap, pre-command reap, content subprocess, post-reap, and residual time separate. Never call their sum a wall-clock decomposition across parallel workers.
- Before `ready_for_parent_review`, run the adversarial review and claim-ledger gates in `AGENTS.md`.

---

## File Map

- Modify `scripts/analyze_candidate6_smoke.py`: reusable extraction, phase attribution, JSON/Markdown output, and explicit sufficiency verdicts.
- Create `tests/test_analyze_candidate6_smoke.py`: synthetic raw-event tests for phase separation, residual handling, missing schema, duplication, and percentile calculations.
- Create `.logs/sharded_lane_series/candidate6_phase_split_validation_decision_packet.md`: ignored live-run authorization record, only after Tasks 1-3 pass.
- Create `.logs/sharded_lane_series/candidate6_phase_split_validation_<timestamp>.json` and `.md`: ignored reducer outputs from the new run.
- Update `HANDOFF.md`, `docs/operations/hot-path-throughput-next-test-plan.md`, and `docs/operations/test-registry.md`: current authority and final branch classification.
- Modify production code only in the mechanism branch selected by Task 5; name the exact file and test in a follow-up implementation packet before editing.

### Task 1: Reconcile Current Authority

**Files:**
- Modify: `HANDOFF.md`
- Modify: `docs/operations/hot-path-throughput-next-test-plan.md`
- Modify: `docs/operations/test-registry.md`

- [ ] **Step 1: Confirm repository state and current evidence**

Run:

```powershell
git status --short
git log -8 --oneline
Get-Content .logs/sharded_lane_series/cross_corpus_overshoot_auth_correlation_packet_current.md
Get-Content .logs/sharded_lane_series/decision_packet_per_attempt_phase_split_current.md
```

Expected: tracked worktree is understood; cross-corpus verdict is `auth_inference_weakened`; phase-split code/tests are complete but no live phase-split data exists.

- [ ] **Step 2: Correct stale authority text**

Update the three authority docs so they say:

```text
Cross-corpus auth/overshoot correlation is complete and weakened the dominant-auth hypothesis.
Per-attempt phase-split instrumentation is implemented and unit-tested.
The next step is a tested reducer followed by one separately authorized telemetry-validation smoke.
No throughput patch or throughput benchmark is currently authorized.
```

- [ ] **Step 3: Verify doc consistency**

Run:

```powershell
rg.exe -n "cross-corpus|phase-split|auth_inference_weakened|next step" HANDOFF.md docs/operations/hot-path-throughput-next-test-plan.md docs/operations/test-registry.md
git diff --check
```

Expected: no document still says the cross-corpus analysis is pending or routes directly to durable auth.

### Task 2: Build a Tested Phase-Split Reducer

**Files:**
- Modify: `scripts/analyze_candidate6_smoke.py`
- Create: `tests/test_analyze_candidate6_smoke.py`

**Interfaces:**
- Produces: `analyze_run(run_root: Path) -> dict`
- Produces: `render_markdown(result: dict) -> str`
- CLI adds: `--json-output PATH` and `--markdown-output PATH`

- [ ] **Step 1: Write failing synthetic-event tests**

Tests must prove all of the following:

```python
def test_phase_split_attributes_auth_content_reap_and_residual_separately(): ...
def test_phase_split_does_not_double_count_attempt_or_retry_rows(): ...
def test_phase_split_reports_missing_schema_as_insufficient_evidence(): ...
def test_phase_split_uses_iteration_as_unit_and_preserves_branch_and_pass(): ...
def test_phase_split_percentiles_are_computed_from_observations_not_aggregate_sums(): ...
def test_phase_split_json_and_markdown_outputs_round_trip(): ...
```

Use fixtures with known iteration durations. Include one case where phase sums are less than `iteration_elapsed_s`; assert the difference is reported as `residual_elapsed_s`, not silently assigned to content or auth.

- [ ] **Step 2: Run tests and confirm they fail for missing interfaces**

```powershell
python -m pytest tests/test_analyze_candidate6_smoke.py -q
```

Expected: failure because `analyze_run` and structured outputs do not yet exist.

- [ ] **Step 3: Implement minimal structured analysis**

For every iteration record, retain:

```python
{
    "run": run_label,
    "stage": stage,
    "lane": lane,
    "batch_index": batch_index,
    "worker": worker_id,
    "profile": notebooklm_profile,
    "video_id": video_id,
    "source_id": source_id,
    "pass_name": pass_name,
    "attempt_index": attempt_index,
    "iteration": iteration,
    "branch": branch,
    "iteration_elapsed_s": iteration_elapsed_s,
    "pre_reap_elapsed_s": pre_reap_elapsed_s,
    "auth_elapsed_s": auth_elapsed_s,
    "pre_command_reap_elapsed_s": pre_command_reap_elapsed_s,
    "content_subprocess_elapsed_s": content_subprocess_elapsed_s,
    "post_reap_elapsed_s": post_reap_elapsed_s,
    "residual_elapsed_s": max(0.0, iteration_elapsed_s - phase_sum),
}
```

Report counts, coverage, p50/p95/max, total observation burden, and >30s iteration counts by phase, branch, pass, lane, worker, and profile. Label totals as per-observation aggregates, not elapsed wall time.

- [ ] **Step 4: Add evidence-sufficiency gates**

Return `insufficient_evidence` when any of these holds:

```text
no phase-split rows
phase fields present on <95% of iteration records
fewer than 20 >30s iterations for a tail-specific conclusion
unknown/residual is the dominant phase on >20% of >30s iterations
run summary missing or run identity/shape/environment cannot be proven
```

- [ ] **Step 5: Verify reducer**

```powershell
python -m pytest tests/test_analyze_candidate6_smoke.py -q
python -m py_compile scripts/analyze_candidate6_smoke.py tests/test_analyze_candidate6_smoke.py
python scripts/analyze_candidate6_smoke.py --run-root .logs/sharded_lane_series/candidate6_telemetry_validation_run02_current
git diff --check
```

Expected: tests pass; the historical run is explicitly classified as lacking the new phase fields rather than yielding a causal result.

### Task 3: Prepare the Telemetry-Validation Decision Packet

**Files:**
- Create: `.logs/sharded_lane_series/candidate6_phase_split_validation_decision_packet.md`

- [ ] **Step 1: Reconcile the live command with the installed binary**

```powershell
python bin/csf-sharded-lane-series --help
Test-Path .logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_lanes.json
```

Use a fresh root such as `.logs/sharded_lane_series/candidate6_phase_split_validation_run01_current`.

- [ ] **Step 2: Define telemetry gates before launch**

The packet must require:

```text
worker shape = 3+3
environment = home_300mb
clean browser/profile preflight
phase fields on >=95% of emitted iteration records
no negative phase durations
phase sum <= iteration elapsed + timer tolerance
no prior run-root reuse
stop if no summary appears within 20 minutes
```

VPH is a health guard, not a promotion metric. The run does not need to beat `3788.53` to validate telemetry.

- [ ] **Step 3: Define launch authority**

End the packet with exactly one of:

```text
live_validation_justified: yes
live_validation_justified: no
```

Do not launch in this task. Parent approval remains a separate gate.

### Task 4: Execute One Authorized Smoke and Reduce It

**Precondition:** Task 3 says `yes` and the parent explicitly authorizes the run.

- [ ] **Step 1: Perform time-sensitive preflight immediately before launch**

Use the established headless worker-profile auth recipe in `docs/operations/notebooklm-auth-rerun-recipe.md`; do not rediscover interactive auth. Verify all six intended profiles, browser health, worker shape, environment, free disk, and a fresh output root.

- [ ] **Step 2: Launch exactly one 400-video telemetry smoke**

Use the reconciled command from the packet. Do not promote automatically to soak.

- [ ] **Step 3: Run the reducer on raw artifacts**

```powershell
python scripts/analyze_candidate6_smoke.py `
  --run-root .logs/sharded_lane_series/candidate6_phase_split_validation_run01_current `
  --json-output .logs/sharded_lane_series/candidate6_phase_split_validation_current.json `
  --markdown-output .logs/sharded_lane_series/candidate6_phase_split_validation_current.md
```

- [ ] **Step 4: Classify run validity before interpreting mechanism**

Use `valid_telemetry`, `insufficient_evidence`, `invalid_environment`, or `invalid_run`. A failed VPH health guard does not by itself invalidate telemetry, but it makes generalization to healthy sustained throughput weaker.

### Task 5: Branch on the Measured Dominant Phase

**Files:**
- Create: `.logs/sharded_lane_series/candidate6_phase_split_mechanism_packet.md`

- [ ] **Step 1: Build a claim ledger from raw evidence**

Compare both absolute time and tail frequency. Do not infer causality from correlation alone.

- [ ] **Step 2: Select exactly one branch**

```text
AUTH: auth_elapsed_s dominates >30s iterations and survives lane/worker/profile controls.
CONTENT: content_subprocess_elapsed_s dominates and failure/branch labels identify a repeatable command path.
REAP: pre/post reap dominates and is concentrated by profile/process state.
RESIDUAL: unexplained residual dominates; instrumentation remains incomplete.
MIXED: no phase dominates or the cohort is degenerate; gather a healthier control before patching.
```

- [ ] **Step 3: Apply branch-specific next action**

```text
AUTH -> inspect durable-auth issue #965 code path; design the smallest deduplication/cache/session-lifetime change.
CONTENT -> inspect _run_cmd failure/retry branches; design the smallest admission, retry, or command-path change supported by branch/status evidence.
REAP -> inspect profile cleanup/reap ownership; design the smallest process-lifecycle fix.
RESIDUAL -> extend timing boundaries and tests; no behavior patch or benchmark.
MIXED -> stop with insufficient evidence; authorize only a paired healthy telemetry control if a packet proves it is discriminating.
```

### Task 6: Implement and Validate One Mechanism Candidate

**Precondition:** Task 5 identifies a narrow production mechanism with a falsifier. Otherwise skip this task.

- [ ] **Step 1: Write a candidate implementation packet**

Name the exact production function, behavioral invariant, failing test, falsifier, rollback, and expected telemetry change. Separate expected mechanism movement from expected VPH movement.

- [ ] **Step 2: Add a discriminating failing test**

The test must fail on the current implementation for the mechanism reason, not because a fixture omits a field.

- [ ] **Step 3: Implement the smallest change**

Do not combine auth, retry, threshold, geometry, or worker-count changes in one candidate.

- [ ] **Step 4: Run focused and regression tests**

```powershell
python -m pytest <focused-test-node> -q
python -m pytest tests/test_nlm_batch.py -q
python -m py_compile <changed-python-files>
git diff --check
```

- [ ] **Step 5: Adversarially review the candidate**

Try to falsify the three load-bearing claims from code and tests. Check for tautological metrics, duplicate attempts, cohort degeneracy, stale summaries, and alternative explanations. Report `needs_fix` if any load-bearing claim fails.

### Task 7: Guarded Throughput Validation and Closure

**Precondition:** candidate code is verified and a fresh throughput decision packet authorizes the run.

- [ ] **Step 1: Run one smoke with mechanism and VPH gates**

Abort on wrong environment/shape, auth/profile failure, missing telemetry, repeated source-add/materialization failure, or no summary within the packet window.

- [ ] **Step 2: Promote to soak only when both mechanism and smoke gates pass**

Require the predicted phase metric to improve. VPH improvement without mechanism movement is not proof that the patch worked.

- [ ] **Step 3: Require confirmation before declaring a new sustained leader**

A single candidate run may justify continued testing, not a claim of optimal sustained VPH. Require a second valid confirmation or paired current-control comparison when variance remains material.

- [ ] **Step 4: Close the branch**

Update `HANDOFF.md`, the hot-path plan, registry, decision packet, and claim ledger with one of:

```text
promoted_candidate
negative_mechanism_result
partial_or_invalid
insufficient_evidence
```

## Completion Criteria

This plan is complete only when either:

1. A mechanism-specific candidate beats the current control under valid sustained conditions and is confirmed; or
2. The current branch is closed with a falsified mechanism and the next discriminating evidence gap is named precisely.

“No patch yet” is acceptable only after the reducer, sufficiency gates, and adversarial review have been completed. “Current observed leader” must never be restated as “optimal.”
