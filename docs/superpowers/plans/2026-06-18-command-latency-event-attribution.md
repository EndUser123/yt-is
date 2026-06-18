# Command-Latency Event Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing command-latency packet with reconciled event-level attribution by attempt, lane, batch, profile, and status.

**Architecture:** Keep worker aggregates as metric authority and add a term-event parser beside the existing stdout parser. Event totals reconcile against worker command totals; reports become discriminating only when both count and elapsed coverage reach 95% for every compared run.

**Tech Stack:** Python 3.14, pytest, JSONL benchmark logs, Markdown/JSON report generation.

---

### Task 1: Parse and aggregate command events

**Files:**
- Modify: `scripts/analyze_command_latency_attribution.py`
- Test: `tests/test_analyze_command_latency_attribution.py`

- [ ] Add synthetic event helpers and a failing test covering attempt-1, retry, malformed, and projection events.

```python
def _write_events(run_root: Path, phase: str, lane: str, batch: str, events: list[str]) -> None:
    path = run_root / phase / lane / batch / "policy" / "stamp" / "workers_03" / "logs" / "term_test.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(events), encoding="utf-8")


def _command_event(*, attempt: int, elapsed_s: float, status: str, profile: str) -> str:
    return json.dumps({"action": "nlm_source_content_command_completed", "data": {
        "attempt": attempt, "elapsed_s": elapsed_s, "status": status,
        "notebooklm_profile": profile, "worker_id": "worker-01", "source_ready_age_s": 42.0,
    }})
```

- [ ] Run `python -m pytest -q tests/test_analyze_command_latency_attribution.py -k event` and confirm RED because event attribution is absent.
- [ ] Implement `iter_command_events()` and `aggregate_command_events()`.

Required behavior:

- derive phase/lane/batch using existing path context;
- accept only `nlm_source_content_command_completed` as commands;
- group attempts as `attempt_1`, `retry`, or `unknown`;
- retain worker/profile/status/elapsed/source age;
- count invalid JSON and missing required fields;
- count projection evidence separately without adding command elapsed.

- [ ] Rerun the event tests and confirm GREEN.

### Task 2: Add reconciliation and event deltas

**Files:**
- Modify: `scripts/analyze_command_latency_attribution.py`
- Test: `tests/test_analyze_command_latency_attribution.py`

- [ ] Add failing complete/incomplete reconciliation tests asserting `discriminating` at 100% coverage and `bounded` below 95%.
- [ ] Run `python -m pytest -q tests/test_analyze_command_latency_attribution.py -k reconciliation` and confirm RED.
- [ ] Implement count/elapsed reconciliation against `overall_rows[0]`:

```python
count_ratio = event_count / worker_count if worker_count else 0.0
elapsed_ratio = event_elapsed / worker_elapsed if worker_elapsed else 0.0
gate = "discriminating" if count_ratio >= 0.95 and elapsed_ratio >= 0.95 else "bounded"
```

- [ ] Add event comparison rows keyed by phase/lane/batch/profile/attempt class/status, sorted by elapsed delta.
- [ ] Rerun reconciliation tests and confirm GREEN.

### Task 3: Render event output without breaking aggregate output

**Files:**
- Modify: `scripts/analyze_command_latency_attribution.py`
- Test: `tests/test_analyze_command_latency_attribution.py`

- [ ] Add a failing report-contract test requiring `Attempt-1 Versus Retry Attribution`, `Top Event-Level Command Deltas`, `Projection Evidence`, and `Event Reconciliation Gate`, while preserving existing sections.
- [ ] Run `python -m pytest -q tests/test_analyze_command_latency_attribution.py -k report` and confirm RED.
- [ ] Render concise event, projection, and reconciliation tables. If any run is bounded, state that event-level causal interpretation is not authoritative.
- [ ] Run `python -m pytest -q tests/test_analyze_command_latency_attribution.py` and confirm all analyzer tests pass.

### Task 4: Generate and interpret run01 versus run08

**Files:**
- Generate: `.logs/sharded_lane_series/command_latency_attribution_packet_run01_vs_run08.md`
- Generate: `.logs/sharded_lane_series/command_latency_attribution_packet_run01_vs_run08.json`
- Modify: `docs/operations/hot-path-throughput-next-test-plan.md`

- [ ] Run:

```powershell
python scripts/analyze_command_latency_attribution.py `
  --run-root .logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_run01_current `
  --run-root .logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run08_current `
  --phase soak `
  --output .logs/sharded_lane_series/command_latency_attribution_packet_run01_vs_run08.md `
  --json-output .logs/sharded_lane_series/command_latency_attribution_packet_run01_vs_run08.json
```

- [ ] Confirm both summaries are valid `3+3`, `home_300mb`; record reconciliation and refuse causal interpretation below 95%.
- [ ] Update the operating plan with the dominant slice, attempt split, falsifier, and next action gate. Do not recommend a live run without a concrete mechanism and regression test.

### Task 5: Verify, critically review, and commit

**Files:**
- Verify all files above.

- [ ] Run:

```powershell
python -m pytest -q tests/test_analyze_command_latency_attribution.py
python -m py_compile scripts/analyze_command_latency_attribution.py tests/test_analyze_command_latency_attribution.py
git diff --check
```

- [ ] Request independent review of reconciliation math, event double-counting, path context, compatibility, and causal claims.
- [ ] Perform parent critical review covering proof limits, environment confounding, precision, delegation value, and whether another benchmark is justified.
- [ ] Commit scoped files only:

```powershell
git add -- scripts/analyze_command_latency_attribution.py tests/test_analyze_command_latency_attribution.py docs/operations/hot-path-throughput-next-test-plan.md docs/superpowers/plans/2026-06-18-command-latency-event-attribution.md
git commit -m "Add event-level command latency attribution"
```
