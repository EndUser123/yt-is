# Sharded Lane Artifact Audit

_Generated: audit_sharded_lane_runs.py — 0 runs audited_

_Run root: `P:\packages\yt-is\.logs\sharded_lane_series`_


## Table 1 — Sorted by Combined VPH (Descending)

| Run | Environment | Geometry | Status | Throughput Valid | Contract | Limit | Combined VPH | Success/Fail/Processed | Fail Rate | Pro VPH | Free VPH | source_age_cliff | command_failed | worker_idle_wait_s | Pre-Run Health | Post-Run Hygiene |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Table 2 — Grouped by Metric Contract


## Table 3 — Contract Normalization Check

This table recomputes combined VPH from the reducer-compatible formula: `combined.hot_path_success_count_total / elapsed_s * 3600`. Current artifacts use `combined.throughput_elapsed_s` when present. Older artifacts usually lack that field, so they can only be recomputed as wall-equivalent from `combined.finished_at-started_at`.

| Run | Contract | Original VPH | Recomputed VPH | Delta | Denominator (s) | Denominator Source | Confidence | Absent Fields |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

**Normalization result**: the old high-VPH artifacts do not collapse under the reducer-compatible wall recomputation; their recomputed VPH values match the published values. What remains unproven for old artifacts is whether the newer parent Chrome reap / worker cleanup boundary would add time outside the recorded wall span. The metric-contract difference alone is therefore not enough to explain the drop from the historical ceiling to current runs.

## Table 4 — Sorted by max(source_ready_age_s_max) Descending

| Run | Pro max (s) | Free max (s) | Combined max (s) | source_age_cliff | command_failed | Combined VPH | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Table 5 — Failure Mode Table (source_age_cliff desc, then command_failed desc)


### Sorted by source_age_cliff descending

| Run | source_age_cliff | command_failed | Combined Fail Count | Combined VPH | Fail Rate | cliff% of combined fails |
| --- | --- | --- | --- | --- | --- | --- |

### Sorted by command_failed descending

| Run | command_failed | source_age_cliff | Combined Fail Count | Combined VPH | Notes |
| --- | --- | --- | --- | --- | --- |

## Table 6 — Retry Queue Window (drain_ready_age desc, then sleep total desc)

| Run | Retry Windows | Local Deferred/Recovered/Final Failed | Drain Skips | Drain Skip Reasons | Shared Deferred/Recovered/Final Failed | Primary Queued | Projected Skip Reasons | Max Projected Retry Age | Max Projected+Margin Age | Max Retry Age Margin | Retry Pass Statuses | Drain Ready Age Max | Retry Wait Max/Count | Queue Sleep Total | Combined VPH | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Table 7 — Content-Fetch Command Latency (total desc, then avg desc)

| Run | Environment | Geometry | Command Total(s) | Command Count | Command Avg(s) | Pro Command Total(s) | Free Command Total(s) | Combined VPH |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Notes on Absent vs Zero Fields

| Field | Meaning when absent |
|---|---|
| source_age_cliff | Field did not exist in this artifact format. Do NOT treat as zero. |
| command_failed | Field did not exist in this artifact format. Do NOT treat as zero. |
| throughput_valid | Field did not exist in older artifacts. Do not infer pass/fail. |
| run_environment_label | Field did not exist in older artifacts. Keep absent-label artifacts out of same-universe comparisons unless another authority source establishes their environment. |
| worker_shape_signature | Field not written by older tooling. Geometry derived from runs[*].workers instead. |
| setup_elapsed_s_total | Not present in old-format artifacts (pre-worker_cleanup instrumentation). |
| extract_elapsed_s_total | Not present in old-format artifacts. |
| startup_prepare_total_elapsed_s_total | Not present in old-format artifacts. |
| content_fetch_status_counts_total | Older artifacts may omit this; only reports failures from content-fetch phase, not all failures. |
| content_fetch_command_elapsed_s_* | Content-fetch command timing fields. Absence means the artifact predates command-latency instrumentation. |
| retry_queue_* / shared_retry_* | Batch-local nlm_batch_extract_completed fields; older artifacts may omit them before retry-window instrumentation landed. Drain-skip fields are only present after the actual-drain projected-cliff guard was added. |

**Critical distinction**: `content_fetch_status_counts_total` (source_age_cliff, command_failed) is a
bucket of failures from the content-fetch stage only. `combined.fail_count_total` is the final benchmark
fail count across all phases. These are NOT interchangeable. Report both separately.

**Do not infer**: Do not set source_age_cliff=0 or command_failed=0 simply because the field is absent.
An absent field means the artifact was written before that instrumentation existed, not that no such events occurred.

## Per-Lane Detail
