# compute_health warm profile (30-40s)

Provenance: agent: zcode, host: both. Measured 2026-08-25, read-only probes
(`P:/tmp/burst-profile/probe.py`), MonitorContext excluded per task scope
(double-build already tracked separately). `compute_health` full call
measured 19.6s (first probe pass) and 33.0s (second pass) — consistent with
the watcher's 30-40s guarded-subprocess measurement. Variance is disk/page
cache contention on the two multi-GB SQLite files.

Measured environment: status=completed_with_failures, 3 chunk records
(2 executed). During an active run the event-scan phases grow (see F6).

## Phase breakdown (measured)

| # | Phase (call site in health.py / core.py) | Time (2 passes) | What it does | Why slow |
|---|---|---|---|---|
| F1 | `latest_transcript_cached_at` (progress, core.py:450) | 7.0s / 14.2s | `SELECT MAX(cached_at) FROM transcript_cache` on transcripts.sqlite (4.25 GB, 293K rows, transcript TEXT inline) | No index on `cached_at` → full table scan of 4.25 GB. Largest single cost; latency scales with OS cache pressure, explaining the 7-14s spread |
| F2 | `drain_composition` (core.py:249) | 8.0s / 10.2s | Two aggregate scans of `analysis_status` (1.24M rows): pending GROUP BY `has_captions`; 12h window GROUP BY on `updated_at` | Both are `SCAN analysis_status` (EXPLAIN confirmed) — no usable index on `has_captions`, `updated_at`; `COALESCE(has_captions,-1)` defeats any index anyway |
| F3 | `host_telemetry` (core.py:905) | 4.1s / 4.5s | `psutil.process_iter(["name","cmdline","memory_info"])` over every process, string-joining each cmdline | `cmdline` per-process on Windows is expensive (native call per PID); scans all processes to find yt-is browser profiles |
| F4 | `probe_scheduled_tasks` (core.py:821) | 3.2s / 3.1s | 3 serial `powershell -NoProfile` subprocesses (`Get-ScheduledTask` + `Get-ScheduledTaskInfo` each) | ~1s per PowerShell startup; serialized loop, no reuse of one PS session |
| F5 | `backlog_counts` (core.py:228) | 2.3s / 2.7s | `SELECT status, COUNT(*) FROM analysis_status GROUP BY status` | Uses covering index `idx_analysis_status_channel_id_status` (EXPLAIN), so 0.25s when page cache is warm — the 2-3s in-process figure is cold-read of the 2.5 GB DB's index pages |
| F6 | current-chunk event scan (`scan_account_events`, core.py:645) | ~0s now / grows with run | Parses every line of every `accounts/<acct>/events/*.jsonl` in the current chunk | Negligible today (completed state, swept chunks). During a multi-day active run this reads all event JSONL linearly; also scanned a SECOND time by `chunk_failures` (retry-recovery join) — duplicate full parse of the same files |
| — | everything else (state, runtime receipt, keepalive, summaries, `visual_pipeline_state`, integrity, prior_analyses) | <0.1s each | JSON reads, one sqlite_master probe, per-record `is_dir` checks | Cheap at current chunk counts |

Sum of F1-F5 ≈ 24-34s — accounts for the entire warm 30-40s budget. The
Python state machine, event parsing, and chunk analysis are not material
today.

## Optimization opportunities (ranked)

1. **F1 — stop scanning transcripts.sqlite (biggest win, ~7-14s).**
   `SELECT MAX(cached_at)` needs only the newest row. Options:
   index on `cached_at` (one-time writer-side migration, makes the query
   ~0ms), or `SELECT cached_at FROM transcript_cache ORDER BY cached_at DESC
   LIMIT 1` (same plan without the index — still a scan), or reading the
   file mtime / a small sidecar receipt instead of the 4.25 GB table. An
   index is the clean fix; the monitor is read-only but the schema change
   belongs to the DB owner.
2. **F2 — index or eliminate the two full scans (~8-10s).** A partial/index
   on `analysis_status(status, has_captions)` would cover the pending
   query if the `COALESCE` is dropped in favor of `CASE WHEN has_captions
   IS NULL THEN -1` computed after a `WHERE status='pending'` index seek
   (437K pending rows). The 12h window query needs an index on
   `updated_at`. Alternatively compute both aggregates incrementally at
   write time.
3. **F3 — narrow host_telemetry (~4s).** Filter by process name
   (`chrome`/`chromium`/browser image names) before fetching `cmdline`;
   `proc.info["name"]` is much cheaper than `cmdline`. Only candidates get
   the cmdline join.
4. **F4 — single PowerShell session (~2s saved).** All three tasks can be
   fetched by one `Get-ScheduledTask -TaskName a,b,c` invocation, or the
   loop can reuse one `powershell -Command` that emits all three JSON
   objects. Startup cost is per-process, not per-task.
5. **F5 — acceptable.** 2.3s cold index read on a 2.5 GB DB; an index on
   `(status)` alone (or reusing F2's) would trim it further.
6. **F6 — deduplicate the event scan.** `chunk_failures` (health.py:287)
   re-parses all account event files that `analyze_chunk` (health.py:285)
   just parsed. Pass the scans from the analysis into `chunk_failures`
   instead of re-reading. Latent cost that grows with run length.

Fastest no-schema-change path: F1 via mtime/sidecar, F2 via a maintained
summary table, F3 name prefilter, F4 single PS call — would bring
compute_health to roughly 3-5s warm.
