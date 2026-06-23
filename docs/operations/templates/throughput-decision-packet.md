# Throughput Decision Packet

Completed packet for the retry-queue primary-command projection validation. Historical example only: the live benchmark has already been exercised once and closed as a negative smoke-gated branch, so this document serves as a post-run decision record and not as authorization to rerun the branch.

If a no-patch design packet already exists for the branch, or if the proposed lever is not narrower than the current projection/retry guard path, do not use this template to reopen it; create or update the design packet first.

## Decision

- Decision: `historical post-run record`
- Agent: Codex
- Date: 2026-06-22
- Branch or hypothesis name: `fresh_state_3plus3_extract_schema_primary_command_projection_60_retry_queue_fix_run01`

## Current Authority

- Latest plan section: `docs/operations/hot-path-throughput-next-test-plan.md` current status around the primary-command projection branch and the retry-queue follow-up note
- Latest registry row: `Account sharding | Pro+Free fresh-state 3+3 source-age primary-command projection 60s live probe`
- Observability checklist reviewed: `yes`
- Contract reviewed: `yes`

## Metric Authority

- Headline metric: `combined.hot_path_videos_per_hour`
- Owning artifact: `sharded_lane_series_summary.json`
- Excluded metrics: Whisper fallback throughput, fallback-lane time, and any non-hot-path recovery throughput
- Diagnostic-only counters: `source_age_cliff`, `command_failed`, `worker_idle_wait_s_total`, `content_fetch_command_elapsed_s_total`, `retry_queue_skipped_reason`, `projected_primary_command_completion_age_s`, `projected_primary_command_completion_age_with_margin_s`

## Baseline And Candidate

- Historical high-water mark artifact: `P:/packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v1/sharded_lane_series_summary.json`
- Historical high-water mark VPH: `5572.04`
- Current control artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current/soak/sharded_lane_series_summary.json`
- Current control VPH: `3788.53`
- Candidate artifact or proposed output root: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_retry_queue_fix_run01_current`
- Candidate environment label: `home_300mb`
- Same environment as control: `yes`
- Same cohort/sample/limit as control: `yes`
- Same worker shape as control: `yes`

## Raw Evidence Inspected

- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current/smoke/sharded_lane_series_summary.json`
- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current/soak/sharded_lane_series_summary.json`
- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run08_current/sharded_lane_series_summary.json`
- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/command_latency_attribution_packet_run01_vs_run08.md`
- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/source_content_timeline_packet_run01_vs_run08.md`
- Artifact: `P:/packages/yt-is/.logs/sharded_lane_series/primary_command_age_projection_packet_projection60_vs_margin20_run03_smoke.md`
- Artifact: `P:/packages/yt-is/docs/operations/sharded-lane-artifact-audit.md`

## Hypothesis

- One-sentence hypothesis: skipping retries whose projected retry-ready age plus primary-command projection or margin would cross the cliff will reduce batch-1 old-window command latency enough to preserve or improve sustained hot-path VPH versus the current control.
- Failure mode being tested: the retry queue still sleeps into NotebookLM work that is already destined to cross the source-age cliff, wasting time that should be avoided up front.
- Why this lever is active in the latest evidence: run08 closed the earlier local-retry projection branch as negative, the row-level projection packet showed residual retry-drain source-age pressure in batch 1, and the new code path adds a narrower retry-queue gate exactly on that retry/projection boundary.
- Why offline analysis is insufficient: the guard exists in code and tests now, but only one live run can prove whether the narrower queue gate actually reduces real batch-1 command latency without regressing smoke or soak throughput.

## Falsifier

- Result that falsifies the hypothesis: the validation run does not show the new projected-primary-command skip path in live artifacts, or it fails to improve/hold sustained throughput versus the current control.
- Metric threshold: soak `combined.hot_path_videos_per_hour` must be greater than `3788.53`.
- Failure/status threshold: any `partial`, `invalidated`, or `blocked_before_soak` result, or any smoke run with `source_age_cliff > 0` or `fail_count_total > 0`, falsifies the branch.
- Environment or harness condition that invalidates the run: wrong worker shape, wrong environment label, missing primary-command projection env snapshot, stale default-profile/browser health, or a lane config that does not point at fresh worker-state roots for this branch.

## Early-Abort Gates

- Maximum wait before status review: 20 minutes after worker launch, then classify from the first smoke artifact that lands.
- Stop if smoke shows: any `source_age_cliff`, any `command_failed`, or combined hot-path VPH below `3000`.
- Stop if lane logs repeat: `source_add_failed`, `materialization_wait_failed`, or `NotebookSourceMaterializationTimeout` in batch 1 after the retry-queue change, because that means the old hot path is still burning time on a different bottleneck.
- Stop if browser/profile/worker-shape/environment gate shows: unexpected worker-shape drift, non-home environment label, any default NotebookLM profile left open, or missing `YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S=60.0` in the lane env snapshot.
- Stop if no summary appears by: 20 minutes after start for smoke, or if soak cannot begin immediately after a passing smoke.

## Exact Run Command

```powershell
# Copy the current control lane config to a fresh retry-queue validation config and
# rewrite worker_state_root to match the new run root before launching.
python P:/packages/yt-is/bin/csf-sharded-lane-sequence `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_retry_queue_fix_run01_lanes.json `
  --run-root P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_retry_queue_fix_run01_current `
  --smoke-limit 400 `
  --smoke-batch-size 200 `
  --soak-limit 400 `
  --soak-batch-size 200 `
  --expected-worker-shape 3+3 `
  --run-environment-label home_300mb `
  --reusable-pipeline-mode serial `
  --preserve-worker-state-root `
  --smoke-promotion-max-source-age-cliff 0 `
  --smoke-promotion-max-fail-count 0 `
  --smoke-promotion-min-hot-path-vph 3000
```

## Promotion Rule

- Promote only if: smoke passes the documented gates, soak stays `status=ok` and `throughput_valid=true`, the soak VPH beats `3788.53`, and the live artifact shows the new primary-command projection skip reason in the retry-completed rows.
- Require paired current-control rerun: `no`
- Require second confirmation run: `no`
- Docs to update immediately after result: `docs/operations/test-registry.md`, `docs/operations/hot-path-throughput-next-test-plan.md`, `HANDOFF.md`

## Do Not Run Next

- Branches explicitly rejected by this evidence: another local-retry projection repeat, another margin25 rerun, another margin15 branch, or another same-shape run without the retry-queue primary-command projection gate present.
- Old shapes that remain closed: the local-retry projection rerun at `run08`, the margin25 neighbor probe, and the margin15 dead branch.
- Neighbor probes that require a code or harness change first: any new projection or margin sweep that changes retry-queue semantics or worker-state rooting.

## Result Classification

Fill this after the run or offline attribution:

- Status: `negative`
- Summary artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_retry_queue_fix_run01_current/sharded_lane_series_summary.json`
- Combined hot-path VPH: `2957.0`
- Success/fail/processed: `795/5/800`
- Worker shape signature: `3+3`
- Environment label: `home_300mb`
- Main failure class: `smoke promotion gate failed before soak`
- Registry row added or updated: `yes`
- Plan/handoff updated: `yes`
