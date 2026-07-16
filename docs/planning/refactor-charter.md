# yt-is — Real Problems That Need Fixing

**Status:** grounded list of verified codebase issues. Not a multi-slice program. Not a worktree authorization. Not a promote gate.

**Provenance:** LLM-generated draft (`2026-07-16`) critically reviewed by red-team specialists against the actual codebase. The original version described a 5-slice, multi-month structural refactor that nobody asked for, with invented policy classifications that don't exist in code. This version keeps only what is real.

---

## What's already fixed

These were identified and fixed during the 2026-07-15/16 session:

| Problem | Fix | Status |
|---------|-----|--------|
| `set_status_batch` could downgrade `complete` rows to `pending` | Changed `INSERT OR REPLACE` to `UPSERT CASE WHEN status='complete' THEN 'complete' ELSE excluded.status END` in both `set_status_batch` and `set_status` | Shipped (`batch_status.py:811-812, 1494-1495`) |
| `set_status_batch` docstring claimed transient fields were "overwritten" but they used COALESCE (null preserved) | Corrected docstring to match code; then changed transient fields to forced overwrite (`excluded.x`) to match | Shipped (`batch_status.py:1429-1431`, `:1510-1512`) |
| Pre-read SELECT in `set_status_batch` was dead code after UPSERT migration | Removed the pre-read block | Shipped |
| `fetch_custom_sources.py` created a new notebook per batch (168 stale notebooks accumulated) | Switched to `process_industrial_batch_reusable` — one notebook per run, deleted in `finally` | Shipped |
| `fetch_custom_sources.py` never cached transcripts from NotebookLM returns | Added `set_cached_transcript` call before `mark_complete` | Shipped |
| `fetch_custom_sources.py` timeout wrapper was theater (`future.cancel()` on running thread) | Now uses `process_industrial_batch_reusable` with internal CLI timeouts | Shipped |
| `_batch_worker` was defined as a closure inside `_run_with_timeout` — crashes on Windows spawn | Moved to module-level function | Shipped |
| Batch loop had no `finally` — Ctrl-C leaked the notebook | Wrapped in `try/finally: close_reusable_ingestor(delete=True)` | Shipped |
| 112 empty worker notebooks in NotebookLM account | Batch-deleted | Cleanup done |
| 56 stale non-empty worker notebooks from detached run | Batch-deleted | Cleanup done |

---

## What's still broken (verified, actionable)

These are real problems confirmed by reading the code. Each is bounded and fixable without a program charter.

### I-1: set_status_batch silently swallows per-row errors

**Location:** `csf/batch_status.py:1528-1530`

```python
except Exception:
    # Best-effort: skip bad entries, continue with the rest
    pass
```

**What's wrong:** If a single row in a 500-entry batch throws, the error is swallowed with no log, no counter, no record of which `video_id` failed. The return type is `int` — callers see `count=499` and think all 500 succeeded.

**Fix:** Replace `pass` with:
```python
except Exception as e:
    log_action("set_status_batch_row_failed", {"video_id": video_id, "error": str(e)})
    fail_count += 1
```
Change return type from `int` to `tuple[int, int]` (ok_count, fail_count) or a struct. Update all callers.

**Risk if fixed:** The only caller that catches the return value and acts on it is `import_history_full.py` which logs it. Callers that ignore the return (`csf-source` bulk paths) get the same behavior they have now. No regression.

**Risk if NOT fixed:** Silent data loss — a row fails but the operator never knows. Happened this session (4320 transcripts lost).

---

### I-2: Hardcoded shared state paths create cross-run contamination

**Location:** Multiple files hardcode `P:\.data\yt-is\` state roots:

| File | Path |
|------|------|
| `csf/cache.py:23` | `P:\.data/yt-is/transcripts.sqlite` |
| `csf/nlm_batch.py:39-41` | `P:\.data/yt-is/industrial-worker-states` |
| `csf/batch_status.py:106` | `P:\.data/yt-is/batch_status.sqlite` |
| `csf/shared_retry_pool.py:17` | `P:\.data/yt-is/nlm_shared_retry_pool.sqlite` |
| `csf/retry_queue.py:25` | `P:\.data/yt-is/retry_queue.sqlite` |
| `csf/playlist_imports.py:21` | `P:\.data/yt-is/playlists.sqlite` |
| `bin/csf-source:55` | `P:/.data/yt-is/locks` |

**What's wrong:** Concurrent non-lane runs write to the same SQLite databases. SQLite's internal locking prevents file corruption but doesn't prevent logical cross-run interference — one run's pending rows can be consumed by another. Lane runs (`sharded_lane_series.py`) already solve this with per-run `lane_output_root`, but the rest of the codebase doesn't use it.

**Fix:** Introduce a `RuntimeLayout` helper that resolves a run-scoped root directory (from `YTIS_RUN_ID` env var, or a generated UUID). Non-lane entrypoints (`csf-source fetch`, `yt-is sync`) initialize with isolated roots unless explicitly pointed at live state via a `--live` flag.

**Scope:** ~5 files need the helper; the helper itself is ~30 lines.

---

### I-3: Legacy env var aliases create shadow state paths

**Location:** `csf/nlm_batch.py:447-454, 461-463`

```python
override = os.getenv("YTIS_NLM_OWNER_STATE_PATH")
legacy_override = os.getenv("YTIS_NLM_REUSABLE_STATE_PATH")
```

**What's wrong:** Both old and new env vars control the same knob. If an operator has the legacy var set in their environment, and the code prefers the new var, the operator's setting is silently ignored. Same pattern for `YTIS_NLM_OWNER_NOTEBOOK_TITLE` vs `YTIS_NLM_REUSABLE_NOTEBOOK_TITLE`.

**Fix:** Add a deprecation warning when the legacy var is used but the new var is also set. In a later cleanup pass, remove the legacy vars.

---

### I-4: 88 YTIS_ environment variables with no isolation scheme

**Verified count:** 88 unique `YTIS_*` names across `csf/`.

**What's wrong:** Operators and agents discover these via grep, not via a registry. Some are aliases for the same concern (state root, timeout, batch size). No single file documents which are live-only vs staging-only vs trial-only.

**Fix:** Consolidate path-root env vars into the `RuntimeLayout` from I-2. The remaining per-knob vars (timeouts, batch sizes, feature flags) are legitimate configuration that belongs in a config file, not env vars — but that's a long-term cleanup, not a blocker.

---

### I-5: No run-scoped lock directory

**Location:** `bin/csf-source:55`

**What's wrong:** `_LOCK_DIR = Path("P:/.data/yt-is/locks")` is shared across all terminals but only guards NLM auth check counting, not state mutations. Concurrent non-lane runs can't coordinate which one owns the state.

**Fix:** Scope lock directory to run_id as part of I-2's `RuntimeLayout`.

---

## What's NOT fixed (but the original charter claimed was)

These were the charter's central proposals. They do not correspond to real code or real fixes.

| Claim | Truth |
|-------|-------|
| Mapping rank A/B/C/D system | **Invented.** No rank classifier exists. The zip-based pairing in `nlm_batch.py:3031-3090` does not classify into ranks. Adding this would be a new feature, not a fix. |
| C1-C10 "characterization" tests | **Do not exist.** §9.3 admits markers are "to add when implementing." These are TDD tests for the invented rank system, not characterization of existing behavior. |
| State plane as Slice 1 priority | **Wrong order.** Module extraction should come first. The existing env-override + promote pattern works for staging. |
| Four-plane architecture (code/state/experiment/evidence) | **Speculative.** Only the code plane has real problems. The experiment and evidence planes describe software that doesn't exist. |
| Worktree topology + promote gates | **Pre-authorizes work that wasn't requested.** No canary should fire from an unratified document. |
| Canary/rollback regime | **Not ready.** Thresholds were deferred to "canary design time" which is "we'll decide later" — exactly what the charter itself says it prohibits. |

---

## What to do next

1. **Fix I-1** (silent pass → logged + counted). One function, one change. ~1 hour with tests.
2. **Fix I-2** (RuntimeLayout for state paths). ~3-4 hours with migration.
3. **Fix I-3** (legacy env var deprecation warnings). ~30 minutes.
4. **Delete the original charter's speculative sections** (rank policy, four-plane architecture, worktree topology, promote gates, canary regime, characterization suite C1-C10). These describe software that doesn't exist and weren't requested. If a human wants them in the future, they can ask.

Items 1-3 are the real structural debt the codebase has. Everything else was LLM-generated scaffolding that mistook "things that could be done" for "things that need doing."

---

## Amendment log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-16 | Original draft (LLM-generated, unratified, 5-slice refactor program) | agent |
| 2026-07-16 | Red-team /check identified 7 BLOCK findings, 13 REVISE findings, 0 PROCEED findings. Replaced speculative program with grounded issue list. | human-directed rewrite after adversarial review |
