# yt-is critical review — 2026-07-17

**Package:** `P:\packages\yt-is`  
**Profile:** read-only adversarial review via `/go` (then named `/grok-sdlc`)  
**Package HEAD (at original review):** `main @ ccfb95b`  
**Package HEAD (re-verify 2026-07-17):** `main @ 4cc2dd7` (includes A1 merge `ccfb95b`, doc handoff)  
**Refactor branch tip (re-verify):** `refactor/yt-is-control-planes @ 0d22eb4` (A2 mapping logic; supersedes `2b96382`)  
**Worktree:** `P:\.worktrees\yt-is-refactor-control-planes`  
**A2 merge attempt 2026-07-17:** merged into worktree `P:\.worktrees\yt-is-merge-a2` (branch `merge-a2`) → **39 test failures, NOT merged to main** (see finding #1 + claim ledger).  
**Refactor skill hardening handoff:** `docs/operations/refactor-skill-handoff.md` (design only; not yet built into `~/.grok/skills/refactor/SKILL.md`)
**Scope:** architecture, correctness, data integrity, concurrency, auth, doc authority  
**Not in scope (original review):** live benchmarks, DB mutation  

**Supporting artifacts:**

| Artifact | Role |
|---|---|
| This file | Ranked findings + claim ledger (durable) |
| `P:\tmp\source-discovery-yt-is-20260716-235421.json` | Source-authority inventory (~7MB); **not** a substitute for this ledger |
| Session chat + explore subagents | Architecture critic, correctness/security critic, entrypoint map |
| Worktree handoff | `P:\.worktrees\yt-is-refactor-control-planes\AGENT_HANDOFF.md` |

**Overall verdict:** `needs_attention` (A1 closed on main; A2 still open on main — present only on refactor branch)

---

## How to read evidence labels

| Label | Meaning |
|---|---|
| **verified** | Parent or critic claim re-checked against source in the review session (file:line or direct read) |
| **critic_static** | Strong static finding from critic subagent; not re-run as a concurrent test in this session |
| **inference** | Plausible from call graph / docs; needs a fixture or live artifact to promote |
| **historical** | From HANDOFF / prior packets; not re-derived this session |

Do not promote **inference** to implement-or-benchmark authority without a falsifier test.

---

## What looks strong

- Process scar tissue: claim ledger, decision packets, parent handoff, ban on same-shape live runs without a packet (`AGENTS.md`, `HANDOFF.md`).
- HANDOFF self-corrects bad metrics (tautological reconciliation, `primary_batch_wait` as artifact).
- Routing strategy for backlog: sticky terminal skip; live → fallback; `no_captions` → NotebookLM by default.
- Staging/promote + backup CLIs for transcripts and channel state (CLI fail-closed).
- Broad test surface (~72–75 test modules vs ~53–57 `csf` modules).
- Production default batch size **50** in `nlm_config.py` (**verified**).

---

## Implementation status (post-review — re-verified 2026-07-17)

| ID | Intent | Status | Evidence |
|---|---|---|---|
| **A1** | `set_status_batch`: no silent `pass`; log + `fail_count` | **Shipped on main** | `e7a8bbd` + merge `ccfb95b`. `SetStatusBatchResult(ok_count, fail_count)` at `csf/batch_status.py:31–39`; per-row `except` logs `set_status_batch_row_failed` and increments `fail_count` (~1543–1568); public API returns the NamedTuple (~2188–2207). |
| **A2** | Fail-closed uncorroborated list-order source mapping | **Committed on branch; merge BLOCKED (broken tests)** | `0d22eb4` (tip of `refactor/yt-is-control-planes`; supersedes `2b96382`). Diff removes the `elif missing_video_ids` fallback zip at `nlm_batch.py:3054-3063` (the uncorroborated gap-fill), keeps the corroborated canonical bind at `:3042` (set from add-stdout order at `:2432`), adds `pairing_mode` telemetry. **Main still has the order zip** (see finding #1). **Merge attempt 2026-07-17 FAILED:** merging the branch into worktree `P:\.worktrees\yt-is-merge-a2` passed all 9 mapping/fail-closed tests but broke 39 unrelated tests (`TestNotebookCapRotation`, `TestCandidate6Instrumentation`) — the branch's `tests/test_nlm_batch.py` (105 lines changed) drifted from current `main`. Do NOT merge the branch as-is. |

**Not yet merged to main:** A2. **Not claimed fixed on main:** findings #2–#7 and remaining P1 items below.

---

## P0 — Data integrity

### 1. Positional / zip source→video mapping — **bug** — **verified** (main still open)

- **File:** `csf/nlm_batch.py` (~3042, ~3059–3063 on **main @ 4cc2dd7**)
- **Issue:** When lengths match, mapping uses `dict(zip(batch_ids, canonical_source_ids))` at `:3042` — this is the **corroborated** canonical bind (source IDs come from the add-response order, parsed at `:2432`), so it is NOT the bug. The bug is the **fallback `elif missing_video_ids` block at `:3054-3063`**: when title matches are incomplete, remaining IDs are filled by leftover-source-list-order zip (`for vid, source_id in zip(fallback_video_ids, fallback_source_ids)`) with no title/URL/video_id evidence — a wrong transcript can be cached under the wrong `video_id`.
- **Evidence (main, re-read 2026-07-17):** canonical bind `dict(zip(...))` at `:3042`; uncorroborated fallback fill at `:3059-3063` (`:3061-3063` zip). Blind cross-model verifier (mmx/MiniMax-M3) confirmed independently: "uncorroborated_label_accurate_for= the fallback loop," canonical path "arguably also lacks corroboration but is gated by prior title-match attempt."
- **Branch fix (A2, not on main):** `0d22eb4` — deletes the `elif missing_video_ids` fallback zip entirely; fails with `pairing_mode=fail_closed_uncorroborated`; keeps the corroborated canonical bind. **Correct target, correct logic.**
- **MERGE STATUS (2026-07-17):** Attempted merge into worktree `P:\.worktrees\yt-is-merge-a2` → **REVERTED/NOT MERGED**. Cause: the branch's `tests/test_nlm_batch.py` (105 lines changed) is stale vs current `main`; 39 unrelated tests fail (`TestNotebookCapRotation`, `TestCandidate6Instrumentation` assert log events the branch code no longer emits). Mapping tests (9) pass. The *fix* is sound; the *branch test file* is broken.
- **Suggestion (for next LLM):** Do NOT `git merge refactor/yt-is-control-planes` as-is — it will turn `main` red. Instead either (a) cherry-pick only the `csf/nlm_batch.py` A2 hunk + its 9 passing mapping tests onto current `main`, verify full suite green, then merge; or (b) rebase the branch onto current `main`, repair the 39 broken test classes, verify, then merge. No positional zip of notebook list order except explicit debug flag.

### 2. Transcript cache first-write-wins forever — **bug** — **verified**

- **File:** `csf/cache.py` (`_write_entry`, ~111–127)
- **Issue:** `INSERT OR IGNORE` permanently keeps first write for `(video_id, lang, source)` key; empty/bad content can block better content; promote cannot heal existing rows.
- **Evidence:** Direct read of INSERT OR IGNORE path.
- **Suggestion:** Reject empty transcripts; allow upgrade when existing is empty/shorter/error-marked.

### 3. Channel promote REPLACE + secondary UNIQUE(`channel_id`) — **bug** — **critic_static**

- **File:** `csf/batch_status.py` (`promote_batch_status_db` / `_copy_table_rows`, ~1993–2044)
- **Issue:** `INSERT OR REPLACE` plus unique `channel_id` can delete live rows / lose fields when URL forms differ for the same channel.
- **Evidence:** Critic static path; existing tests cover disjoint channel IDs, not same-identity dual-URL promote.
- **Falsifier test:** Promote staging `…/channel/UC1` over live `@handle` with same `channel_id` — live-only fields must survive.
- **Suggestion:** Merge-by-`channel_id` with explicit field policy; never blind REPLACE across secondary unique keys.

### 4. provider_score dual identity / NULL channel_id — **bug** — **critic_static**

- **File:** `csf/batch_status.py` (`_record_provider_result`, ~567–616; schema ~305–323)
- **Issue:** PK vs `ON CONFLICT(channel_id, provider)` mismatch; NULL `channel_id` fragments scores or raises IntegrityError on URL PK.
- **Suggestion:** Require `channel_id` on write; single conflict target; fail closed when resolve fails.

---

## P0 — Concurrency / multi-worker

### 5. Shared retry enqueue clears in-flight claims — **bug** — **verified**

- **File:** `csf/shared_retry_pool.py` (`enqueue`, ~139–145)
- **Issue:** `ON CONFLICT DO UPDATE` sets `claimed_by=NULL`, `claimed_at=NULL` — concurrent enqueue can unclaim mid-flight work.
- **Evidence:** Direct read of UPDATE clause.
- **Suggestion:** Only update when not actively claimed (or claimant matches); never clear a fresh claim unless stale.

### 6. Local retry_queue has no claim/lease — **bug** — **critic_static**

- **File:** `csf/retry_queue.py`
- **Issue:** Multi-terminal `get_pending` can process the same `video_id`.
- **Suggestion:** Prefer shared pool as sole cross-process queue, or add claim semantics.

### 7. Parallel industrial failure handoff ≠ serial — **bug** — **inference** (high)

- **Files:** `bin/csf-source`, `dev/worker_pool/worker_main.py`
- **Issue:** Serial industrial path re-queues NLM failures into `transcript_fallback`; parallel worker path largely tallies failures without the same handoff.
- **Falsifier:** Worker failure fixture showing failed IDs re-queued / `mark_failed` + fallback.
- **Suggestion:** Unify failure contract across serial and parallel.

---

## P1 — Auth / ops

### 8. `youtube_auth.validate_auth()` always True — **bug** — **verified**

- **File:** `csf/youtube_auth.py` (~44–50)
- **Evidence:** Function body is `return True`.
- **Suggestion:** Real probe or remove API so callers cannot trust a green light.

### 9. Auth-on-every `_run_cmd` + import-frozen knobs — **risk** — **historical** + architecture critic

- **Files:** `csf/nlm_batch.py`, `csf/nlm_config.py`
- **Issue:** HANDOFF (2026-07-01) routes next effort to durable auth (#965); auth churn suspected as VPH ceiling driver; config often frozen at import.
- **Suggestion:** Auth budget outside per-command path; config per process/worker start.

### 10. Cookie/profile sync races + hardcoded emails — **risk** — **critic_static**

- **File:** `csf/nlm_worker_auth.py`
- **Suggestion:** Atomic write (temp + replace); move account emails out of tree.

### 11. Process-local auth-check cache — **risk** — **critic_static**

- **Files:** `nlm_worker_auth.py` / `nlm_auth_guard.py`
- **Issue:** Can mask cross-process session death for TTL window; fail-open when expected email empty.

### 12. Library promote weaker than CLI — **risk** — **critic_static**

- **Files:** `bin/csf-promote-*` vs library promote APIs
- **Suggestion:** Move all fail-closed checks into library used by CLI and tests.

### 13. Staging column names interpolated into SQL — **risk** — **critic_static**

- **File:** `batch_status._copy_table_rows`
- **Suggestion:** Allowlist columns against dest schema.

---

## P1 — Architecture / maintainability

### 14. God modules — **risk** — **verified** (size)

| File | Approx size (review session) |
|---|---|
| `csf/nlm_batch.py` | ~6.3k lines |
| `bin/csf-source` | ~4.0k lines |
| `csf/nlm_scraper.py` | ~2.5k lines |
| `csf/batch_status.py` | ~2.1k lines |
| `csf/transcript.py` | ~2.0k lines |

### 15. Multiple NLM control planes — **risk** — **critic_static** / entrypoint map

- Industrial CLI + `worker_main`, DOM scraper (`nlm_scraper`), deprecated `csf_nlm_ingest` via `yt-nlm`.
- **Suggestion:** One primary plane documented; quarantine dead bootstrap.

### 16. Multiple SQLite planes + package-local copies — **risk** — **verified** (listing)

- Defaults under `P:\.data\yt-is\` (`batch_status.py`, `cache.py`).
- Package-local copies present: `packages/yt-is/batch_status.sqlite`, `csf/batch_status.sqlite`, `transcripts.sqlite`.
- **Suggestion:** Single path registry; quarantine in-repo DBs.

### 17. Doc dual-authority — **risk** — **verified** (samples)

| Claim | Source |
|---|---|
| Batch size 50 (production default) | `nlm_config.py` **verified** |
| README / older handoffs may claim 200 | doc drift |
| HANDOFF 2026-07-01: no same-shape live run without packet | current ops authority |
| CODEX_MEMORY 2026-04-20 | historical scars |
| AGENT_HANDOFF | refactor worktree stream only |

### 18. `block_channel` deletes analysis history — **risk** — **verified**

- **File:** `batch_status.py` (~1265–1285)
- **Issue:** DELETE from `analysis_status` (and metadata) on block — not soft block.
- **Suggestion:** Soft-block by default; explicit purge + backup.

### 19. Tautological per-attempt reconciliation — **suggestion** — **historical**

- HANDOFF already documents; do not treat as measured zero overhead.

### 20. Marketplace skills lag package skills — **risk** — **critic_static** (entrypoint map)

- `cc-skills-media` skills delegate to package bins (no second codebase) but shorter escalation chains / stale notes.
- Code remains source of truth.

---

## Entrypoint map (compact)

```
/yt-is skill → bin/yt-is → bin/csf-source
                              ├─ sync/add  → source_enumerator + batch_status
                              ├─ fetch industrial → nlm_batch + worker_main + auth*
                              └─ fetch surgical → transcript (+ selenium/whisper/cache)

/yt-nlm skill → bin/yt-nlm → csf_nlm_ingest  [deprecated]
                real industrial: csf-source fetch / nlm_batch

marketplace yt-is/yt-nlm → same package bins (docs may lag)
```

Full entrypoint inventory was produced by explore subagent 2026-07-17 (session); not duplicated here in full.

---

## Claim ledger

| Claim | Type | Evidence | Falsifier | Action allowed |
|---|---|---|---|---|
| Source mapping can zip by position **on main** | verified | main `nlm_batch.py:3042`, `3062–3063` (read 2026-07-17) | Path removed/gated on main | Do not trust industrial cache until A2 merged |
| A2 fail-closed mapping **on branch** | verified | `0d22eb4` `nlm_batch.py` (deletes `elif missing_video_ids` fallback zip at `:3054-3063`; keeps corroborated canonical bind `:3042`) | Branch reverts or merge reintroduces zip | **NOT safe to merge as-is**: branch `tests/test_nlm_batch.py` is stale → 39 unrelated failures (2026-07-17). Cherry-pick the `nlm_batch.py` hunk + 9 mapping tests, or rebase+repair tests first. |
| A1 `set_status_batch` no silent pass **on main** | verified | `batch_status.py` `SetStatusBatchResult` + row fail logs (`ccfb95b` / current main) | Bare `pass` returns | Closed — do not re-implement |
| Cache first-write permanent | verified | `cache.py` INSERT OR IGNORE | Upgrade path exists | Fix empty/poison policy |
| Enqueue unclaims in-flight | verified | `shared_retry_pool.py:143–145` | Conditional UPDATE | Fix before multi-worker scale |
| `validate_auth` is no-op | verified | `youtube_auth.py:50` | Real probe | Do not trust callers |
| `block_channel` deletes status | verified | `batch_status.py:1282–1285` | Soft-block only | Fix before bulk block ops |
| Production batch size default 50 | verified | `nlm_config.py` | Config change | Prefer over README “200” |
| Promote REPLACE loses dual-URL rows | critic_static | promote path + UNIQUE channel_id | Dual-URL promote fixture | Fix + test or gather fixture evidence |
| provider_score NULL/PK mismatch | critic_static | critic | NULL channel_id score test | Fix + test |
| Parallel path drops fallback requeue | inference | critic call graph | Worker failure fixture | Confirm before large rewrite |
| 3788 VPH is optimal | unsupported (HANDOFF) | HANDOFF | — | Do not authorize “optimize” live runs |

---

## Recommended next actions (no live benchmark)

1. **Fix + merge A2 — but NOT by merging the branch as-is.** The branch `refactor/yt-is-control-planes` (`0d22eb4`) has a correct mapping fix but a **broken test file** (39 unrelated failures on 2026-07-17 merge attempt). Either cherry-pick the `csf/nlm_batch.py` A2 hunk + its 9 passing mapping tests onto current `main` and verify green, or rebase the branch onto current `main` and repair `TestNotebookCapRotation` / `TestCandidate6Instrumentation` before merging. Closes finding #1.  
2. Shared retry enqueue must not clear active claims.  
3. Transcript cache: reject empty; allow quality upgrade.  
4. Channel promote: merge-by-`channel_id`, not blind REPLACE.  
5. Unify industrial failure → fallback/status for parallel workers.  
6. Doc hygiene: demote stale README/April authority; HANDOFF + ops contracts + this file for integrity risks. (Main package `AGENT_HANDOFF.md` may lag worktree handoff.)  
7. Single DB path registry; quarantine in-repo `*.sqlite`.  
8. Only then: auth-budget work with a decision packet (HANDOFF #965 line).  
9. **Refactor skill hardening** (separate from yt-is product work): design in `docs/operations/refactor-skill-handoff.md`; build Wave 1 (seam schema+validator, `claim_type` scope-grep, RED gate) into `~/.grok/skills/refactor/SKILL.md`, then run `/refactor yt-is` as falsifier.

**Closed since original review:** A1 (finding class: silent bulk status loss).  
**Not recommended:** another same-shape throughput live run without a completed decision packet (`AGENTS.md`).

---

## Implementation follow-up (optional)

```text
# A2 is NOT a clean merge. Branch tests are stale (39 failures on 2026-07-17).
# Option A (cleanest): cherry-pick only the mapping hunk + its 9 passing tests.
git worktree add -b a2-clean P:\.worktrees\yt-is-a2-clean HEAD
git cherry-pick -n 0d22eb4 -- csf/nlm_batch.py tests/test_nlm_batch.py
#   then keep only the A2 hunk in nlm_batch.py + the 9 mapping tests;
#   restore the other 96 changed test lines from main;
#   run: python -m pytest tests/test_nlm_batch.py -q   (expect 199+9 green)
#   merge to main; update this file (A2 -> done_main).
# Option B: rebase refactor/yt-is-control-planes onto main, repair the 39
#   broken test classes, verify, then merge.
```

Remaining P0 after A2:

```text
/go implement remaining P0 integrity fixes for yt-is: shared_retry enqueue
must not unclaim, reject empty transcript cache; use this findings file as
authority; no live benchmark
```

---

## Review meta

| Item | Result |
|---|---|
| Code changes in original review session | none |
| Live runs | none |
| Parent spot-checks (original) | validate_auth, enqueue unclaim, cache IGNORE, block_channel DELETE, zip mapping, batch_size=50 |
| Re-verify 2026-07-17 (1st pass) | A1 present on main; order zip still on main; A2 present on branch `2b96382`; HEAD main `f1ffca9`, branch `0d22eb4` |
| Re-verify 2026-07-17 (2nd pass, merge attempt) | A2 merge into `P:\.worktrees\yt-is-merge-a2` → 9 mapping tests pass, **39 unrelated tests fail** (branch `tests/test_nlm_batch.py` stale). NOT merged. HEAD main now `4cc2dd7`. |
| Subagents | architecture critic, correctness/security critic, entrypoint inventory |
| Discovery JSON | `P:\tmp\source-discovery-yt-is-20260716-235421.json` |
| Self-review (findings file) | Labels distinguish verified vs critic_static vs inference; A1/A2 status separated main vs branch; A2 merge status records the 2026-07-17 failure; no claim of optimality for VPH |

**Parent handoff:** original review was `ready_for_parent_review` with implementation not started. **Update:** A1 implemented and merged; A2 implemented on branch with **correct logic but broken test file — merge blocked 2026-07-17**; do not merge branch as-is (see finding #1). Refactor skill hardening design parked in `refactor-skill-handoff.md`.
