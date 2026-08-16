# Throughput Decision Packet — sub-batch tracker trace canary

**Status: description-only authorization.** This packet authorizes the
**description** of exactly one bounded live trace canary. **No live run is
approved by this packet.** A parent reviewer sign-off is required before any
launch. This is a correlation-measurement packet, not a throughput packet: it
produces JSONL evidence and claims no VPH delta.

## Decision

- Decision: `canary_description_authorized_no_live_run`
- Agent: LLM2 (canary-packet author; instrumentation shipped by prior relay)
- Date: 2026-08-12
- Branch or hypothesis name: `subbatch_tracker_trace_canary`
- Research question: does the unconditional rate-limit-tracker reset at the
  sub-batch boundary in
  `csf/nlm_batch.py:_add_sources_in_subbatches` mask genuine NotebookLM
  account-wide rate-limit correlation across sub-batches in the same process?

## Current Authority

- Contract reviewed: `docs/operations/throughput-optimization-llm-contract.md` — binding
- Packet template reviewed: `docs/operations/templates/throughput-decision-packet.md`
- Instrumentation receipt: `.logs/multi_account_fetch/20260812_subbatch_tracker_trace_instrumentation/decision_packet.md`
  and `result_receipt.md` — shipped code, no live canary, awaiting this packet

## Metric Authority

- **No headline metric.** This packet measures a runtime distribution of two
  counters, not videos-per-hour. Per the contract, a single-run measurement
  authorizes no throughput claim, and this packet makes none.
- Measured observables (from `nlm_batch_rate_limit_tracker_event` JSONL):
  `pre_reset_failures`, `pre_reset_delay_s` (at `subbatch_reset` events), plus
  supporting event counts: `record_failure` (with `crossed_threshold`),
  `record_success` (with `failures_before`), `apply_delay_slept` (with
  `slept_s`).
- **Schema note (verified against source, corrects the "five kinds" prose in
  the task and the instrumentation packet's prose):** the instrumentation
  emits **four** event kinds, matching the instrumentation packet's own schema
  table and the four `_emit_rate_limit_tracker_event` call sites
  (`csf/nlm_batch.py:2348, 2362, 2377, 4052`):
  `record_failure`, `record_success`, `apply_delay_slept`, `subbatch_reset`.

## Baseline And Candidate

- Current control artifact (environment/cohort reference only, **no VPH
  comparison**):
  `P:/packages/yt-is/.logs/sharded_lane_series/fresh_state_3plus3_extract_schema_source_age_cadence_run01_current/sharded_lane_series_summary.json`
  (cadence01, `3636.16` VPH, `793/7/800`, `status=ok`,
  `throughput_valid=true`, `3+3`, `home_300mb`).
- Candidate artifact / proposed output root:
  `P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/`
- Same cohort/environment?: **yes** — home network, current contract, `3+3`
  account universe, matching cadence01's environment label. The canary itself
  runs **one Pro lane only** (per the stop gate); "3+3" names the environment
  universe, not the canary's lane shape.

## Raw Evidence Inspected (paths)

- `csf/nlm_batch.py:2211-2399` — `_RATE_LIMIT_TRACKER_TRACE` flag (default
  off), `_emit_rate_limit_tracker_event()` helper (error-swallowing,
  lock-safe snapshot), `record_failure` / `record_success` / `apply_delay`
  trace emission.
- `csf/nlm_batch.py:4044-4058` — the sub-batch reset site: captures
  `pre_reset_failures` and `pre_reset_delay_s` under the lock, then wipes
  `_consecutive_failures = 0` / `_current_delay = 0.0`, then emits
  `subbatch_reset` with `subbatch_index`, `subbatch_size`, and the two
  pre-reset fields.
- `csf/nlm_batch.py:3913-3932` — sub-batch window sizing:
  `window_size = min(subbatch_size, remaining)`, default
  `DEFAULT_NOTEBOOKLM_BATCH_SIZE = 50` (`csf/nlm_config.py:23,94`).
- `csf/nlm_batch.py:2203-2206` — tracker constants: `_INITIAL_DELAY = 0.5`,
  `_MAX_DELAY = 60`, `_MAX_CONSECUTIVE_FAILURES = 3`.
- `tests/test_nlm_batch.py::TestRateLimitTrackerTrace` (lines 227-350) —
  7 tests: default-off, `record_failure` payload, `record_success`
  `failures_before`, `apply_delay_slept` only-when-slept, `subbatch_reset`
  pre-state capture, error-swallowing helper, overhead assertion.
- Prior receipt (the gating artifact):
  `.logs/multi_account_fetch/20260812_subbatch_tracker_trace_instrumentation/decision_packet.md`
  and `result_receipt.md` — the 5-finding verdict ledger is §11 of that
  decision packet (finding #4 "Sub-batch tracker reset masking correlation"
  was converted to this research question).
- `csf/nlm_config.py:23,94` — `notebook_batch_size` default 50
  (`YTIS_NLM_BATCH_SIZE`), `notebook_source_cap` default 50.
- `bin/csf-source` lines 782-821, 3192-3195 — `YTIS_NLM_ACCOUNT_PROFILE` is
  required for industrial workers; `NOTEBOOKLM_PROFILE` per worker comes from
  `YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES`.

## Hypothesis

- One-sentence hypothesis (verbatim): **The sub-batch rate-limit-tracker
  reset masks genuine cross-sub-batch throttling signal; if true, the
  empirical distribution of `pre_reset_failures` and `pre_reset_delay_s`
  will show non-zero values at a meaningful fraction of sub-batch
  boundaries.**
- Failure mode being tested: **silent correlation masking under load** — the
  reset wipes accumulated throttle state at every sub-batch boundary, so if
  real rate-limit failures occur inside one sub-batch, the state is discarded
  before the next sub-batch can be influenced by it.

## Falsifier

- Verbatim: **if all `pre_reset_failures == 0` across the run, the reset is a
  confirmed no-op and the research question is closed as a non-issue.
  Document this outcome as a valid result, not a failure.**
- Validity precondition (must be met before "all zeros" is classified as a
  confirmed no-op — see self-review Q2): the run log must contain
  (a) exactly the expected `subbatch_reset` boundary count with
  `subbatch_index` 1..N (N = 3 for this canary), **and**
  (b) at least one `record_failure` event **or** at least one
  `record_success` event with `failures_before > 0` (the tracker was actually
  exercised). If (b) is false, a clean run's zeros are indeterminate
  (no signal existed to mask), classified
  `correlation_absent_with_no_observed_failures`, flagged for the parent
  reviewer, and **not retried** (per the no-retry rule).
- Non-zero outcome: any `pre_reset_failures > 0` (or `pre_reset_delay_s > 0`)
  at any boundary is evidence the reset discards real signal →
  `correlation_present`, which triggers a separate decision packet for a
  controlled A/B (gated reset). This packet does not propose that change.

## Early-Abort / Stop Gates (concrete, machine-checkable)

All five conditions are individually checkable from the JSONL events written
under the canary output root (`INTELLIGENCE_STREAM_LOG_DIR`, action
`nlm_batch_rate_limit_tracker_event`):

1. **30-source cap** — the manifest must contain exactly 30 IDs (built with
   `--limit 30`); verify the manifest length before launch. Not the full 600.
2. **One Pro lane only** — `YTIS_NLM_ACCOUNT_PROFILE=a.hominidae` only, one
   worker, no Free account env, no Free adaptation.
3. **Flag-on duration bounded to a single run** — `YTIS_NLM_RATE_LIMIT_TRACKER_TRACE=1`
   applies to exactly this one fetch invocation; no continuation, no
   follow-up run without a new packet.
4. **Abort if `apply_delay_slept` count > 10** — count JSONL records with
   `event == "apply_delay_slept"`; abort the run if the count exceeds 10
   (signal saturation; the run is no longer diagnostic).
5. **Abort if `record_failure` with `crossed_threshold=true` count > 3** —
   count records with `event == "record_failure"` and
   `crossed_threshold is True`; abort if the count exceeds 3 (we are
   throttling, not observing).

Events written before an abort remain valid evidence (the pre-abort
boundaries already captured their `pre_reset_*` snapshots). Gate 4 and 5 are
checked against the log after the run's process exits (bounded 30-source run),
and a gate breach classifies the run `aborted_by_gate`.

## Why Offline Analysis Is Insufficient

The distribution of `pre_reset_failures` and `pre_reset_delay_s` across real
sub-batch boundaries is a runtime observable: it depends on live NotebookLM
source-add behavior under load inside one process. Static reading of the
source can confirm the reset exists and what it captures, but cannot resolve
whether real failures ever accumulate across boundaries in practice. Only the
flag-on trace can.

## Exact Run Command

> **Launch-blocking design note (read first):** with the default
> `YTIS_NLM_BATCH_SIZE=50`, a 30-source run is a **single** sub-batch → one
> `subbatch_reset` event at index 1 with `pre_reset_failures=0` by
> construction (fresh process). That trace is degenerate and cannot answer the
> question. The command below therefore sets `YTIS_NLM_BATCH_SIZE=10`, giving
> 30 sources → 3 sub-batches → boundaries at indices 1, 2, 3, so boundaries 2
> and 3 can carry `pre_reset_*` state accumulated by the preceding sub-batch.
> A run that launches without this env override produces a degenerate trace
> and must be classified `invalidated`.

```powershell
# 0) Exact-account token-only preflight (mandatory, AGENTS.md auth gate)
python -c "from csf.nlm_client import ensure_account_session; p=ensure_account_session('a.hominidae', worker_id='subbatch-trace-preflight'); print(p.ok, p.reason)"
# Expected: True ok. If not ok, do not launch; report.

# 1) Create the output root and clone canonical DBs into isolated staging
#    (canonical files are opened read-only for the clone; never written)
New-Item -ItemType Directory -Force -Path "P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/events" | Out-Null
python -c "import sqlite3; s=sqlite3.connect(r'P:/.data/yt-is/batch_status.sqlite'); d=sqlite3.connect(r'P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/staging_batch_status.sqlite'); s.backup(d); s.close(); d.close()"
python -c "import sqlite3; s=sqlite3.connect(r'P:/.data/yt-is/transcripts.sqlite'); d=sqlite3.connect(r'P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/staging_cache.sqlite'); s.backup(d); s.close(); d.close()"

# 2) Build the 30-ID manifest (deterministic, from local analysis_status pending rows)
python P:/packages/yt-is/scripts/build_video_selection_manifest.py `
  --output P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/manifest.json `
  --selection-name subbatch_tracker_trace_canary_20260812 `
  --status pending --limit 30 --overwrite
# Verify the manifest contains exactly 30 IDs before launching.

# 3) Launch the single bounded trace canary (one Pro lane, 3 sub-batches of 10)
$env:YTIS_NLM_RATE_LIMIT_TRACKER_TRACE = "1"
$env:YTIS_NLM_BATCH_SIZE = "10"                  # 30 sources -> 3 sub-batch boundaries (see note above)
$env:YTIS_NLM_ACCOUNT_PROFILE = "a.hominidae"
$env:YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX = "ytis-pro-worker"
$env:YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES = "ytis-pro-worker-01"
$env:YTIS_BATCH_STATUS_DB_PATH = "P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/staging_batch_status.sqlite"
$env:YTIS_TRANSCRIPT_CACHE_DB_PATH = "P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/staging_cache.sqlite"
$env:INTELLIGENCE_STREAM_LOG_DIR = "P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/events"
python P:/packages/yt-is/bin/csf-source fetch `
  --video-manifest P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/manifest.json `
  --workers 1

# 4) After the run exits: run the stop-gate checks against the JSONL (counts per gate 4/5),
#    verify the expected 3 subbatch_reset boundaries, then classify (see Result Classification).
```

Output root: `P:/packages/yt-is/.logs/multi_account_fetch/20260812_subbatch_tracker_trace_canary/`.

## Exact Promotion Rule

- **This packet promotes nothing.** It produces JSONL evidence only
  (`nlm_batch_rate_limit_tracker_event`). No behavior is promoted, no VPH
  delta is claimed, and no comparison with projection-60 run02 or cadence01
  numbers is made anywhere in this packet (cadence01 is cited only as the
  cohort/environment reference).
- Any follow-up code change to the sub-batch reset (e.g., a gated reset A/B,
  or removal) is a **separate decision packet**, required only if the outcome
  is `correlation_present`.

## Exact Doc / Registry Update After Result

- **Registry:** `docs/operations/test-registry.md` gets a new dated section
  "Sub-batch tracker trace canary (2026-08-12)" with one outcome row.
  Column fields (values come from the run; **not pre-filled**):
  `date | branch | classification | subbatch_boundary_count |
  boundaries_with_pre_reset_failures_gt0 | max_pre_reset_delay_s |
  apply_delay_slept_count | crossed_threshold_count |
  validity_preconditions_met | falsifier_outcome | packet_path`.
- **Plan:** `docs/operations/hot-path-throughput-next-test-plan.md` gets a
  one-line Current Status pointer bullet **only if** the classification is
  `correlation_present` or `invalidated` (outcome text not pre-filled).
- Values are recorded after the run; this packet does not pre-fill them.

## Do Not Run Next / Out of Scope

- **No** live throughput benchmark or VPH comparison from this run.
- **No** removal or gating of the sub-batch reset (separate packet).
- **No** mutation of any canonical DB, auth state, or live worker
  (canonical DBs are cloned read-only into staging).
- **No** edit to any `bin/csf-*` script.
- **No** new dependency.
- **No** instrumentation extension — the four event kinds are sufficient.
- **No** retry branch for an inconclusive outcome. A conclusive non-result
  (all zeros with preconditions met) is a valid result, documented as such,
  not retried.
- **No** reducer work in this packet; reducing the JSONL to a summary
  statistic is a follow-up packet.

## Self-Review Checklist

1. **Can a fresh agent read this and run the canary without asking a
   clarifying question?** Yes — the command block is complete (preflight,
   staging clone, manifest build, env, CLI, output root) and the
   launch-blocking `YTIS_NLM_BATCH_SIZE=10` note prevents the degenerate-trace
   trap. The only run-time input not pre-selected is the 30-ID manifest
   content, which the manifest builder produces deterministically at run time
   from local pending rows; that is the intended selection step, not a
   missing field.
2. **Does the falsifier actually falsify?** Mostly — with one honest caveat:
   all-zero `pre_reset_failures` proves the reset is a no-op **only if the
   tracker was actually exercised** (the run saw failures or a
   success-after-failure). A completely clean run would produce zeros whether
   or not the reset masks anything. The packet handles this with the validity
   precondition (b): zeros + exercised tracker → confirmed no-op (closed);
   zeros + never-exercised tracker → `correlation_absent_with_no_observed_failures`
   (indeterminate, parent decides, no retry). A measurement bug (flag not
   propagated, wrong log root, single sub-batch) is additionally caught by
   requiring exactly 3 `subbatch_reset` events at indices 1..3 before
   interpreting zeros.
3. **Is the stop gate concrete and machine-checkable?** Yes — all five
   conditions map to single checks on the JSONL: manifest length == 30;
   account env == `a.hominidae` only; one invocation only; count of
   `event == "apply_delay_slept"` ≤ 10; count of
   `event == "record_failure" and crossed_threshold is True` ≤ 3. No
   subjective thresholds.
4. **Is the early-abort on `crossed_threshold=true > 3` appropriate, or does
   it bias toward a "no" result?** Appropriate and disclosed: a
   `crossed_threshold=true` event means `_consecutive_failures >= 3`
   (`_MAX_CONSECUTIVE_FAILURES = 3`), i.e., the run is actively backing off
   (up to `_MAX_DELAY = 60s` sleeps) — at that point it is throttling, not
   observing. The abort preserves the measurement framing. It cannot bias the
   falsifier toward "no": boundaries before the abort already captured their
   `pre_reset_*` snapshots, and if real signal exists it appears at the first
   exercised boundary before any abort can trigger. The 30-source cap keeps
   the run short regardless.
5. **Does the packet respect the contract's single-run / no-VPH rule?**
   Yes — the packet makes no VPH or throughput claim, cites cadence01 only as
   the cohort reference, and the promotion rule states that no behavior is
   promoted and no number is compared.

## Result Classification (filled after the run; not pre-filled)

- Status: `awaiting_parent_review_signoff` (no run launched by this packet)
- Classification space (exactly one of): `correlation_absent` (falsifier met,
  preconditions satisfied) / `correlation_present` / `invalidated` (gate 1-3
  or degenerate-trace violation) / `aborted_by_gate` (gate 4-5) /
  `correlation_absent_with_no_observed_failures` (indeterminate, no retry).
- Registry row / plan pointer: per "Exact Doc / Registry Update After Result".

**Parent handoff: decision_required** — this packet authorizes the description
only; a parent reviewer must sign off before any live launch.
