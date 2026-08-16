# Throughput Decision Packet — batch-1 old-window `nlm source content` latency

Completed packet for the leading ranked hot-path lever. Verdict: **no run — packet failed**.
A no-patch design packet already exists for this branch
(`P:/packages/yt-is/.logs/sharded_lane_series/design_packet_batch1_old_window_source_content_latency_no_patch_current.md`,
decision `no patch candidate yet`), so per the contract this template is used only to
record the evaluation and the failed-packet disposition, not to reopen the branch.

## Decision

- Decision: `no run — packet failed`
- Agent: Grok Build (fresh agent, `/goal` yt-is hot-path throughput)
- Date: 2026-08-12
- Branch or hypothesis name: `batch1_old_window_source_content_latency`

## Current Authority

- Latest plan section: `docs/operations/hot-path-throughput-next-test-plan.md` — Current Status / Recommended Next Action (2026-08-12)
- Latest registry row: `docs/operations/test-registry.md` L370 — current observed leader `3788.53`, "mixed diagnostic branch rather than a promoted control"
- Observability checklist reviewed: `yes`
- Contract reviewed: `yes`

## Metric Authority

- Headline metric: `combined.hot_path_videos_per_hour` from `sharded_lane_series_summary.json`
- Owning artifact: `sharded_lane_series_summary.json` (top-level, combined object)
- Excluded metrics: Whisper fallback time, recovery counts, cleanup outside the lane-process throughput span
- Diagnostic-only counters: `source_age_cliff`, `command_failed`, `worker_idle_wait_s_total`, `content_fetch_command_elapsed_s_total`, `retry_queue_*`, `source_list_probe_elapsed_s_total`, `nlm_source_content_command_completed`

## Baseline And Candidate

- Historical high-water mark artifact: `P:/packages/yt-is/.logs/sharded_lane_series/pro_free_source_map_v1/sharded_lane_series_summary.json`
- Historical high-water mark VPH: `5572.04` — **non-reproduced** (`pro_free_source_map_v1_replay_run02` = `2109.58`), old metric contract (`wall_elapsed_s`, no `throughput_elapsed_s`). Kept separate; not the control.
- Current control artifact: `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current/sharded_lane_series_summary.json`
- Current control VPH: `3788.53` (soak; `status=ok`, `throughput_valid=true`, `worker_shape_signature=3+3`, `run_environment_label=home_300mb`, `794/6/800`). Single unpaired sample; smoke promotion gate failed; not a promoted control.
- Cleanest reproducible same-shape leader (for reference only): `fresh_state_3plus3_extract_schema_source_age_cadence_run01_current` = `3636.16` (`793/7/800`, zero `source_age_cliff`, command time `2652.777s`).
- Candidate artifact or proposed output root: none — no new code/harness mechanism exists on disk for this lever.

## Raw Evidence Inspected

- `P:/packages/yt-is/.logs/sharded_lane_series/command_latency_attribution_packet_current.md` (regenerated this session: control07 vs cadence01 vs projection60_run02 soaks; reconciliation gates 1.0/0.989/0.968)
- `P:/packages/yt-is/.logs/sharded_lane_series/design_packet_batch1_old_window_source_content_latency_no_patch_current.md` (decision: `no patch candidate yet`)
- `P:/packages/yt-is/docs/operations/sharded-lane-artifact-audit.md` (Tables 1–10; 26 runs audited; contract-normalization + worker/auth skew attribution)
- `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_primary_command_projection_60_run02_current/sharded_lane_series_summary.json` (verified this session)
- `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_run01_current/sharded_lane_series_summary.json` (verified this session)
- `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_control_run07_current/sharded_lane_series_summary.json` (verified this session)
- `P:/packages/yt-is/docs/operations/test-registry.md` (rows 625–631: projection-60, margin20/25/15, retry-queue-fix; L370 current leader)
- `P:/packages/yt-is/docs/operations/hot-path-throughput-next-test-plan.md` (Current Status + ranked hypotheses + "Do not run next")

## Hypothesis

- One-sentence hypothesis: reducing batch-1 old-window `nlm source content` command latency would raise sustained hot-path VPH above the current observed leader `3788.53`.
- Failure mode being tested: batch-1 sources age past the cliff while content commands run long (audit Table 7: the leader's Free batch_01 carries `2482.911s` of command time with retry rows at `60-119s` source age; the negative neighbors show the same pressure exploding pre-soak).
- Why this lever is active in the latest evidence: offline attribution (this session) shows the cleanest leader (cadence01) pays only `2652.777s` of command time vs `5041.379s` for the control and `4685.822s` for the projection-60 leader, so command latency remains the largest separable cost.
- Why offline analysis is insufficient: it is not insufficient — offline attribution is complete and discriminating (98.9–100% event reconciliation). No live run is justified because **no patch candidate exists** and same-shape reruns are prohibited by the contract.

## Falsifier

- Result that falsifies the hypothesis: any live rerun of this branch's shape that does not show a new mechanism active in the artifact, or whose soak `combined.hot_path_videos_per_hour` does not exceed `3788.53` with an adjacent current-control run in the same cohort/time window.
- Metric threshold: soak `combined.hot_path_videos_per_hour` must be greater than `3788.53` (the current control), with an adjacent same-window control run.
- Failure/status threshold: any `partial`, `invalidated`, or `blocked_before_soak` result falsifies the branch; any smoke with `source_age_cliff > 0` or `fail_count_total > 0` stops before soak.
- Environment or harness condition that invalidates the run: wrong worker shape (not `3+3`), missing `run_environment_label=home_300mb`, degraded browser health, or any hotel/home environment comparison as a ceiling.

## Early-Abort Gates

- Maximum wait before status review: 20 minutes after worker launch (contract default), then classify from the first smoke artifact.
- Stop if smoke shows: any `source_age_cliff`, any `command_failed`, or combined hot-path VPH below `3000` (contract default gate; `--smoke-promotion-*` flags).
- Stop if lane logs repeat: `source_add_failed`, `materialization_wait_failed`, `NotebookSourceMaterializationTimeout`/`TerminalError`, or `nlm_batch_source_mapping_failed` in batch 1.
- Stop if browser/profile/worker-shape/environment gate shows: unexpected worker-shape drift, non-home environment label, default NotebookLM profile left open, or unexpected-Chrome budget overage.
- **Not applicable this iteration:** no run is authorized; these gates bind any future run launched under a new mechanism.

## Exact Run Command

- **None. Cannot be filled.** The only candidate commands for this branch are prohibited same-shape reruns (margin/projection/geometry neighbors, cadence repeats). Contract rule: "No same-shape reruns ... unless (a) code changed and (b) offline attribution shows that exact lever is still active." No code change exists for this lever (design packet: `no patch candidate yet`; `git diff` of `csf/nlm_batch.py`, `csf/nlm_config.py`, `csf/sharded_lane_series.py` shows only the already-falsified source-add knobs and invalidation hardening, no new hot-path mechanism).

## Promotion Rule

- **Cannot be satisfied.** Promotion would require a new mechanism whose soak VPH beats `3788.53` with an adjacent current-control run, but the current control itself is a single unpaired sample and "Do not promote a candidate from a single unpaired run when current-control variance is unresolved" applies. The design packet requires any future patch to be narrower than the existing projection/retry guard path and to pass its "Exact Proposed Test Coverage" layer first.

## Do Not Run Next

- Branches explicitly rejected by this evidence: any same-shape rerun of projection-60, margin20/25/15, retry-queue-fix, first-window-cap, rotation-180, shared-retry, warmup-state, auth-interval, worker-balance, agecap-200, profile-swap, or browser-default (all have registry rows with negative/invalidated/closed classifications).
- Neighbor probes that require a code or harness change first: any new projection/margin/retry-queue mechanism; the design packet's candidate-mechanism table lists all four ranked hypotheses as `No` patch candidates.

## Result Classification

- Status: `no run — packet failed`
- Reason: "Exact Run Command" and "Promotion Rule" unfillable (no new mechanism; same-shape reruns prohibited; no-patch design packet governs the branch).
- Registry row added or updated: `yes` — `no run — packet failed` rows per lever family + ceiling-classification row (see `docs/operations/test-registry.md`, "Hot-path throughput loop outcome (2026-08-12)").
- Plan/handoff updated: `yes`
