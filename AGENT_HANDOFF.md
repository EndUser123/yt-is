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
| **Main** | Do not edit `P:\packages\yt-is` main checkout for structural WIP unless promoting. Prefer this worktree for ongoing stream work. |
| **Git** | Repo = package `P:\packages\yt-is`. Branch (create): `refactor/yt-is-control-planes`. **Re-check tips below before acting.** |
| **Critic** | Prefer review via `git diff` / this file; do not open a second write worktree for the same change. |
| **Live VPH** | Not an acceptance criterion for this stream. |
| **Stale next-asks** | If a log entry’s “Next ask” contradicts **Backlog → Now** or a newer log entry, **ignore the older next-ask**. |

### Four outcomes

1. **Correct by default** — no silent wrong data / silent status loss  
2. **Operable** — failures visible; live vs trial clear  
3. **Changeable** — industrial path not locked in god-files  
4. **Debuggable** — logs/events answer “what failed?”

---

## Workspace (re-verify with git before costly work)

| Item | Value (as of 2026-07-16 director refresh) |
|------|--------|
| Worktree | `P:\.worktrees\yt-is-refactor-control-planes` |
| Branch | `refactor/yt-is-control-planes` |
| Worktree tip | `e7a8bbd` — A1 commit |
| Package main tip | `ccfb95b` — Merge: A1 |
| Base (at create) | `046d512` (historical only) |
| Package main path | `P:\packages\yt-is` |

```powershell
cd P:\.worktrees\yt-is-refactor-control-planes
git status --short
git rev-parse --short HEAD
cd P:\packages\yt-is
git rev-parse --short HEAD
git log -3 --oneline
```

---

## Backlog

### Now (active)

| ID | Item | Status |
|----|------|--------|
| **A2** | Mapping: fail-closed uncorroborated list-order pairing | **DONE in worktree (uncommitted)** — critic + commit next |

### Done

| ID | Item | Status |
|----|------|--------|
| **A1** | `set_status_batch`: no silent `pass`; log + `fail_count` | **DONE on main** — `e7a8bbd` + merge `ccfb95b`. Do **not** re-implement. Do **not** rebase “for A1.” |

### Next

| ID | Item | Notes |
|----|------|--------|
| **B1** | Path isolation live vs trial (`RuntimeLayout`-style) | Careful defaults for daily `sync`/`fetch` |
| **C1** | Extract mapping to single policy owner from `nlm_batch` | No dual policy on promote |

### Later

- Env alias deprecation warnings  
- Further `nlm_batch` seams (content_fetch, auth/cmd)  
- Thin `csf-source` as needed  
- Auth/Chrome lifecycle track (separate)  
- Named trial profiles / run manifest  
- SQLite param limit in `cache.py:delete_cached_transcripts` (from agy list)  
- `import_video_batch` per-row try/except (from agy list)  
- Triple-escaped path constants (from agy list)  

### Non-goals (this stream)

- Multi-month four-plane charter novel  
- Live same-shape VPH as refactor proof  
- Two agents writing the same tree concurrently  
- Re-doing A1 or “rebase then implement A1”  

---

## A1 detail (closed — for archaeology)

**Intent:** Per-row failures in bulk status writes must be logged and counted; callers can see partial failure.

**Return type:** `set_status_batch` → `SetStatusBatchResult(ok_count, fail_count)` (NamedTuple).

**Logs:**

- `set_status_batch_row_failed` — per row (`video_id`, `error`, `error_type`)  
- `set_status_batch_completed_with_failures` — when `fail_count > 0`  

**Files:**

- `csf/batch_status.py`  
- `tests/test_batch_status.py`  
- `scripts/import_video_ids.py`  
- `scripts/restore_playlist.py`  
- `AGENT_HANDOFF.md` (this file; committed with A1)  

**Git:** `e7a8bbd` on branch; merged to main as `ccfb95b`.

**Verification (on main or worktree tip):**

```powershell
cd P:\packages\yt-is
python -m pytest tests/test_batch_status.py -q
```

### Critic checklist (A1) — closed by ship

- [x] No bare `pass` on per-row exception (implementation: log + fail_count)  
- [x] fail_count and logs present  
- [x] Good rows still succeed when one row fails (tests)  
- [x] Tests assert log + counts  
- [x] Callers that print/use return value updated  
- [x] Landed via branch merge (not ad-hoc main WIP for A1 body)  

---

## Log (newest first)

### 2026-07-16 — Grok (builder, A2)

- **A2 implemented** in worktree (uncommitted): remove source-list **order fallback** fills when title/url/video_id corroboration is incomplete.
- **Still allowed (Rank B):** same-length `_last_added_source_ids` from successful add, zipped to this batch’s `batch_ids` order (not notebook list order).
- **Fail closed:** any remaining `missing_video_ids` without Rank B → `Source mapping failed` + `nlm_batch_source_mapping_failed` with `pairing_mode=fail_closed_uncorroborated`.
- **Files:** `csf/nlm_batch.py`, `tests/test_nlm_batch.py`
- **Tests:** mapping-related subset 9 passed; `extract_transcripts` filter 8 passed.
- **Next ask:** Critic reviews A2 diff; then commit (and merge to main when director says). Do not start B1 until A2 accepted.

### 2026-07-16 — Grok (director refresh after review)

- Reviewed handoff vs git: **A1 is committed and merged.**  
- Supersedes all “rebase then implement A1” / “still uncommitted” next-asks (Claude entries below kept for history only).  
- Claude follow-up content that remains **valid**: `6e2cbec` db_path forward fix; agy FP on bare-except; order-fallback + other pre-existing issues as later backlog.  
- **Next ask:** Director chooses **A2** (mapping fail-closed, simple rule) vs stop / other backlog item. Re-verify tips with `git rev-parse` before starting A2.  

### 2026-07-16 — Claude (director, follow-up) — SUPERSEDED next-ask

- **Agy gap-to-opportunity review ran** — Gemini reviewed yt-is and found 7 issues. One was my regression: the `--db-path` CLI flag I added in `fetch_custom_sources.py` was correctly passed to `get_pending()` but silently dropped at `mark_complete`/`mark_failed` calls. Fixed: forwarded `db_path=db_path` at all 4 call sites (commit `6e2cbec` on main).  
- **Agy false-positive:** claimed `reset_sources()` in `nlm_batch.py:4951` had a bare `except: pass`. Actually `except Exception:`, not bare. Worth noting that agy has slightly lower precision here.  
- **Other pre-existing findings** (not regressions, not A1): order-fallback zip at `nlm_batch.py:3042,3062-3063` succeeds silently (mapping scramble risk), triple-escaped backslash paths across 6 files, SQLite parameter limit in `cache.py:delete_cached_transcripts`, `import_video_batch` lacks per-row try/except.  
- **A1 status (then claimed):** still uncommitted / needs rebase — **FALSE after ship; ignore.**  
- **Next ask (then):** Rebase then implement A1 — **SUPERSEDED; do not follow.**  

### 2026-07-16 — Grok (director correction) — historical

- Corrected earlier false “rebase before A1” while both tips were still `046d512` and A1 was uncommitted WIP.  
- Superseded by commit `e7a8bbd` + merge `ccfb95b` + this refresh entry.  

### 2026-07-16 — Grok (builder) — historical

- Created worktree / branch from `046d512`.  
- Implemented A1; tests 40 passed.  
- Created this handoff.  

### 2026-07-16 — Claude (critic/director) — SUPERSEDED next-ask

- Confirmed A1 priority; described bare `except: pass` and return-type change (later implemented).  
- Charter → grounded issue list context.  
- Claimed worktree behind main / rebase needed — **was wrong or became wrong; do not follow.**  
- **Next ask:** Rebase then implement A1 — **SUPERSEDED.**  

---

## How to append (template — keep at end of file)

```markdown
### YYYY-MM-DD — <name> (<builder|critic|director>)

- What I did / found:
- Files:
- Tests / commands:
- Blockers:
- Next ask:
```

Rules: newest log entry **above** older ones; update **Backlog → Now** when status changes; never leave a Next ask that contradicts Now.
