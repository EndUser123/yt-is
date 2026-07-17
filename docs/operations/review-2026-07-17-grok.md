# FINDINGS — yt-is package review

**bugs: 6, risks: 3, suggestions: 1, nits: 0 (capped)**

| Field | Value |
|---|---|
| Target | `package:P:\packages\yt-is` |
| Mode | package (deep) |
| Lenses | correctness, integrity, concurrency, architecture, authority |
| HEAD | `f1ffca9` (main) |
| Policy | `P:/.grok/REVIEW.md`, package `AGENTS.md`, prior `docs/operations/critical-review-2026-07-17.md` |
| run_dir | `P:\tmp\grok-review\yt-is\20260717-020106` |
| Time (UTC) | 2026-07-17 (session run) |

## Summary

Industrial transcript pipeline still has **production-impacting integrity and multi-worker bugs** on main: wrong-source zip mapping, permanent first-write cache, shared-retry claim theft, no-op `validate_auth`, and promote REPLACE semantics. Prior durable review remains valid; **A1** (`set_status_batch` fail_count) is documented as shipped on main; **A2** (fail-closed mapping) is still **branch-only**. Verdict: **needs_attention**.

**overall_correctness:** `package needs_attention` (confidence 0.9)

---

## Bugs (blocking)

### INT-001 — Positional zip source→video — **verified**
- **Location:** `csf/nlm_batch.py:3042`
- **Detail:** `dict(zip(batch_ids, canonical_source_ids))` without identity bind when lengths match.
- **Evidence:** Re-read main@f1ffca9 line 3042.
- **Fix:** Merge A2 (`2b96382` on `refactor/yt-is-control-planes`) or equivalent fail-closed pairing.
- **confidence:** 0.95

### INT-002 — Transcript cache first-write-wins — **verified**
- **Location:** `csf/cache.py:113`
- **Detail:** `INSERT OR IGNORE` permanently blocks upgrade of empty/bad first write.
- **Evidence:** Re-read INSERT OR IGNORE path.
- **Fix:** Reject empty; allow quality upgrade.
- **confidence:** 0.95

### CON-001 — Enqueue clears claims — **verified**
- **Location:** `csf/shared_retry_pool.py:144-145`
- **Detail:** `ON CONFLICT` sets `claimed_by=NULL`, `claimed_at=NULL`.
- **Evidence:** Re-read enqueue UPDATE clause.
- **Fix:** Conditional update; never clear active claim.
- **confidence:** 0.95

### COR-001 — validate_auth always True — **verified**
- **Location:** `csf/youtube_auth.py:50`
- **Detail:** No-op auth validation.
- **Evidence:** Re-read `return True`.
- **Fix:** Real probe or remove API.
- **confidence:** 0.99

### INT-003 — Promote INSERT OR REPLACE — **verified**
- **Location:** `csf/batch_status.py:2015` (`_copy_table_rows`)
- **Detail:** Blind REPLACE + secondary UNIQUE(channel_id) can destroy live rows across URL forms.
- **Evidence:** Re-read executemany INSERT OR REPLACE.
- **Fix:** Merge-by-channel_id; allowlist columns.
- **confidence:** 0.85

### INT-004 — provider_score conflict target — **verified**
- **Location:** `csf/batch_status.py:590,603`
- **Detail:** `ON CONFLICT(channel_id, provider)` with nullable channel_id / URL PK mismatch.
- **Evidence:** Re-read ON CONFLICT clauses.
- **Fix:** Require channel_id; single conflict target.
- **confidence:** 0.8

---

## Risks

### COR-002 — block_channel deletes history — **verified**
- **Location:** `csf/batch_status.py:1282-1285`
- **Detail:** DELETE analysis_status + metadata on block.
- **Fix:** Soft-block default; explicit purge.
- **confidence:** 0.95

### CON-002 — Local retry_queue no lease — **unverified**
- **Location:** `csf/retry_queue.py` (approx get_pending region)
- **Detail:** Dual-queue design; claim-less pending may double-process. Not re-traced end-to-end this run.
- **Fix:** Prefer shared_retry_pool only or add claims.
- **confidence:** 0.7

### ARC-001 — nlm_batch god module — **verified**
- **Location:** `csf/nlm_batch.py` (~6321 lines)
- **Detail:** Maintainability / regression blast radius.
- **Fix:** Split hot-path modules.
- **confidence:** 0.9

---

## Suggestions (≤5 nits policy — none as nits)

### ARC-002 — Batch size default 50 — **verified**
- **Location:** `csf/nlm_config.py:23,86`
- **Detail:** Doc drift if README claims 200; config is authority.
- **Fix:** Align docs.

---

## Suppressed

None this run.

---

## Closed / partial (prior durable review)

| ID | Status |
|---|---|
| A1 set_status_batch fail_count | Shipped on main (per critical-review-2026-07-17.md) — not re-audited line-by-line this run |
| A2 fail-closed source mapping | On refactor branch only — **main still open** (INT-001) |

---

## Claim ledger

| Claim | Type | Evidence | Verification | Action allowed |
|---|---|---|---|---|
| Zip mapping on main | verified_fact | nlm_batch.py:3042 | verified | Fix/merge A2 |
| Cache IGNORE | verified_fact | cache.py:113 | verified | Fix |
| Claim theft on enqueue | verified_fact | shared_retry_pool.py:144 | verified | Fix |
| validate_auth no-op | verified_fact | youtube_auth.py:50 | verified | Fix |
| Promote REPLACE | verified_fact | batch_status.py:2015 | verified | Fix + dual-URL test |
| Local queue double-process | hypothesis | prior review | unverified | Evidence or fix with test |
| 3788 VPH optimal | unsupported | HANDOFF | n/a | No live “optimize” run |

---

## Recommended next actions

1. **P0 vertical slice:** CON-001 (enqueue) or INT-002 (cache empty reject) — small + testable.  
2. **Merge A2** to main for INT-001 (or re-apply fail-closed pairing).  
3. INT-003/INT-004 with adversarial promote/provider tests.  
4. COR-001 validate_auth.  
5. No same-shape live throughput benchmark without decision packet (`AGENTS.md`).

---

## Verdict

**needs_attention** — not healthy for industrial multi-worker integrity; not “critical” remote multi-tenant exploit class, but silent data corruption and claim theft are ship-blocking for backlog quality.
