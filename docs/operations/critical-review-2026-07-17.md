# yt-is critical review — 2026-07-17

**Package:** `P:\packages\yt-is`  
**Profile:** read-only adversarial review via `/go` (then named `/grok-sdlc`)  
**Package HEAD (at review):** `main @ ccfb95b` (package worktree)  
**Scope:** architecture, correctness, data integrity, concurrency, auth, doc authority  
**Not in scope:** live benchmarks, DB mutation, code changes  

**Supporting artifacts:**

| Artifact | Role |
|---|---|
| This file | Ranked findings + claim ledger (durable) |
| `P:\tmp\source-discovery-yt-is-20260716-235421.json` | Source-authority inventory (~7MB); **not** a substitute for this ledger |
| Session chat + explore subagents | Architecture critic, correctness/security critic, entrypoint map |

**Overall verdict:** `needs_attention`

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

## P0 — Data integrity

### 1. Positional / zip source→video mapping — **bug** — **verified**

- **File:** `csf/nlm_batch.py` (~3042)
- **Issue:** When lengths match, mapping uses `dict(zip(batch_ids, canonical_source_ids))` without identity check — wrong transcript can be cached under the wrong `video_id`.
- **Evidence:** Direct read of `source_id_by_video_id = dict(zip(batch_ids, canonical_source_ids))`.
- **Suggestion:** Fail closed unless title/URL/video_id bind; no positional zip except explicit debug flag.

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
| Source mapping can zip by position | verified | `nlm_batch.py:3042` | Path removed/gated | Fix before trusting industrial cache |
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

1. Fail-closed NLM source↔video mapping (kill positional zip except debug).  
2. Shared retry enqueue must not clear active claims.  
3. Transcript cache: reject empty; allow quality upgrade.  
4. Channel promote: merge-by-`channel_id`, not blind REPLACE.  
5. Unify industrial failure → fallback/status for parallel workers.  
6. Doc hygiene: demote stale README/April authority; HANDOFF + ops contracts + this file for integrity risks.  
7. Single DB path registry; quarantine in-repo `*.sqlite`.  
8. Only then: auth-budget work with a decision packet (HANDOFF #965 line).

**Not recommended:** another same-shape throughput live run without a completed decision packet (`AGENTS.md`).

---

## Implementation follow-up (optional)

```text
/go implement P0 integrity fixes for yt-is: fail-closed source mapping,
shared_retry enqueue must not unclaim, reject empty transcript cache;
use this findings file as authority; no live benchmark
```

---

## Review meta

| Item | Result |
|---|---|
| Code changes in review session | none |
| Live runs | none |
| Parent spot-checks | validate_auth, enqueue unclaim, cache IGNORE, block_channel DELETE, zip mapping, batch_size=50 |
| Subagents | architecture critic, correctness/security critic, entrypoint inventory |
| Discovery JSON | `P:\tmp\source-discovery-yt-is-20260716-235421.json` |
| Self-review (findings file) | Labels distinguish verified vs critic_static vs inference; no claim of optimality for VPH |

**Parent handoff:** `ready_for_parent_review` (review deliverable complete; implementation not started)
