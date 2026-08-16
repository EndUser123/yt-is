# yt-is NLM Auth Architecture

**Status:** Canonical. Read this before touching anything NLM-auth-related.
**Last architecture/preflight verification:** 2026-08-08
**Owner:** solo developer (brsth)
**Authority:** this document supersedes any prior auth design in yt-is docs.

---

## TL;DR

The active path is account-first: an exact external identity selects one
canonical `storage_state.json` file, and each worker process owns its own
typed `notebooklm-py` client and event loop. Before work starts, the
account-specific preflight runs a read-only probe and, when needed, invokes
the durable non-interactive master-token repair path. If the account has no
master token, the explicit first-time bootstrap may use the already-established,
account-specific headless CDP family and wait for that exact account. Normal
active runs never open the shared/default profile or ask for a sign-in, and no
path copies state between accounts.
If that path fails, launch fails closed with an actionable reason.
`notebooklm-py 0.8.0+`, `gpsoauth`, and `playwright` are declared in
`requirements.txt` for this contract.

## Current failure-interpretation rule

The active runner must distinguish authentication/profile invalidation from
source-add/materialization invalidation. A generic phrase such as
`auth/source failures` is not sufficient evidence to request another login.
Use the exact account-specific probe and the raw lane events first. The
sharded runner emits `failure_category` values in invalidated lane reports:

- `auth_or_profile_artifacts`
- `source_add_or_materialization_artifacts`
- `mixed_auth_and_source_artifacts`

The second category is not an authentication diagnosis. It covers
`source_add_failed`, source-count/mapping failures, and materialization
failures. Only an immediate failed probe for the exact account in the lane
configuration, or an explicit auth/profile event, reopens account repair.

The 2026-08-08 `candidate6_telemetry_validation_run03` validation is preserved
as a concrete anti-regression example in
`.logs/sharded_lane_series/candidate6_telemetry_validation_run03_result.md`:
the `a.hominidae` and `troup.hominidae` probes passed, the six repeated source
IDs caused source-add invalidation in both lanes, and no valid VPH was
produced. Do not interpret its `0.00` combined value as throughput and do not
ask for another login based on that result.

---

## Why this exists

Previous auth models in yt-is failed repeatedly:

1. **Extracted CLI cookies** (`~/.notebooklm-mcp-cli/profiles/*/cookies.json`) — Google rotates `__Secure-*PSIDRTS` tokens frequently. Stale cookies cause `network_error: ClientAuthenticationError` even when the main session cookies are still valid by date.
2. **CDP browser-family refresh** (`csf/nlm_worker_auth.py` family machinery) — designed to propagate one source profile's cookies to sibling profiles via filesystem copy. In practice, the refresh was a 0.0-second no-op (red-team FM-4 finding, 2026-07-19), the `sync_worker_profiles` shutil.copy was race-prone, and the CDP browser profiles themselves kept getting wiped.
3. **Dedicated browser profiles** (`P:/.data/yt-is/browser/notebooklm*/`) — worked when populated, but were deleted (by cleanup scripts, worktree removal, or LLM-driven wipe) and all cookie files were found 0-byte on 2026-07-20. Multiple auth paths multiplied the failure surface.

The current model uses **one canonical file per external identity**, with one
path map and a separate protected backup per account. Path monopoly applies
within each account; account files are never merged.

---

## Architecture

### Canonical account storage

The active identity map is defined in `csf/nlm_auth_check.py`:

| External identity | Canonical storage | Expected email |
|---|---|---|
| `a.hominidae` | `P:/.data/yt-is/nlm-auth/storage_state.json` | `a.hominidae@gmail.com` |
| `troup.hominidae` | `P:/.data/yt-is/nlm-auth/storage_state_troup_hominidae.json` | `troup.hominidae@gmail.com` |
| `brsthomson` | `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json` | `brsthomson@hotmail.com` |

Each account also has a separate durable token at
`P:/.data/yt-is/nlm-auth/master-tokens/<account-profile>.json`. The token is
the account-scoped repair source for re-minting fresh cookies and must never
be copied between identities.

`inspect_account_storage()` checks the exact path, non-empty valid JSON, and
stored email. Missing, empty, invalid, expired, or mismatched state is a
preflight failure. Worker labels such as `a.hominidae-worker-02` are routing
and telemetry names only; they never select authentication state.

The active client uses `NotebookLMClient.from_storage(path=...)`, not a
browser profile. Each worker subprocess creates its own `NLMSyncClient`, event
loop, HTTP client, worker state path, and notebook title. The lane-level
`browser_profile_root` fields retained in old manifests are compatibility
telemetry and are not an active authentication boundary.

### Durable non-interactive renewal and first-time bootstrap

The active bridge calls `csf.nlm_auth_headless.ensure_account_session()`. Its
order is:

1. Probe the exact canonical storage/account.
2. If the canonical file is missing, empty, or structurally invalid, restore
   only that exact account from the protected backup and probe again. A
   valid-but-expired file is never replaced by an older backup.
3. If expired, re-mint from the matching durable master token under an
   account-specific inter-process lock.
4. If the token does not exist, launch or attach to the exact dedicated
   headless CDP family, wait for that one exact account to appear, capture a
   one-use OAuth token from the same account, create the durable master token,
   mint canonical storage, and probe again. The package CLI also accepts one
   explicit loopback CDP endpoint for this one-time bootstrap. Start the CLI
   before signing in; it waits while the operator completes sign-in in the
   dedicated context. The endpoint is restricted to one exact profile and the
   context must expose only the expected email; `--all` cannot share it.
5. If any account check, token binding, CDP check, or post-repair probe fails,
   stop. No interactive login or cross-account fallback is attempted.

The only operator-dependent event is the exceptional first-time bootstrap when
the dedicated browser root is not already signed in. The bootstrap command
waits for the exact account while the operator signs in; it must pass
exact-account discovery before any one-use token is consumed. That event is
outside an active run; normal renewal and overnight operation use the master
token with no browser and no human in the loop. The implementation is in
`csf/nlm_auth_headless.py`; the public entry point is the YT-IS client wrapper,
not the legacy `nlm` CLI.

### Backup (one file per account)

**Location:** `C:\Users\brsth\.ytis-nlm-auth-backup\` (bare git repo)

**Properties:**
- No remote configured (`git remote -v` returns empty)
- `hooks/pre-push` is defense-in-depth; the keepalive remote is a fixed local
  path and no network remote is configured
- Lives outside `P:\` so workspace cleanup scripts cannot reach it
- Lives outside `~/.claude/`, `~/.grok/`, `~/.codex/` so agent cleanup cannot reach it

**Pre-push hook source** (at `C:\Users\brsth\.ytis-nlm-auth-backup\hooks\pre-push`):

```sh
#!/bin/sh
# This bare repo holds Google NotebookLM session cookies (one file per account).
# It must NEVER push to a remote. Block all pushes unconditionally.
# Restore only the matching account file; never copy one identity into another.
echo "PRE-PUSH BLOCKED: this repo holds Google session cookies and must never push to a remote." >&2
echo "If you genuinely need to push (you do not), remove this hook explicitly." >&2
exit 1
```

**Honest threat model (reviewer R-3):** the pre-push hook is defense-in-depth, not primary containment. It can be bypassed via `git push --no-verify` or by deleting the hook file. The actual primary containment is: (a) no remote configured, (b) the backup repo lives outside any path LLMs or cleanup scripts typically touch, (c) cookies never enter the yt-is repo (`.gitignore` blocks them). The hook is a third layer that catches careless `git push` invocations — it does not stop a determined adversary with shell access. This is the deliberate residual risk of the design; if it becomes insufficient, move the backup repo to encrypted offline storage.

**Backup command** (run after every successful keepalive, or manually after a fresh bootstrap):
```powershell
python -m csf.nlm_keepalive
```
The keepalive repairs each exact account from its matching durable master token
when needed, then probes it. It publishes backups for every account that is
healthy; an unhealthy account is reported by the exit code and does not prevent
healthy-account backups from landing.

### Restore (automatic for unusable files; operator command remains available)

`ensure_account_session()` automatically restores only the exact account whose
canonical file is missing, empty, or structurally invalid. It does not restore
over a valid-but-expired file; that case goes through the master-token repair
path. The same exact-account helper is available for operator maintenance:

```powershell
python -c "from csf.nlm_auth_check import restore_account_from_backup; raise SystemExit(0 if restore_account_from_backup('troup.hominidae') else 1)"
```

Replace the identity with the exact account being repaired. The helper checks
the embedded email before the atomic write; it never uses another account's
backup. No login is required unless the session itself has expired.

### Daily keepalive (all canonical accounts)

**Scheduled task:** `YtisNlmAuthKeepalive`, runs daily (03:00 local).

The installed task is expected to use the direct Python action from the package
working directory, start when available, run on battery, continue if power
changes, avoid idle-end cancellation, and ignore overlapping instances. The
package installer verifies those properties against the registered XML and
fails if they do not match.

Reinstall or repair the task from the package-owned definition:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File P:/packages/yt-is/scripts/install_nlm_keepalive_task.ps1
```

**Action:**
1. Invoke `python -m csf.nlm_keepalive --log-file
   P:/.data/yt-is/nlm-auth/keepalive.log` from `P:/packages/yt-is`
2. Repair each mapped account only through exact backup recovery or its
   matching durable master token; never launch a browser from the task
3. Call `client.notebooks.list()` once per account
4. Push one matching backup file for every healthy account
5. Log each unhealthy exact identity and continue checking the remaining
   accounts, then exit non-zero

**Purpose:** exercise the account session daily and refresh expired canonical
storage non-interactively when a durable master token exists. If first-time
bootstrap is still required, the task reports the exact account and the
package-owned bootstrap command instead of silently opening a browser.

**Exit codes** (for Task Scheduler monitoring):
- `0` — keepalive complete: probe OK, backup pushed
- `2` — one or more account files are missing and could not be restored (needs manual bootstrap)
- `3` — one or more sessions are not alive (needs account-specific re-bootstrap)
- `4` — session alive but backup push failed (Task Scheduler should flag this distinctly so the operator knows backups are not landing)

### yt-is preflight

Before an active fetch, the coordinator requires `YTIS_NLM_ACCOUNT_PROFILE`
and calls `ensure_account_session(account_profile, worker_id="coordinator",
allow_bootstrap=False)`. Active workers use the same token-only repair
boundary. This performs the cheap canonical session check, exact-account
backup recovery, and master-token repair, but never starts a browser or waits
for account interaction. First-time or human-assisted bootstrap is reserved
for the explicit `bin/csf-nlm-auth` command described above. A failed repair
writes a terminal failure receipt and the run stops before source work.

---

## What is NOT here anymore (intentionally removed)

| Path | Status | Why |
|---|---|---|
| `~/.notebooklm-mcp-cli/profiles/*/` (nlm CLI cookies) | Not used by yt-is | Stale-prone; CLI no longer primary path |
| `~/.notebooklm/profiles/<account>/` (per-account notebooklm-py) | Not used by yt-is | Replaced by the single yt-is-owned path |
| `P:/.data/yt-is/browser/notebooklm*/` (dedicated profiles) | First-time bootstrap only | Pro/Free1 compatibility roots may capture a matching account's one-use OAuth token; they are not the ordinary client boundary |
| `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json.browser_profile` | Free2 bootstrap source | Existing account-owned Chrome root for `brsthomson@hotmail.com`; verified against the exact account and never shared with another identity |
| `csf/nlm_worker_auth.py` family refresh helpers | Compatibility/maintenance only | Active launchers call `ensure_account_session`; the adapter reuses only its validated dedicated-root/CDP primitives |

The nlm CLI remains installed (useful for debugging) but is no longer in the critical path.

---

## Operating manual

### "I see a Google login prompt"

Do not log in from an active run. The normal path is non-interactive: run the
durable account command for the exact failing identity:
```powershell
python P:/packages/yt-is/bin/csf-nlm-auth --profile a.hominidae
```
Replace `a.hominidae` with the exact failing identity. The command first
auto-restores only missing/corrupt storage, then attempts durable master-token
repair. If no master token exists, its exceptional matching headless-CDP
bootstrap may wait for the exact account in the dedicated window; if it remains
unavailable, preserve the account-specific failure and do not start the
benchmark.

### "There is no durable master token yet"

Use a one-time loopback CDP attach for one exact identity only:

```powershell
python P:/packages/yt-is/bin/csf-nlm-auth `
  --profile a.hominidae `
  --cdp-url http://127.0.0.1:18870
```

Use `18870` for `a.hominidae`, `18871` for `troup.hominidae`, and `18872` for
`brsthomson` when using the package's dedicated CDP browser roots. These ports
are one-account bootstrap endpoints, not the user's normal Chrome session.

The attached browser context must be signed into only the requested Google
account. The command discovers the account email from the live NotebookLM
session before consuming the one-use OAuth token. It refuses non-loopback CDP
URLs, multiple visible accounts, and `--all --cdp-url`. Do not pass a shared
Chrome profile, use `--no-sandbox`, or copy cookies into canonical storage. Once
the command succeeds, run the exact-profile command again without `--cdp-url`
and then `python -m csf.nlm_keepalive` to verify and protect all accounts.

### "Restore didn't help, still prompted"

The session truly expired and no durable master token could repair it. The
package bootstrap command will attempt the matching dedicated CDP family and
fail closed if the expected account cannot be verified. If that family is
unavailable, use the explicit one-time loopback CDP command above with a
context containing only the exact account. Do not fall back to the shared/
default Chrome profile or to a human sign-in inside a benchmark run.
After a successful repair, run the account-specific durable auth command and
`python -m csf.nlm_keepalive` to refresh the protected backups.

### "Something deleted my auth file"

The preflight should auto-restore missing/corrupt storage. If it didn't, run
the exact-account command above and investigate what deleted it — check:
- Recent agent activity in `~/.claude/projects/`, `~/.grok/sessions/`
- Worktree cleanup scripts
- Any `Remove-Item` or `os.remove` that touched `P:/.data/yt-is/`

### "I want to add or repair an account"

Use one canonical storage file per exact identity from the table above and a
separate protected backup. Keep account files separate; never merge or copy
cookies between identities. Add a new identity only by changing the canonical
map, tests, and current docs together.

---

## Why this design won't decay

1. **One file per identity.** The explicit map prevents account ambiguity and cross-account copying.
2. **Filesystem isolation.** Backup lives where LLMs and cleanup scripts don't look.
3. **Local-only backup.** The keepalive constructs only a fixed local remote;
   the hook is defense-in-depth and the repo has no network remote.
4. **Read-only active preflight.** Missing or mismatched state fails before source work instead of mutating auth during a run.
5. **Daily token-only keepalive.** The second-most-common failure mode (expired
   canonical storage) is repaired when a matching durable master token exists;
   missing first-time bootstrap remains visible and account-specific.
6. **This document.** The third-most-common failure mode (someone "improving" the auth model and breaking it) is headed off by making the design explicit and the rationale visible.

---

## Open questions (acknowledged, deferred)

- **Account file availability.** The canonical map supports `a.hominidae`, `troup.hominidae`, and `brsthomson`; an account whose mapped file is absent is intentionally unavailable until repaired by the operator.
- **Legacy CLI cleanup.** Compatibility helpers and historical artifacts may still mention `ytis-*` names, but active configs and launch decisions must use exact account identities plus descriptive worker labels.

---

## Change log

- **2026-07-20:** Architecture established. Replaces all prior auth models (CLI cookies, CDP family refresh, dedicated browser profiles). Bootstrap complete (14,350-byte storage file at `P:/.data/yt-is/nlm-auth/storage_state.json`, 62 notebooks visible). Backup pushed to `C:\Users\brsth\.ytis-nlm-auth-backup\`. Pre-push hook blocks any push. Auto-restore verified by deleting live file and confirming preflight restored it from backup.
- **2026-08-07:** Multi-account keepalive/restore implementation verified and
  exercised. `a.hominidae`, `troup.hominidae`, and `brsthomson` each passed an
  identity-checked storage inspection and live read-only session probe.
  `python -m csf.nlm_keepalive` exited `0` and pushed the three matching files
  to the local bare backup. The backup writer now adopts existing local `main`
  history without force-pushing; focused keepalive tests pass. This establishes
  auth readiness, not throughput validation.
- **2026-08-08:** Keepalive hardened for unattended operation: it now uses the
  active token-only repair adapter, writes an explicit log file, preserves
  healthy-account backups when another account is unavailable, and is
  installed as a daily task by `scripts/install_nlm_keepalive_task.ps1`.
