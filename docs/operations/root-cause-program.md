# yt-is industrial trust floor — root-cause program

**Status:** active backlog for implementation (not a findings dump)  
**Last updated:** 2026-07-17  
**Evidence (acceptance only):**

- `docs/operations/review-2026-07-17-grok-deep2.md`
- `docs/operations/critical-review-2026-07-17.md`
- Run (legacy tmp): `P:/tmp/grok-review/yt-is/20260717-020558/`

**Authority order for agents:**

1. **This program** — what to build (contracts + done-when)
2. **FINDINGS / critical-review** — evidence + acceptance cases (IDs)
3. **HANDOFF / throughput packets** — VPH work after trust floor (or explicit waiver)

Do **not** plan implementation solely by finding ID (CON-001, INT-006, …).  
Map IDs to a contract below; close findings **under that contract** when done.

---

## Product constraints

- Multi-worker industrial NotebookLM path is in scope.
- Shared `transcripts.sqlite` / durable channel+status DBs are in scope.
- Refactor OK; prefer **path monopoly** (one write path per concern) over dual paths.
- Never invent session/run identities for telemetry joins.

---

## Contracts (implementation backlog)

### C1 — Work outcomes + shared-retry lease

**Problem class:** Multi-worker retry destroys deferred work and lies about failures.

**Build:**

- Outcome algebra used by **serial and shared** paths: `success | failed | deferred`
- Shared-retry state machine:  
  `pending → claimed → completed | permanent_failure | pending(reschedule)`
- Enqueue never steals a live non-stale claim
- Terminal updates require claimant (or explicit stale reclaim)
- Drain never permanent-fails deferred; never fill deferred as `"Source not found"`
- Deferred has a **re-entry** contract (`reschedule` or equivalent with production callers)

**Done when:**

- Forced `queued_for_retry` under shared pool stays claimable / rescheduled, not `permanent_failure`
- No `"Source not found"` for deferred-only misses
- Multi-worker claim steal on enqueue fails a regression test

**Acceptance findings:** CON-001, CON-002, CON-003, CON-004, CON-005 (CON-008 if that queue stays industrial)

**Falsifier:** Shared-pool drain + deferred batch → row ends `permanent_failure` with `"Source not found"`.

---

### C2 — Identity + cache write gate

**Problem class:** Wrong or synthetic `video_id` permanently poisons shared cache.

**Build:**

- Real YouTube `video_id` only in shared transcript cache
- Fail-closed source↔video bind (merge **A2** from refactor branch or equivalent; **no positional zip** of notebook list order on main)
- Importer must not MD5 NotebookLM source IDs into shared cache keys
- Cache first-write-wins only for valid bound content; quality upgrade / explicit rebind overwrite after mapping fix

**Done when:**

- Reordered source list cannot write transcript under wrong `video_id` (test)
- `csf_nlm_import` refuses synthetic keys
- A2 (or equivalent) on main; zip gap-fill gone or debug-only

**Acceptance findings:** INT-005, INT-006, INT-007 (and COR-003 / COR-010 as dupes)

**Falsifier:** Title/url exact map discarded for same-length `dict(zip(...))` still on main extract path.

---

### C3 — Durable row-merge policy

**Problem class:** Partial upsert / promote / complete / block destroy or contradict durable state.

**Build one merge policy used by channel metadata, promote, analysis_status, blocklist:**

| Operation | Rule |
|-----------|------|
| Partial upsert | Omit unset keys; never pack `None` as “set NULL” |
| keywords/custom_url | Always SELECT + preserve unless explicitly set |
| Promote | Non-null field merge by `channel_id` then URL; refuse null-clobber unless force |
| Status complete | Freeze status **and** diagnostics (or full no-op); no failure_reason on complete |
| Block | Blocklist insert by default; purge is a separate named API |

**Done when:**

- Sparse promote cannot null live `subscriber_count`
- Partial `set_channel_metadata` leaves keywords/playlist intact
- `mark_failed` after complete does not leave contradictory durable pair without force API

**Acceptance findings:** INT-001, INT-002, INT-003, INT-004, INT-008

**Falsifier:** After keywords backfill, `upsert_channel(..., last_checked=now)` clears keywords.

---

### C4 — Fail-closed auth (industrial)

**Problem class:** Auth “success” without expected account; theater APIs; recovery holes.

**Build:**

- Worker profiles require expected email in noninteractive industrial mode
- Auth cache bound to verified account fingerprint; not fail-open forever on hit alone
- Family refresh/sync uses live session check (no `lambda: True`)
- `validate_auth`: real probe **or delete** (no always-True contract)
- Align with HANDOFF durable-auth direction; auth **scheduling** / `_run_cmd` churn is follow-on once fail-closed lands

**Done when:**

- Empty expected_email cannot authorize industrial worker path
- Missing Account line fails closed when email required
- No UnboundLocalError on refresh_reason empty-account path

**Acceptance findings:** COR-001, COR-006, COR-007, COR-008, COR-009

**Falsifier:** `validate_auth()` still returns True unconditionally on main.

---

### C5 — Control-plane collapse (after C1–C4)

**Problem class:** Dual serial/shared semantics and god-module divergence reintroduce bugs.

**Build:**

- One industrial extract outcome path; serial is a mode, not a second algebra
- Extract seams forced by C1–C4 only — **no big-bang `nlm_batch` rewrite first**

**Done when:**

- Shared and serial use the same outcome type
- No second permanent-fail fill-in path

**Acceptance:** CON-002 path split closed under C1; residual architecture #14–17 tracked separately

---

## Ship order (post multi-model critique)

Do **not** leave half-enforced intermediate states if avoidable.

| Phase | Ship | Notes |
|-------|------|--------|
| **1** | **C1 + C2 together** (or same train) | Lease without identity → efficient poison; identity without lease → thrash. Prefer one PR series with both green. |
| **2** | **C3** | Channel/status merge policy; can parallelize with late Phase 1 if writers don’t conflict |
| **3** | **C4** | Fail-closed auth; then HANDOFF auth-scheduling work |
| **4** | **C5** | Only after path monopolies exist |

**SQLite multi-worker hygiene** (busy_timeout, txn boundaries, dead-lease reclaim) lands with **C1**, not as a free-standing epic.

**Explicit non-goals for this program:**

- Same-shape live VPH runs without decision packet
- Historical poison repair job (separate once C2 exists)
- WAL checkpoint micro-optimization alone
- Finding-by-finding PRs that leave dual paths alive

---

## Throughput gate

Until **C1 + C2** are closed (or user writes an explicit waiver in the decision packet):

- Do not treat industrial shared cache / multi-worker shared-retry as trustworthy for “optimal VPH” claims.
- Prefer offline attribution and code fixes over another same-shape soak.

Live benchmarks still require the existing throughput decision-packet rules in AGENTS.md.

---

## Closing findings

When a contract ships:

1. List acceptance IDs as **closed under C#** in this file’s changelog (append below).
2. Optionally note in the next `/review` prior-merge as `closed`.
3. Do not delete durable review history.

### Changelog

| Date | Contract | Closed findings | Notes |
|------|----------|-----------------|-------|
| 2026-07-17 | **C1** Work outcomes + shared-retry lease | CON-001, CON-002, CON-003, CON-004, CON-005 (CON-006 / CON-008 risk items not closed by C1) | Closed on `trust-floor/phase-1 @ 50be8d9`. `enqueue` claim-guard + claimant-aware `mark_*` + extract epilogue skip for deferred + `shared_retry_deferred_video_ids` exposed to worker drain. 8/8 `test_shared_retry_pool.py` green; no new regressions in broader sweep (40 pre-existing A1+A2 failures confirmed unrelated). Multi-worker crash + WAL falsifier (GLM critique) deferred to hardening pass. |
| 2026-07-17 | **C2** Identity + cache write gate | INT-005 (MD5 keys refused), INT-006 (A2 fail-closed mapping carried via refactor branch `2b96382`), INT-007 (first-write still frozen — **partial**; quality upgrade deferred) | Closed on `trust-floor/phase-1 @ 50be8d9`. `cache.set_cached_transcript(..., bind_verified=True)` refuses unbound writes; `csf_nlm_import` resolves real YouTube id from title, counts unbound as `refused` (never synthetic). 10/10 `test_csf_nlm_import.py` green. **Remaining:** INT-007 quality upgrade (`replace_cached_transcript_if_better`) deferred to follow-up. |

---

## Quick implement entry

```text
/go implement yt-is trust floor Phase 1: C1 shared-retry lease+deferred outcomes + C2 identity/A2/cache gate
Acceptance: docs/operations/root-cause-program.md C1–C2 falsifiers
Evidence IDs: CON-001..005, INT-005..007
```
