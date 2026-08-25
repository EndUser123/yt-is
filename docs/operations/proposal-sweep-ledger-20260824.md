# Proposal: stop flagging retention-swept chunks as evidence_missing_unexpectedly

agent: zcode, host: both, mode: read-only investigation (no git commands run)

## 1. Current behavior (receipts)

### Alert production path

- `P:/packages/yt-is/scripts/pipeline_monitor/health.py:259-270` — the "evidence integrity" section of `compute_health`. It calls `core.chunk_evidence_integrity(records, last_activity=ctx.state_updated_at)` (line 260), filters items with `classification == "EVIDENCE_MISSING_UNEXPECTEDLY"` (lines 263-266), and appends alert `{"code": "evidence_missing_unexpectedly", "detail": unexpected[:3]}` (lines 267-270).
- `P:/packages/yt-is/scripts/pipeline_monitor/core.py:1053-1113` — `chunk_evidence_integrity`. Classification rule at lines 1091-1096: when neither the chunk `output_root` dir nor the summary file exists, it is `EVIDENCE_EXPIRED_BY_POLICY` only if the run's quiet age (`last_activity` = `state.json:updated_at`) exceeds `DEFAULT_MAX_AGE_DAYS` (7, imported from `csf.cleanup_staging` at lines 1071-1076); otherwise `EVIDENCE_MISSING_UNEXPECTEDLY`. Only the UNEXPECTEDLY class raises an alert (health.py:267).
- The watcher `P:/packages/yt-is/scripts/pipeline_health_watch.py` writes `P:/.data/yt-is/pipeline-alert.txt` (line 9 docstring, write at ~line 170). Live alert file confirmed (read 2026-08-25T04:36Z) showing all three chunks classified `EVIDENCE_MISSING_UNEXPECTEDLY`, `retention_horizon_days: 7.0`, `run_quiet_age_s: ~430884` (~5.0 days).

### The stale references (exact state entries)

`P:/.data/yt-is/unattended-backlog/state.json` — top-level `status: "completed_with_failures"`, `updated_at: 2026-08-20T04:54:50.198627+00:00` (terminal snapshot; the run is over, nothing will update this file again). Its `chunks[0..2]` all reference now-deleted roots under the experiment dir `P:/packages/yt-is/.logs/multi_account_fetch/unattended-20260820T044911Z/`:

- chunk 1 (`status: "planned"`), `output_root: P:\packages\yt-is\.logs\multi_account_fetch\unattended-20260820T044911Z\chunk-0001`, `summary_path: ...\chunk-0001\multi_account_fetch_summary.json` — both absent on disk.
- chunk 2 (`status: "partial"`), same experiment dir, `chunk-0002` — absent.
- chunk 3 (`status: "no_work"`), same experiment dir, `chunk-0003` — absent.

Verified: the entire package root `P:/packages/yt-is/.logs/multi_account_fetch/` does not exist (only the legacy root `P:/.logs/multi_account_fetch/` remains). No `unattended-20260820*` directory exists anywhere under either log root.

## 2. Root cause

Two clocks disagree and there is no durable sweep record:

1. The sweep: `P:/packages/yt-is/csf/cleanup_staging.py` (`cleanup_staging`, lines 149-272) deletes whole experiment directories (`unattended-*`) under the multi-account log root when directory mtime exceeds `max_age_days` (default 7, line 29) and no file is recent (lines 222-231, `shutil.rmtree`). It is invoked automatically at the end of every run by `P:/packages/yt-is/scripts/run_unattended_backlog.py:2093` and `P:/packages/yt-is/scripts/run_multi_account_fetch.py:2368`, both as `cleanup_staging(output_root.parent)`.
2. The record: `cleanup_staging` returns a report with a full `actions` list (line 213), but callers only (a) print it to stdout (`run_unattended_backlog.py:2097`) or (b) embed it in the run summary (`run_multi_account_fetch.py:2368` → `_write_summary_path`) which lives inside the experiment directory itself. The record of what was swept is destroyed with the sweep or evaporates with console output. No durable ledger exists.
3. The monitor: `chunk_evidence_integrity` only has the state.json quiet-age clock. A root deleted before the state is 7 days quiet is indistinguishable from the Aug-16 deleted-root incident class, so it must alert. That is correct behavior given no sweep record — the defect is the missing durable record, not the classifier.

Timing note (honest caveat): at 2026-08-25 the 2026-08-20 experiment dir is only ~5 days old, below the sweep's own 7-day mtime threshold, so the default-config sweep does not explain this specific deletion (possibly a CLI run with smaller `--max-age-days`, possibly another actor; not verifiable without git history, which was out of scope). This strengthens rather than weakens the recommendation: with a durable ledger, "swept" becomes provable and "deleted by unknown actor inside the horizon" stays an alert.

Self-clear note: the alert will flip to `EVIDENCE_EXPIRED_BY_POLICY` (no alert) automatically once quiet age passes 7 days, i.e. 2026-08-27T04:54:50Z. The fix below makes the class correct immediately and durably rather than waiting out the clock.

## 3. Recommended fix: (c) sweep writes a durable ledger; monitor classifies ledgered paths as expired-by-policy

Chosen over the alternatives:

- (a) monitor prunes state.json — rejected. A read-only monitor mutating supervisor state violates the monitor's own contract (health.py docstring: "Monitor health is a projection over those authorities — never a competing truth model") and creates a writer race with the supervisor's lock files.
- (b) monitor classifies retention-aged missing chunks as benign via age heuristics — rejected as stated. The classifier already does exactly this at the 7-day boundary; lowering the boundary or adding "terminal run ⇒ benign" would blind the detector to real in-horizon evidence loss (the Aug-16 incident class the code explicitly exists to catch, core.py:1064-1066).
- (c) recommended: the sweep is the only actor that *knows* it swept. Make it leave evidence, and let the monitor consume it. This preserves the fail-closed property: within-horizon deletion with no ledger entry still alerts.

### Exact edits

1. `P:/packages/yt-is/csf/cleanup_staging.py` — after each successful `shutil.rmtree(experiment)` (~line 229) and each successful `path.unlink()` (~line 262), append one JSON line to a durable ledger, default `P:/.data/yt-is/cleanup_staging_ledger.jsonl` (path as module constant, e.g. `DEFAULT_LEDGER_PATH`; parent created on demand; append-only write, best-effort with errors captured in `report["errors"]` like other OSError paths). Line shape: `{"ts": <iso>, "action": "delete_directory"|"delete_file", "path": <str>, "reason": <action reason>, "bytes": <size>}`. Function stays pure for tests: derive the ledger path from a new keyword arg `ledger_path: Path | None = DEFAULT_LEDGER_PATH`; `None` disables (existing tests keep passing semantics; they use tmp_path roots anyway).
2. `P:/packages/yt-is/scripts/pipeline_monitor/core.py` — `chunk_evidence_integrity` (line 1053): add keyword param `swept_paths: set[str] | None = None`. In the missing branch (lines 1091-1096), classify as `EVIDENCE_EXPIRED_BY_POLICY` when the output_root itself OR its parent experiment dir (`Path(record.output_root).parent`) matches a swept path (casefolded string compare, resolve not required — sweep logs the path it deleted). Add `"actor": "cleanup_staging_ledger"` to the problem dict for auditability. Also add a small loader `load_sweep_ledger(path) -> set[str]` reading the jsonl and returning deleted directory paths.
3. `P:/packages/yt-is/scripts/pipeline_monitor/health.py` — at the integrity call site (line 260), pass `swept_paths=core.load_sweep_ledger(...)` guarded by try/except (missing file ⇒ empty set ⇒ current behavior unchanged).

### One-time remediation for the current standing red

The 2026-08-20 dirs were already deleted with no ledger. Two acceptable options; pick with the operator:
- Backfill: manually append one ledger line `{"action":"delete_directory","path":"P:/packages/yt-is/.logs/multi_account_fetch/unattended-20260820T044911Z","reason":"manual_backfill_20260824"}` — the alert clears on the next 5-min watch cycle and the record is durable.
- Wait: it self-clears at 2026-08-27T04:54:50Z via the existing 7-day rule.
Prefer the backfill: it exercises the new code path in production immediately.

## 4. Tests to add

In `P:/packages/yt-is/tests/test_pipeline_monitor.py` (existing integrity tests at lines ~620-670 and ~1710-1738 are the template):

- Missing root, quiet age 1 day, `swept_paths` containing the experiment dir (parent of output_root) ⇒ `EVIDENCE_EXPIRED_BY_POLICY`, and via `compute_health` no `evidence_missing_unexpectedly` alert.
- Missing root, quiet age 1 day, `swept_paths=None`/empty ⇒ `EVIDENCE_MISSING_UNEXPECTEDLY` (regression guard: the detector is NOT blinded).
- `load_sweep_ledger` on a tmp jsonl with mixed delete/skip lines returns only deleted directory paths, casefolded.

In `P:/packages/yt-is/tests/test_cleanup_staging.py`:

- Sweep with `ledger_path` set records one line per deleted directory/file; `ledger_path=None` writes nothing; unreadable ledger path surfaces in `report["errors"]` without failing the sweep.

## 5. Risks

- R1: Path-string matching (swept path vs record path) can miss on case/separator differences. Mitigate with casefold + both `/` and `\` normalization, and match both the chunk root and its parent.
- R2: A malicious or buggy actor could pre-write ledger entries to silence loss detection. Low risk (local single-operator machine); the ledger is append-only evidence, and `reason`/`ts` fields make forensics possible.
- R3: Ledger grows unbounded. Trivial rate (a few lines per sweep); note a future compaction option, do not implement now.
- R4: The current standing red is NOT cleared by the code change alone (deletion predates the ledger) — requires the one-time backfill above, or it self-clears 2026-08-27.

## 6. Falsifier

The fix is wrong if, after deployment: a chunk root that was NOT swept (no ledger entry) and is inside the 7-day horizon goes missing and the monitor does NOT emit `evidence_missing_unexpectedly` — i.e., the detector got blinded. Conversely, if a ledgered sweep entry fails to clear the alert (path-matching miss, R1), that is also a disproving observation. Concretely: delete a fake chunk root referenced by a test state, run `python -m scripts.pipeline_monitor` (or the watcher) both with and without a matching ledger line; alert must appear only in the without-case.
