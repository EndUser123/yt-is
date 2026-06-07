# Lane Process Cadence Env Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the benchmark environment flags that determine whether a sharded lane run is cadence-enabled, so future lane_process manifests can be audited without guessing from live state.

**Architecture:** Extend the sharded lane runner's lane-process snapshot to include a small, explicit env summary derived from the launcher environment. Add one regression around the env export path and one regression around the written lane_process.json so the new field stays stable. Keep the payload narrow and diagnostic-only; do not change benchmark math or lane execution behavior.

**Tech Stack:** Python, pytest, existing `csf.sharded_lane_series` runner, JSON snapshots.

---

### Task 1: Add env snapshot coverage

**Files:**
- Modify: `P:/packages/yt-is/tests/test_sharded_lane_series.py`

- [ ] **Step 1: Write the failing test**

```python
def test_lane_env_exports_cadence_and_environment_flags(tmp_path):
    lane = LaneConfig(
        lane="pro",
        account_class="pro",
        workers=4,
        notebooklm_profile_prefix="ytis-pro-worker",
        notebooklm_profiles=("alt", "ytis-pro-worker-02", "ytis-pro-worker-03", "ytis-pro-worker-04"),
        browser_profile_root=Path(r"P:\.data\yt-is\browser\notebooklm-pro"),
        browser_profile_directory="Profile 2",
        worker_state_root=tmp_path / "pro" / "worker_states",
        notebook_prefix="benchmark-shard-pro",
    )
    env = _lane_env(
        {"YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED": "1"},
        lane,
        "serial",
        lane_output_root=tmp_path / "lane",
        worker_state_root=tmp_path / "state",
        run_environment_label="hotel_wifi",
    )
    assert env["YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED"] == "1"
    assert env["YTIS_NLM_RUN_ENVIRONMENT_LABEL"] == "hotel_wifi"
    assert env["YTIS_RUN_ENVIRONMENT_LABEL"] == "hotel_wifi"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_sharded_lane_series.py::test_lane_env_exports_cadence_and_environment_flags -q
```
Expected: FAIL because the runner does not yet surface the cadence env in a way the test can assert.

- [ ] **Step 3: Write minimal implementation**

Add a narrow env snapshot field to the lane process manifest path used by `_run_lane`.

```python
process_snapshot.update(
    {
        "env_snapshot": {
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED": env.get(
                "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED", ""
            ),
            "YTIS_NLM_RUN_ENVIRONMENT_LABEL": env.get("YTIS_NLM_RUN_ENVIRONMENT_LABEL", ""),
            "YTIS_RUN_ENVIRONMENT_LABEL": env.get("YTIS_RUN_ENVIRONMENT_LABEL", ""),
            "YTIS_NLM_WORKER_AUTH_USE_CDP": env.get("YTIS_NLM_WORKER_AUTH_USE_CDP", ""),
        },
    }
)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_sharded_lane_series.py::test_lane_env_exports_cadence_and_environment_flags -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_sharded_lane_series.py csf/sharded_lane_series.py docs/superpowers/plans/2026-06-03-lane-process-cadence-env-manifest.md
git commit -m "feat: record lane cadence env in process snapshot"
```

