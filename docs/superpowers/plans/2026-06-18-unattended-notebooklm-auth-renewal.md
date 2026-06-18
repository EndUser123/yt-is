# Unattended NotebookLM Auth Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore automatic worker-01 credential renewal through account-specific CDP roots without ever requiring user sign-in or permitting direct sibling/default-profile login.

**Architecture:** Keep `sync` and `doctor` non-interactive by default. For a mapped account family, validate worker-01, use only its configured dedicated CDP root for one bounded unattended renewal when needed, reject sign-in/challenge pages, restore the previous profile snapshot on failure, then copy verified credentials to siblings. Unknown profiles and direct sibling renewal remain fail closed.

**Tech Stack:** Python, pytest, NotebookLM CLI, Chrome DevTools Protocol, PowerShell process verification.

---

### Task 1: Restore bounded dedicated-CDP renewal

**Files:**
- Modify: `csf/nlm_worker_auth.py`
- Modify: `csf/sharded_lane_series.py`
- Test: `tests/test_nlm_worker_auth.py`
- Test: `tests/test_sharded_lane_series.py`

- [ ] Add a failing test proving non-interactive `refresh_source_profile()` may invoke `nlm login --profile <worker-01> --provider openclaw --cdp-url <family-port> --force`, but never plain sibling login or the shared default profile.
- [ ] Add a failing test proving a sign-in/challenge CDP target causes immediate failure, profile-state restoration, and dedicated-browser cleanup.
- [ ] Run the focused tests and confirm they fail because commit `f738284` returns before dedicated-CDP renewal.
- [ ] Remove the unconditional non-interactive early return and gate renewal on the configured family CDP root plus the unattended-page check.
- [ ] Let sharded preflight call `sync_worker_profiles()` with the normal family refresher; keep unknown profiles fail closed in non-interactive mode.
- [ ] Run focused auth/preflight tests until green.

### Task 2: Reconcile the operational contract

**Files:**
- Modify: `docs/operations/hot-path-throughput-next-test-plan.md`
- Modify: `docs/operations/sharded-lane-series.md`

- [ ] State that non-interactive means no user action, no direct sibling login, and no shared/default browser—not “no dedicated browser process.”
- [ ] Document the bounded worker-01 dedicated-CDP renewal and challenge-page fail-closed gate.
- [ ] Preserve profile pinning and prohibit `nlm login switch` in concurrent workers.

### Task 3: Verify, review, and commit

**Files:**
- Verify all files above.

- [ ] Run `git diff --check` and `python -m py_compile` for touched Python files.
- [ ] Run `pytest tests/test_nlm_worker_auth.py tests/test_nlm_worker_auth_doctor.py tests/test_sharded_lane_series.py -q`, recording the two known unrelated sharded-lane failures separately if they persist unchanged.
- [ ] Request spec-compliance review, then code-quality review.
- [ ] Commit only the scoped code, tests, plan, and auth documentation.

### Task 4: Run bounded auth-only gates

**Files:**
- Read: `.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run08_lanes.json`
- Do not create the run08 root unless doctor passes.

- [ ] Confirm no benchmark, auth helper, default NotebookLM browser, or dedicated auth browser is running.
- [ ] Renew and validate `ytis-pro-worker-01` through the Pro CDP root with a short timeout; fail and clean up if a challenge page appears.
- [ ] Renew and validate `ytis-free1-worker-01` through the Free CDP root with the same gate.
- [ ] Sync and validate the six run08 profiles through `csf-nlm-worker-auth doctor`.
- [ ] Launch run08 only when all checks pass without user interaction; otherwise stop with no benchmark artifacts.
