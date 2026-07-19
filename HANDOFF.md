# yt-is Handoff

Last updated: 2026-07-18 (Phase D cleanup complete)

## Current state

## Industrial trust floor (2026-07-17)

**Status:** C1+C2 implemented on branch trust-floor/phase-1. /check PASS (18/18 focused tests). **Merged to main at `073c4ee` on 2026-07-18 01:18.**

**Charter:** docs/operations/root-cause-program.md

**What changed:** C1 shared-retry lease guards (enqueue/mark_complete/mark_permanent_failure claimant-aware, drain skips deferred). C2 cache write gate (bind_verified=True refuses synthetic keys; importer resolves real YouTube IDs). A2 fail-closed mapping already on refactor branch @ 2b96382.

**Merged (2026-07-18):** Industrial shared cache and shared-retry are now trustworthy for optimal VPH claims. C3 is next.

**Pre-existing:** 40 failures in test_nlm_batch.py are from A1+A2 work, NOT C1+C2.

## Worktree status (2026-07-18, after Phase D cleanup)

All cleanup phases complete. Only the main worktree remains.

| Worktree | Branch | Behind main |
|----------|--------|------------:|
| `P:/packages/yt-is` | `main` (`bad5e43`) | — |

**Removed worktrees (all preserved as backup-tagged refs):**
- `trust-floor/phase-1` (`88ce7c6`, 7 behind) — removed in Phase C
- `refactor/yt-is-control-planes` (`0d22eb4`, 9 behind) — removed in Phase C
- `ai/import-safe-upsert-20260715-182239` (`4181e27`, 41 behind) — removed in Phase D; tracked gitdir pointer untracked; `.claude/worktrees/*` added to `.gitignore` with `!.claude/worktrees/.gitkeep` negation so future worktrees under that path don't get their pointers committed.

**Outstanding ref (not deleted):** `merge-a2` (`250cf51`, 1 ahead of main, NOT reachable from main) is preserved by tag `backup/merge-a2-2026-07-18`. Branch stays as a ref; tag is the only thing preserving the commit if the branch is ever deleted.

## Worktree policy hook (2026-07-18)

A PreToolUse hook is installed at `P:/packages/yt-is/.claude/hooks/worktree_policy_PreToolUse.py` and registered via `P:/packages/yt-is/.claude/settings.json`. It **blocks** direct `git worktree` Bash invocations by default to enforce the managed worktree lifecycle (see `P:/docs/worktree-lifecycle-design.md`).

| Invocation | Behavior |
|------------|----------|
| `git worktree <anything>` | BLOCKED with denial reason pointing to the managed CLI |
| `git status`, `git commit`, etc. | Allowed (only `git worktree` is intercepted) |
| `GO_WORKTREE_SAFETY_BYPASS=1 git worktree ...` | Allowed with stderr advisory |

Scope is package-level (yt-is only); other packages are unaffected. This deviates from the design's recommended default of `warn-then-block-after-2-weeks` — yt-is is running block-by-default as the pilot implementation.


- **Candidate 6 per-attempt telemetry is live-proven (2026-07-01).** The 11-field
  `nlm_batch_source_content_fetch_completed` contract was validated by run02
  (`candidate6_telemetry_validation_run02_current`): Signals 1/2/3 PASS on 1070
  events, the source-age-cliff `in_progress` leak is fixed (169 → 0, verified by
  direct grep), and per-attempt reconciliation = 0.0000 across 660 events proves
  Per-attempt reconciliation = 0.0000 across 660 events (NOTE: tautological by
  construction — `nlm_batch.py:3595`/`:3599` accumulate the same `attempt_elapsed_s`,
  so it is NOT proof of zero overhead; instrumentation runtime cost was not measured).
  The VPH guard failed (593.87) on a degenerate fresh-cohort/source-age confound
  (58% failure), classified separately from instrumentation.
  The Candidate 1-5 ranking derived from the run02 distribution
  ([`.logs/sharded_lane_series/candidate6_mechanism_ranking_after_run02.md`](P://packages/yt-is/.logs/sharded_lane_series/candidate6_mechanism_ranking_after_run02.md))
  shows the retry path is a minority contributor. NOTE: that ranking's headline
  "primary-materialization dominates (median 57.5s)" is a metric artifact, corrected in
  [`.logs/sharded_lane_series/design_packet_primary_batch_wait_run02_mechanism_no_patch_current.md`](P://packages/yt-is/.logs/sharded_lane_series/design_packet_primary_batch_wait_run02_mechanism_no_patch_current.md):
  `primary_batch_wait_time_s` is a fetch-task start-age clock, not an independent
  backend materialization metric; completed-row `source_ready_age_s` can later advance
  to attempt/final age, so treating these fields as independent causal corroboration was
  invalid. The 57.5s median is also inflated by mixing retry-pass rows (primary-only
  median is 12.3s in the offline re-derivation). The current no-patch packet infers
  auth-churn-driven `_run_cmd` iteration inflation (`_ensure_nlm_auth` blocking; run02
  had 926 auth events) slot-hogging the 10-wide fetch pool and cascading queued sources
  past the cliff; that inference still needs the planned cross-corpus overshoot/auth
  correlation before it should authorize a live benchmark. Next effort routes to durable
  auth (#965), not primary-materialization and not the retry tail. Analyzer:
  `scripts/analyze_candidate6_smoke.py --run-root <root>`.
- The current worker run is stopped.
- The current best sustained current-contract result on disk is `fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current` at `3788.53` combined hot-path VPH. It is valid (`status=ok`, `throughput_valid=true`, `3+3`, `home_300mb`) but still a mixed diagnostic branch because the smoke promotion gate failed.
- Do not treat `3788.53` as proven optimal sustained VPH; it is only the current observed leader on disk.
- The nearby June reruns are closed: `run03` is `blocked_before_soak`, `margin20_run06` is `blocked_before_soak`, `margin25` is negative, `post_scope_fix_run05` is `blocked_before_soak`, and `margin15` is invalidated.
- The local-retry projection branch is now closed too: `fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run08_current` validated as a negative rerun and did not extend the ceiling, so the next unresolved lever is batch-1 old-window `nlm source content` command latency / source-age accumulation, not another same-shape projection repeat.
- The batch-1 old-window design packet is now the current decision record for that hypothesis and it concludes `no patch candidate yet`; use [docs/operations/hot-path-throughput-next-test-plan.md](P://packages/yt-is/docs/operations/hot-path-throughput-next-test-plan.md) and [.logs/sharded_lane_series/design_packet_batch1_old_window_source_content_latency_no_patch_current.md](P://packages/yt-is/.logs/sharded_lane_series/design_packet_batch1_old_window_source_content_latency_no_patch_current.md) together before proposing any future work.
- The sharded benchmark harness now records the primary-command projection/margin env knobs in `lane_process.json`, so future runs can prove whether the batch-1 old-window lever was actually enabled from the artifact alone.
- The source-content retry queue now also skips retries whose projected retry-ready age plus primary-command projection would cross the cliff, with a margin-aware variant, so the local-retry branch has a narrower code-path gate instead of sleeping into old-window commands.
- The retry-queue primary-command projection validation rerun has already been exercised once and closed as a negative smoke-gated branch; the new projected-primary-command skip reason did not appear in the live artifact, so there is no promoted branch here.
- The retry-queue primary-command projection validation rerun finished as a negative smoke-gated branch at `2957.0` combined hot-path VPH on `795/5/800`; the new projected-primary-command skip reason did not show up in the live artifact, `retry_queue_skipped_reason` stayed `None` on all `895` fetch-completed rows, and soak did not run.
- No further same-shape live benchmark is justified without a code or harness change and a fresh decision packet.
- Offline ranking from the existing artifacts now points first at batch-1 old-window `nlm source content` latency, especially the Free-lane batch_01/batch_02 retry-heavy rows. Retry sleep and source-list probe cost are secondary signals, and lane/batch skew looks like a symptom rather than a separate ceiling lever.
- Before any future throughput proposal, run the attribution helper in `scripts/analyze_command_latency_attribution.py` against the current control and candidate artifacts so the lever is explicit before any code review or live-run packet.
- The current worker-owned notebook status and throughput conclusions are summarized in [docs/operations/worker-owned-notebooks-handoff.md](P://packages/yt-is/docs/operations/worker-owned-notebooks-handoff.md).
- The benchmark run sheet is [docs/operations/worker-count-trial-run-sheet.md](P://packages/yt-is/docs/operations/worker-count-trial-run-sheet.md).
- The routing split was changed so:
  - terminal/unavailable/private/deleted items stay sticky-skipped
  - live / live_stream / premiere items go to `transcript_fallback`
  - captioned and `no_captions` items go back to `notebooklm`
- We added a durable note at `P://packages/yt-is/CODEX_MEMORY.md` and linked it from `README.md`.
- Whisper empty-output messages now say when the model thinks the audio was likely music or silence.
- The fallback tail now reaches Whisper for `yt-dlp = ok` videos with no captions:
  - audio download now includes `--js-runtimes node` when `node` is available
  - this solves the YouTube `n` challenge on the fallback path
  - successful fallback transcripts are cached in `P://.data/yt-is/transcripts.sqlite`
- Verified live example:
  - `zgf2d8gsy70`
  - source: `whisper`
  - transcript length: `15419`
  - cached at `2026-04-24T23:06:39.164905`

## Why this matters

- The previous worker run had been sending the broad `no_captions` backlog into the slow transcript-fallback lane.
- That was the throughput killer.
- The current split is intended to push the large recoverable backlog back into NotebookLM while keeping live content out of that lane.

## Files that matter

- `P://packages/yt-is/csf/nlm_config.py`
  - NotebookLM batch size, source cap, materialization timeout, and auth policy defaults
- `P://packages/yt-is/csf/nlm_batch.py`
  - worker-owned notebook rotation and source-add subbatch sizing
- `P://packages/yt-is/bin/csf-source`
  - preflight routing split
  - logging for fallback / NotebookLM counts
  - worker-run orchestration
- `P://packages/yt-is/csf/transcript.py`
  - oEmbed probe
  - direct_api classification
  - Whisper empty-result classification
  - negative-cache persistence
- `P://packages/yt-is/csf/batch_status.py`
  - transcript cache / negative cache / status persistence
  - `mark_failed(..., source=...)` fix
- `P://packages/yt-is/tests/test_csf_source_fetch_timing.py`
  - routing regression tests
- `P://packages/yt-is/tests/test_transcript.py`
  - direct_api and Whisper regression tests

## What we learned

- NotebookLM industrial batches were much faster than the fallback lane when healthy.
- The fallback lane is mostly Selenium and is much slower.
- The backlog is large, so putting `no_captions` into fallback caused a big throughput collapse.
- Whisper empty output is not proof of a bug; it usually means no speech, maybe music or silence, and is now labeled that way.
- The remaining failure mode for the fallback tail was not Whisper itself; it was yt-dlp audio acquisition failing before Whisper ran. That is now fixed for the no-caption `yt-dlp = ok` class by enabling a real JS runtime.

## Validation status

- `python -m py_compile` passed on the touched files.
- `P://packages/yt-is/tests/test_csf_source_fetch_timing.py` passed.
- `P://packages/yt-is/tests/test_transcript.py` passed.
- The latest focused split tests passed.
- The fallback tail was validated live against `zgf2d8gsy70` and produced a saved Whisper transcript in `transcripts.sqlite`.
- Before any risky sweep or cleanup, run `python P://packages/yt-is/bin/csf-backup-transcripts` to snapshot `P://.data/yt-is/transcripts.sqlite` into `P://.data/yt-is/backups/`.
- If you want to stage a long run before promoting it, point `YTIS_TRANSCRIPT_CACHE_DB_PATH` at `P://.data/yt-is/transcripts-staging.sqlite`, run the backlog against that staging DB, then promote with `python P://packages/yt-is/bin/csf-promote-transcripts`. The promote step is blocking and fail-closed: it refuses missing source DBs, empty staging DBs, and source/destination collisions.
- Before any tracked-channel sync or blocklist change, run `python P://packages/yt-is/bin/csf-backup-channel-state` to snapshot `P://.data/yt-is/batch_status.sqlite` into `P://.data/yt-is/backups/`.
- If you want to stage channel inventory changes before promoting them, point `YTIS_BATCH_STATUS_DB_PATH` at `P://.data/yt-is/batch-status-staging.sqlite`, run `yt-is sync` against that staging DB, then promote with `python P://packages/yt-is/bin/csf-promote-channel-state`. The promote step is blocking and fail-closed: it refuses missing source DBs, empty staging DBs, and source/destination collisions.
- If you need to backfill legacy URL-keyed rows to the new canonical identity contract, run `python P://packages/yt-is/bin/csf-migrate-channel-ids`. That command snapshots live state first and then fills `channel_id` plus canonical display URLs in `batch_status.sqlite`.
- The current indicative channel filtering rubric is documented in `P://packages/yt-is/docs/operations/channel-filtering-rubric.md`. Treat it as a review guide, not a frozen policy.

## Next action for the new session

1. Do not launch another same-shape throughput benchmark.
2. Do not treat `3788.53` as proven optimal sustained VPH.
3. If you continue the investigation, start from the current contract docs plus the raw artifacts for `run02`, `run03`, `margin20_run06`, `margin25_run05`, `post_scope_fix_run05`, and `margin15_run01`.
4. Use the ranked offline evidence first: batch-1 old-window `nlm source content` latency is the leading hypothesis, with retry-heavy rows and Free batch_01/batch_02 as the main hotspots.
5. Only consider a new live benchmark after a code or harness change, a completed decision packet, and a newer design packet that identifies a narrower mechanism than the current projection/retry guard path.

## Useful reminders

- A large number of `oembed unavailable: HTTP 404` items should now be skipped cheaply and cached negatively.
- `active_workers: 0` in transcript-fallback logs is expected; that lane is not the industrial NotebookLM worker pool.
- If the next worker run looks slow again, first check whether `no_captions` is still going to the wrong lane before changing batch size or retry tuning.
- The current NotebookLM worker notebook capacity note is at [docs/operations/nlm-canary-capacity-note.md](P://packages/yt-is/docs/operations/nlm-canary-capacity-note.md).
## Debugging / Logging Rules That Matter
- Quick pointer: [DEBUGGING_PLAYBOOK.md](P://packages/yt-is/DEBUGGING_PLAYBOOK.md)
- Do not trust the JSONL trace alone. Several important warnings surfaced only in live stderr/stdout.
- When threading a new field through a wrapper, verify the callee signature before assuming it works. The `mark_failed(..., source=...)` bug was exactly this failure mode.
- Treat the worker result file as the source of truth for completed work. Stdout summaries can be stale or incomplete.
- If a worker run emits warnings, check both structured trace events and raw terminal output because they do not always carry the same information.
- For throughput questions, prefer completed-worker totals and stage timings over scan-progress or backlog-size-derived rates.
- If a long scan looks silent, `YTIS_SCAN_STATUS_INTERVAL_S` controls the heartbeat cadence for `/yt-is sync` and fetch scans.
- The most useful live signals have been:
  - `fetch_worker_finished`
  - `worker_completed`
  - `worker_batch_metrics`
  - `worker_source_profile_totals`
  - `negative_cache_reason_counts`
  - `add_cmd_elapsed_s` vs `materialization_wait_elapsed_s`
- When a new logging field is added, smoke-test the exact path that writes it. If it only appears in one code path, the first bug is often a mismatch in another path.
- If valid videos show up as `too_short` or `command_failed`, verify NotebookLM source-to-video mapping before blaming the source itself. We already hit a bug where `source list --json` order was trusted incorrectly in `extract_transcripts()`.

## Session Bootstrap
- Read these first:
  - [HANDOFF.md](P://packages/yt-is/HANDOFF.md)
  - [CODEX_MEMORY.md](P://packages/yt-is/CODEX_MEMORY.md)
  - [DEBUGGING_PLAYBOOK.md](P://packages/yt-is/DEBUGGING_PLAYBOOK.md)
  - [Throughput Optimization LLM Contract](P://packages/yt-is/docs/operations/throughput-optimization-llm-contract.md) before any NotebookLM throughput benchmark decision
  - [Throughput Decision Packet Template](P://packages/yt-is/docs/operations/templates/throughput-decision-packet.md) before any live throughput run
- If you are touching NotebookLM throughput, check `P://packages/yt-is/csf/nlm_config.py` first for the shared NotebookLM defaults before grepping for magic numbers.
- Fresh agents must not launch a live throughput benchmark from chat memory alone. They must read the current hot-path plan and registry, complete the decision packet, and prefer offline reducer/audit attribution unless the packet names a falsifier, early-abort gate, raw artifact path, and promotion rule.
- Key files:
  - [bin/csf-source](P://packages/yt-is/bin/csf-source)
  - [csf/transcript.py](P://packages/yt-is/csf/transcript.py)
  - [csf/batch_status.py](P://packages/yt-is/csf/batch_status.py)
  - [csf/batch_scheduler.py](P://packages/yt-is/csf/batch_scheduler.py)
- Fast verification:
  - `python -m py_compile $CLAUDE_PLUGIN_ROOT/bin\csf-source $CLAUDE_PLUGIN_ROOT/csf\transcript.py $CLAUDE_PLUGIN_ROOT/csf\batch_status.py $CLAUDE_PLUGIN_ROOT/csf\batch_scheduler.py`
  - `PYTHONPATH=P://packages/yt-is python -m pytest $CLAUDE_PLUGIN_ROOT/tests\test_transcript.py -q`
  - `PYTHONPATH=P://packages/yt-is python -m pytest $CLAUDE_PLUGIN_ROOT/tests\test_csf_source_fetch_timing.py -q`
- Current intended worker run:
  - `python $CLAUDE_PLUGIN_ROOT/bin\csf-source fetch --workers <n>`
  - Worker notebook reuse is per worker; the benchmark sweep still continues through `8` workers.
