# NotebookLM Auth State Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize NotebookLM profile pinning and noninteractive auth guardrails so stale default-profile auth cannot leak into worker flows.

**Architecture:** Make `csf.nlm_auth_guard.run_nlm()` the shared policy gate for NotebookLM CLI invocations. It should infer the active profile from the current environment or explicit args, inject `--profile` for auth-bearing commands, and return a clear failure instead of launching default-profile auth in noninteractive mode when no profile is available. Keep repair and recovery in `csf.nlm_worker_auth.py`; keep importer and scraper wrappers thin and explicit.

**Tech Stack:** Python 3.14, pytest, existing `csf` modules.

---

### Task 1: Harden shared NotebookLM command routing

**Files:**
- Modify: `P:/packages/yt-is/csf/nlm_auth_guard.py`
- Test: `P:/packages/yt-is/tests/test_nlm_auth_guard.py`

- [ ] **Step 1: Add failing tests**

Add tests that prove:

- `nlm_auth_guard.add_profile_args(["login", "--check"])` pins `--profile` when `NOTEBOOKLM_PROFILE` is set.
- `nlm_auth_guard.add_profile_args(["login", "profile", "list"])` stays unpinned.
- `nlm_auth_guard.run_nlm(["notebook", "list"], timeout_s=1)` injects the active profile automatically.
- `nlm_auth_guard.run_nlm(["notebook", "list"], timeout_s=1)` returns a nonzero synthetic failure and does not call `subprocess.run` when `YTIS_NLM_AUTH_NONINTERACTIVE=1` and no profile is available.

- [ ] **Step 2: Implement the routing changes**

Make `run_nlm()` call `add_profile_args()` internally, add a small helper that recognizes profile-management login subcommands, and return a clear `CompletedProcess` failure when a profile-bearing command would otherwise run without an explicit profile in noninteractive mode.

- [ ] **Step 3: Run the focused auth-guard tests**

Run:

```powershell
$env:PYTHONPATH = 'P:/packages/yt-is'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
pytest P:/packages/yt-is/tests/test_nlm_auth_guard.py -q
```

Expected: the new auth-guard tests pass.

### Task 2: Make importer auth explicit

**Files:**
- Modify: `P:/packages/yt-is/csf/csf_nlm_import.py`
- Test: `P:/packages/yt-is/tests/test_csf_nlm_import.py`

- [ ] **Step 1: Add failing tests**

Add tests that prove `check_auth()` uses `nlm login --check` and `ensure_auth()` retries with a profile-pinned `nlm login --force`.

- [ ] **Step 2: Update the importer**

Switch `check_auth()` from notebook listing to the authoritative login check and keep the reauth fallback profile-pinned.

- [ ] **Step 3: Run the importer tests**

Run:

```powershell
$env:PYTHONPATH = 'P:/packages/yt-is'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
pytest P:/packages/yt-is/tests/test_csf_nlm_import.py -q
```

Expected: the importer tests pass.

### Task 3: Verify the shared auth path end-to-end

**Files:**
- Test: `P:/packages/yt-is/tests/test_nlm_worker_auth.py`
- Test: `P:/packages/yt-is/tests/test_nlm_batch.py`

- [ ] **Step 1: Confirm no regressions in worker auth**

Run the worker auth tests that already cover profile refresh and source-profile syncing.

- [ ] **Step 2: Confirm no regressions in batch auth**

Run the batch auth tests that already cover noninteractive fail-closed behavior and family refresh routing.

- [ ] **Step 3: Run the full auth-focused subset**

Run:

```powershell
$env:PYTHONPATH = 'P:/packages/yt-is'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
pytest P:/packages/yt-is/tests/test_nlm_auth_guard.py P:/packages/yt-is/tests/test_csf_nlm_import.py P:/packages/yt-is/tests/test_nlm_worker_auth.py P:/packages/yt-is/tests/test_nlm_batch.py -q
```

Expected: all auth-focused tests pass.
