# Hot-Path Throughput Next Test Plan

> For future LLM agents: follow this plan in order. Do not rerun old benchmark shapes unless the named code path has changed. Whisper fallback is allowed for recovery, but Whisper time and recovery counts are never included in sustained hot-path videos/hour.

> Default observability guardrail: read [Observability Contract Checklist](observability-contract-checklist.md) before trusting any metric, reducer output, or interpretation in this plan.

## Goal

Find whether `yt-is` can exceed the current best proven sustained hot-path throughput:

- Latest best artifact: `P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v1/sharded_lane_series_summary.json`
- Latest best combined hot-path VPH: `5572.04`
- Prior control artifact: `P://packages/yt-is/.logs/sharded_lane_series/pro_free_v2/sharded_lane_series_summary.json`
- Prior control combined hot-path VPH: `4148.71`
- Current best shape: Pro+Free lanes, no startup stagger, `4` workers per lane, `--limit 400` per lane, `--batch-size 200`, serial reusable pipeline
- Fresh-state controls: `free_only_fresh_state_control_run01` reached `2825.29` on `400/0/400`; `two_plus_two_pressure_100_run01` reached `1474.74` on `800/800`; `fresh_worker_state_default_3plus3_run01` was actually `4+4`, not `3+3`, and the runner now publishes `worker_shape_signature` so future run labels can be checked against the real worker counts before they are trusted; `verified_3plus3_fresh_run01` completed as a clean true `3+3` run at `1452.24` combined lane-process throughput VPH on `800/800`, which is below the fresh-state solo controls, so the shape is now a negative control rather than an open question; pass `--expected-worker-shape` to the evidence check when you want mislabeled shapes to fail closed
- Metric contract: use `combined.hot_path_videos_per_hour` from `sharded_lane_series_summary.json`; do not include Whisper fallback throughput; the throughput span excludes only parent-process Chrome reap and does not subtract per-batch worker cleanup
- Extraction-status contract: do not use `too_short` as a NotebookLM metric. Use `nlm_content_below_threshold` for below-threshold NotebookLM source content, and record `nlm_content_chars` plus `usable_text_chars` when diagnosing sparse source content.

## Read First

Before running anything, read:

- `P://packages/yt-is/docs/operations/observability-contract-checklist.md`
- `P://packages/yt-is/docs/operations/test-registry.md`
- `P://packages/yt-is/docs/operations/sharded-lane-artifact-audit.md`
- `P://packages/yt-is/docs/operations/sharded-lane-series.md`
- `P://packages/yt-is/docs/operations/notebooklm-auth-family-extension.md`
- `P://packages/yt-is/docs/superpowers/specs/2026-04-28-hot-path-throughput-optimization-series-design.md`

These files record what has already been proven, what was negative, and how the dedicated Pro and Free browser roots must be authenticated.

## Current Session State: 2026-05-27

What has been actioned:

- Worker-profile auth repair was implemented through `python P://packages/yt-is/bin/csf-nlm-worker-auth sync`.
- The sync command validates `ytis-pro-worker-01` as `a.hominidae@gmail.com`, `ytis-free1-worker-01` as `troup.hominidae@gmail.com`, and `ytis-free2-worker-01` as `brsthomson@hotmail.com`, parses `nlm login --check` account output, repairs worker `01` through the dedicated Pro/Free/Free2 CDP root when needed, backs up sibling worker profiles, copies account-family credentials to workers `02`-`04`, and account-checks all twelve worker profiles.
- Bounded whole-batch source-add retry was implemented and covered by focused tests.
- The zero-growth add failure path now has its own bounded retry and regression coverage. The live `pro_free_source_map_v5` rerun showed that the fallback was still needed for remaining Free lane zero-growth `source_add_failed` cases, and the notebook-reset fallback has now been implemented and rerun as `pro_free_source_map_v6`.
- The Pro+Free no-stagger control was rerun twice after the auth/retry work:
- `pro_free_post_retry_v2`: best observed no-stagger control, `4407.40` combined hot-path VPH, `688/112`, `800` processed, wall `561.964s`. Its clean `3+3` transfer test later fell to `3019.43`, so keep it as a diagnostic branch rather than a promoted geometry candidate.
  - `pro_free_post_retry_v3`: negative recheck, `1982.17` combined hot-path VPH, `639/161`, `800` processed, wall `1160.544s`.
- The fresh Pro+Free no-stagger source-map rerun was executed as `pro_free_source_map_v2` and regressed:
  - `pro_free_source_map_v2`: `2917.93` combined hot-path VPH, `397/403`, `800` processed, wall `489.8s`.
  - Per-lane: Pro `721.48` with `98/302` and `content_fetch_status_counts_total={"ready":98,"command_failed":2}`; Free `3035.89` with `299/101` and `content_fetch_status_counts_total={"ready":299,"command_failed":1}`.
- The add-path fix was then validated in a live rerun:
  - `pro_free_source_map_v3`: `3850.52` combined hot-path VPH, `614/186`, `800` processed, wall `574.052s`.
  - Per-lane: Pro `1795.93` with `286/114` and `content_fetch_status_counts_total={"ready":286,"command_failed":14}`; Free `2184.75` with `328/72` and `content_fetch_status_counts_total={"ready":328,"command_failed":22}`.
- A follow-up `pro_free_source_map_v4` attempt was stopped and is invalid. It launched an unprofiled `nlm login --force`, opening the default NotebookLM Chrome profile account chooser. Root cause: `csf/nlm_batch.py` still used unprofiled auth refresh commands while benchmark workers were otherwise profile-pinned. The auth helper now uses `NOTEBOOKLM_PROFILE` for `nlm login --check/--force`, and noninteractive mode fails closed if no profile is set.
- Cleanup-cost optimization was then tried through a bulk `source delete` cleanup path and a bounded settle wait. The live `pro_free_cleanup_opt_v2` rerun remained negative and the cleanup path was restored to the prior stable chunked delete behavior.
- The live `auth_smoke_v2` run was interrupted before it could finish, and it used `YTIS_NLM_AUTH_FORCE_REFRESH_EVERY_CHECKS='1'`, which is now treated as a stress-only setting. For any rerun whose goal is validation rather than browser churn, use `5` or leave the knob unset.
- The NotebookLM sparse-content metric was renamed:
  - Old status: `too_short`
  - New status: `nlm_content_below_threshold`
  - New diagnostic fields: `extraction_outcome`, `nlm_content_chars`, `usable_text_chars`
  - Legacy `too_short` remains only as a retry/defer compatibility input for old traces.
- A targeted isolated probe of representative benchmark `command_failed` videos (`j6lOJPRvuzc`, `MXAvtEHyl0A`, and `u2hmsms-alg`) came back `ready` in fresh notebooks, so the benchmark failures look transient or harness-sensitive rather than content-specific. Artifacts: `P://packages/yt-is/.logs/nlm_content_probe/residual_pro_v1/20260430T002429Z/probe_summary.json` and `P://packages/yt-is/.logs/nlm_content_probe/residual_free_v1/20260430T002429Z/probe_summary.json`.
- Phase 2 JSON corpus scan did not find literal `NOT_FOUND`, `source_add_failed`, or `source_id` strings in `pro_free_staggered_60s_v3/**/*.json`.
- Worker `stdout.txt` artifacts did show duplicate failed source IDs mapped to multiple video IDs. The bad `pro_free_post_retry_v3` run had `48` duplicate failed source IDs across `111` failed fetch lines.

Current interpretation:

- `pro_free_source_map_v1` remains the best sustained NotebookLM hot-path result.
- `pro_free_source_map_v1_replay_run02` completed cleanly after fixing the stale profile-family mapping, but only reached `2109.58` combined hot-path VPH with `715/85/800`, so the historical `5572.04` result is now confirmed as a non-reproduced high-water mark.
- `pro_free_source_map_v2` is a negative rerun: the Pro lane `source_add_failed` pattern dominated and the combined VPH fell well below the current best.
- `pro_free_source_map_v3` validates the add-path salvage fix and improves throughput materially, but it still does not beat `pro_free_source_map_v1`.
- `pro_free_source_map_v6` is a negative recheck after the notebook-reset fallback: `1837.24` combined hot-path VPH, `299/501`, `800` processed, wall `585.88s`. Pro `616.2` with `100/300` and `content_fetch_status_counts_total={"ready":100}`; Free `1394.33` with `199/201` and `content_fetch_status_counts_total={"ready":199,"command_failed":1}`. The fallback did not recover enough throughput to beat the current best.
- `pro_free_cleanup_opt_v2` is a negative cleanup-cost rerun: `1807.26` combined hot-path VPH, `349/451`, `800` processed, wall `695.2s`. Pro `2904.14` with `249/151`, `cleanup_elapsed_s_total=78.039`; Free `518.55` with `100/300`, `cleanup_elapsed_s_total=175.345`. Bulk cleanup did not improve throughput enough, so the code path was rolled back.
- `hotel_wifi_3plus3_shared_retry_canary_run19_current` is the current live hotel `3+3` rerun after hardening the source-count probe to retry once on `NOT_FOUND`. `_add_sources_in_subbatches` now reuses the scheduler's precomputed source count instead of issuing a duplicate pre-add `source list` probe, and the worker logs still confirm the shared retry pool is draining in multiple rounds. This branch is no longer an open correctness question; the remaining throughput bottleneck is extract-stage / worker-balance pressure, not shared-retry recovery. Treat `run18` as superseded for this branch.
- `hotel_wifi_3plus3_baseline_run01_current` is now the active hotel-scoped baseline: `2031.09` combined hot-path VPH, `715/85/800`, `throughput_valid=true`, `run_environment_label=hotel_wifi`, `worker_shape_signature=3+3`, and clean browser health before and after the run. Use this artifact for hotel-only comparisons while the home network is unavailable; do not compare it numerically to home-network ceilings.
- Reducer analysis of the hotel baseline keeps the same branch split visible: Pro is more auth-refresh sensitive (`worker-profile spread 2.0pp` vs `auth-refresh spread 27.7pp`), Free is more worker-balance sensitive (`worker-profile spread 18.2pp` vs `auth-refresh spread 4.9pp`), and both lanes remain extract-dominant at the aggregate stage level. Fetch-recovery attribution also shows the projected-age retry guard firing on primary passes (`projected_source_age_cliff`) while retries still absorb the remaining source-age-cliff and command-failed pressure.
- `hotel_wifi_3plus3_auth_interval75_run02_current` is a negative hotel rerun rather than a new ceiling. It completed cleanly at `2000.06` combined hot-path VPH with `656/144/800`; Pro landed at `1419.26` with `worker-profile spread 24.8pp` versus `auth-refresh spread 39.8pp`, and Free landed at `1159.89` with `worker-profile spread 19.6pp` versus `auth-refresh spread 51.4pp`. Both lanes stayed extract-dominant, so interval 75 is a hotel-specific regression relative to the active baseline and the next hotel-scoped lever remains auth-refresh timing rather than geometry or browser-root swapping.
- `hotel_wifi_3plus3_auth_interval45_run01_current` closed as a negative hotel rerun, not an improvement over the hotel baseline. It completed cleanly at `1602.82` combined hot-path VPH with `668/132/800`; Pro landed at `852.63` with `source_ready_age_s_max=389.134`, `content_fetch_command_elapsed_s_total=2279.507`, `worker_idle_wait_s_total=760.285`, and `content_fetch_status_counts_total={"ready":323,"source_age_cliff":72,"command_failed":50}`; Free landed at `1009.66` with `source_ready_age_s_max=326.358`, `content_fetch_command_elapsed_s_total=2316.044`, `worker_idle_wait_s_total=637.911`, and `content_fetch_status_counts_total={"ready":345,"source_age_cliff":43,"command_failed":42}`. Combined with the hotel baseline and interval 75 rerun, this closes hotel auth cadence as a throughput lever rather than a promising tuning axis.
- The `v3` regression was mostly wall-time and lifecycle variance, not just lower success count; the remaining `command_failed` cases now look transient rather than content-specific.
- A fresh isolated 50-source add on Pro succeeded, and a repeated reusable Pro run succeeded twice as well, so the remaining open issue is not a deterministic add/path break. The current evidence points to transient NotebookLM add flakiness that only shows up under the benchmark run shape.
- Source ID mapping was the prior highest-value correctness change. `nlm source add --wait` stdout is now the canonical add-order mapping path and has been validated live.
- The remaining true add failures on Pro were handled with a bounded zero-growth add retry, not a content-classification change.
- Do not treat `pro_free_source_map_v4` as benchmark evidence; rerun under a new output root after confirming no unprofiled `nlm login --force` appears in the process table.
- The remaining failure analysis must distinguish "NotebookLM returned less than the configured content threshold" from "the video is too short to matter." Short videos remain valid content candidates.
- The new `idle_wait_validation_run01` validation stayed clean (`200/0/200`) but still showed large idle cost: combined hot-path VPH `1038.73`, Pro `worker_idle_wait_s_total=478.6s`, Free `worker_idle_wait_s_total=542.0s`.
- Corrected auth accounting on that run shows family-refresh plus failed-login time of about `234.0s` on Pro and `247.6s` on Free, which is roughly `35%` of lane wall time and about half of the lane idle wait. Auth is a real contributor, but it does not explain all idle cost.
- The fresh `idle_wait_validation_run03` rerun with corrected auth-age logging improved to `1602.89` combined hot-path VPH and stayed clean (`200/0/200`). Auth refreshes were dominated by `cache_expired` rather than `cache_miss` (`312` vs `30` auth-refresh events in the worker logs), and the logged refresh age now looks sane: Pro `auth_cache_session_age_s` centered around `33.983s` with a `30.004s` minimum and `74.568s` maximum; Free showed the same pattern. This keeps auth cadence in play as a possible lever, but the reducer still labels extract as the lane bottleneck.
- The auth TTL 120 A/B `idle_wait_validation_auth_ttl120_run02` was also a negative branch: combined hot-path VPH fell to `988.48`, idle wait rose to `1126.331s`, and `source_ready_age_s_avg` rose to `124.626s` even though Pro `cache_expired` auth refreshes dropped from `312` to `231`. That makes TTL 120 a dead branch for this cohort.
- The tighter `YTIS_NLM_SOURCE_AGE_CLIFF_S=150` follow-up `idle_wait_validation_run04` was a hard negative: combined hot-path VPH fell to `257.95`, success/fail dropped to `180/20`, and worker idle wait exploded to `3402.45s`. The worker logs show `cache_expired` auth refreshes ballooned to `1206` versus `30` `cache_miss` events, so this threshold is too aggressive for the current cohort and should not be used as the default branch.
- Age-guard observability is live in the same run: `nlm_batch_subbatch_age_guard_rotation_requested` and `nlm_batch_notebook_recycled` appeared in the worker logs, so the guard path is now observable and functioning.
- The `clean_3plus3_pressure_run02` rerun with `--batch-size 100` completed only as a partial benchmark and did not become a new ceiling:
  - Combined hot-path VPH `814.6`, `700/100`, `700` processed, `status=partial`
  - Pro `248/102`, `worker_idle_wait_s_total=1820.633`, `source_ready_age_s_max=697.106s`
  - Free `349/1`, `worker_idle_wait_s_total=1776.019`, `source_ready_age_s_max=501.54s`
  - The final Pro failure was `NOT_FOUND` after retry, with `source_id_validated_after_not_found=null`; the Free lane was the slower lane overall and dominated the combined result
- The isolated Free-only 3-worker rerun completed as a negative branch at `free_only_retest_current_profile_run01`:
  - Combined hot-path VPH `1608.92`, `395/5`, `400` processed
  - `worker_idle_wait_s_total=30.591`, `source_ready_age_s_max=221.47s`
  - This is still well below the historical Free-only 3-worker leader, so it does not justify more geometry tuning on the Free lane
- The current Free regression is concentrated in startup and add cost, not browser-root cleanup or notebook creation:
  - `startup_notebook_create_elapsed_s_total=0.0` in both batches, so notebook creation is not the limiter
  - lane stderr does not show `closed default NotebookLM chrome-profile`, so browser-root cleanup is not the dominant signal here
  - current batch logs show `nlm_worker_notebook_cleanup_started` followed by deletion of `3-4` stale worker notebooks before work begins, and `startup_notebook_check_elapsed_s_total` / `startup_prepare_cleanup_elapsed_s_total` are both higher than the historical Free leader
- Next investigation should focus on worker-state hygiene and the notebook-check/add path: compare the shared `worker_states` lifecycle against the historical Free leader, then decide whether a fresh worker-state root or stricter preflight pruning is warranted before any more lane geometry work.
- The fresh worker-state-root control `free_only_fresh_state_control_run01` completed at `2825.29` combined hot-path VPH with `400/0/400` across two 200-item batches. Batch 1 was `2236.72` VPH and batch 2 was `3782.61` VPH. Both batches used clean per-run worker states and did not show stale-worker cleanup. That is now the strongest evidence that worker-state hygiene is the lever, not lane geometry.
- Throughput accounting is now split from cleanup accounting: benchmark summaries publish `throughput_wall_elapsed_s` separately from full `wall_elapsed_s`, and the combined VPH uses the lane-process throughput span. Treat any cleanup timing as hygiene cost, not sustained throughput.
- The sharded lane runner now defaults to a fresh per-run worker-state root under `<run-root>/<lane>/worker_states`, with `--preserve-worker-state-root` retained only for explicit reuse experiments. The next best investigation is to rerun the canonical lane-series comparison on the same cohort with that default in place. Do not spend more time on the stale Free-only rerun shape unless the worker-state path changes again.
- The fresh Pro-only control `fresh_state_pro_only_run01` completed cleanly but is still negative evidence for a ceiling:
  - combined lane-process throughput VPH `1556.64`
  - `397` hot-path successes, `3` failures, `400` processed
  - smoke `1108.22` and soak `1556.64` are both below the fresh-state Free-only control
  - `worker_shape_signature=4`
- The verified `3+3` fresh-state control `verified_3plus3_fresh_run01` completed cleanly but is negative for a ceiling: combined lane-process throughput VPH `1452.24`, Pro `779.39`, Free `818.34`, `800/800`, `worker_shape_signature=3+3`. It is below both fresh-state solo controls, so the next benchmark branch is not another geometry sweep; if further benchmarking is warranted, make it a stage-attribution probe on add/extract/idle timing first.
- The stage reducer now compares `setup_elapsed_s` as setup excluding add, because `nlm_batch.py` measures add inside setup. With that corrected, `fresh_state_pro_only_run01` and `verified_3plus3_fresh_run01` are both `stage-sum-suggested:extract`, while the historical `sweep_phase3_2lane_3w_run01` leader and `free_only_fresh_state_control_run01` are add-dominant. That means the remaining gap is not a worker-count problem; the next useful probe should explain why current combined runs spend far more aggregate extract time than the leader under similar add pressure.
- In the current extract-heavy runs, `content_fetch_command_elapsed_s_total` dominates the extract sub-metrics; on `fresh_state_3plus3_source_age_cadence_run06`, the per-run aggregate shows `content_fetch_command_elapsed_s_total=8138.072`, `source_list_probe_elapsed_s_total=36.876`, `content_fetch_retry_sleep_elapsed_s_total=154.277`, and `content_fetch_retry_queue_sleep_elapsed_s_total=120.0`. Treat the next probe as a `nlm source content` command-latency attribution problem first, not a geometry, retry-marker, or source-list-probe problem. The outer breadth-series summary now propagates `content_fetch_command_elapsed_s_total`, `source_list_probe_elapsed_s_total`, and the readiness-probe bucket from `fetch_completed.worker_stage_totals`; the archived logs in this plan predate that fix.
- The probe-enabled `fresh_state_3plus3_extract_schema_ready_probe_run01_current` smoke batch confirms the readiness-probe bucket is active but small: Pro batch 1 recorded `source_content_readiness_probe_count=1` and `source_content_readiness_probe_elapsed_s_total=2.002` against `content_fetch_command_elapsed_s_total=746.565`, while Free batch 1 recorded `source_content_readiness_probe_count=1` and `source_content_readiness_probe_elapsed_s_total=2.236` against `content_fetch_command_elapsed_s_total=660.714`. Treat readiness probing as a diagnostic marker, not the throughput ceiling.
- The top-level `sharded_lane_series_summary.json` combined object now also carries those stage totals. Live validation on `combined_stage_totals_validation_run01` (true `3+3`, `limit 20`, `40/0/40`) verified `combined.content_fetch_command_elapsed_s_total=116.89` and the merged aggregate block is present in the summary.
- The stage reducer now also accepts the benchmark-summary-only free control directly and exposes startup notebook check/create/retire plus startup-prepare cleanup sub-buckets. The fresh `1+1` pressure control was invalidated by a malformed JSONL parse in `worker_count_sweep.py`; that loader is now hardened and regression-tested, but the run itself is not throughput evidence.

## Non-Negotiable Controls

- Run from `P://packages/yt-is`.
- Keep the control comparison against `pro_free_v2`, not against the slower `pro_free_staggered_60s_v3`.
- Keep no-stagger Pro+Free as the default benchmark shape unless this plan explicitly says to test a stagger variant.
- Keep `--batch-size 200`; it has already beaten nearby and larger batch sizes for this workload.
- Keep `--reusable-pipeline-mode serial`; double-buffered runs have not established a stable win.
- Keep profile-pinned NotebookLM commands. Do not use `nlm login switch` in concurrent worker code.
- For any new root, run `doctor` first, then the smoke, then `csf-run-evidence-check`, then the long soak.
- Keep dedicated Chrome roots:
  - Pro: `P://.data/yt-is/browser/notebooklm-pro`
  - Free: `P://.data/yt-is/browser/notebooklm-free`
- Keep account mapping:
  - Pro: `a.hominidae@gmail.com`
  - Free: `troup.hominidae@gmail.com`
- Keep source cleanup recheck protection before deleting stale worker notebooks.
- Do not count Whisper fallback in VPH. If a summary includes fallback fields, report them separately.

## Preflight

- [ ] Confirm no old benchmark process is running.

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'csf-sharded-lane-series|csf-source|nlm_batch' } |
  Select-Object ProcessId, CommandLine
```

Expected: no active benchmark processes. If a benchmark process is active, stop and decide whether it is the intended run before starting a new one.

- [ ] Confirm no unprofiled NotebookLM auth browser is running.

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'nlm login --force|remote-debugging-port=9222|\.notebooklm-mcp-cli\\chrome-profile' } |
  Select-Object ProcessId, Name, CommandLine
```

Expected: no default NotebookLM auth browser and no unprofiled `nlm login --force`. A transient `nlm login --force --profile <worker-profile>` is acceptable only when tied to one of the named worker profiles. If `nlm login --force` appears without `--profile`, stop the run and mark it invalid.

- [ ] Run the worker-notebook preflight cleanup before the benchmark starts.

Expected: stale worker notebooks are cleared through the existing worker-notebook cleanup path before any timed trial begins. The worker process still prewarms its notebook before processing, so the measured run starts from a clean, reproducible notebook state.

- [ ] Validate all NotebookLM worker profiles.

```powershell
foreach ($profile in @(
  'ytis-pro-worker-01', 'ytis-pro-worker-02', 'ytis-pro-worker-03', 'ytis-pro-worker-04',
  'ytis-free1-worker-01', 'ytis-free1-worker-02', 'ytis-free1-worker-03', 'ytis-free1-worker-04'
)) {
  nlm login --check --profile $profile
}
```

Expected: every profile is authenticated. If any profile fails, use the dedicated browser auth refresh commands in `sharded-lane-series.md`. Do not use a shared/default Chrome profile.

The sharded runner now also performs this as a mandatory preflight. If a profile is expired, it runs one bounded `nlm login --force --profile <profile>` recovery before launching any lane. During benchmark subprocesses, `csf-source` runs with `YTIS_NLM_AUTH_NONINTERACTIVE=1`; expired auth uses `nlm login --force` instead of plain interactive `nlm login`.

- [ ] Run the existing focused regression tests before changing code.

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
pytest tests/test_nlm_batch.py -q
python -m py_compile csf/nlm_batch.py tests/test_nlm_batch.py bin/csf-source
```

Expected: tests pass and compile succeeds. If this fails before new edits, stop and inspect the current worktree before modifying behavior.

## Verified Test Suite

Use this suite before and after the next code change:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-nlm-worker-auth sync
pytest tests/test_nlm_batch.py tests/test_nlm_config.py tests/test_sharded_lane_series.py tests/test_nlm_worker_auth.py -q
python -m py_compile csf/nlm_batch.py csf/nlm_config.py csf/nlm_worker_auth.py tests/test_nlm_batch.py tests/test_nlm_config.py tests/test_nlm_worker_auth.py bin/csf-source bin/csf-nlm-worker-auth
```

Last verified results:

- `pytest tests/test_nlm_batch.py -q`: `68 passed` after `nlm_content_below_threshold` metric update.
- `pytest tests/test_nlm_scraper.py -q`: `59 passed` after staging scraper readiness-probe metric update.
- `pytest tests/test_worker_count_sweep.py tests/test_fallback_crossover_benchmark.py -q`: `10 passed` after reporting fixtures were updated to the new status.
- `python bin/csf-nlm-worker-auth sync`: uses account-aware `nlm login --check` parsing, repairs expired or wrong-account worker `01` profiles through the dedicated CDP root by default, then copies only after the renewed source profile matches the expected account.
- `pytest tests/test_nlm_batch.py tests/test_nlm_config.py tests/test_sharded_lane_series.py tests/test_nlm_worker_auth.py -q`: `79 passed`.
- `pytest tests/test_nlm_batch.py -q -k 'records_source_ids_from_stdout_in_order or rejects_duplicate_source_ids_before_fetch'`: `2 passed`.
- `pytest tests/test_nlm_worker_auth.py -q -k "real_nlm_process or worker_auth_cli_sync"`: `2 passed`; these are process-boundary tests that run a real temporary `nlm` executable and verify `check -> force -> check -> copy`, including the `bin/csf-nlm-worker-auth sync` wrapper, without mocking `subprocess.run`.
- `pytest tests/test_nlm_batch.py tests/test_nlm_config.py tests/test_sharded_lane_series.py tests/test_nlm_worker_auth.py tests/test_csf_source_fetch_timing.py -q -k "not cmd_check_all_emits_elapsed_scan_status_heartbeat and not logs_fetch_start_and_first_download_started_industrial and not limit_caps_selected_pending_items and not logs_worker_prewarm_summary_before_dispatch"`: `104 passed, 4 deselected`.
- `python -m py_compile ...`: passed for the touched `nlm_batch`, config, auth helper, tests, and CLI wrappers.

## Auth Renewal Proof Gate

Run this before the next full benchmark whenever any worker profile has expired:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-nlm-worker-auth sync
foreach ($profile in @(
  'ytis-pro-worker-01', 'ytis-pro-worker-02', 'ytis-pro-worker-03', 'ytis-pro-worker-04',
  'ytis-free1-worker-01', 'ytis-free1-worker-02', 'ytis-free1-worker-03', 'ytis-free1-worker-04'
)) {
  nlm login --check --profile $profile
}
```

Expected:

- If a worker `01` source profile is expired or mapped to the wrong account but recoverable, `csf-nlm-worker-auth sync` should refresh it through the configured dedicated CDP root, pass the follow-up account check, then copy refreshed credentials to sibling workers.
- If Google requires passkey/browser approval or the dedicated CDP root is itself on the wrong account, the command must fail before copying sibling credentials. Refresh only the affected worker `01` through the manual CDP flow in `sharded-lane-series.md`, then rerun this gate.
- Do not start `pro_free_source_map_v1` until all twelve `nlm login --check --profile ...` commands pass.

## Metrics To Record

For every full benchmark, record these values from `sharded_lane_series_summary.json`:

- `combined.hot_path_videos_per_hour`
- `combined.hot_path_success_count`
- `combined.hot_path_failure_count`
- `combined.processed_count`
- `combined.wall_elapsed_s`
- Per-lane hot-path VPH
- Per-lane success and failure counts
- Per-lane `cleanup_elapsed_s`
- Per-lane `add_elapsed_s`
- Per-lane `idle_elapsed_s`
- `content_fetch_status_counts_total`
- Any `source_add_failed` count
- Any content-fetch `NOT_FOUND` count
- Whisper fallback counts, reported separately and excluded from hot-path VPH

Use completed-worker totals and stage timings as throughput truth. Do not use backlog scan rate, queued item count, or fallback recovery count as sustained hot-path VPH.

## Phase 1: Fix Bounded Source-Add Retry

Purpose: recover transient whole-batch `source_add_failed` events without hiding permanent failures or creating duplicate add loops.

Known evidence: `pro_free_staggered_60s_v3` still had a counted Free lane `source_add_failed` where a 50-video subbatch failed quickly with zero added sources. That is a correctness and throughput opportunity.

- [x] Inspect the current source-add path in `P://packages/yt-is/csf/nlm_batch.py`.
- [x] Add or update focused tests in `P://packages/yt-is/tests/test_nlm_batch.py` for:
  - transient source-add command failure retries once and then succeeds
  - permanent source-add command failure stops after the configured retry limit
  - retry logs include attempt count and worker profile
  - retry path still passes `--profile <worker-profile>` to every `nlm source` command
  - retry does not call `nlm login switch`
- [x] Implement bounded retry only around the source-add command failure class.
- [x] Do not retry content-fetch failures in this phase.
- [x] Do not retry Whisper fallback in this phase.

Run:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
pytest tests/test_nlm_batch.py -q
python -m py_compile csf/nlm_batch.py tests/test_nlm_batch.py bin/csf-source
```

Pass criteria:

- [x] Focused tests pass.
- [x] Permanent failure still exits quickly.
- [x] Command construction remains profile-pinned.
- [x] Logs make retries auditable.

Stop criteria:

- If retry requires broad pipeline restructuring, stop and document the blocker before running a full benchmark.
- If retry causes duplicate source IDs or duplicate transcripts in a smoke run, revert only the new retry change and investigate before continuing.

## Phase 2: Diagnose Content-Fetch NOT_FOUND

Purpose: reduce counted `command_failed`/`NOT_FOUND` failures after source materialization.

Known evidence: `pro_free_staggered_60s_v3` still had content-fetch `NOT_FOUND` cases after the profile race and cleanup-race materialization timeout were fixed. Repeated source IDs appeared across multiple video IDs, so source-to-video mapping must be verified before assuming NotebookLM backend loss.

- [ ] Use the existing v3 artifacts as the failure corpus.

```powershell
Select-String -Path '.logs/sharded_lane_series/pro_free_staggered_60s_v3/**/*.json' -Pattern 'NOT_FOUND','source_add_failed','source_id' -List
```

- [ ] Add diagnostics or tests that prove whether one materialized source ID maps to exactly one input video ID inside a worker batch.
- [ ] Check whether source list parsing can reuse a stale source row, duplicate source ID, or wrong title/url match.
- [ ] If mapping is ambiguous, fix the mapping logic so the worker relists and remaps before `nlm source content`.
- [ ] If NotebookLM legitimately returns `NOT_FOUND` for a previously listed source, classify it distinctly from auth failure and source-add failure.

Run:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
pytest tests/test_nlm_batch.py -q
python -m py_compile csf/nlm_batch.py tests/test_nlm_batch.py bin/csf-source
```

Pass criteria:

- Tests cover the observed duplicate or stale mapping failure mode.
- Content-fetch failures have stage-specific status, not generic conflation with auth or source-add failures.
- The fix does not add broad sleeps to the hot path.

Stop criteria:

- If the root cause is not reproducible from logs or unit seams, document the uncertainty and proceed to Phase 3 only if Phase 1 is already passing.

Phase 2 evidence update:

- The summary JSON files did not contain the target strings, but worker stdout did.
- Use stdout as the failure corpus for source ID mapping diagnosis:

```powershell
rg -n "Fetch failed for|Source ID:|source_id_title_match_count|source_id_order_fallback_count" `
  P://packages/yt-is/.logs/sharded_lane_series/pro_free_post_retry_v3 `
  -g "stdout.txt" -g "*.jsonl"
```

- Reproduce the duplicate-mapping risk in a unit test by creating a source list where one source entry exact-matches a video ID and the remaining entries rely on order fallback. The final mapping must be one-to-one and must not assign one source ID to multiple video IDs.
- Preferred implementation: parse `Source ID:` lines from the successful `nlm source add --wait` stdout in add order and persist that as the canonical mapping for the just-added video IDs. Keep `source list` as a materialization/count check, not the primary correlation source.
- Add a defensive duplicate-source-ID guard before `nlm source content` fetches. If duplicates are detected, log the duplicated source IDs and affected video IDs, classify the batch as a mapping failure, and do not waste hot-path time retrying duplicated content fetches.

## Phase 3: Run Fresh No-Stagger Control

Purpose: prove whether the fixes beat the current best under the same benchmark shape.

Use a new output root. Do not overwrite prior evidence.

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-sharded-lane-series `
  --lane-config P://packages/yt-is/.logs/sharded_lane_series/pro_free_lanes.json `
  --output-root P://packages/yt-is/.logs/sharded_lane_series/pro_free_post_retry_v1 `
  --cohort-json P://packages/yt-is/.logs/sharded_lane_series/pro_free_post_retry_v1/cohort.json `
  --limit 400 `
  --batch-size 200 `
  --reusable-pipeline-mode serial
```

Extract summary:

```powershell
@'
import json
from pathlib import Path

path = Path("P://packages/yt-is/.logs/sharded_lane_series/pro_free_post_retry_v1/sharded_lane_series_summary.json")
summary = json.loads(path.read_text())
print(json.dumps({
    "artifact": str(path),
    "combined_hot_path_vph": summary["combined"]["hot_path_videos_per_hour"],
    "success": summary["combined"]["hot_path_success_count"],
    "failure": summary["combined"]["hot_path_failure_count"],
    "processed": summary["combined"]["processed_count"],
    "wall_elapsed_s": summary["combined"]["wall_elapsed_s"],
    "lanes": {
        lane["lane"]: {
            "hot_path_vph": lane["hot_path_videos_per_hour"],
            "success": lane["hot_path_success_count"],
            "failure": lane["hot_path_failure_count"],
            "cleanup_elapsed_s": lane.get("cleanup_elapsed_s"),
            "add_elapsed_s": lane.get("add_elapsed_s"),
            "idle_elapsed_s": lane.get("idle_elapsed_s"),
            "content_fetch_status_counts_total": lane.get("content_fetch_status_counts_total"),
        }
        for lane in summary["lanes"]
    },
}, indent=2))
'@ | python -
```

Decision:

- If VPH is greater than `4148.71` and there are no harness/auth failures, this is the new best known method.
- If VPH is at least `4356`, treat it as a strong win because it is more than `5%` above control.
- If VPH is lower than or equal to `4148.71`, keep `pro_free_v2` as the max known sustained result and document the negative result.
- If the run has `PERMISSION_DENIED`, same-account behavior, or materialization-timeout cleanup race, mark it invalid, fix the harness, and rerun once.

Fresh rerun outcome:

- The fresh no-stagger rerun after worker auth sync completed cleanly and beat the prior control:
  - combined hot-path VPH: `4407.40`
  - hot-path success/failure: `688/112`
  - relative to `pro_free_v2`, this is a strong win and the new best sustained result
- Pro lane hot-path VPH: `2518.32`
- Free lane hot-path VPH: `1984.00`
- The lane stdout summaries showed no `PERMISSION_DENIED` or cleanup-race invalidation.

Later control recheck:

- A subsequent fresh no-stagger control rerun under `pro_free_post_retry_v3` regressed sharply:
  - combined hot-path VPH: `1982.17`
  - hot-path success/failure: `639/161`
  - Pro lane hot-path VPH: `1202.13`
  - Free lane hot-path VPH: `1036.97`
- Treat that as a negative control recheck, not the new best method.

## Phase 4: Cleanup Cost Optimization

Purpose: reduce measured hot-path wall time only after source-add and content-fetch correctness are stable.

Candidate approaches:

- Avoid full notebook delete/recreate when source delete/reset is sufficient and faster.
- Move nonessential stale-notebook inventory outside the measured hot path, but only if active worker notebooks remain protected.
- Keep notebook reuse/audit behavior deterministic; do not reintroduce accidental deletion of active worker notebooks.

Test shape:

- Use the same Pro+Free no-stagger control.
- Use the same `--limit 400`, `--batch-size 200`, and serial pipeline.
- Use a new output root such as `P://packages/yt-is/.logs/sharded_lane_series/pro_free_cleanup_opt_v1`.
- Compare against both `pro_free_v2` and the Phase 3 post-retry result.

Pass criteria:

- Combined hot-path VPH increases.
- `cleanup_elapsed_s` decreases materially.
- Failure count does not increase.
- No active worker notebook is deleted.

Stop criteria:

- If cleanup optimization lowers cleanup time but increases failures enough to reduce VPH, record it as negative and keep the old cleanup path.

Outcome:

- `pro_free_cleanup_opt_v2` was a negative cleanup-cost rerun:
  - combined hot-path VPH: `1807.26`
  - hot-path success/failure: `349/451`
  - processed: `800`
  - wall elapsed: `695.2s`
- Pro lane hot-path VPH: `2904.14`
- Free lane hot-path VPH: `518.55`
- The bulk source-delete cleanup path did not improve throughput enough and the prior chunked cleanup path was restored.

## Phase 5: Focused Sparse-Content And Command-Failed Probe

Purpose: explain the four residual failures from `pro_free_source_map_v1` before spending more time on broad worker/load sweeps.

Known evidence from the latest best run:

- `juXI9QbzzgM` failed in both Pro and Free lanes as below-threshold NotebookLM content.
- `u2hmsms-alg` failed in both Pro and Free lanes as `command_failed`.
- The same two video IDs failed across lanes with different NotebookLM source IDs, so the next hypothesis should be content/path behavior, not lane auth or source-ID mapping.

Live probe result:

- `juXI9QbzzgM` is stable below-threshold NotebookLM content on both `ytis-pro-worker-01` and `ytis-free1-worker-01`.
- `u2hmsms-alg` recovered immediately on both `ytis-pro-worker-01` and `ytis-free1-worker-01` in isolated probe runs, so the benchmark `command_failed` result looks transient or harness-sensitive rather than content-intrinsic.
- The probe harness is now available as [`bin/csf-nlm-content-probe`](../../bin/csf-nlm-content-probe) and writes JSON artifacts under `.logs/nlm_content_probe/`.

Required setup:

- Use the new status name `nlm_content_below_threshold`.
- Capture `nlm_content_chars`, `usable_text_chars`, raw `nlm source content` return code, stdout, and stderr.
- Do not classify short videos as low value. A short video can be valuable; the question is whether NotebookLM source-content extraction produced usable text.
- Do not run another full 800-item benchmark until this probe has been completed.

Recommended live probe:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-nlm-worker-auth sync

# Build the smallest possible source-content probe around these two IDs.
# If no dedicated probe command exists yet, implement one rather than running
# another full sharded benchmark.
$videoIds = @('juXI9QbzzgM', 'u2hmsms-alg')
```

Probe requirements:

- Add each video to a disposable or explicitly named probe notebook under both account families.
- Parse `Source ID:` from `nlm source add --wait` stdout and use that source ID directly.
- Run `nlm source content <source-id> --json` immediately, then after delayed retries such as `30s`, `60s`, and `120s`.
- Record per attempt:
  - account family and profile
  - video ID
  - source ID
  - `video_duration_s` if available from yt-dlp metadata
  - `nlm_content_chars`
  - `usable_text_chars`
  - `extraction_outcome`
  - `returncode`
  - first 500 chars of stdout/stderr

Expected interpretation:

- If `juXI9QbzzgM` consistently returns below-threshold NotebookLM content but fallback transcript extraction yields usable text, route that class to a short-video fallback rather than counting it as a failed video.
- If `u2hmsms-alg` recovers immediately or after delay, treat the benchmark `command_failed` as transient and revisit only the retry/harness timing if it reappears in full-benchmark concurrency.
- If either video recovers after delayed retry, adjust retry timing/classification before repeating the full benchmark.

Pass criteria:

- The two residual failure classes have distinct, auditable outcomes.
- Future benchmark summaries no longer report `too_short` for NotebookLM source-content results.
- The next full benchmark can distinguish hot-path failures from fallback-recoverable sparse content.
- The residual `command_failed` class is shown to be transient or harness-sensitive, not a stable content class.

Stop criteria:

- If the probe requires user passkey/auth intervention, stop and repair worker-profile auth before collecting evidence.
- If source IDs cannot be mapped directly from add stdout, stop and fix the probe harness rather than trusting `source list` order.
- If a broad benchmark is started before this probe, mark it as premature in the registry.

## Failure Triage Rules

- `PERMISSION_DENIED` during source materialization usually means profile/account mismatch or a command that was not pinned with `--profile`; fix auth/profile routing before trusting throughput.
- `source_add_failed` with zero added sources is a source-add stage failure; apply Phase 1 retry logic, then measure.
- `NOT_FOUND` during content fetch after source materialization is not the same as cleanup-race materialization timeout; use Phase 2 diagnostics.
- A run with the wrong account in either lane is invalid.
- A smoke run can validate behavior, but it cannot establish sustained VPH.
- A staggered run is not the control unless a no-stagger run with the same code path has already been recorded.

## Documentation Requirements

After each full benchmark:

- [ ] Add a row to `P://packages/yt-is/docs/operations/test-registry.md`.
- [ ] Update `P://packages/yt-is/docs/operations/sharded-lane-series.md` if the recommended method, current best, auth contract, or caveats change.
- [ ] Include the exact artifact path.
- [ ] Include combined hot-path VPH.
- [ ] Include success, failure, processed count, and wall time.
- [ ] Include whether Whisper was used and explicitly state that it was excluded from VPH.
- [ ] Mark the result `proven`, `negative`, `invalid`, or `pending`.
- [ ] Add a rerun guard naming the code path that would justify repeating the test.

## Recommended Next Action

Source-add retry, worker auth sync, auth auto-renew regression tests, source ID mapping hardening, the zero-growth add retry, notebook-reset fallback for zero-growth add failures, profile-pinned `nlm_batch` auth refresh, and the `nlm_content_below_threshold` metric rename are now implemented. The fresh source-map reruns did not improve on the current best: `pro_free_source_map_v2` regressed to `2917.93` combined hot-path VPH with the Pro lane dominated by `source_add_failed`; `pro_free_source_map_v3` improved materially to `3850.52` but still trailed the best; `pro_free_source_map_v5` completed cleanly but still showed Free lane `source_add_failed`; and `pro_free_source_map_v6` after the notebook-reset fallback was negative at `1837.24`. Phase 5 has now been executed: `juXI9QbzzgM` is stable sparse content and representative benchmark `command_failed` cases were recoverable in isolated probes, so those failures are treated as transient/harness-sensitive rather than content-specific. A fresh isolated 50-source add on Pro succeeded, and a repeated reusable Pro run succeeded twice, so the remaining open issue is now narrowed to transient NotebookLM add flakiness under the benchmark run shape rather than a deterministic add bug. The root cause for the Pro regression is now understood: `nlm source add` can return nonzero even when the notebook source count reaches the full batch size, and the batch ingestor now treats that as recovered success instead of a hard failure. The add path now also logs retry attempt counts and the active NotebookLM worker profile so retries are auditable. `pro_free_source_map_v1` remains the best sustained Pro+Free result. Cleanup-cost optimization was attempted next, but `pro_free_cleanup_opt_v2` stayed negative, so the cleanup path was rolled back and no documented phase remains to rerun without a new hypothesis.

The completed `sweep_phase3_2lane_3w_run05` auth-check cache TTL A/B is now also negative evidence. It finished cleanly at `1958.94` combined hot-path VPH, with `132` Pro logins, `128` Free logins, `session_age_s` still in the `0-30s` band, and higher `add_elapsed_s_total`, `worker_idle_wait_s_total`, and `source_ready_age_s_avg` than the `run04` comparator. That makes auth-check cache TTL a dead branch for this cohort and shifts the next investigation toward source-add/readiness/setup cost, startup/setup overhead, or another non-TTL limiter.
The later guarded `sweep_phase3_2lane_3w_run06` rerun recovered the browser-health gate and the auth state but still only reached `2284.56` combined hot-path VPH with `794/6/800`; it improved over run05, but it remained far below the historical `3+3` leader, so browser-health hygiene is now a solved preflight issue rather than the throughput limiter.
The later single-lane calibration pair sharpened the same point: Pro-only stayed at `1980.19` combined hot-path VPH with `worker_idle_wait_s_total=243.778`, while Free-only reached `3361.75` with `worker_idle_wait_s_total=0.0`. The per-worker traces show the Pro lane also paid a much larger `extract_elapsed_s_total` on at least one worker, so the next useful probe is the Pro startup/setup -> extract path, not another auth TTL or lane-count repeat.
The fresh Pro-only rerun after lane-config repair was even weaker at `1105.3` combined hot-path VPH with `398/2/400`, `worker_idle_wait_s_total=804.382`, and `source_ready_age_s_avg=67.471`; a fresh Free-only rerun completed at `929.05` combined hot-path VPH with `199/1/200`, but that sample is only auxiliary because the processed count was `200`, not a like-for-like 400-item comparator. The branch still points at startup/setup and extract costs, but it is now noisy enough that another lane-width or TTL repeat is not the next best move.
The fresh guarded repeat `sweep_phase3_2lane_3w_run07` completed cleanly but only reached `1974.57` combined hot-path VPH. Its `37` `command_failed` events were all `NOT_FOUND`, the new source-list probe marked every one `source_validated=true`, and the failure rate by source age was sharply skewed: `0%` below `200s`, `25%` in `200-300s`, `95.65%` in `300-400s`, and `100%` above `400s`. That points more toward source-id remap/staleness or notebook-age pressure than a missing retry marker, and it makes notebook rotation/cadence or source-readiness refresh the more promising next probe than broader retry policy. The probe now also records the matched source-row metadata, so the next benchmark can distinguish a stale ID from the notebook showing the expected row.
The follow-up `run07_age_300_probe` on a single source stayed `ready` at both the immediate and 300-second fetches, so notebook age alone does not reproduce the `NOT_FOUND` behavior on a one-source notebook. That leaves the remaining hypothesis space centered on multi-source notebook load, rotation cadence, or benchmark-specific source remapping under batch pressure.
The fresh guarded repeat `sweep_phase3_2lane_3w_run08` regressed to `1779.65` combined hot-path VPH with `794/6/800`. The Pro lane saw `45` `command_failed` events and the Free lane saw `45`; the live `NOT_FOUND` completions still had `source_validated_after_not_found=true`, and the failed rows were age-skewed into the `243s`-`382s` range while fresh recreated notebook batches near `5.6s` stayed healthy. That keeps the remaining hypothesis centered on notebook-age / rotation cadence under batch pressure, not a missing retry marker or a stale-source-id-only problem. The next useful experiment is a narrower age-capped benchmark or notebook-rotation probe that keeps `source_ready_age_s` below the cliff, not another same-shape repeat.
The age-capped guarded sequence `sweep_phase3_2lane_3w_agecap_200_run02` improved the same 3+3 shape to `3084.08` combined hot-path VPH with `398/2/400` and clean post-run hygiene. The age cap held both lanes under the earlier cliff, with Pro `source_ready_age_s_max=211.292` and Free `source_ready_age_s_max=160.966`, and the residual failures shifted to `nlm_content_below_threshold` rather than `NOT_FOUND`. That means the age cap reduced the cliff but still did not beat the historical `4123.28` leader, so the next branch is notebook rotation or age-guard refinement, not broader retry markers.
The full-load age-cap scaling test `highest_vph_agecap_400_run02` completed cleanly but only reached `1385.45` combined hot-path VPH with `616/184/800`. Pro landed at `289/111` with `source_ready_age_s_max=622.639`, and Free landed at `327/73` with `source_ready_age_s_max=598.112`; the residual failures stayed age-cliff dominated (`ready` plus `source_age_cliff` only), so the age cap did not scale under 4+4 load. The next branch after this result is notebook rotation or age-guard refinement, not sparse-content or retry-marker tuning.
The worker-auth-repaired full-load retest `highest_vph_agecap_400_run03` still did not become a ceiling: it completed cleanly with `1792.5` combined hot-path VPH and `660/140/800`, but Pro still reached `source_ready_age_s_max=392.256` with `39` `command_failed` and `89` `source_age_cliff` rows, and Free reached `source_ready_age_s_max=354.611` with `71` `command_failed` and `43` `source_age_cliff` rows. The run is valid negative evidence for the 4+4 branch, not a new ceiling.
The follow-up targeted probe on `juXI9QbzzgM` and `u2hmsms-alg` after auth refresh repeated the same split: `juXI9QbzzgM` stayed below threshold on every delayed retry, while `u2hmsms-alg` returned `ready` immediately on the first attempt. That reconfirms the residual pair is not a new content-class regression; it is the same stable below-threshold case plus a harness-sensitive/variable `command_failed` case, so the next live probe should target notebook-age or rotation cadence rather than the video pair itself.

## Phase 6: Source-Map Rerun After Profile-Pinned Auth Fix

Purpose: validate whether the zero-growth source-add retry plus profile-pinned `nlm_batch` auth refresh closes the remaining source-map regression. This is a benchmark-only phase. Do not add notebook-reset fallback, cleanup changes, worker-count changes, batch-size changes, or stagger changes before this rerun.

The interrupted `pro_free_source_map_v4` attempt is invalid and must not be used as throughput evidence. It stopped before a `sharded_lane_series_summary.json` was produced and exposed an unprofiled `nlm login --force` path that opened the default NotebookLM Chrome profile account chooser.

Preflight:

- Confirm no stale benchmark or auth process is running with the two process checks above.
- Run the auth renewal proof gate.
- Run `pytest tests/test_nlm_batch.py -q` after any auth-path edit.
- Confirm the lane config still uses the dedicated Pro and Free worker profiles from `pro_free_lanes.json`.

Run exactly one fresh source-map rerun under a new output root:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-sharded-lane-series `
  --lane-config P://packages/yt-is/.logs/sharded_lane_series/pro_free_lanes.json `
  --output-root P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v5 `
  --cohort-json P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v5/cohort.json `
  --limit 400 `
  --batch-size 200 `
  --reusable-pipeline-mode serial
```

Live guard while the benchmark is running:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'csf-sharded-lane-series|csf-source fetch|nlm login --force|remote-debugging-port=9222|\.notebooklm-mcp-cli\\chrome-profile' } |
  Select-Object ProcessId, Name, CommandLine
```

Expected:

- `nlm login --force --profile <worker-profile>` may appear briefly only if a worker profile expires.
- `nlm login --force` without `--profile` invalidates the run; stop it and fix the caller.
- Chrome using `C:\Users\brsth\.notebooklm-mcp-cli\chrome-profile` invalidates the run for Pro+Free sharded benchmarking.
- Chrome using the dedicated Pro or Free roots is acceptable only when tied to the configured lane roots.

Extract the result:

```powershell
@'
import json
from pathlib import Path

path = Path("P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v5/sharded_lane_series_summary.json")
summary = json.loads(path.read_text())
print(json.dumps({
    "artifact": str(path),
    "combined_hot_path_vph": summary["combined"]["hot_path_videos_per_hour"],
    "success": summary["combined"]["hot_path_success_count"],
    "failure": summary["combined"]["hot_path_failure_count"],
    "processed": summary["combined"]["processed_count"],
    "wall_elapsed_s": summary["combined"]["wall_elapsed_s"],
    "lanes": {
        lane["lane"]: {
            "hot_path_vph": lane["hot_path_videos_per_hour"],
            "success": lane["hot_path_success_count"],
            "failure": lane["hot_path_failure_count"],
            "cleanup_elapsed_s": lane.get("cleanup_elapsed_s"),
            "add_elapsed_s": lane.get("add_elapsed_s"),
            "idle_elapsed_s": lane.get("idle_elapsed_s"),
            "content_fetch_status_counts_total": lane.get("content_fetch_status_counts_total"),
            "source_add_failed": lane.get("source_add_failed"),
        }
        for lane in summary["lanes"]
    },
}, indent=2))
'@ | python -
```

Decision:

Fresh rerun outcome:

- `pro_free_source_map_v5` completed cleanly with no unprofiled auth browser activity.
- Combined hot-path VPH: `3930.79`
- Hot-path successes/failures: `638/162`
- Processed: `800`
- Wall elapsed: `584.31s`
- Pro lane: `2406.66` hot-path VPH, `390/10`, `content_fetch_status_counts_total={"ready":390,"nlm_content_below_threshold":1,"command_failed":9}`
- Free lane: `2001.72` hot-path VPH, `248/152`, `content_fetch_status_counts_total={"ready":248,"nlm_content_below_threshold":1,"command_failed":1}`
- Free lane stdout still shows `source_add_failed` in batch 01 and batch 02, so the add-path fallback is still incomplete even though auth is fixed.

Decision:

- `pro_free_source_map_v5` improved over `pro_free_source_map_v3` but did not beat `pro_free_source_map_v1`.
- The next code change should be a bounded notebook-reset retry fallback for the zero-growth add failure class, targeted at the Free lane path that still emits `source_add_failed`.
- Do not rerun the same source-map shape again until that fallback exists or a stronger reason to repeat it is documented.

## Phase 7: Implement Notebook-Reset Fallback For Zero-Growth Add Failure

Purpose: recover the remaining Free lane `source_add_failed` cases shown in `pro_free_source_map_v5` without broad retries, hidden duplicate adds, or another auth regression.

Current evidence:

- `pro_free_source_map_v5` completed with profile-pinned auth, so auth is not the current blocker.
- Pro lane was comparatively healthy: `390/10`.
- Free lane had `248/152` and stdout showed zero-growth `source_add_failed` in both batch 01 and batch 02.
- The existing bounded zero-growth retry did not recover those Free lane failures.

Implementation target:

- Primary file: `P://packages/yt-is/csf/nlm_batch.py`
- Primary tests: `P://packages/yt-is/tests/test_nlm_batch.py`
- Existing code points:
  - `_add_sources_chunk(...)` contains the current zero-growth add retry.
  - `_ZERO_GROWTH_ADD_RETRY_LIMIT` and `_ZERO_GROWTH_ADD_RETRY_DELAY_S` control the first retry.
  - `_rotate_notebook()` currently clears/recycles a notebook when source count approaches the cap.
  - `_add_sources_in_subbatches(...)` records subbatch status and source counts.

Required behavior:

- Only trigger notebook-reset fallback when all of these are true:
  - `nlm source add` returned nonzero.
  - `source_count_after == source_count_before`.
  - Failure reason is `source_add_failed`.
  - The normal bounded zero-growth retry has already been used.
- On fallback:
  - Retire or reset the current worker notebook through the existing worker-owned notebook lifecycle path.
  - Prepare a fresh notebook for the same worker profile and same notebook prefix.
  - Retry the same subbatch once.
  - Keep every `nlm` command profile-pinned through `NOTEBOOKLM_PROFILE`.
  - Preserve source-ID mapping from `nlm source add --wait` stdout after the retry.
  - Do not split the batch into smaller subbatches as the fallback.
  - Do not retry content-fetch failures in this phase.

Required logs:

- Emit a distinct scheduling log such as `nlm_batch_subbatch_add_notebook_reset_retry_scheduled`.
- Include `nb_id`, `subbatch_index`, `subbatch_size`, `retry_depth`, source counts before/after, `source_profile`, `notebooklm_profile`, and the old/new notebook IDs when available.
- Emit a distinct exhausted log if the reset retry also fails.
- Keep existing `nlm_batch_subbatch_add_failed` behavior for final failure.

Required tests:

- Zero-growth add failure uses the existing in-place retry first.
- If the in-place retry also fails with zero growth, the notebook-reset fallback is scheduled once.
- The reset fallback retries the same video IDs and recovers when the fresh notebook add succeeds.
- If the reset fallback also fails, the subbatch returns empty and logs final failure.
- The fallback does not run for nonzero add returns that already grew the source count to the expected total.
- The fallback does not run for content-fetch `command_failed` or `nlm_content_below_threshold`.
- The fallback keeps configured batch size; it must not shrink recursively.
- The fallback does not create duplicate source IDs and preserves stdout-derived source-ID order.
- The auth context remains profile-pinned; no test should expect unprofiled `nlm login --force`.

Run after implementation:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python -m pytest P://packages/yt-is/tests/test_nlm_batch.py -q -k "zero_growth_add_failure or notebook_reset or source_id or auth_context"
python -m pytest P://packages/yt-is/tests/test_nlm_batch.py -q
python -m py_compile P://packages/yt-is/csf/nlm_batch.py P://packages/yt-is/tests/test_nlm_batch.py P://packages/yt-is/bin/csf-source
```

Then run exactly one full source-map benchmark under a new output root:

```powershell
$env:PYTHONPATH = 'P://packages/yt-is'
python P://packages/yt-is/bin/csf-nlm-worker-auth sync
python P://packages/yt-is/bin/csf-sharded-lane-series `
  --lane-config P://packages/yt-is/.logs/sharded_lane_series/pro_free_lanes.json `
  --output-root P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v6 `
  --cohort-json P://packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v6/cohort.json `
  --limit 400 `
  --batch-size 200 `
  --reusable-pipeline-mode serial
```

Decision:

- If `pro_free_source_map_v6` removes or materially reduces Free lane `source_add_failed` and beats `3930.79`, keep the fallback and compare against the current best `5572.04`.
- If it improves Free failures but still does not beat `5572.04`, record it as an improvement but not a new best.
- If it increases wall time enough to reduce VPH, record it as negative and keep the fallback only if it materially improves correctness.
- If unprofiled auth appears again, mark the run invalid and fix auth before trusting throughput.
- Do not proceed to cleanup optimization, stagger tests, worker-count changes, or content-fetch retry tuning until this phase is recorded in `test-registry.md`.

Outcome:

- `pro_free_source_map_v6` completed cleanly with profile-pinned auth but remained negative:
  - combined hot-path VPH: `1837.24`
  - hot-path success/failure: `299/501`
  - processed: `800`
  - wall elapsed: `585.88s`
- Pro lane hot-path VPH: `616.2`
- Free lane hot-path VPH: `1394.33`
- The notebook-reset fallback reduced the zero-growth add failure class but did not recover enough throughput to beat the current best.

## Phase 8: Source-Readiness And Extract Attribution Probe

Purpose: determine which part of the hot path is actually consuming the remaining wall time before any routing change is made. The first `NOT_FOUND` in a batch still justifies a source-list validation because it can confirm the source row and separate stale-ID cases from missing-item cases. Repeated validation during a `NOT_FOUND` storm is now a throughput cost, so this probe must be bounded in sustained runs. The current evidence does not justify a captioned-video switch to `yt-dlp` first. Caption-rich items stay on NotebookLM unless a same-shape A/B proves otherwise.

Current evidence:

- `idle_wait_validation_run03` shows auth accounting is now sane, but idle cost remains high.
- `repro_v1_pro_only_4w_run02` still shows large Pro idle wait and extract cost on the same 400-item shape.
- `sweep_phase3_2lane_3w_run07` and `run08` show the age cliff / `NOT_FOUND` pressure is real, but it does not by itself identify whether the bottleneck is materialization wait, content fetch latency, or notebook rotation cadence.
- `highest_vph_attribution_probe_run02` collapsed to `977.45` combined hot-path VPH; batch 1 carried heavy `NOT_FOUND` pressure and the source-list probe cost was large, so repeated validation is no longer a free diagnostic in storm conditions.
- `highest_vph_not_found_probe_cap_sequence_run01` improved the same 400-item sequence shape to `1204.97` combined hot-path VPH with the bounded probe cap in place; the first `NOT_FOUND` probe remained diagnostic, repeated validation no longer dominated the storm cost, and the run still stayed well below the age-capped control.
- `fresh_state_3plus3_extract_attr_run01` collapsed to `610.12` combined hot-path VPH; fetch logs showed very strong source-age dependence, with near-clean results below about `220s` and near-total failure above `500s`, so the new fetch-side `source_age_cliff` fast-fail path is now in place.
- `fresh_state_3plus3_extract_attr_run02` improved the failure shape but not the outcome: combined hot-path VPH `786.98`, `750/800` processed, Pro `388/12`, Free `293/57`, and the Free lane still stopped early with source-age pressure clustered right around the cliff.
- `pro_free_source_map_v1` remains the sustained captioned control until a newer like-for-like run beats it.

Implementation target:

- Primary file: `P:/packages//yt-is//csf//nlm_batch.py`
- Primary tests: `P:/packages//yt-is//tests//test_nlm_batch.py`
- Primary docs: this plan, `test-registry.md`, and `observability-contract-checklist.md`

Required probe shape:

- Keep the current captioned NotebookLM-first control path.
- Add or surface per-stage timing that separates at least:
  - materialization wait
  - NotebookLM content fetch / retry work
  - source-list probe time
  - yt-dlp probe time
  - any extract-side sleep or backoff time
- Preserve the existing `combined.hot_path_videos_per_hour` contract.
- Do not change lane width, batch size, or auth TTL in the same probe.

Decision gates:

- On the first `NOT_FOUND`, run one source-list validation and keep the matched source-row metadata so the run can still separate stale-ID from missing-item cases.
- If `NOT_FOUND` keeps repeating in the same lane or subbatch, cap source-list validation at one check per 10 additional `NOT_FOUND` events, or one check per 60s, whichever is less frequent.
- If source-list validation consumes more than 5% of lane wall time in any 5-minute window, or if three sampled checks in a row add no new source-row mismatch, disable the probe until the next clean batch boundary.
- If materialization wait dominates, test a shorter readiness poll interval next.
- If content fetch dominates, profile the NotebookLM CLI boundary and subprocess overhead next.
- If source age climbs before extract completes, test rotation cadence or smaller subbatches next.
- If none of those explain the idle time, re-check worker-side auth and browser-root propagation before changing routing.

## Phase 9: Bounded Source-List Validation During NOT_FOUND Storms

Purpose: keep the first-miss diagnostic value from Phase 8 without letting repeated source-list validation distort sustained-throughput runs.

Current evidence:

- `highest_vph_attribution_probe_run02` showed the source-list probe is useful on the first `NOT_FOUND`, but repeated validation becomes a throughput drag under storm conditions.
- `highest_vph_not_found_probe_cap_sequence_run01` confirmed the bounded probe cap helps, but the remaining gap is still source-readiness / age behavior, not more probe volume.
- `small_subbatch_source_readiness_run01` improved the same 2-lane shape further to `1998.83` combined hot-path VPH with `batch-size 100`, with Pro `source_ready_age_s_max=225.028s` and Free `source_ready_age_s_max=195.392s`.
- `small_subbatch_source_readiness_run04` regressed to `980.81` combined hot-path VPH with `710/90/800`; Pro landed at `509.01` VPH with `source_ready_age_s_max=330.066s`, and Free landed at `660.31` VPH with `source_ready_age_s_max=284.495s`, so the batch-size `100` follow-up did not sustain the earlier full-load subbatch result.
- `small_subbatch_source_readiness_run02` regressed to `1549.75` combined hot-path VPH with `batch-size 150`, with Pro `source_ready_age_s_max=416.751s` and Free `source_ready_age_s_max=438.977s`.
- `pro_free_source_map_v1` remains the historical best sustained control, so any bounded probe must be measured against that baseline, not against a single diagnostic sample.

Implementation target:

- Primary file: `P:/packages//yt-is//csf//nlm_batch.py`
- Primary tests: `P:/packages//yt-is//tests//test_nlm_batch.py`
- Preserve the first `NOT_FOUND` source-list validation from Phase 8.
- Apply the Phase 8 cap/sample/disable gate during sustained runs so repeated `NOT_FOUND` storms do not keep paying full probe cost.
- Do not change lane width, batch size, or auth TTL in the same bounded-probe change.

Do not:

- Do not switch captioned items to a `yt-dlp`-first default without a same-shape controlled A/B.
- Do not retry the old broad route-mix experiments until this attribution probe has a clean result.
- Do not treat `yt-dlp` fast-path performance on no-caption or failure-recovery cases as evidence that it should replace NotebookLM for captioned throughput.

## Phase 10: Smaller Subbatch Geometry For Source-Readiness

Purpose: test whether the remaining throughput loss comes from too much source age accumulating between notebook boundaries rather than from the bounded `NOT_FOUND` probe itself.

Current evidence:

- `highest_vph_not_found_probe_cap_sequence_run01` improved the 400-item sequence shape to `1204.97` combined hot-path VPH with the bounded probe cap in place.
- The same run still showed the Free lane crossing `source_ready_age_s_max=1026.949s` in batch 2, with `command_failed=40`, so the age cliff is still not under control.
- The Pro lane recovered much better than the uncapped attribution run, which means the probe cap is a keep, not a new variable to reopen.
- `small_subbatch_source_readiness_run02` and `small_subbatch_source_readiness_run03` both finished as partial lanes (`processed_count_total=700`), so they are not clean full-load negatives even though they were recorded as `status=ok`.
- `small_subbatch_source_readiness_run01` remains the only clean full-load subbatch result in this branch.

Implementation target:

- Primary file: `P:/packages//yt-is//docs//operations//hot-path-throughput-next-test-plan.md`
- Primary benchmark runner: `P:/packages//yt-is//bin//csf-sharded-lane-sequence`
- Preserve `YTIS_NLM_NOT_FOUND_SOURCE_LIST_PROBE_CAP=1`.
- Do not change auth TTL.
- Do not widen `_SOURCE_AGE_CLIFF_S`.

Experiment:

1. Re-run the same 2-lane, 4-worker, captioned `limit 400` shape with `batch-size 100` and `reusable-pipeline-mode serial`.
2. Keep the same lane config, source URL, and bounded `NOT_FOUND` probe cap.
3. Compare the result against `highest_vph_not_found_probe_cap_sequence_run01` and the age-capped control `sweep_phase3_2lane_3w_agecap_200_run02`.

```powershell
$env:PYTHONPATH = 'P:/packages//yt-is'
python P:/packages//yt-is//bin//csf-sharded-lane-sequence `
  --lane-config P:/packages//yt-is//.logs//sharded_lane_series//pro_free_lanes.json `
  --run-root P:/packages//yt-is//.logs//sharded_lane_series//small_subbatch_source_readiness_run01 `
  --smoke-limit 400 `
  --smoke-batch-size 100 `
  --soak-limit 400 `
  --soak-batch-size 100 `
  --reusable-pipeline-mode serial
```

Decision gates:

- If the lane summary can finish `ok` while `processed_count_total` is below the configured `limit`, fix the validity gate first and stop using those runs as clean negatives.
- If the current summary gate is fixed, rerun a clean `3+3` pressure comparison before pursuing more subbatch tuning.
- If the clean `3+3` rerun still trails `highest_vph_agecap_400_run01`, move to earlier notebook rotation or an age-trigger refinement rather than expanding subbatch size further.
- `small_subbatch_source_readiness_run01` remains the only clean full-load subbatch result in this branch, so do not treat `run02` or `run03` as evidence against the subbatch effect until the validity gate is fixed.
- If `source_ready_age_s_max` still crosses the cliff or `command_failed` rises materially, the workload still needs earlier notebook rotation rather than more diagnostic probing.
- If `worker_idle_wait_s_total` remains high while `content_fetch_command_elapsed_s_total` does not fall, test readiness polling interval and NotebookLM CLI boundary overhead next.
- If `source_list_probe_elapsed_s_total` grows back above the current capped sequence run, stop and tighten the probe cap further before changing anything else.

Do not:

- Do not widen the source-age cliff.
- Do not change auth TTL in the same run.
- Do not switch captioned items to `yt-dlp` first without a same-shape A/B.

## Phase 11: Source-Age-Controlled Active Windows

Purpose: test whether clearing NotebookLM sources inside a reusable worker batch can keep source age under the cliff without giving up too much throughput. The mechanism remains useful as an opt-in diagnostic, but it is not the default hot-path strategy.

Implementation completed:

- `P:/packages//yt-is//csf//nlm_config.py` exposes `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE`, default `0` / disabled.
- `P:/packages//yt-is//csf//nlm_batch.py` uses active windows in `NLMReusableIngestor.process_batch` only when the configured window is greater than `0` and smaller than the worker batch.
- Each active window is `add -> extract -> reset_sources`; per-window logs are emitted as `nlm_batch_reusable_active_window_started` and `nlm_batch_reusable_active_window_completed`.
- Batch summaries include `active_window_enabled`, `active_window_size`, and `active_window_count`.
- Window extract metrics are aggregated back into the normal reusable-process summary fields, including `content_fetch_status_counts`, source-age totals/max/avg, attempts, and command timing.

Completed evidence:

- `fresh_state_3plus3_active_window_run01` is invalid for this phase because it used the stale `4+4` lane config despite the `3+3` run-root name.
- `fresh_state_3plus3_active_window_run02` used the correct `3+3` lane config and completed cleanly: combined hot-path VPH `1608.12`, `791/9` hot-path success/failure, `800` processed, `worker_shape_signature=3+3`, Pro `source_ready_age_s_max=219.474s`, Free `source_ready_age_s_max=190.255s`.
- `fresh_state_3plus3_active_window_run03` repeated the same shape with `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE=50`: combined hot-path VPH `1484.65`, `706/94` hot-path success/failure, `800` processed, `worker_shape_signature=3+3`.

Conclusion:

- Active windows can control source age, but they are not a ceiling path. Window size `25` stayed clean enough but far below the age-capped `3+3` control; window size `50` worsened failures and VPH.
- Keep active windows opt-in only. Do not run more active-window widening tests unless a later code change materially reduces the `add -> extract -> reset_sources` overhead.

Verification completed:

```powershell
python -m pytest -q tests/test_sharded_lane_stage_reducer.py tests/test_worker_count_sweep.py tests/test_run_evidence_check.py tests/test_sharded_lane_sequence.py tests/test_sharded_lane_series.py tests/test_nlm_config.py tests/test_nlm_batch.py
```

Result after changing active windows to opt-in: `170 passed`.

## Phase 12: Next Testing Handoff - Add / Materialization / Content-Fetch Attribution

Purpose: give the next LLM one diagnostic run that explains the remaining latency/failure split without changing code or running another geometry sweep.

Run contract:

- Do not change code before the run.
- Do not set `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE`; active windows should remain disabled by default.
- Use the true `3+3` lane config: `P:/packages//yt-is//.logs//sharded_lane_series//tmp_pro_free_3w.json`.
- Use a fresh run root: `P:/packages//yt-is//.logs//sharded_lane_series//fresh_state_3plus3_add_materialization_attr_run01`. If it already exists, use `run02`.
- Stop if auth fails, if `worker_shape_signature` is not `3+3`, or if fewer than `800` items are processed. Mark partial results as partial, not a ceiling.
- Do not delete notebooks outside the industrial worker state.

Preflight:

```powershell
cd P:/packages//yt-is
$env:PYTHONPATH = 'P:/packages//yt-is'
Remove-Item Env:\\YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE -ErrorAction SilentlyContinue
$env:YTIS_NLM_NOT_FOUND_SOURCE_LIST_PROBE_CAP = '1'
python -m pytest -q tests/test_nlm_config.py tests/test_nlm_batch.py tests/test_sharded_lane_sequence.py tests/test_run_evidence_check.py
```

Benchmark command:

```powershell
python P:/packages//yt-is//bin//csf-sharded-lane-sequence `
  --lane-config P:/packages//yt-is//.logs//sharded_lane_series//tmp_pro_free_3w.json `
  --run-root P:/packages//yt-is//.logs//sharded_lane_series//fresh_state_3plus3_add_materialization_attr_run01 `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --reusable-pipeline-mode serial
```

Report back with:

- Test result and exact benchmark command.
- `combined.hot_path_videos_per_hour`, `combined.hot_path_success_count_total`, `combined.fail_count_total`, and `combined.processed_count_total`.
- `worker_shape_signature` and `throughput_valid`.
- Per-lane `add_elapsed_s_total`, `setup_elapsed_s_total`, `extract_elapsed_s_total`, `worker_idle_wait_s_total`, `source_ready_age_s_max`, and `content_fetch_status`.

Completed control:

- `status=ok`
- `throughput_valid=true`
- `worker_shape_signature=3+3`
- `combined.hot_path_videos_per_hour=1174.91`
- `combined.hot_path_success_count_total=632`
- `combined.fail_count_total=168`
- `combined.processed_count_total=800`
- Pro lane: `285/115`, `setup_elapsed_s_total=1461.099`, `add_elapsed_s_total=1332.381`, `extract_elapsed_s_total=2325.48`, `worker_idle_wait_s_total=341.772`, `source_ready_age_s_max=469.374`, `content_fetch_status_counts_total={"ready":285,"source_age_cliff":114,"command_failed":1}`
- Free lane: `347/53`, `setup_elapsed_s_total=1480.073`, `add_elapsed_s_total=1380.487`, `extract_elapsed_s_total=2377.502`, `worker_idle_wait_s_total=312.632`, `source_ready_age_s_max=481.475`, `content_fetch_status_counts_total={"ready":347,"source_age_cliff":52,"command_failed":1}`
- Conclusion: normal serial lands between the two extract-window controls and still shows source-age-cliff pressure, so the extract-window mode stays diagnostic only.
- From worker logs, summarize `nlm_batch_source_materialization_wait_succeeded`, `nlm_batch_subbatch_age_guard_checked`, and source-content fetch events by lane/account/worker/source age/status.

Decision gates:

- If add/materialization wait dominates before source age rises, investigate NotebookLM add/materialization latency.
- If content fetch latency or failures cluster by source age, keep the problem framed as add-to-extract cadence and avoid geometry changes.
- If failures cluster by one lane/account/worker, investigate that profile/auth/browser root before any throughput tuning.
- If latency/failure is uniform across lanes/workers/source ages, treat it as NotebookLM backend variance and rerun the same command once with a fresh root before changing code.

Do not do next:

- Do not run more active-window sizes.
- Do not widen the source-age cliff.
- Do not change auth TTL.
- Do not switch captioned items to `yt-dlp` without a same-shape A/B.
- Do not run a new worker-count geometry sweep until this attribution run identifies a code-path reason to do so.

Implementation note:

- The source-age-aware cadence is now implemented behind `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED=1`.
- The default soft/hard thresholds are `160s` and `190s`, with a minimum cadence window size of `5`.
- Leave the active-window and extract-window knobs unset when using the new cadence; the next diagnostic should isolate only the age-aware scheduler, not the older window modes.
- The cadence selector now also projects the next window using the previous window elapsed time, so shrinkage is driven by projected source age rather than only the current notebook age. The reusable carryover survives source clears within the same notebook; only notebook replacement or explicit recovery resets it.
- The next live rerun candidate is the dedicated cadence universe in `P:/packages/yt-is/.logs/sharded_lane_series/hotel_wifi_3plus3_shared_retry_source_age_cadence_run29_lanes.json`, which carries the cadence env overrides in lane config instead of inheriting them from shell state. Treat it as a separate comparison universe from `hotel_wifi_3plus3_shared_retry_canary_run28_current`.

Completed run:

- `fresh_state_3plus3_add_materialization_attr_run01` completed cleanly with `status=ok`, `throughput_valid=true`, and `worker_shape_signature=3+3`.
- Combined hot-path VPH was `1653.75` with `782/18` hot-path success/failure and `800` processed.
- Pro lane: `385/15`, `source_ready_age_s_max=199.361s`, `worker_idle_wait_s_total=212.004s`, `content_fetch_status_counts_total={"ready":385,"source_age_cliff":14,"command_failed":1}`.
- Free lane: `397/3`, `source_ready_age_s_max=233.89s`, `worker_idle_wait_s_total=108.99s`, `content_fetch_status_counts_total={"ready":397,"source_age_cliff":1,"command_failed":2}`.
- The run is clean but still not a ceiling. It is materially better than the active-window runs, but still far below the better age-capped and historical `3+3` controls, so the next step should be log-level attribution rather than another geometry or active-window sweep.

Artifact review:

- Materialization wait was not the bottleneck. The completed batch logs showed materialization waits in the roughly `3s` to `7s` range, and every subbatch age-guard check before add was `skipped_no_epoch`.
- The source-age failures came from content-fetch drain inside the worker chunk. Pro batch 1 had `14` `source_age_cliff` events with logged source ages from roughly `200.479s` to `445.884s`; the same batch had a max content-fetch command time near `398.944s`.
- The top-level Pro `source_ready_age_s_max=199.361s` was a pre-fix aggregate blind spot: `source_age_cliff` fast-fail rows were counted in `content_fetch_status_counts_total` but their ages were not included in `source_ready_age_s_total/max/avg`.
- Observability correction: `source_age_cliff` fast-fail rows now contribute to the normal source-age aggregate metrics. Future summaries should not show a below-cliff `source_ready_age_s_max` while also reporting source-age-cliff failures.

Next after this correction:

- Do not launch another geometry sweep. If another run is needed, rerun this exact Phase 12 shape once under a fresh root to regenerate summaries with corrected source-age aggregates.
- Use the corrected aggregates plus per-fetch command timing to decide whether to change add-to-extract cadence; do not tune age cliff, auth TTL, or active-window sizes from the pre-fix summary fields.

Rerun result:

- `fresh_state_3plus3_add_materialization_attr_run02` reproduced the same shape with corrected source-age aggregation, but the smoke run returned `status=partial` because the Free lane processed only `200/400` items. The run is useful as validation of the observability fix, not as a new throughput ceiling.
- Smoke Pro lane now reports `source_ready_age_s_max=489.649s` with `content_fetch_status_counts_total={"ready":347,"source_age_cliff":52,"command_failed":1}`.
- Smoke Free lane now reports `source_ready_age_s_max=302.378s` with `content_fetch_status_counts_total={"source_age_cliff":102,"ready":98}`.
- That is the expected post-fix behavior: the cliff-fast-fail ages are now visible in the summary, so the old below-cliff max no longer hides them.

## Phase 13: Reusable Extract Windows Without Per-Window Reset

Purpose: test whether shorter add/extract windows can keep source age under the cliff without paying the notebook reset tax after every window. This is the next ceiling candidate after the active-window diagnostic proved that reset-per-window lowers age but also drags throughput down.

Implementation completed:

- `P:/packages/yt-is/csf/nlm_config.py` exposes `YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE`, default `0` / disabled.
- `P:/packages/yt-is/csf/nlm_batch.py` uses extract windows in `NLMReusableIngestor.process_batch` when the configured window is greater than `0` and smaller than the worker batch.
- Each extract window is `add -> extract` without `reset_sources` after the window; cleanup/reset still happens at the normal batch boundary.
- Active windows remain available as the diagnostic reset-per-window mode via `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE`.
- Batch summaries include `extract_window_enabled`, `extract_window_size`, and `extract_window_count`.
- Window metrics are aggregated back into the normal reusable-process summary fields, including `content_fetch_status_counts`, source-age totals/max/avg, attempts, and command timing.

Run contract:

- Do not change code before the run.
- Do not set `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE`; active windows should remain disabled by default.
- Set `YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE=25`.
- Use the true `3+3` lane config: `P:/packages/yt-is/.logs/sharded_lane_series/tmp_pro_free_3w.json`.
- Use a fresh run root: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_window_run01`. If it already exists, use `run02`.
- Stop if auth fails, if `worker_shape_signature` is not `3+3`, or if fewer than `800` items are processed. Mark partial results as partial, not a ceiling.
- Do not delete notebooks outside the industrial worker state.

Preflight:

```powershell
cd P:/packages/yt-is
$env:PYTHONPATH = 'P:/packages/yt-is'
Remove-Item Env:\YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE -ErrorAction SilentlyContinue
$env:YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE = '25'
$env:YTIS_NLM_NOT_FOUND_SOURCE_LIST_PROBE_CAP = '1'
python -m pytest -q tests/test_nlm_config.py tests/test_nlm_batch.py tests/test_sharded_lane_sequence.py tests/test_run_evidence_check.py
```

Benchmark command:

```powershell
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/tmp_pro_free_3w.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_window_run01 `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --reusable-pipeline-mode serial
```

Report back with:

- Test result and exact benchmark command.
- `combined.hot_path_videos_per_hour`, `combined.hot_path_success_count_total`, `combined.fail_count_total`, and `combined.processed_count_total`.
- `worker_shape_signature` and `throughput_valid`.
- Per-lane `add_elapsed_s_total`, `setup_elapsed_s_total`, `extract_elapsed_s_total`, `worker_idle_wait_s_total`, `source_ready_age_s_max`, and `content_fetch_status`.
- From worker logs, summarize `nlm_batch_source_materialization_wait_succeeded`, `nlm_batch_subbatch_age_guard_checked`, and source-content fetch events by lane/account/worker/source age/status.

Decision gates:

- If the run completes cleanly and keeps source age under the cliff with lower wall time than the active-window run, keep extract windows as the default next candidate.
- If the run still spends most of its time in content fetch or source-age cliff failures, keep the problem framed as add-to-extract cadence and avoid geometry changes.
- If failures cluster by one lane/account/worker, investigate that profile/auth/browser root before any throughput tuning.
- If latency/failure is uniform across lanes/workers/source ages, treat it as NotebookLM backend variance and rerun the same command once with a fresh root before changing code.

Do not do next:

- Do not run more active-window sizes.
- Do not widen the source-age cliff.
- Do not change auth TTL.
- Do not switch captioned items to `yt-dlp` without a same-shape A/B.
- Do not run a new worker-count geometry sweep until this attribution run identifies a code-path reason to do so.

Completed run:

- `fresh_state_3plus3_extract_window_run01` completed cleanly with `status=ok`, `throughput_valid=true`, and `worker_shape_signature=3+3`.
- Combined hot-path VPH was `1260.84` with `693/107` hot-path success/failure and `800` processed.
- Pro lane: `320/80`, `setup_elapsed_s_total=92.591`, `add_elapsed_s_total=1825.089`, `worker_idle_wait_s_total=0.0`, `source_ready_age_s_max=277.452`, `content_fetch_status_counts_total={"ready":320,"source_age_cliff":2,"command_failed":5}`.
- Free lane: `373/27`, `setup_elapsed_s_total=78.501`, `add_elapsed_s_total=1800.1`, `worker_idle_wait_s_total=0.0`, `source_ready_age_s_max=206.467`, `content_fetch_status_counts_total={"ready":373,"source_age_cliff":1,"command_failed":1}`.
- `fresh_state_3plus3_extract_window_run02` completed cleanly with `status=ok`, `throughput_valid=true`, and `worker_shape_signature=3+3`.
- Combined hot-path VPH was `1120.27` with `756/44` hot-path success/failure and `800` processed.
- Pro lane: `393/7`, `setup_elapsed_s_total=93.154`, `add_elapsed_s_total=1949.02`, `extract_elapsed_s_total=4755.724`, `worker_idle_wait_s_total=307.798`, `source_ready_age_s_max=323.436`, `content_fetch_status_counts_total={"ready":393,"source_age_cliff":7,"command_failed":4}`.
- Free lane: `363/37`, `setup_elapsed_s_total=82.165`, `add_elapsed_s_total=1925.905`, `extract_elapsed_s_total=4629.668`, `worker_idle_wait_s_total=131.068`, `source_ready_age_s_max=293.572`, `content_fetch_status_counts_total={"ready":363,"source_age_cliff":10,"command_failed":2}`.
- The summary schema now surfaces `extract_elapsed_s_total` in the lane aggregates, so the next attribution pass can separate add from extract instead of reading add as a catch-all bucket.

Conclusion:

- The no-reset extract-window mode is not a ceiling path on its own. Both reruns kept the notebook alive, but throughput landed far below the active-window diagnostic and below the better age-capped control.
- The key remaining signal is still add-to-extract cadence and content-fetch failure behavior, not notebook rotation geometry.
- Treat extract windows as a useful option for targeted diagnosis, not the default throughput winner.

## Phase 14: True `3+3` Serial Control With Windows Disabled

Purpose: establish the corrected serial baseline for the same true `3+3` shape now that add and extract are separable in the summary schema. This is the missing A/B against the extract-window runs.

Run contract:

- Do not change code before the run.
- Do not set `YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE`.
- Do not set `YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE`.
- Use the true `3+3` lane config: `P:/packages/yt-is/.logs/sharded_lane_series/tmp_pro_free_3w.json`.
- Use a fresh run root: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run01`. If it already exists, use `run02`.
- Stop if auth fails, if `worker_shape_signature` is not `3+3`, or if fewer than `800` items are processed. Mark partial results as partial, not a ceiling.
- Do not delete notebooks outside the industrial worker state.

Preflight:

```powershell
cd P:/packages/yt-is
$env:PYTHONPATH = 'P:/packages/yt-is'
Remove-Item Env:\YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE -ErrorAction SilentlyContinue
Remove-Item Env:\YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE -ErrorAction SilentlyContinue
$env:YTIS_NLM_NOT_FOUND_SOURCE_LIST_PROBE_CAP = '1'
python -m pytest -q tests/test_breadth_series.py tests/test_nlm_config.py tests/test_nlm_batch.py tests/test_sharded_lane_sequence.py tests/test_run_evidence_check.py
```

Benchmark command:

```powershell
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/tmp_pro_free_3w.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run01 `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --reusable-pipeline-mode serial
```

Report back with:

- Test result and exact benchmark command.
- `combined.hot_path_videos_per_hour`, `combined.hot_path_success_count_total`, `combined.fail_count_total`, and `combined.processed_count_total`.
- `worker_shape_signature` and `throughput_valid`.
- Per-lane `add_elapsed_s_total`, `setup_elapsed_s_total`, `extract_elapsed_s_total`, `worker_idle_wait_s_total`, `source_ready_age_s_max`, and `content_fetch_status`.
- From worker logs, summarize `nlm_batch_source_materialization_wait_succeeded`, `nlm_batch_subbatch_age_guard_checked`, and source-content fetch events by lane/account/worker/source age/status.

Decision gates:

- If normal serial has materially lower `extract_elapsed_s_total` than the extract-window run, extract windows remain diagnostic only.
- If normal serial still shows large `extract_elapsed_s_total` and the same age/failure pattern, the next move is source-age-aware extraction cadence.
- If failures cluster by one lane/account/worker, investigate that profile/auth/browser root before any throughput tuning.
- If latency/failure is uniform across lanes/workers/source ages, treat it as NotebookLM backend variance and rerun the same command once with a fresh root before changing code.

Completed run:

- `fresh_state_3plus3_source_age_cadence_run01` completed cleanly with `status=ok`, `throughput_valid=true`, and `worker_shape_signature=3+3`.
- Combined hot-path VPH was `1238.70` with `670` hot-path successes, `130` failures, and `800` processed.
- Pro lane: `317/83`, `setup_elapsed_s_total=77.218`, `add_elapsed_s_total=1075.465`, `extract_elapsed_s_total=3276.988`, `worker_idle_wait_s_total=453.228`, `source_ready_age_s_max=338.197`, and `content_fetch_status_counts_total={"ready":317,"source_age_cliff":83,"command_failed":13}`.
- Free lane: `353/47`, `setup_elapsed_s_total=71.613`, `add_elapsed_s_total=1103.617`, `extract_elapsed_s_total=3330.867`, `worker_idle_wait_s_total=681.246`, `source_ready_age_s_max=367.341`, and `content_fetch_status_counts_total={"ready":353,"source_age_cliff":47,"command_failed":15}`.
- This beat the true serial control (`1174.91` VPH) and reduced failures, but it still sits well below the age-capped control, so the cadence knob is an improvement, not the ceiling.
- `fresh_state_3plus3_source_age_cadence_run03` reran the same `3+3` cadence after restoring the hard-threshold quarter-window behavior and completed cleanly at `1688.15` combined hot-path VPH with `759` hot-path successes, `41` failures, and `800` processed. Pro landed at `377/22` with `source_ready_age_s_max=293.889`, `add_elapsed_s_total=1510.867`, `extract_elapsed_s_total=1288.014`, and `worker_idle_wait_s_total=102.734`; Free landed at `382/18` with `source_ready_age_s_max=261.959`, `add_elapsed_s_total=1246.975`, `extract_elapsed_s_total=1082.693`, and `worker_idle_wait_s_total=0.0`. That is materially better than the prior source-age cadence controls, but it still trails the age-capped `3084.08` result, so cadence is stronger now but still not the ceiling.
- `source_age_cadence_run04` turned into a falsification run: combined hot-path VPH `1736.63` on `664/86/750`, `status=partial`, and `throughput_valid=false`. Pro stayed at `312/38` with `source_ready_age_s_max=228.659`, while Free stopped at `352/48` with `source_ready_age_s_max=441.116`; the cadence env values did reach the worker (`soft=120`, `hard=150`), but the code only uses them to size windows at boundaries, so a fresh notebook can still age past the threshold inside a long window. That makes this branch a heuristic limit, not an env propagation failure.
- `fresh_state_3plus3_source_age_cadence_run04` repeated the same quarter-window cadence but only finished smoke as a partial run at `1184.99` combined hot-path VPH with `596/4/600`; Pro stayed complete at `397/3/400`, while Free stopped at `199/1/200` with `source_ready_age_s_max=234.278`. The branch still helps when it lands cleanly, but the repeat was not a usable full-load control, so do not treat the cadence branch as stable enough to be the ceiling yet.
- `fresh_state_3plus3_source_age_projected_rotation_run01` completed cleanly with `status=ok`, `throughput_valid=true`, `779/21/800`, and `1585.31` combined hot-path VPH. Pro landed at `388/12` with `source_ready_age_s_max=259.493` and `885.03` VPH; Free landed at `391/9` with `source_ready_age_s_max=279.678` and `999.0` VPH. The projected-age guard is valid, but it did not beat `fresh_state_3plus3_source_age_cadence_run03`, so notebook-rotation / age-guard refinement is not the next ceiling branch.
- `fresh_state_3plus3_source_age_cadence_run05` preserved the notebook-age anchor across source clears and reran the same `3+3` cadence to `1829.83` combined hot-path VPH with `789/11/800`. Pro landed at `397/3` with `source_ready_age_s_max=266.953`, `add_elapsed_s_total=626.903`, and `extract_elapsed_s_total=1313.285`; Free landed at `392/8` with `source_ready_age_s_max=228.764`, `add_elapsed_s_total=544.615`, and `extract_elapsed_s_total=2012.724`. That is the strongest source-age-aware cadence result so far, but it still trails the age-capped control, so the branch is improved but not ceiling-setting.
- `fresh_state_3plus3_source_age_cadence_hygiene_run04` repeated the same `3+3` cadence with the cleanup/prewarm contract in place and completed cleanly at `1155.96` hot-path VPH with `667` hot-path successes, `133` failures, and `800` processed. Pro landed at `331/69` and Free at `336/64`; this is valid confirmation evidence, but it did not beat `run01`, so cadence is confirmed but not ceiling-setting.
- `fresh_state_3plus3_source_age_cadence_hygiene_run05` used the minimum hard-threshold window size and finished smoke only with `status=partial`, `throughput_valid=false`, `613/137/750` combined, and a free-lane shortfall at `350/400`. The more aggressive window collapse did not produce a valid control, so do not treat it as throughput evidence.
- `fresh_state_3plus3_worker_balance_ab_pro0213_run05` is the new best worker-balance follow-up. It finished cleanly at `1533.33` combined hot-path VPH with `789` hot-path successes, `11` failures, and `800` processed. Pro landed at `873.14` VPH and Free at `1332.79` VPH; the profile-order rebalance improved the worker-balance branch over the prior `1377.29` reference, but it still remains below the stronger age-capped control.
- `fresh_state_3plus3_worker_balance_ab_pro0213_run06` is the completed same-shape confirmation repeat, and it regressed instead of confirming the median. It finished cleanly at `1403.64` combined hot-path VPH with `770` hot-path successes, `30` failures, and `800` processed. Pro regressed to `773.72` VPH with `388/12`, `worker_idle_wait_s_total=394.357`, and `source_ready_age_s_max=253.873`; Free regressed to `974.44` VPH with `382/18`, `worker_idle_wait_s_total=264.295`, and `source_ready_age_s_max=270.492`. Close the worker-balance branch for now.
- `fresh_state_3plus3_worker_balance_ab_nlm069_run02` remains useful negative evidence for the `nlm069` fallback path. It finished cleanly at `1319.33` combined hot-path VPH with `357` hot-path successes, `43` failures, and `400` processed in soak. Pro landed at `719.87` VPH and Free at `840.52` VPH, so the `nlm069` fallback path is reliability-only, not throughput-improving.
- `fresh_state_3plus3_extract_schema_control_run04` only completed smoke and stayed partial: combined hot-path VPH `494.24` on `460/290/750`; Pro `202/148` with `processed_count_total=350` and `source_ready_age_s_max=438.695`; Free `258/142` with `processed_count_total=400` and `source_ready_age_s_max=585.414`. Soak never started, so this run is not a usable control baseline or ceiling candidate.
- Offline attribution over the new `nlm_source_content_command_completed` events in `fresh_state_3plus3_extract_schema_control_run04` shows mixed pressure rather than a single age cliff: `740` commands total; pro `worker-03` failed `76/147` (`51.7%`), free `worker-03` failed `42/109` (`38.5%`), and `last_auth_refresh_age_s` in the `5-19s` bucket had a `41.1%` failure rate versus `26.8%` for `20-59s`. Source age still matters (`60-119s` failed `40.1%`, `180+s` failed `42.6%`), but the slow and failed commands are not age-pure enough to justify another cadence tweak first.
- `fresh_state_3plus3_extract_schema_control_run05` is the fresh same-shape control rerun with the live worker logs. The updated stage reducer now reads `workers_*/logs/*.jsonl`, understands the nested `action`/`data` shape, and surfaces command attribution automatically: Pro `worker-01/02/03` split `83/134/146` commands with `31/19/19` failures; Free `worker-01/02/03` split `207/102/87` commands with `37/41/8` failures. The reducer now also compares worker-profile spread against auth-refresh spread and the run05 split says worker balance is the stronger signal, with auth-refresh age secondary.
- Offline comparison against `sweep_phase3_2lane_3w_run01` still leaves the historical control without `nlm_source_content_command_completed` attribution, so it cannot be used to split worker/profile/auth effects the way run05 can. Keep `run05` as the live attribution baseline and treat `run01` as the pre-attribution ceiling control only.
- `fresh_state_3plus3_extract_schema_control_run07_current` is the latest live same-shape baseline. It completed cleanly at `3291.38` combined hot-path VPH with `744/56/800` and `worker_shape_signature=3+3`; Pro landed at `398/2` with `content_fetch_command_elapsed_s_total=2014.284`, `setup_elapsed_s_total=734.149`, `add_elapsed_s_total=699.144`, `extract_elapsed_s_total=427.838`, `worker_idle_wait_s_total=43.476`, and `source_ready_age_s_max=98.644`; Free landed at `346/54` with `content_fetch_command_elapsed_s_total=3027.095`, `setup_elapsed_s_total=621.562`, `add_elapsed_s_total=590.336`, `extract_elapsed_s_total=689.237`, `worker_idle_wait_s_total=43.264`, and `source_ready_age_s_max=151.637`. The reducer still shows worker-profile spread slightly ahead of auth-refresh spread, so the baseline is live and clean but still below the historical ceiling.
- `fresh_state_3plus3_extract_schema_control_run12_current` is the repaired same-shape rerun after worker-notebook cleanup hardening. It completed cleanly at `3174.70` combined hot-path VPH with `791/9/800` and `worker_shape_signature=3+3`; Pro landed at `395/5` with `content_fetch_command_elapsed_s_total=3507.36`, `setup_elapsed_s_total=623.529`, `add_elapsed_s_total=423.147`, `extract_elapsed_s_total=1160.564`, `worker_idle_wait_s_total=257.445`, and `source_ready_age_s_max=226.69`; Free landed at `396/4` with `content_fetch_command_elapsed_s_total=3194.53`, `setup_elapsed_s_total=603.9`, `add_elapsed_s_total=571.3`, `worker_idle_wait_s_total=51.7`, and `source_ready_age_s_max` in the same clean range as the lane summary. The reducer now says Pro worker-profile spread `5.9pp` is only slightly ahead of auth-refresh spread `0.3pp`, while Free worker-profile spread `6.1pp` is stronger than auth-refresh spread `0.0pp`. Recovery-event parsing is now live too: Pro logged `71` default-profile recoveries and Free `44`, with Free skewing toward `pre_auth` on `source content` / `source list`. Recovery events and auth events now carry browser profile root/directory and worker-state root, so the next live rerun can attribute the Free recovery path and auth-refresh pressure precisely instead of inferring it indirectly. The next highest-leverage unresolved branch is the Free auth/source-content recovery path, not another cadence toggle or auth-TTL tweak.
- `fresh_state_3plus3_extract_schema_control_run13_current` is a severe regression sample rather than a new ceiling. It completed cleanly at `1882.50` combined hot-path VPH with `684/116/800`; Pro landed at `1039.21` with `worker-profile spread 10.4pp` versus `auth-refresh spread 16.5pp`, and Free landed at `1037.42` with `worker-profile spread 29.7pp` versus `auth-refresh spread 25.2pp`. The new recovery logs confirmed the browser/worker-state attribution context is present in `nlm_auth_recovered`, but the sample itself is still too volatile to treat as the baseline.
- `fresh_state_3plus3_extract_schema_control_run14_current` confirmed the regression is not a one-off: it completed cleanly at `1675.54` combined hot-path VPH with `576/224/800`; Pro landed at `1262.72` with `worker-profile spread 19.7pp` versus `auth-refresh spread 57.6pp`, and Free landed at `1007.92` with `worker-profile spread 14.5pp` versus `auth-refresh spread 42.4pp`. Recovery logs on this run show the browser profile roots and worker-state roots for both lanes, and auth events now carry the same context, so the dominant signal can be attributed directly rather than inferred. The next highest-leverage unresolved branch is auth-refresh timing, not browser-root swapping or worker-order swapping.
- `fresh_state_3plus3_extract_schema_control_run15_current` is a later home-network same-shape repeat after returning home. It completed cleanly at `2205.73` combined hot-path VPH with `751/49/800`, `throughput_valid=true`, and `run_environment_label=home_300mb`; Pro landed at `382/18` with `content_fetch_command_elapsed_s_total=3354.524`, `worker_idle_wait_s_total=310.186`, and `source_ready_age_s_max=319.703`; Free landed at `369/31` with `content_fetch_command_elapsed_s_total=5193.556`, `worker_idle_wait_s_total=692.818`, and `source_ready_age_s_max=377.993`. This is a valid negative sample against the current `run07_current` baseline, not a replacement for it. Batch-level comparison shows the regression is concentrated in batch 1, especially Free, while batch 2 largely recovers, so the next useful branch is first-batch warmup / ordering attribution rather than another same-shape repeat.
- `fresh_state_3plus3_extract_schema_auth_interval45_run01_current` is the first auth-refresh calibration point that materially improved the baseline. It completed cleanly at `2356.82` combined hot-path VPH with `766/34/800`; Pro landed at `1512.61` with `worker-profile spread 18.0pp` versus `auth-refresh spread 48.3pp`, and Free landed at `1361.04` with `worker-profile spread 24.9pp` versus `auth-refresh spread 1.3pp`. The run shows the auth-refresh branch is still the dominant tuning surface, but interval 45 is a better operating point than the prior 60-second baseline and should be treated as the current candidate until a neighboring interval proves better or worse.
- `fresh_state_3plus3_extract_schema_auth_interval45_run03_current` is the same-shape live rerun after the source-list validation recovery fix and summary-field propagation. It completed cleanly at `1357.58` combined hot-path VPH with `638/162/800`; Pro landed at `846.06` with `worker-profile spread 16.1pp` versus `auth-refresh spread 39.9pp`, and Free landed at `660.91` with `worker-profile spread 37.3pp` versus `auth-refresh spread 55.1pp`. The live branch is still negative, but the reducer now says auth-refresh age is the stronger signal on both lanes, so the next highest-leverage unresolved branch is auth-refresh timing again rather than browser-root swapping or worker-order swapping.
- `fresh_state_3plus3_extract_schema_auth_interval45_run05_current` is the follow-up rerun after the auth-context and reducer propagation work. It completed cleanly at `2215.43` combined hot-path VPH with `713/87/800`; Pro landed at `1416.84` with `worker-profile spread 23.6pp` versus `auth-refresh spread 32.7pp`, and Free landed at `1234.42` with `worker-profile spread 37.0pp` versus `auth-refresh spread 55.6pp`. This is a clear regression from the current live baseline `run07_current`, so interval 45 is not the better branch; the interval search should move wider or stop, and the next unresolved bottleneck is back on auth/source-content recovery rather than narrower auth cadence.
- `fresh_state_3plus3_extract_schema_auth_interval30_run01_current` is the neighboring auth-interval probe that came back worse. It completed cleanly at `1419.74` combined hot-path VPH with `711/89/800`; Pro landed at `795.37` with `worker-profile spread 30.0pp` versus `auth-refresh spread 17.4pp`, while Free landed at `840.05` with `worker-profile spread 5.0pp` versus `auth-refresh spread 22.1pp`. The auth-refresh branch is still active, but interval 30 is not the better point, so 45 remains the current candidate.
- `fresh_state_3plus3_extract_schema_auth_interval50_run01_current` is the wider neighboring auth-interval probe and it regressed versus `45`. The completed smoke summary is valid at `2351.42` combined hot-path VPH with `761/39/800`; Pro landed at `1387.74` with `content_fetch_command_elapsed_s_total=4514.852`, `worker_idle_wait_s_total=400.153`, and `source_ready_age_s_max=225.731`; Free landed at `1310.74` with `content_fetch_command_elapsed_s_total=5229.668`, `worker_idle_wait_s_total=377.911`, and `source_ready_age_s_max=231.125`. This closes the `50s` branch as a regression, not an improvement over `45s`.
- `fresh_state_3plus3_extract_schema_auth_interval47_run01_current` is the finer neighboring auth-interval probe and it is a major regression. The completed smoke summary is valid at `864.87` combined hot-path VPH with `442/358/800`; Pro landed at `619.96` VPH with `content_fetch_command_elapsed_s_total=13686.435`, `worker_idle_wait_s_total=924.973`, `source_ready_age_s_max=376.729`, and `content_fetch_status_counts_total={"ready":265,"source_age_cliff":133,"command_failed":8}`; Free landed at `416.25` with `content_fetch_command_elapsed_s_total=15530.452`, `worker_idle_wait_s_total=987.102`, `source_ready_age_s_max=358.537`, and `content_fetch_status_counts_total={"ready":177,"source_age_cliff":223,"command_failed":3}`. This closes the `47s` branch as clearly worse than `45s` and much worse than `50s`.
- `fresh_state_3plus3_extract_schema_auth_interval40_run01_current` never became throughput-valid. The first attempt hit `WinError 112` while copying `cookies.json` during auth session sync; the inactive home-directory Chrome profile trees were then quarantined to `P:\.data\yt-is\chrome-profile-quarantine` and replaced with junctions so the same `C:` pressure does not recur. Do not treat `40s` as a throughput datapoint.
- `hotel_wifi_3plus3_auth_interval75_run01` is an environment-scoped diagnostic after worker-auth profile sync, not a home-network throughput datapoint. The auth sync created backup `C:\Users\brsth\.notebooklm-mcp-cli\profiles\backup-before-worker-auth-sync-20260526-155742`, and all Pro/Free worker profiles validated before the run. The run completed as `status=partial`, `throughput_valid=false`, and `invalidated=false` at `2662.72` combined hot-path VPH on `739/11/750`: Pro stopped at `350/0/350` with `source_ready_age_s_max=198.303`, while Free completed at `389/11/400` with `source_ready_age_s_max=234.963`. The reducer comparison against the home-network `fresh_state_3plus3_extract_schema_control_run07_current` shows the hotel environment materially increased command/probe/retry/idle pressure: combined `content_fetch_command_elapsed_s_total=8115.136` versus `5041.38`, `worker_idle_wait_s_total=840.916` versus `86.74`, `source_list_probe_elapsed_s_total=165.125` versus `23.61`, retry sleep `304.0`, retry queue sleep `360.0`, and `content_fetch_status_counts_total={"ready":739,"command_failed":40}`. Use this run to confirm auth repair and environment sensitivity only; do not rank it against home-network ceiling candidates.
- `fresh_state_3plus3_preserve_worker_state_run01_current` invalidated during smoke on the Free lane with `source_count_probe_failed subbatch_size=50 sources=0->0` after the preserved worker-state path hit repeated source-count probe failures. Pro finished cleanly at `326/74`; Free did not produce throughput evidence. The preserve-worker-state-root branch is closed as a negative result, not a throughput win.
- `nlm_batch` now retries the source-count probe once after an auth failure before classifying the notebook as dead, and the add path now reuses the outer source-count probe result instead of re-running `source list` a second time on the same subbatch. This hardening is meant to reduce false `source_count_probe_failed` invalidations and redundant probe overhead on stale worker-state paths, but it is a code-path fix rather than a fresh throughput datapoint until a live rerun confirms it.
- `fresh_state_3plus3_preserve_worker_state_run02_current` is that live confirmation and it is still a negative throughput result. It completed cleanly at `1285.73` combined hot-path VPH with `629/171/800`; Pro landed at `764.87` with `source_age_cliff=63`, `command_failed=41`, `content_fetch_command_elapsed_s_total=12136.886`, and `source_ready_age_s_max=297.765`; Free landed at `806.77` with `source_age_cliff=102`, `command_failed=20`, `content_fetch_command_elapsed_s_total=12036.465`, and `source_ready_age_s_max=433.307`. The auth-retry hardening removed the false invalidation, but preserving worker-state root still does not move the throughput needle, so this branch remains closed as a negative result.
- `fresh_state_3plus3_source_age_cadence_run12_threshold_60_120` reran the cadence branch after the auth-check recovery fix and completed cleanly at `2144.84` combined hot-path VPH with `774/26/800`. Pro landed at `384/16` with `source_ready_age_s_max=211.70`, `content_fetch_command_elapsed_s_total=4876.71`, `source_age_cliff=13`, `command_failed=14`, and `worker_idle_wait_s_total=396.84`; Free landed at `390/10` with `source_ready_age_s_max=257.59`, `content_fetch_command_elapsed_s_total=6198.68`, `source_age_cliff=0`, `command_failed=23`, and `worker_idle_wait_s_total=518.15`. The more aggressive cadence windowing improved throughput versus `run09_current`, but it still sits far below the live `run07_current` baseline, so cadence remains a calibration branch rather than the ceiling.
- `fresh_state_3plus3_source_age_cadence_run13_threshold_50_100` improved the cadence calibration again. It completed cleanly at `2359.61` combined hot-path VPH with `749/51/800`. Pro landed at `368/32` with `source_ready_age_s_max=281.81`, `content_fetch_command_elapsed_s_total=4718.60`, `source_age_cliff=31`, `command_failed=7`, and `worker_idle_wait_s_total=257.38`; Free landed at `381/19` with `source_ready_age_s_max=304.81`, `content_fetch_command_elapsed_s_total=5172.80`, `source_age_cliff=16`, `command_failed=2`, and `worker_idle_wait_s_total=247.87`. This is the strongest cadence calibration point so far, but it still trails the live `run07_current` baseline, so cadence remains a tuning branch, not the ceiling.
- `fresh_state_3plus3_source_age_cadence_run14_threshold_40_80` regressed versus `run13_threshold_50_100` but stayed above `run12_threshold_60_120`. It completed cleanly at `2164.58` combined hot-path VPH with `780/20/800`. Pro landed at `396/4` with `source_ready_age_s_max=245.93`, `content_fetch_command_elapsed_s_total=6172.48`, `source_age_cliff=0`, `command_failed=24`, and `worker_idle_wait_s_total=310.46`; Free landed at `384/16` with `source_ready_age_s_max=298.95`, `content_fetch_command_elapsed_s_total=6337.40`, `source_age_cliff=14`, `command_failed=15`, and `worker_idle_wait_s_total=293.32`. The cadence sweep now has a clear local peak around `50/100`, so the next useful action is a targeted threshold refinement or a repeat of the current peak to confirm stability rather than another broad geometry shift.
- `fresh_state_3plus3_source_age_cadence_run15_threshold_45_90` confirmed the cadence peak instead of extending it. It completed cleanly at `2348.45` combined hot-path VPH with `781/19/800`. Pro landed at `394/6` with `source_ready_age_s_max=240.01`, `content_fetch_command_elapsed_s_total=5255.996`, `source_age_cliff=4`, `command_failed=2`, and `worker_idle_wait_s_total=93.81`; Free landed at `387/13` with `source_ready_age_s_max=279.699`, `content_fetch_command_elapsed_s_total=5550.368`, `source_age_cliff=11`, `command_failed=2`, and `worker_idle_wait_s_total=262.58`. Reducer attribution on the latest cadence result is lane-mixed: Pro auth-refresh spread `20.4pp` is stronger than worker-profile spread `14.3pp`, while Free worker-profile spread `26.1pp` is stronger than auth-refresh spread `8.8pp`. Treat cadence threshold refinement as peaked around `50/100`; the next highest-leverage unresolved work splits into Pro auth-refresh timing and Free worker-state/profile analysis, not more cadence stepping.
- `fresh_state_3plus3_source_age_cadence_series_run01_projected_window_elapsed_enabled_current` invalidated on Pro even though the artifact is actually `4+4` (`worker_shape_signature=4+4`, `lane_worker_counts={"a_hominidae_pro":4,"troup_hominidae_free":4}`), and the failure trace still shows the stale JSON-shaped notebook-create blob flowing into `source_count_probe_failed subbatch_size=50 sources=0->0`. Treat this as pre-parser-fix evidence for notebook-id normalization, not as a 3+3 cadence result; the next rerun should use the fixed parser and a clean output root once the shared hotel profiles free up.
- The queued cadence rerun launcher now waits on both the active `run28` lane processes and the shared hotel browser-root PIDs before starting `run29`, so the next launch should not collide with a still-open shared profile. That keeps the queued rerun boundary aligned with the intended universe rather than just the lane-process JSON.
- The staged `run29` lane config now points its configured worker-state roots at the clean `run29_current` output-root tree rather than the older `run24` smoke path, so any preserved-worker-state inspection will start from the same universe as the rerun itself.
- `hotel_wifi_3plus3_shared_retry_source_age_cadence_run29_current` completed the dedicated cadence universe, but only as `status=partial` with `throughput_valid=false`. The launch/config question is closed: the summary reports `worker_shape_signature=3+3`, `run_environment_label=hotel_wifi`, and both lane manifests carry `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED=1` with the `160/190/5` cadence thresholds. The throughput question is not closed: Pro finished at raw `453` processed with `56` shared-retry processed, leaving `397/400` primary processed, while Free finished at raw `397` with `48` shared-retry processed, leaving `349/400` primary processed. The saved reducer artifact `.logs/sharded_lane_series/hotel_wifi_3plus3_shared_retry_source_age_cadence_run29_stage_reducer.txt` shows the dominant signal is source-content command/profile pressure rather than cadence propagation: Pro worker-01/02 failed `56.9%`/`50.9%` of commands while worker-03 failed `6.2%`; Free worker-01/02 failed `74.2%`/`66.7%` while worker-03 failed `0.0%`. The canonical audit now renders the same worker/auth skew in `Table 9 — Worker / Auth Skew Attribution`, so the split is visible in the derived audit artifact as well. The industrial source dispatcher now prefers healthier free slots and treats source-list probe activity as a stronger penalty when it launches a new batch wave, so the next rerun can test whether the visible skew stays off the probe-flagged slot instead of leaving worker-01/02 overloaded. Do not launch another cadence rerun from this result alone; next work should inspect source-content command-latency, worker/profile state, source-list probe overhead, and shared-retry accounting on the partial lanes.
- `hotel_wifi_3plus3_shared_retry_source_age_cadence_run30_current` is the current hotel throughput-valid confirmation of the dispatcher change. It completed cleanly at `3156.57` combined hot-path VPH with `763/157/920`, `throughput_valid=true`, and `worker_shape_signature=3+3`; Pro landed at `2014.77` with `source_list_probe_count=5` and `worker_idle_wait_s_total=98.620`, while Free landed at `2294.94` with `source_list_probe_count=6` and `worker_idle_wait_s_total=122.730`. The slot-health ordering now prefers probe-free slots and uses probe elapsed plus source-content command latency as tie-breakers, and this rerun materially improved the hotel cadence branch over `run29` without changing the universe shape. The remaining open branch is whether source-list probe cost or source-content command latency can be reduced further, not whether the probe-penalty ordering itself works.
- `hotel_wifi_3plus3_shared_retry_source_age_cadence_run31_current` is a partial regression check on the same hotel universe. It fell back to `1710.32` combined hot-path VPH with `507/402/909`, `throughput_valid=false`, and `worker_idle_wait_s_total=436.682`; Pro batch 1 hit `source_age_cliff=50/100` while Free batch 1 stayed cliff-free, so the latest rerun looks like a source-age/command-latency regression rather than a throughput gain. The dispatcher has since been adjusted so failure rate outranks command latency in the free-slot score; the next rerun should validate that this demotion prevents the weak Pro slot from being preferred. Do not treat `run31` as a ceiling or as evidence that the browser-cleanup fix affected VPH; it only confirms that the current dispatcher still has a fragile batch-1 tail.
- `hotel_wifi_3plus3_shared_retry_source_age_cadence_run33_current` invalidated during the command-latency-removal experiment because worker-03 hit an auxiliary `yt-dlp -J --skip-download --no-playlist` `TimeoutExpired` on one video in Pro batch 2. That timeout is now treated as a non-fatal classification error in `youtube_page_inspector.py`, so the next rerun should no longer die because a probe helper exceeded its wall-clock budget. Treat `run33` as a regression and keep the selector on the restored `run32` ordering rather than the removed-command-latency branch.
- `fresh_state_3plus3_extract_schema_ready_probe_run03_current` is the same ready-probe shape rerun after the post-run cleanup soft-fail fix. It completed cleanly at `1980.83` combined hot-path VPH with `784/16/800`, `throughput_valid=true`, `source_content_readiness_probe_count=27`, `source_ready_age_s_max=323.089`, and `content_fetch_command_elapsed_s_total=11965.612`. Relative to `fresh_state_3plus3_extract_schema_control_run07_current`, the ready-probe branch still pays much higher command latency and probe overhead rather than a cleanup penalty, so the next useful probe is extract-command attribution, not another cleanup rerun.
- The reducer comparison between `fresh_state_3plus3_extract_schema_control_run07_current` and `fresh_state_3plus3_extract_schema_ready_probe_run03_current` shows the same conclusion: `run03_current` roughly doubles content-fetch command latency (`11965.612` vs `5041.38`) and adds `27` readiness probes while the worker/auth skew remains secondary (`worker-profile spread 10.8pp` vs `auth-refresh spread 8.3pp`). The slow tail is broad rather than a single abort: in the live worker logs, the slowest command-completed events in the control run are about `67-97s`, while the ready-probe run reaches about `142-152s`, concentrated in `worker-02` and `worker-03` with `ready` status and only a small number of `command_failed` outliers. Treat the next step as `nlm_source_content_command_completed` command-latency attribution inside the extract path, not another cleanup or probe rerun.
- `fresh_state_3plus3_profile_swap_run01_current` completed cleanly but is a clear negative result for the profile-order hypothesis: swapping worker-02/worker-03 profile order in both lanes dropped combined hot-path VPH to `1249.17` with `568/232/800`, Pro to `785.47` with `298/102`, and Free to `702.25` with `270/130`. This closes the profile-order swap branch for now; do not rerun it unless the worker/profile assignment logic changes.
- `fresh_state_3plus3_free_browser_default_run01_current` changed only the Free browser profile from `Profile 1` to `Default` on the current live baseline shape, and it regressed to `2752.18` combined hot-path VPH with `726/74/800`. Pro remained comparatively healthy at `398/2` with `content_fetch_command_elapsed_s_total=2924.017`, `worker_idle_wait_s_total=131.548`, and `source_ready_age_s_max=159.966`; Free fell to `328/72` with `content_fetch_command_elapsed_s_total=7720.07`, `worker_idle_wait_s_total=201.316`, and `source_ready_age_s_max=353.569`. This closes the Free-browser-default branch as a regression, not a win, so browser-profile directory alone is not the next lever. The stage reducer on this soak still points at extract as the lane bottleneck (`extract=39%` of aggregate stage sum on Pro and `57%` on Free), and the Free lane command log skews hard onto `worker-03` (`107/183` commands with `8` failures) while the control Free lane stayed much more balanced (`52/107/50` with only `11` failures total). The ready split shifted from control's balanced `49/99/50` on workers `01/02/03` to `10/20/99`, with failures concentrated on `worker-01` and `worker-02`; that makes Free worker-state / assignment analysis the next highest-leverage branch, not another browser-profile swap.
- The latest reducer comparison against `fresh_state_3plus3_extract_schema_control_run07_current` keeps the same conclusion but sharpens the failure shape: control Free still shows only a modest worker-profile spread (`10.6pp`) and no auth-refresh signal (`0.0pp`), while the browser-default Free sample is dominated by worker-01/02 retry pressure on the stateful paths (`retry` rows at `worker-01 18/19 source_age_cliff`, `worker-02 14/14 source_age_cliff`) with worker-03 remaining comparatively healthier. That reinforces worker-state / assignment analysis as the next unresolved branch rather than another browser-tree swap.
- The latest offline reducer rerender on the current control and browser-default artifacts keeps the same branch split but makes it more explicit: the control Free lane still concentrates failure-rate pressure on worker-03, while the browser-default Free lane shifts retry pressure onto worker-01/02 and still shows the retry rows living in the `source_age_cliff` band. That keeps the next home-network rerun focused on worker-state / assignment versus source-content command-latency separation, not on another browser-root or cadence change.
- Command-attribution payloads now also carry `browser_profile_root`, `browser_profile_directory`, and `worker_state_root`, and the stage reducer surfaces them when available. Fetch-recovery payloads now surface the same browser/worker-state context in a dedicated `Fetch Recovery Attribution` section, so the retry path can be attributed directly instead of inferred indirectly from command completions. The reducer now also preserves per-command `elapsed_s` and reports average source-content command latency by worker/profile and per worker-batch, plus `run_environment_label` when present. The reducer now also falls back to rendering fetch-recovery attribution from fetch-only logs when command attribution is absent, so the section no longer disappears on sparse worker traces; the new regression uses `json.dumps`-built JSONL fixtures to avoid escape-artifact failures. The current `fresh_state_3plus3_free_browser_default_run01_current` artifact predates this fetch-context emission, so the new section is still mostly empty until we rerun the same shape with the current code. This removes the remaining blind spot for the Free worker-state branch: future comparisons can separate browser-tree state, command latency, retry gating, and auth-refresh age before deciding whether a code-path fix is justified.
- The stage reducer now splits source-content command latency into `Avg Cmd(s)`, `Avg Ready Cmd(s)`, and `Avg Failed Cmd(s)` for both worker/profile and batch-attribution rows. On the current home-network baseline `fresh_state_3plus3_extract_schema_control_run07_current`, the Free lane shows worker-03 as the failure-rate outlier (`61/256` failed, `23.8%`) while worker-01 and worker-03 both have elevated ready-command latency (`9.73s` and `9.38s`). In the ready-probe regression, Free worker-01 and worker-02 failures are also materially slower than their ready commands (`15.60s` and `19.72s` failed averages), so the bottleneck is a source-content command/retry/recovery path, not a single bad profile label. The next code-path work should inspect `_fetch_content_round` retry, ytdlp deferral, source-list probe, and ready-probe behavior before any live benchmark.
- The stage reducer now also parses `nlm_batch_source_content_fetch_completed` and reports fetch recovery attribution by worker/profile/pass with status distribution, average attempts, retry-gate reason, source-ready age, command time, source-list probe time, ytdlp probe time, and per-window `batch_index` when the windowed path emits it. Applied to `fresh_state_3plus3_extract_schema_control_run07_current`, Free retry rows stay mostly below the cliff band (`worker-01 avg/max=162.5s/212.5s`, `worker-02=130.7s/188.2s`, `worker-03=153.0s/180.7s`) with zero source-list probes. Applied to `fresh_state_3plus3_extract_schema_ready_probe_run03_current`, Free retries move into the cliff-risk band (`worker-01=188.5s/207.6s`, `worker-02=217.2s/323.1s`, `worker-03=168.0s/231.8s`) with zero source-list probes. Applied to `fresh_state_3plus3_free_browser_default_run01_current`, the Free retry rows are cliff-dominant (`worker-01 retry source_age_cliff=18/19 at avg/max `343.7s/353.6s`; `worker-02=14/14` at `350.7s/350.7s`). On the archived hotel `run23` artifact, the updated reducer now shows the primary fetches are mostly `ytdlp_ok` or `status_not_retryable`, which means retry gating is not the active hotel bottleneck in that universe; the next hotel-scoped work should pivot back to worker-balance / command-latency attribution rather than retry gating. The buffered-drain dispatch change then produced `run24`, which improved combined hot-path VPH to `1540.52` and lowered both lanes' worker-idle wait, so the remaining open question is no longer "does the first ready batch monopolize the queue?" but "how much of the remaining partial shortfall is pure source-content command latency versus cohort/order effects?"
- `nlm_batch` now guards the local source-content retry queue against projected source-age cliffs. If a failed source-content fetch is otherwise locally retry-queue deferrable but `source_ready_age_s + retry_queue_delay_s` would meet or exceed `YTIS_NLM_SOURCE_AGE_CLIFF_S`, the item is not queued for the local drain and the fetch-completed event records `retry_queue_skipped_reason=projected_source_age_cliff` plus `projected_retry_ready_age_s`. If an item looked safe when queued but the primary round takes long enough that the actual local drain plus queue delay would now cross the cliff, the drain skips the local sleep/retry and records `retry_queue_drain_skipped_count` plus `retry_queue_drain_skipped_reason_counts={drain_projected_source_age_cliff:...}`. Shared retry-pool handoff remains eligible because the receiving worker re-adds and re-materializes in another notebook. Projected retry-age fields are now emitted only for actual retry-queue candidates; retry-pass rows no longer invent a second projection when retry queuing is disabled for that pass. The stage reducer surfaces both `Max Projected Retry Age(s)` and `Retry Queue Skipped` in the fetch recovery table, suppresses stale retry-pass projection fields from older traces, and now renders missing projected-age fields as `absent` rather than `0.0`, so the next artifact can show whether the guard fired without implying false zero-age projections. This is a code-path fix for the retry rows above, not a throughput datapoint; do not run a live validation on hotel Wi-Fi unless the goal is environment-scoped diagnostics. The exact-threshold boundary and actual-drain boundary are now covered by regression tests so the cliff remains inclusive.
- The local retry projected-age guard now also has an opt-in margin knob: `YTIS_NLM_SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S` defaults to `0` through shared `NLMConfig.source_content_retry_queue_age_margin_s` and does not change current behavior unless explicitly set. When set, a retry whose `projected_retry_ready_age_s` is still below the cliff but whose margin-adjusted age would meet or exceed it is skipped locally with `retry_queue_skipped_reason=projected_source_age_cliff_margin`; fetch attribution records both `retry_queue_age_margin_s` and `projected_retry_ready_age_with_margin_s`, and the reducer/audit reports surface max margin-adjusted projected age plus max configured margin. This exists to test the current retry-window hypothesis without another code change once home-network validation is available; it is not a validated throughput improvement yet.
- Queued primary source-content retry deferrals now also emit `nlm_batch_source_content_fetch_completed` with `queued_for_retry=true` before local drain or shared retry-pool handoff. The stage reducer surfaces this as `Retry Queued` in `Fetch Recovery Attribution`, separating primary queued deferrals from projected-age skips and final retry-pass failures. Existing hotel artifacts predate this field and rerender as `0`; the next live artifact can directly explain whether high retry-window deferrals are entering the queue, being skipped by the projected-age guard, or dying on the retry pass.
- The source-content fetch outcome now carries `retry_queue_skipped_reason` and `projected_retry_ready_age_s` in addition to logging them, so the reducer and higher-level callers can inspect the guard decision without scraping logs. This is still a code-path fix, not a throughput datapoint, and it should stay paired with the same home-network same-shape validation path rather than a hotel benchmark.
- The stage reducer now also parses `nlm_batch_extract_completed` and renders a lane-level `Retry Queue Window` section with deferred/recovered/final-failed counts, drain-skip counts/reasons, delay/budget, and `drain ready age max`, even on sparse traces that only contain extract-completed rows. On the current hotel control and source-content diagnostic artifacts this section appears with `drain ready age max=absent`, `retry queue wait max/count=absent/absent`, and drain skips absent because those archived traces predate the actual-drain skip guard, so it is still instrumentation rather than a new throughput signal; it exists so the next live artifact can attribute the drain window directly instead of inferring it from fetch rows alone.
- The batch-level audit generator now lifts the same retry-window signal from the batch-local `nlm_batch_extract_completed` logs, including both local retry deferred/recovered/final-failed counts, local drain-skip counts/reasons, shared retry-pool handoff counts, queued primary deferrals from `nlm_batch_source_content_fetch_completed.queued_for_retry`, projected-age skip reasons and max projected retry age from primary fetch-completed rows, margin-adjusted projected age and configured margin when present, retry-pass status distributions from rows where `pass_name=retry`, and actual retry-queue wait max/count from future extract-completed rows. For older artifacts it falls back to `nlm_batch_source_content_retry_queued` without double-counting future traces that emit both events. It aggregates every existing direct, `smoke`, and `soak` lane root instead of stopping at the first matching phase. The audit report also surfaces `run_environment_label` in the top-level table so hotel and home artifacts stay separated before any numerical comparison. The current hotel artifacts now surface retry-window counts, primary queued counts, projected-age skips, retry-pass status distributions, and queue sleep totals in the generated audit report, but `retry_queue_drain_ready_age_s_max`, `retry_queue_wait_elapsed_s_max`, and drain-skip counts are still absent in the archived hotel traces, so actual drain/wait remains instrumentation until the next live rerun produces those fields.
- Current recommendation while on hotel Wi-Fi: do not run full ceiling benchmarks except when the purpose is explicitly environment-scoped. Continue with non-destructive code-path analysis, reducer attribution, focused regression tests, and small smoke diagnostics. Future guarded or direct sharded-lane runs should pass `--run-environment-label hotel_wifi` or `--run-environment-label home_300mb` so comparison boundaries are explicit in the artifact. While away from home, compare all new hotel runs against `hotel_wifi_3plus3_baseline_run01_current` and keep the home-network ceiling separate. With the hotel baseline, interval-75 rerun, and interval-45 rerun now jointly closing the hotel auth-cadence branch, the next hotel-scoped work should pivot to source-content command-latency / worker-state attribution rather than more geometry or browser-root swapping. The fresh `hotel_wifi_3plus3_shared_retry_canary_run22_current` rerun invalidated on repeated Free `source_count_probe_failed` / auth-source failure at worker `ytis-free1-worker-03`, and the current auth path now falls back to a bounded non-CDP source-profile refresh when the dedicated Free CDP browser cannot be reached, so the current blocker is Free auth/source-state recovery plus CDP/browser availability, not another shared-retry or duplicate-probe rerun. The follow-up `hotel_wifi_3plus3_shared_retry_canary_run23_current` rerun confirmed the fallback path keeps Free alive long enough to complete the benchmark, but it still finished `status=partial` and `throughput_valid=false`; the reducer now surfaces the lane-level `expected processed` and `lane partial reason` fields directly, and the batch audit generator preserves the same shortfall metadata in generated markdown, so the processed shortfall is visible without re-reading the raw summary. That makes `run23` diagnostic evidence rather than a new ceiling. The buffered-drain dispatch branch then produced `run24`, which raised the combined hot-path VPH to `1540.52` from `648.10` on the same hotel shape and cut worker idle wait on both lanes, but it still finished partial (`386/379` processed against `400` expected per lane). The updated reducer now renders the mixed-layout `run24` artifact correctly, and the batch tables show the residual tail is still worker-skewed across both batches rather than a pure smoke-root artifact issue. So the queue timing problem is improved but not the whole answer; the next ranked branch is source-content command latency / worker-state attribution, and the reducer now has the batch/window index needed to separate earlier windows from later ones in that attribution when a fresh rerun is justified.
- `hotel_wifi_3plus3_shared_retry_canary_run25_current` did not become a throughput comparison candidate. Smoke stayed partial at `25/50` processed per lane, and the sequence aborted on `summary status is partial` before soak could start. Pro batch 1 hit `220.14` hot-path VPH and Free batch 1 hit `283.14`, but the second batch never became real hot-path work because the preflight cleanup gate failed before batch 2 could run. That means the new `batch_index` attribution is wired in but still not exercised live, so the next action path is cleanup-gate / wrapper handling, not another same-shape hotel rerun for window ordering.
- `hotel_wifi_3plus3_shared_retry_canary_run26_current` is the follow-up soft-fail cleanup validation. It is still partial and not throughput-valid, but it proves the cleanup warning no longer aborts the second batch: batch 2 started on both lanes and completed, Pro batch 1 / batch 2 measured `186.38` / `126.48` hot-path VPH with `20/20` and `11/20` success/fail splits, Free batch 1 / batch 2 measured `140.78` / `157.41` with `14/15` and `18/11`, and the combined smoke artifact reached `268.92` hot-path VPH with `63/66/129` overall. The cleanup gate is therefore no longer the blocker; the remaining shortfall now lives in the batch path itself, so the next useful question is whether the batch tail is pure source-content latency, retry pressure, or lane-skew rather than cleanup.
- `hotel_wifi_3plus3_shared_retry_canary_run27_current` is the completed root artifact for the current hotel `3+3` shared-retry branch. It is still partial and not throughput-valid, but it closes the current evidence loop with `status=partial`, `throughput_valid=false`, `invalidated=false`, combined hot-path VPH `1052.95`, `933` processed, `460` successes, `473` failures, `shared_retry_deferred_count_total=316`, `shared_retry_recovered_count_total=133`, `source_list_probe_count=12`, `source_ready_age_s_avg=76.94`, and `content_fetch_command_elapsed_s_total=29238.249`. Pro finished at `458` processed with a `386` expected shortfall and Free finished at `475` processed with a `395` expected shortfall, so the lanes are still batch-tail limited rather than cleanup-limited. The batch split is now explicit: Pro batch 02 is slower than batch 01 on both source-age and command time, and Free batch 02 is the heaviest tail with `source_ready_age_s_avg=247.08s` versus `180.25s` in batch 01, so the remaining bottleneck is specifically batch-02 source-age and command-latency pressure, especially on Free. The current stage reducer sharpens that split further: Pro still looks auth-refresh sensitive (`auth-refresh spread 13.5pp` vs `worker-profile spread 12.0pp`), while Free remains more worker-balance sensitive (`worker-profile spread 21.1pp` vs `auth-refresh spread 9.0pp`). The notebook-create path is now hardened to parse JSON-shaped create output into a plain notebook id, and reusable notebook state files are normalized on load/save too, so future dead-notebook recovery traces should only be treated as real notebook churn if the new parser still sees a plain-id failure. The regenerated audit artifact now renders a batch-tail table from the batch sweep summaries, and it now includes `run26` as labeled smoke fallback evidence when the root summary is absent, so the batch-02 asymmetry is visible directly in the comparison artifact. `run26` remains partial smoke evidence rather than the final hotel comparison point.
- Browser cleanup rule for all live diagnostics: never stop Chrome, Edge, or any browser by executable name alone. Only stop processes that are provably owned by the active yt-is run, either by an explicit harness-recorded PID or by a command line using a lane browser root from the active lane config, such as `P:\.data\yt-is\browser\notebooklm-pro` or `P:\.data\yt-is\browser\notebooklm-free`. If a process cannot be tied to those roots, treat it as user/browser state and leave it running while inspecting logs.
- Space discipline audit: before pruning old browser roots or completed benchmark roots, run `python P:\packages\yt-is\bin\csf-space-audit` and keep only what is still live or not yet promoted into the docs.
- `hotel_wifi_3plus3_source_content_attr_run02_current` is the current true `3+3` hotel diagnostic. The smoke phase completed at `1259.32` VPH with `571/229/800`, but the overall command timed out before soak finished. That smoke artifact is still useful: Pro shows `30` drain skips and `144.1s` max retry wait, Free shows `37` drain skips and `108.7s` max retry wait, and both lanes remain extract-dominant with heavy source-age pressure. Treat this as the live hotel evidence base until a smaller, shorter diagnostic or a home-network rerun is available; do not treat the timed-out soak as throughput evidence. The run root itself has no top-level summary, so the batch audit tool skips it and the reducer remains the correct comparison path for this artifact.
- `hotel_wifi_3plus3_shared_retry_canary_run13_current` is the post-fix hotel `3+3` canary that exists specifically to validate the shared-retry override path. The benchmark wrapper previously forced `YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED=false` for the active policy, which meant pre-fix hotel canaries (`run11`, `run12`) were not valid evidence for shared-pool behavior even if they were running under `hotel_wifi`. The wrapper now restores hotel override priority, so `run13` is the first valid hotel canary for this question. Live worker traces confirm `resolved_source_content_shared_retry_pool_enabled=true` on both lanes, and the Pro lane already completed a shared-retry drain with `shared_retry_deferred_count=4`, so the override is active in practice even though the run later invalidated on worker-notebook preflight cleanup before the new non-fatal cleanup gate landed. A fresh hotel rerun is required to validate throughput on the updated orchestration gate; do not use `run11` or `run12` to infer shared-pool behavior, and do not use the stale `run13` invalidation as evidence against the shared-pool path.
- `hotel_wifi_3plus3_source_content_attr_run01_current` is the new hotel-scoped source-content attribution diagnostic, but the artifact itself reports `worker_shape_signature=4+4`, so it is not same-shape with the active hotel `3+3` baseline. It completed cleanly at `2379.18` combined hot-path VPH with `700/100/800`, and the reducer fetch-recovery section is populated on this fresh artifact, so browser profile roots/directories and worker-state roots are now directly attributable in both lanes instead of being inferred from command completions alone. Pro is now the lane where worker-profile spread (`17.2pp`) dominates auth-refresh spread (`7.2pp`), while Free still leans auth-refresh-sensitive (`36.9pp` versus `23.6pp` worker-profile spread). The auth logs on this run are mostly `cache_expired` rather than `cache_miss`, so the Free signal looks like session-age pressure, not a cache-hole bug. Do not compare this numerically to the active hotel `3+3` baseline without a same-shape `4+4` control; use it only as a hotel diagnostic artifact. The next hotel-scoped follow-up is lane-mixed again: Pro wants worker-balance investigation, while Free wants auth-refresh timing / session-cache analysis.
- `hotel_wifi_4plus4_control_run03_current` is the matching hotel-scoped same-shape `4+4` control for that diagnostic universe, and it regressed hard to `1149.72` combined hot-path VPH with `453/347/800`. The final soak split is lopsided: Pro batch 1 reached `698.92` VPH and batch 2 `1251.35`, while Free batch 1 reached `739.22` VPH and batch 2 collapsed to `329.28`; combined `content_fetch_status_counts_total={"ready":453,"command_failed":146,"source_age_cliff":333}` and `source_ready_age_s_max=455.318`. This is a clear regression versus the active hotel `3+3` baseline, so the `4+4` universe remains diagnostic only and should not be promoted as the hotel ceiling.
- Within that control, the Free lane collapse is age-cliff driven rather than a pure command-failure spike: batch 1 had `source_age_cliff=49` and `command_failed=42`, while batch 2 jumped to `source_age_cliff=142` with `command_failed=38` and `source_ready_age_s_max=455.318`. That keeps the next hotel-scoped question pointed at Free source-age / retry recovery rather than a worker-count promotion.
- Corrected audit aggregation now reads both smoke and soak lane roots for the hotel `4+4` pair and reports max projected retry age from primary fetch-completed rows only. The source-content diagnostic has `86` local retry deferrals, `32` projected-age skips before local queueing, `420.000s` retry-queue sleep, `17543.126s` content-fetch command time, max projected retry age `373.127s`, and retry-pass statuses `{command_failed=35, nlm_content_below_threshold=1, source_age_cliff=49}`, while the same-shape control has `250` local retry deferrals, `197` projected-age skips before local queueing, `1410.000s` retry-queue sleep, `36150.745s` content-fetch command time, max projected retry age `453.582s`, and retry-pass statuses `{command_failed=53, nlm_content_below_threshold=1, source_age_cliff=195}`. That makes the `4+4` control regression a retry-window / source-age / command-latency pressure artifact, not just a top-line VPH regression.
- `nlm_batch` now records `retry_queue_wait_elapsed_s_total`, `retry_queue_wait_elapsed_s_max`, and `retry_queue_wait_elapsed_s_count` on local retry-window completion and the extract summary. It also fails queued local retries before sleeping when the actual drain projection (`current source age + retry_queue_delay_s`) would cross the cliff. This changes local retry behavior only for would-be same-notebook retries already projected to age out at drain time; it closes the remaining evidence gap between the initial projection (`source_ready_age_s + retry_queue_delay_s`) and the actual retry drain time after all primary futures complete.
- Reducer comparison against the earlier hotel-scoped `4+4` diagnostic makes the regression sharper: combined `content_fetch_command_elapsed_s_total` rises from `17543.126` to `36150.745`, `source_age_cliff` rises from `95` to `333`, and `source_ready_age_s_avg` rises from `67.086` to `134.84`. That keeps the same-shape `4+4` universe useful for attribution, but it is still not a ceiling candidate; the next hotel-scoped branch remains Free command-latency / retry recovery rather than 4+4 promotion.
- Fresh offline home-network reducer comparison artifact: `P://packages/yt-is/.logs/home_stage_compare_retry_window_current.txt`. It compares `fresh_state_3plus3_extract_schema_control_run07_current` against `fresh_state_3plus3_free_browser_default_run01_current` with the current absent-field reducer contract. Control Free retry rows remain mostly below or near the cliff band (`worker-01 avg/max=162.5s/212.5s`, `worker-02=130.7s/188.2s`, `worker-03=153.0s/180.7s`), while browser-default Free worker-01/02 retry rows are cliff-dominant (`18/19` source_age_cliff at `343.7s/353.6s`, and `14/14` at `350.7s/350.7s`) with zero source-list probes. That keeps the home-network unresolved branch focused on Free worker-state/browser-tree assignment and retry source-age pressure, not source-list recovery.
- Root-cause note for the browser-default branch: the `Default` browser tree under `P:\.data\yt-is\browser\notebooklm-free` exists and is populated, while the baseline `Profile 1` path seeds a session root without copying a populated subprofile tree from source. That makes the browser-default rerun a real state-hygiene regression, not a profile-name typo.
- The source-fetch partitioner rotation experiment was validated on `fresh_state_3plus3_extract_schema_control_run10_current` and rejected: the same-shape control regressed to `1942.14` combined hot-path VPH with `781/19/800`, Pro `390/10`, and Free `391/9`. On run10, Pro auth-refresh spread widened to `33.3pp` versus `9.1pp` worker-profile spread, and Free auth-refresh spread widened to `20.1pp` versus `12.1pp` worker-profile spread. The rotation patch is not retained, and the next highest-leverage branch is auth-refresh timing on both lanes, with Free worker-state remaining secondary.
- `fresh_state_3plus3_extract_schema_control_run10_current` is the fresh same-shape control rerun after the partition-rotation experiment. It completed cleanly at `1942.14` combined hot-path VPH with `781/19/800`; Pro landed at `390/10` with `content_fetch_command_elapsed_s_total=3916.8`, `worker_idle_wait_s_total=532.5`, and `source_ready_age_s_max=174.5`; Free landed at `391/9` with `content_fetch_command_elapsed_s_total=3949.6`, `worker_idle_wait_s_total=425.3`, and `source_ready_age_s_max=129.5`. The run is clean but negative, so partition rotation is closed and auth-refresh timing is the next unresolved bottleneck.
- `fresh_state_3plus3_extract_schema_auth_ttl60_run01_current` did not produce a usable full-load comparison. The smoke stopped partial at Pro `349/1/350` while Free completed `398/2/400`, so `status=partial` and `throughput_valid=false`. Do not treat TTL 60 as throughput evidence; the auth-refresh timing branch is still open, but the next step needs a more durable auth-path change than cache-TTL alone.
- The auth-path change is now in code: `csf/nlm_batch.py` persists `session_established_at` on successful auth checks and now also repopulates the auth cache after successful direct or family refresh paths, so `auth_check_interval_s` is a real interval gate for recently validated sessions instead of a cache-hole simulation. Offline auth-focused verification currently passes (`python -m pytest tests\test_nlm_batch.py tests\test_nlm_auth_guard.py -q -k "auth"`: `55 passed, 106 deselected`), so the current branch is no longer "dead config"; the next useful evidence would be a same-shape live validation to see whether the interval gate narrows the Pro/Free auth-refresh spread and reduces the extract tail.
- `fresh_state_3plus3_extract_schema_ready_probe_run02_current` is invalidated and must not be used as throughput evidence. The Pro lane failed in post-run cleanup (`worker notebook post-run cleanup failed after workers=3: deleted=0 failed=1`), so this branch is only useful as a reminder that cleanup failures are now classified separately from throughput outcomes. Use `fresh_state_3plus3_extract_schema_ready_probe_run03_current` as the valid ready-probe rerun.
- `fresh_state_3plus3_extract_schema_ready_probe_run01_current` reached its final summary but remained partial and is not throughput-valid: combined hot-path VPH `1855.39` with `746/4/750` processed, `source_content_readiness_probe_count=18`, `source_content_readiness_probe_elapsed_s_total=58.489`, `source_list_probe_elapsed_s_total=34.108`, `content_fetch_command_elapsed_s_total=6221.325`, and `source_ready_age_s_max=169.327`. Pro stopped at `348/2` because worker-03 hit `NotebookSourceMaterializationTimeout` after `600s` waiting for NotebookLM sources to become ready (`expected_total=50`, `source_count_before_wait=0`); Free completed `398/2` cleanly. Treat this as a source-materialization timeout branch, not a new ceiling.
- The worker-03 timeout mode from `fresh_state_3plus3_extract_schema_ready_probe_run01_current` is now guarded in `csf/nlm_batch.py`: if a successful add is followed by a source-count probe `NOT_FOUND`, the ingestor now treats it as a dead-notebook recovery and retries immediately instead of waiting the full `600s`.
- Next move after this attribution: do not launch another live auth-interval calibration while on hotel Wi-Fi. The current non-destructive branch is source-content retry/recovery attribution and the local retry projected-age guard; validate that guard on the next home-network same-shape run rather than as a standalone hotel-Wi-Fi ceiling benchmark. The `fresh_state_3plus3_worker_balance_ab_pro0213` confirmation repeat already exists as run06 and regressed materially, so do not run another worker-balance confirmation unless worker-profile assignment, cleanup, or `nlm` fallback behavior changes; and do not reopen agecap-200 or cadence until there is another code-path or source-readiness mechanism change.

## Next Probe: First-Batch Warmup / Ordering Attribution

Status: ready to run as a config-only probe. No harness code change is needed before this experiment.

Question:

- Did `fresh_state_3plus3_extract_schema_control_run15_current` regress because the measured soak paid first-batch worker/notebook/auth startup cost, especially on Free, rather than because the steady-state `3+3` extract path is slower than `run07_current`?

Why this is not another same-shape repeat:

- The measured comparison remains true `3+3`, `--limit 400`, `--batch-size 200`, serial reusable pipeline, `run_environment_label=home_300mb`.
- The discriminating change is that smoke warms the exact same fresh worker-state roots used by soak.
- The normal guarded sequence does not do this: without `--preserve-worker-state-root`, smoke uses `<run-root>/smoke/<lane>/worker_states` and soak uses `<run-root>/soak/<lane>/worker_states`.
- This probe creates run-specific worker-state roots under the new run root and passes `--preserve-worker-state-root`, so smoke and soak share only that run's fresh state. Do not point this at the old global `a_hominidae_pro/worker_states` or `troup_hominidae_free/worker_states` roots.

Create a run-specific lane config:

```powershell
$runRoot = 'P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current'
$laneConfig = 'P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_lanes.json'
@(
  [ordered]@{
    lane = 'a_hominidae_pro'
    account_class = 'pro'
    workers = 3
    notebooklm_profile_prefix = 'ytis-pro-worker'
    notebooklm_profiles = @('ytis-pro-worker-01', 'ytis-pro-worker-02', 'ytis-pro-worker-03')
    browser_profile_root = 'P:/.data/yt-is/browser/notebooklm-pro'
    browser_profile_directory = 'Profile'
    worker_state_root = "$runRoot/a_hominidae_pro/worker_states"
    notebook_prefix = 'benchmark-shard-a-hominidae-pro'
  }
  [ordered]@{
    lane = 'troup_hominidae_free'
    account_class = 'free'
    workers = 3
    notebooklm_profile_prefix = 'ytis-free1-worker'
    notebooklm_profiles = @('ytis-free1-worker-01', 'ytis-free1-worker-02', 'ytis-free1-worker-03')
    browser_profile_root = 'P:/.data/yt-is/browser/notebooklm-free'
    browser_profile_directory = 'Profile 1'
    worker_state_root = "$runRoot/troup_hominidae_free/worker_states"
    notebook_prefix = 'benchmark-shard-troup-hominidae-free'
  }
) | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path $laneConfig
```

Preflight:

```powershell
$env:PYTHONPATH = 'P:/packages/yt-is'
Remove-Item Env:\YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE -ErrorAction SilentlyContinue
Remove-Item Env:\YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE -ErrorAction SilentlyContinue
Remove-Item Env:\YTIS_NLM_AUTH_FORCE_REFRESH_EVERY_CHECKS -ErrorAction SilentlyContinue
python -m pytest -q `
  tests/test_sharded_lane_sequence.py `
  tests/test_run_evidence_check.py `
  tests/test_sharded_lane_series.py::test_lane_env_exports_run_environment_label `
  tests/test_sharded_lane_series.py::test_run_sharded_lane_series_can_preserve_configured_worker_state_root
```

Benchmark command:

```powershell
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_lanes.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --run-environment-label home_300mb `
  --reusable-pipeline-mode serial `
  --preserve-worker-state-root
```

Interpretation gates:

| Observation | Conclusion | Next action |
|---|---|---|
| Soak batch 1 Free recovers toward `run07_current` and batch 2 stays healthy | `run15_current` was likely first-batch warmup / cold worker-state cost | Keep the probe as evidence; consider making a deliberate warmup phase part of future ceiling validation, but do not promote until a second discriminating shape confirms it |
| Soak batch 1 still regresses while batch 2 recovers | Warmup alone is insufficient; order/cohort or profile assignment is still likely | Add the smallest ordering probe next, preferably a configurable first-dispatch rotation rather than another full same-shape repeat |
| Both soak batches regress | The issue is broader steady-state command/retry latency or environment pressure | Return to source-content command/retry attribution; keep `run07_current` as baseline |
| Smoke is partial or invalidated | The warmup probe failed before it became throughput evidence | Investigate the first invalidation; do not compare VPH |

Completed run:

- `fresh_state_3plus3_extract_schema_warmup_state_run01_current` completed `status=ok` and `throughput_valid=true` at `1508.94` combined soak hot-path VPH with `745/55/800`; smoke stayed valid at `1511.39` with `688/112/800`, so the warmup path did not lift the soak meaningfully above smoke. Free batch 1 improved from `617.12` VPH in smoke to `857.17` in soak, but Free batch 2 dropped from `1219.92` to `784.36`, and Pro batch 2 also fell from `2090.79` to `898.29`. The probe therefore does not support a simple warmup-state explanation; the remaining signal still looks broader than a first-batch-only artifact.

### Offline Attribution Packet

Artifacts inspected:

- [`fresh_state_3plus3_extract_schema_control_run07_current/sharded_lane_series_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run07_current/sharded_lane_series_summary.json)
- [`fresh_state_3plus3_extract_schema_control_run15_current/sharded_lane_series_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run15_current/sharded_lane_series_summary.json)
- [`fresh_state_3plus3_extract_schema_warmup_state_run01_current/sharded_lane_series_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current/sharded_lane_series_summary.json)
- [`fresh_state_3plus3_extract_schema_control_run15_current/soak/a_hominidae_pro/benchmark_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run15_current/soak/a_hominidae_pro/benchmark_summary.json)
- [`fresh_state_3plus3_extract_schema_control_run15_current/soak/troup_hominidae_free/benchmark_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run15_current/soak/troup_hominidae_free/benchmark_summary.json)
- [`fresh_state_3plus3_extract_schema_warmup_state_run01_current/smoke/troup_hominidae_free/benchmark_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current/smoke/troup_hominidae_free/benchmark_summary.json)
- [`fresh_state_3plus3_extract_schema_warmup_state_run01_current/soak/a_hominidae_pro/benchmark_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current/soak/a_hominidae_pro/benchmark_summary.json)
- [`fresh_state_3plus3_extract_schema_warmup_state_run01_current/soak/troup_hominidae_free/benchmark_summary.json`](../../.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_warmup_state_run01_current/soak/troup_hominidae_free/benchmark_summary.json)

Comparison:

| Run | Combined VPH | Pro `cmd / idle / age` | Free `cmd / idle / age` | Status counts |
| --- | --- | --- | --- | --- |
| `run07_current` | `3291.38` | `2014.284 / 43.476 / 98.644` | `3027.095 / 43.264 / 151.637` | Pro `ready=398`, `command_failed=1`; Free `ready=346`, `command_failed=3` |
| `run15_current` | `2205.73` | `3354.524 / 310.186 / 319.703` | `5193.556 / 692.818 / 377.993` | Pro `ready=382`, `command_failed=18`, `source_age_cliff=15`; Free `ready=369`, `command_failed=24`, `source_age_cliff=26` |
| `warmup_state_run01_current` | `1508.94` | `10101.908 / 1019.662 / 489.526` | `8565.125 / 1153.025 / 496.703` | Pro `ready=364`, `command_failed=30`, `source_age_cliff=27`; Free `ready=381`, `command_failed=32`, `source_age_cliff=12` |

Batch-window notes:

- `run15_current` soak batch 1 carried the regression on both lanes, but batch 2 recovered sharply. Pro moved from `184/16/200` with `source_ready_age_s_max=202.327` and `content_fetch_command_elapsed_s_total=2836.882` to `198/2/200` with `source_ready_age_s_max=319.703` and `content_fetch_command_elapsed_s_total=517.642`; Free moved from `171/29/200` with `source_ready_age_s_max=377.993` and `content_fetch_command_elapsed_s_total=4953.396` to `198/2/200` with `source_ready_age_s_max=66.43` and `content_fetch_command_elapsed_s_total=240.16`.
- `warmup_state_run01_current` did not recover from smoke to soak. Free smoke batch 1 / batch 2 were `143/57/200` and `198/2/200`, while soak batch 1 / batch 2 were `195/5/200` and `186/14/200`; soak also kept `source_age_cliff` and `command_failed` pressure in batch 2. Pro soak batch 2 also worsened relative to batch 1, from `192/8/200` to `172/28/200`.
- `run07_current` remains the clean comparator: Pro soak batch 1 / batch 2 were `199/1/200` and `199/1/200`; Free soak batch 1 / batch 2 were `148/52/200` and `198/2/200`, with far lower `source_ready_age_s_max` and `content_fetch_command_elapsed_s_total` than either later run.
- Retry-drain skip evidence is not the driver here: `shared_retry_*` totals are `0` across all three runs, and the newer summaries report `retry_queue_drain_skipped_count_total=0` where that field exists. The visible retry pressure is instead the high `content_fetch_retry_queue_sleep_elapsed_s_total` and the elevated `source_age_cliff` / `command_failed` counts.

Conclusion:

- Warmup-state preservation did not explain the run15 regression. The warmup run is worse than both `run15_current` and `run07_current`, and smoke/soak are nearly flat overall.
- Best fit is broader command/retry latency with cohort/order noise. Preserved-state/source-age carryover is not a fix and may be part of the problem, but it is not the sole explanation.
- The next discriminating action is offline reducer/log attribution across the three runs, focused on source-age cliffs, retry sleeps/drain fields, command latency, and batch/cohort assignment. Do not rerun the benchmark shape.

### Attempted Home Shared-Retry Probe

`fresh_state_3plus3_extract_schema_shared_retry_run01_current` completed cleanly, but it is not discriminating evidence for shared retry. The run finished `status=ok`, `throughput_valid=true`, `run_environment_label=home_300mb`, and `worker_shape_signature=3+3` at `1749.44` combined hot-path VPH with `644/156/800`. Pro remained comparatively healthy at `396/4`, while Free collapsed to `248/152` and soak Free batch 2 fell to `99/101`.

The key attribution result is configuration, not throughput: all `shared_retry_*` totals are still `0`, and the lane-process env snapshot did not include a shared-retry override. The benchmark wrapper's policy default forced `YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED=false` for `notebooklm_route_plus_fallback_30s_1w` unless `run_environment_label=hotel_wifi`, so the home-network command did not actually exercise the intended shared-retry path.

Harness fix:

- `bin/csf-fallback-crossover-benchmark` now honors `YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED` after applying policy defaults.
- `csf/sharded_lane_series.py` now records the benchmark shared-retry override, the resolved NLM shared-retry env var, and the shared retry DB path in `lane_process.json`.

If this branch is reopened, do not rerun the old command. Create a run-specific lane config whose lane `env` includes:

```json
{
  "YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED": "true",
  "YTIS_NLM_SHARED_RETRY_POOL_DB_PATH": "P:/packages/yt-is/.logs/sharded_lane_series/<run-name>/nlm_shared_retry_pool.sqlite"
}
```

Then verify the generated `lane_process.json` env snapshot shows `YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED=true` before interpreting throughput.

`fresh_state_3plus3_extract_schema_shared_retry_run02_current` confirms the corrected activation path but is still not throughput evidence. The smoke phase produced `status=partial`, `throughput_valid=false`, `run_environment_label=home_300mb`, and `worker_shape_signature=3+3`; no soak artifact was produced. Shared retry was active in the lane env snapshots and summaries: combined smoke reported `shared_retry_deferred_count_total=6`, `shared_retry_recovered_count_total=3`, `shared_retry_final_failed_count_total=0`, and `shared_retry_processed_count_total=4`.

Shared-retry accounting note: judge partial status from primary processed count, not raw processed count. In `run02`, Pro was partial because `processed_count_total=400` and `shared_retry_processed_count_total=1`, leaving `399/400` primary processed; Free was OK because `403-3=400/400` primary processed. Treat shared-retry work as separate recovery work, not as primary throughput. This matches the existing sharded-lane accounting contract, so do not relax the top-level partial gate. The lower-level runner now continues primary selection when completed shared-retry worker work would otherwise consume the requested lane limit; regression coverage lives in `tests/test_csf_source_fetch_timing.py::test_cmd_fetch_limit_counts_primary_items_when_shared_retry_processes_work`.

`fresh_state_3plus3_extract_schema_shared_retry_run03_current` is not benchmark evidence. The delegated process orphaned during smoke shortly after `benchmark_policy_started` / auth-family refresh start: both `lane_process.json` files stayed at `status=running`, the recorded PIDs were gone, and no smoke, soak, or top-level summary was written. The stale-lane guard now classifies this shape as `orphaned_lane_process` instead of leaving it looking live forever.

`fresh_state_3plus3_extract_schema_shared_retry_run04_current` ran after the stale-lane guard and lower-level primary-limit fix. Smoke stopped as `status=partial`, `throughput_valid=false`, `invalidated=false`, `worker_shape_signature=3+3`, and `run_environment_label=home_300mb`; no soak artifact was produced. Activation is confirmed in both lane `lane_process.json` snapshots (`YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED=true`, `YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED=true`, and the run04 shared retry DB path). Combined smoke processed `871` raw outcomes with `745` hot-path successes, `126` failures, and shared retry totals `deferred/recovered/final_failed/processed=112/71/0/84`. Pro completed its subprocess but stayed partial at `400/34/434` raw with `41` shared-retry processed, leaving `393/400` primary processed; Free stayed partial at `345/92/437` raw with `43` shared-retry processed, leaving `394/400` primary processed. Browser health was degraded because unexpected Chrome processes exceeded the soft budget (`68` unexpected, default profile count `0`), but the partial gate was driven by primary processed shortfall rather than browser-health invalidation. Treat run04 as activation/accounting evidence only, not throughput evidence.

`fresh_state_3plus3_extract_schema_shared_retry_run05_current` ran after the scan-limit boundary wait fix. The persisted smoke summary still says `status=partial` and `throughput_valid=false`, but the artifact exposed a second accounting bug in the lane completeness gate rather than a real primary-processing shortfall. Each lane's batch summaries discovered the full `200` primary items per batch. The old gate subtracted `shared_retry_processed_count_total`, which counts shared retry claims, including entries that were re-deferred and did not become final outcomes. Corrected accounting subtracts only final shared-retry outcomes (`shared_retry_recovered_count_total + shared_retry_final_failed_count_total`) from raw `success+fail+skip`. Under that corrected gate, Pro is `436 - 36 = 400/400` primary outcomes and Free is `442 - 42 = 400/400` primary outcomes. The run remains smoke-only because the original sequence aborted before soak, and the measured smoke is still very poor (`819.05` combined VPH, `422/456/878` raw, Pro `source_age_cliff=183`, Free `source_age_cliff=66`), so do not promote it as throughput evidence. Use it as the evidence that the shared-retry lane gate must subtract outcomes, not claims; regression coverage is in `tests/test_sharded_lane_series.py::test_lane_processed_count_reason_subtracts_shared_retry_outcomes_not_claims`.

`fresh_state_3plus3_extract_schema_shared_retry_run06_current` is the first full shared-retry smoke+soak artifact after both accounting fixes. It completed `status=ok`, `throughput_valid=true`, `worker_shape_signature=3+3`, and `run_environment_label=home_300mb`, but it is a strong negative throughput result. Smoke landed at `701.87` combined VPH with `479/421/900` raw and shared retry `deferred/recovered/final_failed/processed=252/76/24/112`. Soak landed at `585.40` combined VPH with `372/526/861` raw and shared retry `25/29/32/64`. Pro soak collapsed to `129.56` VPH with `63/393/440`, `primary_outcomes=400`, `source_age_cliff=103`, and `32` final shared-retry failures in batch 2; Free soak reached `566.99` VPH with `309/133/421`, `primary_outcomes=400`, and `source_age_cliff=42`. Follow-up artifact inspection separated two effects in Pro soak batch 2: the `32` final failures came from shared-retry drain residuals, while the `200` primary failures had empty content-fetch metrics because reusable source-add shortfalls were not being counted as `source_add_failed` in that path. That gap was isolated to Pro soak batch 2; every other run06 smoke/soak lane batch had non-empty content-fetch counts, subbatch metrics, and nonzero add/materialization timing. Browser health was degraded before smoke (`66` unexpected Chrome processes, default profile count `0`), but the run itself stayed valid. This closes shared retry as a ceiling branch under current behavior: it validates activation and accounting, but the shared-pool/retry-window path worsens source-age pressure and final failures rather than recovering the home-network baseline; future reusable source-add shortfalls are attributed as `source_add_failed`, and metric-level `source_add_failed` invalidates sharded-lane evidence rather than being treated as normal throughput failure.

Current non-live ranking after comparing `run07_current`, `run15_current`, `warmup_state_run01_current`, and the non-empty-metric portions of `shared_retry_run06_current`: the strongest remaining explanation is source-age / retry-window pressure with command latency as the visible mechanism. `source_age_cliff` rises from `0` in `run07_current` to `41` in `run15_current`, then `96/39` in warmup smoke/soak, then `155/145` in run06 smoke/soak; command elapsed and worker idle rise in the same direction. Command latency alone is the second hypothesis, cohort/order noise is third, shared-retry drain mechanics are local to run06, and source-add collapse is limited to the isolated Pro soak batch-2 hole. The next non-live gate is a reducer/audit pass that splits `source_age_cliff`, `content_fetch_command_elapsed_s_total`, and `worker_idle_wait_s_total` by lane and batch across those same artifacts; accept the pressure hypothesis if the same lane/batch cohorts carry the age and command spike, and reject it if the pressure is evenly distributed.

That non-live gate is now complete in `docs/operations/sharded-lane-artifact-audit.md` Table 9. It accepts the source-age / retry-window pressure hypothesis: the spike is concentrated in batch-1 cohorts rather than evenly distributed. `smoke/a_hominidae_pro/batch_01`, `soak/a_hominidae_pro/batch_01`, and `soak/troup_hominidae_free/batch_01` carry the clearest `source_age_cliff`, command elapsed, and worker-idle growth across the compared artifacts. Batch 2 is secondary for source-age pressure and mostly matters for command/idle load, except `shared_retry_run06_current` Pro soak batch 2, which remains an unusable source-add/accounting hole because failures are nonzero while content-fetch status counts are empty.

The next cheapest discriminating probe is config-only source-age cadence on the same extract-schema `3+3` home-network universe, prepared as `fresh_state_3plus3_extract_schema_source_age_cadence_run01_lanes.json`. This is not a ceiling candidate: prior cadence evidence eliminated cliffs but stayed slow, so promote it only if it both reduces batch-1 `source_age_cliff` / command elapsed / worker idle and moves combined hot-path VPH back toward `run07_current`. Use it to decide whether a smarter first-window source-age mechanism is worth coding. If it lowers cliffs while VPH remains near the warmup/shared-retry negatives, do not tune cadence further; pivot to a narrower first-window policy in `NLMReusableIngestor.process_batch` / `_select_source_age_cadence_window_size`.

`fresh_state_3plus3_extract_schema_source_age_cadence_run01_current` passed that gate. It completed `status=ok`, `throughput_valid=true`, `worker_shape_signature=3+3`, and `run_environment_label=home_300mb`; all four lane-process snapshots confirmed `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED=true` with `160/190/5` thresholds. Soak landed at `3636.16` combined hot-path VPH with `793/7/800`, above `run07_current` (`3291.38`) and far above `run15_current` (`2205.73`), `warmup_state_run01_current` (`1508.94`), and `shared_retry_run06_current` (`585.40`). The pressure gate improved directly: soak had no `source_age_cliff` in either lane, Pro fell to `content_fetch_command_elapsed_s_total=904.651`, `worker_idle_wait_s_total=57.746`, `source_ready_age_s_max=109.925`, and Free fell to `1748.126`, `210.416`, `160.247`. Batch-1 pressure also dropped versus the negative samples; the remaining tail is Free soak batch 1 (`199/1`, command `1359.805`, idle `210.416`, age max `160.247`, `command_failed=16`). Treat cadence as the current live same-shape leader and the next VPH branch as optimizing or narrowing the first-window cadence policy, not reopening shared retry or warmup-state probes.

The first-window policy refinement is now implemented behind `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE`, defaulting to `0` so the `run01` leader remains unchanged. The next prepared probe is `fresh_state_3plus3_extract_schema_source_age_cadence_first_window_run02_lanes.json`, which keeps the proven `160/190/5` cadence but caps fresh no-age first cadence windows at `25`. This should be interpreted only against `source_age_cadence_run01_current`: promote it if it preserves `throughput_valid=true`, keeps `source_age_cliff` absent, and reduces the Free soak batch-1 command/idle tail without dropping combined VPH below `run07_current`; reject it if the added first-window overhead lowers VPH or simply moves the tail to later windows.

`fresh_state_3plus3_extract_schema_source_age_cadence_first_window_run02_current` rejected that branch. It completed `status=ok`, `throughput_valid=true`, `worker_shape_signature=3+3`, and `run_environment_label=home_300mb`; all four lane-process snapshots confirmed cadence `160/190/5` plus `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE=25`. The first-window cap improved the originally targeted Free soak batch 1 from `run01` (`199/1`, command `1359.805`, idle `210.416`, age max `160.247`, `command_failed=16`) to `199/1`, command `451.096`, idle `0.000`, age max `68.335`, and no `command_failed`, but the total run regressed to `2140.40` combined hot-path VPH with `639/161/800`, `source_age_cliff=3`, and fail rate `20.12%`. The pressure moved instead of disappearing: Free soak batch 2 fell to `193/7`, command `1210.237`, idle `230.157`, age max `367.065`, and `source_age_cliff=3`; Pro soak batch 1/2 also fell to `100/100` and `147/53`.

The offline source-content packet `source_content_failure_event_packet_run01_vs_run02_current` splits that regression by mechanism. Pro soak batch 1 is not a source-content retry or age-cliff failure: worker accounting reports `100/100/200`, but reusable-process accounting reports `100/0/200`, the cadence windows selected and added only `100/100`, and two source-add problem events attempted `50` URLs with `0` added (`could_not_add_url_sources=2`). Pro soak batch 2 is mixed but still source-add/window dominated (`50/0` problem URLs plus only `3` command failures and no `source_age_cliff`). Free soak batch 2 is the actual source-age-pressure case (`source_age_cliff=3`, max ready age `367.065`). Treat this as a negative result for first-window capping and as positive evidence that small fresh first windows increase source-add churn/accounting holes. Keep `source_age_cadence_run01_current` as the current live same-shape leader, leave the cap defaulted off, and do not run another first-window-cap live probe without a source-add/failure-accounting mechanism change.

The follow-up harness fix closes the remaining empty-metrics hole in that attribution path: reusable notebook-create failure now emits `source_add_failed` counts for all requested inputs, so future `notebook_create_failed` worker failures will invalidate evidence instead of looking like throughput-valid empty fetch metrics.

`fresh_state_3plus3_extract_schema_source_age_cadence_first_window_post_accounting_run03_current` was launched only after that accounting fix and is not throughput evidence. Smoke aborted before soak with `status=partial`, `throughput_valid=false`, and combined diagnostic VPH `1033.98` on `572/128/700`; the proximate invalidation is Pro smoke batch 1 worker-03, which logged `nlm_batch_source_materialization_wait_failed` after waiting `606.057s` for sources to materialize (`expected_total=38`, `sources=14->14`) and then returned `NotebookSourceMaterializationTimeout`. Follow-up artifact inspection found the code-path bug: after a capacity rotation reset the notebook to `14` sources, `_add_sources_in_subbatches()` still passed the cumulative batch position as `expected_total` (`38`) instead of the current notebook count plus the new subbatch (`14`). The sharded-lane guard now treats both the materialization-wait failure event and the wrapper `fetch_worker_finished` terminal error as invalidating evidence, and the expected-total calculation has regression coverage. Treat run03 as a materialization/accounting bug sample, not as another first-window-cap VPH sample.

`fresh_state_3plus3_extract_schema_source_age_cadence_first_window_post_rotation_fix_run04_current` closes the post-fix validation as a valid negative. The guarded sequence completed smoke and soak with `status=ok`, `throughput_valid=true`, `worker_shape_signature=3+3`, and `run_environment_label=home_300mb`; all lane configs kept cadence `160/190/5` plus `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE=25`. Smoke was valid at `2263.57` combined VPH with `781/19/800`, but soak regressed to `1986.14` with `642/158/800`, far below `source_age_cadence_run01_current` at `3636.16`. Pro soak landed at `1092.76` with `327/73/400`, `content_fetch_command_elapsed_s_total=2382.897`, `worker_idle_wait_s_total=387.714`, and `source_ready_age_s_max=243.673`; Free soak landed at `1519.76` with `315/85/400`, `content_fetch_command_elapsed_s_total=4776.196`, `worker_idle_wait_s_total=122.872`, `source_ready_age_s_max=276.884`, and `source_age_cliff=13`. The materialization-target fix made the branch valid, but the first-window cap still fails the promotion gates and should remain default-off.

Offline run01-vs-run04 attribution explains why the branch should stay closed: the cap increased source-window/materialization churn instead of removing age pressure. Run01 soak used cadence only; run04 added the first-window cap and raised total window count from `8 -> 13` on Pro and `8 -> 14` on Free. The materialization wait/add path also expanded (`13 -> 23` waits and `15 -> 26` add operations on Pro, `14 -> 24` waits and `15 -> 28` add operations on Free), with age-guard rotations rising from Pro `0 -> 4` and Free `1 -> 3`. The sharp symptoms then split by lane: Free soak batch 1 carried the source-age recurrence (`source_age_cliff=13`, command elapsed `4437.752`), while Pro soak batch 2 carried a command-latency tail (`1452.331`, age max `243.673`). This makes first-window capping a churn amplifier under current behavior, not a VPH path.

The durable command-latency packet in `.logs/sharded_lane_series/command_latency_attribution_packet_current.{md,json}` separates command time from retries and probes. From run01 to run04, soak content-fetch command time rose `2652.777 -> 7159.093` (`+4506.316s`) while command count fell `991 -> 962`; retry sleep plus queue sleep rose only `622.000 -> 780.826` (`+158.826s`), source-list probes rose `43.814 -> 449.312` (`+405.498s`), and readiness-probe time stayed `0`. Free batch 1 accounts for `+3077.947s` of the command delta and Pro batch 2 for `+1137.601s`. This supports old-window command latency as the dominant measured regression, with source-list and retry pressure as secondary contributors. Run04 also began with degraded browser health while run01 began clean, so browser state remains a recorded confound rather than a proven causal explanation.

Per-command event reconciliation covers `98.89%` of run01 and `98.13%` of run04 command counts. It sharpens the mechanism: attempt-1 elapsed rose only `2191.209 -> 2563.993`, while retry-attempt elapsed rose `437.802 -> 4503.921`, accounting for `4066.119s` (`91.6%`) of the measured command-time delta. Run04 had `36` commands / `2014.330s` that launched below the `190s` cadence hard threshold and completed beyond it; `21` / `1245.514s` were retry attempts. A plain current-age recheck would miss these because crossed-hard retries launched at about `160s` on average. The targeted code candidate therefore projects the next local retry's completion age from current age, planned sleep, and the prior attempt's measured duration; if that projection reaches the `200s` source-age cliff, it skips the local retry and same-notebook retry queue with `retry_queue_skipped_reason=projected_local_retry_completion_age_cliff`.

`fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run05_current` exercised that guard in a clean-browser-health `3+3` sequence. Its persisted summaries say smoke `2701.11` VPH (`751/49/800`) and soak `3056.57` VPH (`744/56/800`), and the new projection reason fired once in soak at projected completion age `204.583s`. The artifact is not valid throughput evidence, however: Free soak batch 1 worker-01 emitted `nlm_batch_source_mapping_failed` (`46` title matches, `4` order fallbacks, `canonical_source_id_count=0`, `expected_source_id_count=50`) and then returned `0/50` with no fetch commands. The old sequence gate missed that hard failure and incorrectly left `throughput_valid=true`; both lane and standalone evidence scanners now reject the event unconditionally. Treat run05 only as proof that the projection guard is live. It does not displace `source_age_cadence_run01_current`, and its single activation is insufficient to claim a VPH effect.

The config surface does not expose a lower-churn variant of the first-window cap. `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE=0` preserves the run01 behavior, while positive values only cap the first fresh no-age window and mainly change the number of cadence windows. Prior readiness-probe evidence also stayed diagnostic or negative rather than becoming a ceiling path. Do not run another first-window-cap or readiness-probe live experiment. Keep `source_age_cadence_run01_current` as the live leader. Do not rerun the local-retry projection shape until the source-mapping/dead-notebook-recovery path that produced run05's empty-fetch `0/50` outcome has a concrete fix or an explicit diagnostic goal.

Historical first-window cap command:

```powershell
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_first_window_run02_lanes.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_first_window_run02_current `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --run-environment-label home_300mb `
  --reusable-pipeline-mode serial `
  --preserve-worker-state-root
```

Before interpreting throughput, confirm all four lane `lane_process.json` env snapshots show `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED=true`, the default `160/190/5` cadence thresholds, and `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE=25`.

Historical run01 command:

```powershell
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_run01_lanes.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_run01_current `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --run-environment-label home_300mb `
  --reusable-pipeline-mode serial `
  --preserve-worker-state-root
```

Before interpreting throughput, confirm both lane `lane_process.json` env snapshots show `YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED=true` and the default `160/190/5` cadence thresholds.

Required report fields:

- Top-level `status`, `throughput_valid`, `worker_shape_signature`, `run_environment_label`, and `pre_run_browser_health`.
- Combined `hot_path_videos_per_hour`, `hot_path_success_count_total`, `fail_count_total`, and `processed_count_total`.
- Per-lane `content_fetch_command_elapsed_s_total`, `worker_idle_wait_s_total`, `source_ready_age_s_max`, and `content_fetch_status_counts_total`.
- Batch-tail rows for smoke and soak, especially Free `batch_01` vs `batch_02`.
- Reducer comparison against `fresh_state_3plus3_extract_schema_control_run07_current` and `fresh_state_3plus3_extract_schema_control_run15_current`.

Agecap-200 Revalidation Branch — CLOSED:

- `sweep_phase3_2lane_3w_agecap_200_run02` was revalidated as `sweep_phase3_2lane_3w_agecap_200_run03_current` under current code and instrumentation. All three promotion gates failed: combined hot-path VPH `1382.39` (threshold `2800`), fail rate `47.25%` (threshold `2%`), and `source_age_cliff` was dominant (`138/189` failures = `73%`). The run02 result of `3084.08` VPH was not reproduced. No further agecap-200 live probe will run until there is a code or source-readiness mechanism change.
- `fresh_state_3plus3_source_age_cadence_run05` eliminated `source_age_cliff` as a failure mode (`0` cliff events out of `800` items) and reached `1829.83` VPH, but that is well below the agecap-200 run02 control and far below the historical `3+3` ceiling of `4123.28`. Cadence is confirmed as stability/diagnostic work, not a ceiling path.
- Pivot next analysis back to the strongest clean control: `sweep_phase3_2lane_3w_run01` at `4123.28` combined hot-path VPH. Do not run another live probe on agecap-200, cadence tuning, or worker-balance geometry without a code-path or source-readiness mechanism change.
- Artifact-wide contract normalization is now documented in `docs/operations/sharded-lane-artifact-audit.md`. Recomputing all 10 audited runs from `combined.hot_path_success_count_total / elapsed_s * 3600` exactly reproduces the published VPH values. Old-format high-VPH artifacts remain `wall-equivalent` rather than exact current-contract replays because `combined.throughput_elapsed_s` is absent, but the metric-contract denominator alone does not explain the historical-to-current throughput drop.

Do not do next:

- Do not run more extract-window sizes.
- Do not widen the source-age cliff.
- Do not change auth TTL.
- Do not switch captioned items to `yt-dlp` without a same-shape A/B.
- Do not run a new worker-count geometry sweep until this attribution run identifies a code-path reason to do so.
- Do not rerun the worker-balance branch as a ceiling candidate without a new worker-profile, cleanup, or `nlm` fallback code change.
- Do not rerun agecap-200 as a live probe without a code or source-readiness mechanism change.
- Do not reopen metric-contract normalization as the primary explanation for the throughput gap unless new raw fields show cleanup/reap time outside the old artifacts' recorded wall span.
