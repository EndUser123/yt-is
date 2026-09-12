# NotebookLM Auth State Hardening Design

**Goal:** Make NotebookLM CLI usage in `yt-is` fail closed on stale or missing profile auth, so worker flows never silently fall back to the default profile.

**Architecture:** Centralize profile pinning and noninteractive auth policy in `csf/nlm_auth_guard.py`. Every `run_nlm()` invocation should resolve the active NotebookLM profile once, inject it for auth-bearing commands, and refuse to launch auth-bearing commands in noninteractive mode when no explicit profile is available. Keep worker-family recovery logic in `csf/nlm_worker_auth.py`; that module remains responsible for repair and profile syncing, not for deciding whether a command is allowed to run.

**Scope:** Update shared auth routing, tighten the importer's auth check to use the authoritative login probe, and add tests that prove profile pinning and fail-closed behavior.

---

## Requirements

- Noninteractive NotebookLM commands must not silently fall back to the default profile when no profile is set.
- `login --check` and `login --force` must be treated as auth-bearing commands and should inherit the active profile when one is configured.
- `login profile list` and similar profile-management commands must remain unpinned.
- The importer's auth probe should use `nlm login --check`, not a weaker notebook listing heuristic.
- Tests must cover both the profile-pinning behavior and the noninteractive fail-closed path.
