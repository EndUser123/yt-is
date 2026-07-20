# yt-is NLM Auth Architecture

**Status:** Canonical. Read this before touching anything NLM-auth-related.
**Last verified working:** 2026-07-20
**Owner:** solo developer (brsth)
**Authority:** this document supersedes any prior auth design in yt-is docs.

---

## TL;DR

One Google login → one `storage_state.json` file → used by `notebooklm-py` for all NLM access. The file is protected against deletion by a local bare-repo backup. You should not need to log in again for months. If you ever see a login prompt, run the restore command (below) first; only re-bootstrap if the session truly expired.

---

## Why this exists

Previous auth models in yt-is failed repeatedly:

1. **Extracted CLI cookies** (`~/.notebooklm-mcp-cli/profiles/*/cookies.json`) — Google rotates `__Secure-*PSIDRTS` tokens frequently. Stale cookies cause `network_error: ClientAuthenticationError` even when the main session cookies are still valid by date.
2. **CDP browser-family refresh** (`csf/nlm_worker_auth.py` family machinery) — designed to propagate one source profile's cookies to sibling profiles via filesystem copy. In practice, the refresh was a 0.0-second no-op (red-team FM-4 finding, 2026-07-19), the `sync_worker_profiles` shutil.copy was race-prone, and the CDP browser profiles themselves kept getting wiped.
3. **Dedicated browser profiles** (`P:/.data/yt-is/browser/notebooklm*/`) — worked when populated, but were deleted (by cleanup scripts, worktree removal, or LLM-driven wipe) and all cookie files were found 0-byte on 2026-07-20. Multiple auth paths multiplied the failure surface.

The current model collapses to **one file, one location, one backup mechanism**. Path monopoly (per `root-cause-program.md:27`).

---

## Architecture

### Single source of truth

```
P:/.data/yt-is/nlm-auth/storage_state.json
```

This file is produced by `notebooklm-py`'s login flow (Playwright-format cookie storage). It is the ONLY auth file yt-is reads for NLM access. Everything that previously read CLI cookies, browser profiles, or family snapshots now reads this file.

### Bootstrap (one-time)

```powershell
python -m notebooklm login --storage P:/.data/yt-is/nlm-auth/storage_state.json --browser chrome
```

Opens Chrome. You log into Google once. The library writes the storage file. Done.

### Backup

**Location:** `C:\Users\brsth\.ytis-nlm-auth-backup\` (bare git repo)

**Properties:**
- No remote configured (`git remote -v` returns empty)
- `hooks/pre-push` script blocks any push attempt with a clear error message
- Lives outside `P:\` so workspace cleanup scripts cannot reach it
- Lives outside `~/.claude/`, `~/.grok/`, `~/.codex/` so agent cleanup cannot reach it

**Pre-push hook source** (at `C:\Users\brsth\.ytis-nlm-auth-backup\hooks\pre-push`):

```sh
#!/bin/sh
# This bare repo holds Google NotebookLM session cookies (storage_state.json).
# It must NEVER push to a remote. Block all pushes unconditionally.
# To restore storage_state.json from backup:
#   git -C C:\Users\brsth\.ytis-nlm-auth-backup show HEAD:storage_state.json > P:/.data/yt-is/nlm-auth/storage_state.json
echo "PRE-PUSH BLOCKED: this repo holds Google session cookies and must never push to a remote." >&2
echo "If you genuinely need to push (you do not), remove this hook explicitly." >&2
exit 1
```

**Honest threat model (reviewer R-3):** the pre-push hook is defense-in-depth, not primary containment. It can be bypassed via `git push --no-verify` or by deleting the hook file. The actual primary containment is: (a) no remote configured, (b) the backup repo lives outside any path LLMs or cleanup scripts typically touch, (c) cookies never enter the yt-is repo (`.gitignore` blocks them). The hook is a third layer that catches careless `git push` invocations — it does not stop a determined adversary with shell access. This is the deliberate residual risk of the design; if it becomes insufficient, move the backup repo to encrypted offline storage.

**Backup command** (run after every successful keepalive, or manually after a fresh login):
```powershell
# In a scratch working tree of the backup repo
$tmp = New-Item -ItemType Directory -Path "$env:TEMP\ytis-auth-backup-$(Get-Date -Format yyyyMMddHHmmss)" -Force
Set-Location $tmp.FullName
git init
git remote add backup C:/Users/brsth/.ytis-nlm-auth-backup
Copy-Item P:/.data/yt-is/nlm-auth/storage_state.json .
git add storage_state.json
git -c user.email=ytis-local@local -c user.name="yt-is local backup" commit -m "backup $(Get-Date -Format o)"
git push backup main
Set-Location $env:TEMP
Remove-Item -Recurse -Force $tmp.FullName
```

### Restore

If `P:/.data/yt-is/nlm-auth/storage_state.json` is missing or 0 bytes:

```powershell
git -C C:/Users/brsth/.ytis-nlm-auth-backup show HEAD:storage_state.json > P:/.data/yt-is/nlm-auth/storage_state.json
```

This single command restores the most recent backup. No login required unless the session itself has expired (Google-side).

### Weekly keepalive

**Scheduled task:** `YtisNlmAuthKeepalive`, runs weekly (Sunday 03:00 local).

**Action:**
1. Load `storage_state.json` via `notebooklm-py`
2. Call `client.notebooks.list()` (one cheap API call)
3. On success: push the (possibly refreshed) storage file to the backup repo
4. On failure: log clearly, do nothing destructive

**Purpose:** tells Google the session is still active so it doesn't expire for inactivity. If Google still expires it, the keepalive surfaces the failure visibly (not as a silent browser popup).

**Exit codes** (for Task Scheduler monitoring):
- `0` — keepalive complete: probe OK, backup pushed
- `2` — storage missing and auto-restore failed (needs manual bootstrap)
- `3` — session not alive (needs re-bootstrap via `python -m notebooklm login`)
- `4` — session alive but backup push failed (Task Scheduler should flag this distinctly so the operator knows backups are not landing)

### yt-is preflight

Before any fetch or NLM operation, yt-is checks:
- `storage_state.json` exists AND is non-empty
- If missing: try auto-restore from backup repo; if backup also missing, fail with `Run: python -m notebooklm login --storage P:/.data/yt-is/nlm-auth/storage_state.json --browser chrome`

This prevents the failure mode where a deleted cookie file silently causes "Auth failed" 30 minutes into a fetch.

---

## What is NOT here anymore (intentionally removed)

| Path | Status | Why |
|---|---|---|
| `~/.notebooklm-mcp-cli/profiles/*/` (nlm CLI cookies) | Not used by yt-is | Stale-prone; CLI no longer primary path |
| `~/.notebooklm/profiles/<account>/` (per-account notebooklm-py) | Not used by yt-is | Replaced by the single yt-is-owned path |
| `P:/.data/yt-is/browser/notebooklm/` (dedicated profile) | Gone — not recreated | Persistent Chrome profile was the right idea but kept getting wiped; `storage_state.json` is more durable |
| `P:/.data/yt-is/browser/notebooklm-pro/`, `notebooklm-free/`, `notebooklm-free-2/` | Not used by yt-is | CDP-family-refresh machinery (red-team FM-4: 0.0s no-op) |
| `csf/nlm_worker_auth.py` `DEFAULT_FAMILIES`, `AuthFamily`, `refresh_source_profile`, `sync_worker_profiles` | To be removed (migration phase 6) | Replaced by single-file model |

The nlm CLI remains installed (useful for debugging) but is no longer in the critical path.

---

## Operating manual

### "I see a Google login prompt"

Don't log in immediately. Run restore first:
```powershell
git -C C:/Users/brsth/.ytis-nlm-auth-backup show HEAD:storage_state.json > P:/.data/yt-is/nlm-auth/storage_state.json
```
Then retry whatever triggered the prompt. Restore handles 90% of "login prompt" cases because the usual cause is the live file getting deleted/zeroed, not the session actually expiring.

### "Restore didn't help, still prompted"

The session truly expired (Google-side). Re-bootstrap:
```powershell
python -m notebooklm login --storage P:/.data/yt-is/nlm-auth/storage_state.json --browser chrome
```
Log in once. Then push a fresh backup (see Backup command above).

### "Something deleted my auth file"

The preflight should auto-restore. If it didn't, restore manually (see above). Then investigate what deleted it — check:
- Recent agent activity in `~/.claude/projects/`, `~/.grok/sessions/`
- Worktree cleanup scripts
- Any `Remove-Item` or `os.remove` that touched `P:/.data/yt-is/`

### "I want to add a second Google account"

Same pattern, different paths:
- `P:/.data/yt-is/nlm-auth/storage_state_<account>.json`
- `C:/Users/brsth/.ytis-nlm-auth-backup-<account>/` (separate bare repo, same protection)

Keep one account per storage file. Don't merge.

---

## Why this design won't decay

1. **One file, not many.** Path monopoly. Nothing to drift between.
2. **Filesystem isolation.** Backup lives where LLMs and cleanup scripts don't look.
3. **Push-blocked backup.** Even if an agent discovers the backup repo, it cannot push credentials anywhere.
4. **Auto-restore preflight.** The most common failure mode (file deletion) is self-healing.
5. **Weekly keepalive.** The second-most-common failure mode (Google inactivity expiry) is prevented.
6. **This document.** The third-most-common failure mode (someone "improving" the auth model and breaking it) is headed off by making the design explicit and the rationale visible.

---

## Open questions (acknowledged, deferred)

- **Profile-name → account mapping simplification.** yt-is still uses `ytis-pro-worker-01..05` etc. as routing labels even though there's now one storage file. This is preserved per user direction (2026-07-20): "keep profile-name mapping for now, note to look at it in the future." Revisit when adding a second account or simplifying the worker dispatch.
- **Migration phases 3-7** of the nlm-CLI → notebooklm-py refactor remain paused in worktree `refactor/nlm-migration-20260720-114644`. The auth model is settled (this doc); the source-add bug fix (Phase 2) is committed but not yet end-to-end verified because auth was unstable. Resume migration after this auth model is bootstrapped and verified stable.
- **Single-account limitation.** Currently `a.hominidae@gmail.com` (pro tier) only. Free-tier accounts (`troup.hominidae`, `brsthomson`) are not in use. If quota becomes an issue, add them via the multi-account pattern above.

---

## Change log

- **2026-07-20:** Architecture established. Replaces all prior auth models (CLI cookies, CDP family refresh, dedicated browser profiles). Bootstrap complete (14,350-byte storage file at `P:/.data/yt-is/nlm-auth/storage_state.json`, 62 notebooks visible). Backup pushed to `C:\Users\brsth\.ytis-nlm-auth-backup\`. Pre-push hook blocks any push. Auto-restore verified by deleting live file and confirming preflight restored it from backup. Weekly keepalive scheduled task `YtisNlmAuthKeepalive` registered (next run Sunday July 26, 3am local).
