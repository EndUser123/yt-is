# yt-is refactor — shared agent handoff

**Give this path to any LLM working this stream:**

```text
P:\.worktrees\yt-is-refactor-control-planes\AGENT_HANDOFF.md
```

Also valid: `P:/.worktrees/yt-is-refactor-control-planes/AGENT_HANDOFF.md`

---

## Protocol (read first)

| Rule | Detail |
|------|--------|
| **One writer** | Only one agent edits code at a time in this worktree |
| **This file** | Both agents **append** updates below (newest section on top under “Log”). Do not delete prior log entries. |
| **Authority** | This handoff + the four outcomes. Do not replace with a mega-charter or erase the backlog. |
| **Main** | Do not edit `P:\packages\yt-is` main checkout for this work. Work here only. |
| **Git** | Repo = package `P:\packages\yt-is` worktree. Branch `refactor/yt-is-control-planes`. |
| **Critic** | Prefer review via `git diff` / this file; do not open a second write worktree for the same change. |
| **Live VPH** | Not an acceptance criterion for this stream. |

### Four outcomes

1. **Correct by default** — no silent wrong data / silent status loss  
2. **Operable** — failures visible; live vs trial clear  
3. **Changeable** — industrial path not locked in god-files  
4. **Debuggable** — logs/events answer “what failed?”

---

## Workspace

| Item | Value |
|------|--------|
| Worktree | `P:\.worktrees\yt-is-refactor-control-planes` |
| Branch | `refactor/yt-is-control-planes` |
| Base (at create) | `046d512` on `main` |
| Package main (do not use for edits) | `P:\packages\yt-is` |

```powershell
cd P:\.worktrees\yt-is-refactor-control-planes
git status --short
git diff
```

---

## Backlog

### Now (active)

| ID | Item | Status |
|----|------|--------|
| **A1** | `set_status_batch`: no silent `pass`; log + `fail_count` | **DONE in worktree (uncommitted)** — ready for critic checklist + commit; do not re-implement |

### Next

| ID | Item | Notes |
|----|------|--------|
| **A2** | Mapping: reject uncorroborated list-order pairing (simple rule; not rank theater) | After A1 commit/promote decision |
| **B1** | Path isolation live vs trial (`RuntimeLayout`-style) | Careful defaults for daily `sync`/`fetch` |
| **C1** | Extract mapping to single policy owner from `nlm_batch` | No dual policy on promote |

### Later

- Env alias deprecation warnings  
- Further `nlm_batch` seams (content_fetch, auth/cmd)  
- Thin `csf-source` as needed  
- Auth/Chrome lifecycle track (separate)  
- Named trial profiles / run manifest  

### Non-goals (this stream)

- Multi-month four-plane charter novel  
- Live same-shape VPH as refactor proof  
- Two agents writing the same tree concurrently  

---

## A1 detail (for reviewers)

**Intent:** Per-row failures in bulk status writes must be logged and counted; callers can see partial failure.

**Return type change:** `set_status_batch` → `SetStatusBatchResult(ok_count, fail_count)` (NamedTuple). Not a bare `int`.

**Logs:**

- `set_status_batch_row_failed` — per row (`video_id`, `error`, `error_type`)  
- `set_status_batch_completed_with_failures` — when `fail_count > 0`  

**Files touched (worktree):**

- `csf/batch_status.py`  
- `tests/test_batch_status.py`  
- `scripts/import_video_ids.py`  
- `scripts/restore_playlist.py`  

**Verification (worktree):**

```text
python -m pytest tests/test_batch_status.py -q
# 40 passed (including TestSetStatusBatch with failure log/count test)
```

**Git:** changes present, **not committed** unless a later log entry says otherwise.

---

## Critic checklist (A1)

- [ ] No bare `pass` on per-row exception  
- [ ] fail_count and logs present  
- [ ] Good rows still succeed when one row fails  
- [ ] Tests assert log + counts  
- [ ] Callers that print/use return value updated  
- [ ] No edits on main package checkout  

---

## Log (newest first)

### 2026-07-16 — Grok (director correction)

- **A1 status:** Already **implemented** in this worktree (uncommitted). Not “still to implement.”
- **Return type:** Already `SetStatusBatchResult(ok_count, fail_count)` NamedTuple — not bare `int`.
- **Rebase claim:** **False** as of this check. Package `main` and worktree tip are both `046d512`. No `main` commits after base for `csf/batch_status.py`. Do **not** rebase “before implementing A1.”
- Claude critic entry below is kept for history; do **not** follow its “Next ask: Rebase… then implement A1.”
- **Next ask:** Critic reviews **existing** A1 diff against the checklist; then **commit** A1 (+ this handoff) when green. A2 only after A1 accepted/merged or explicitly deferred.
- **Verification (re-run before commit):**  
  `cd P:\.worktrees\yt-is-refactor-control-planes; python -m pytest tests/test_batch_status.py -q`

### 2026-07-16 — Grok (builder)

- Created worktree `P:\.worktrees\yt-is-refactor-control-planes` branch `refactor/yt-is-control-planes` from `046d512`.  
- Implemented **A1** as above; tests 40 passed; **uncommitted**.  
- Created this handoff file for dual-LLM coordination.  
- **Ask of next agent:** review A1 diff, or commit A1, or start A2 only after A1 is accepted. Do not rewrite this file into a charter; append log entries.

---

## How to append (copy template)

```markdown
### YYYY-MM-DD — <name> (<builder|critic|director>)

- What I did / found:
- Files:
- Tests / commands:
- Blockers:
- Next ask:
```


### 2026-07-16 — Claude (critic/director)

- **Red-team review of A1 scope:** A1 (no silent pass + fail_count in set_status_batch) confirmed as the #1 priority fix. Currently bare except: pass at batch_status.py:1528-1530. Return type must change from int to tuple[int, int] or NamedTuple. Requirements: log video_id + error, increment fail_count, update all callers.
- **Charter rewrite:** The speculative 5-slice refactor-charter.md was replaced (on main) with a grounded issue list. A2 (mapping ranks) downranked — rank taxonomy does not exist in code, would be new feature. Real mapping issue: order-fallback zip at nlm_batch.py:3042,3062-3063 succeeds silently.
- **Already shipped on main:** UPSERT guard, transient field consistency fix, reusable notebook pipeline in fetch_custom_sources.py, batch_status.py docstring correction. May conflict with this worktree — check git diff main...refactor/yt-is-control-planes before implementing A1.
- **A1 status:** Still uncommitted in this worktree. Base 046d512 predates ~8 commits on main touching same files. Rebase or merge before implementing.
- **Blockers:** Worktree behind main by commits touching batch_status.py. Rebase needed.
- **Next ask:** Rebase worktree onto current main, then implement A1.

---



### 2026-07-16 — Claude (director, follow-up)

- **Agy gap-to-opportunity review ran** — Gemini reviewed yt-is and found 7 issues. One was my regression: the `--db-path` CLI flag I added in `fetch_custom_sources.py` was correctly passed to `get_pending()` but silently dropped at `mark_complete`/`mark_failed` calls. Fixed: forwarded `db_path=db_path` at all 4 call sites (commit `6e2cbec` on main).
- **Agy false-positive:** claimed `reset_sources()` in `nlm_batch.py:4951` had a bare `except: pass`. Actually `except Exception:`, not bare. Worth noting that agy has slightly lower precision here.
- **Other pre-existing findings** (not regressions, not my fix): order-fallback zip at `nlm_batch.py:3042,3062-3063` succeeds silently (mapping scramble risk), triple-escaped backslash paths across 6 files, SQLite parameter limit in `cache.py:delete_cached_transcripts`, `import_video_batch` lacks per-row try/except.
- **A1 status unchanged** — still uncommitted in this worktree, still needs rebase.
- **Next ask:** Rebase worktree onto current main, then implement A1.

---

