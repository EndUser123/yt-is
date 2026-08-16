# Unattended Backlog Operation

This is the operational procedure for the canonical multi-account backlog
path. It is separate from the daily auth keepalive task.

## Source Of Truth

- Database: `P:/.data/yt-is/batch_status.sqlite`
- Coordinator: `P:/packages/yt-is/scripts/run_multi_account_fetch.py`
- Supervisor: `P:/packages/yt-is/scripts/run_unattended_backlog.py`
- New-run output root: `P:/packages/yt-is/.logs/multi_account_fetch/`
- Legacy output root retained for restart compatibility: `P:/.logs/multi_account_fetch/`
- Authorization receipt builder:
  `P:/packages/yt-is/scripts/build_full_backlog_authorization.py`
- Read-only health: `P:/packages/yt-is/scripts/check_unattended_backlog.py`
- Task installer: `P:/packages/yt-is/scripts/install_unattended_backlog_task.ps1`
- Auth maintenance: `python -m csf.nlm_keepalive`
- Staging cleanup: `python -m csf.cleanup_staging --dry-run`

The staging cleanup policy and retention boundaries are documented in
`P:/packages/yt-is/docs/operations/staging-artifact-cleanup.md`. The
supervisor runs a guarded parent-tree sweep at the end of each invocation;
cleanup failures are receipt-visible and do not change the fetch result.

The package-local `HANDOFF.md` and the handoff chain under `P:/docs/handoffs/`
remain the authority for current residuals and live decisions. A plan receipt
is not completed work.

The consolidated current gate ledger is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_reconciliation.md`.
Read it before changing the scheduler or authorizing a full-backlog run; it is
status evidence, not authorization.
The corresponding adversarial review is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_adversarial_review.md`.

## Latest throughput gate closure (2026-08-11)

The unknown-caption pair at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/`
is invalid throughput evidence. Fixed controls reconciled `86/90` and `79/90`
after fresh source-add RPC9 and post-add `SourceNotFoundError` failures;
adaptive was withheld by design. Immediate token-only auth passed for
`a.hominidae`, `troup.hominidae`, and `brsthomson`. Staged DB integrity and
cleanup passed, but selected-cache completeness failed because failed selected
items have no transcript text. The observed rates are diagnostic only, not
VPH promotion or optimality evidence. Receipt:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/result_receipt.md`.
The source-add recurrence analysis is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/source_add_recurrence_packet.md`.

Do not treat `rpc_code=9` or `SourceNotFoundError` as an auth request or
direct-replay authorization. Reopen only through an exact isolated recovery
packet, and require a fresh clean control before adaptive or full-backlog
execution.

### Current source-class mechanism evidence (2026-08-12)

The fresh isolation canary at
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run01/`
assigned 24 fresh pending local IDs across all three exact account identities,
using one worker per account, `batch_size=1`, serialized staging databases,
and no account pacing. It observed `11/12` typed RPC9 failures in the prior
high-risk channel class versus `0/12` in the clean control class. Canonical
database/cache hashes and integrity remained unchanged.

This is a mechanism association only. It does not prove causality, diagnose
the provider, authorize a source filter, authorize direct RPC9 replay, or
measure VPH. Do not replay the cohort. A disjoint repeat with a falsifier and
promotion rule is required before changing default source-add behavior.

The disjoint repeat at
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run02/`
completed with the same serialized three-account shape. It measured `12/12`
RPC9 failures in the high-risk class versus `0/12` in the control class; the
control had `11/12` complete outcomes and one independent content-threshold
failure. Attribution, staging integrity, canonical hashes, and process cleanup
passed, with no outside IDs or duplicate attempt identities. This upgrades the
class association to replicated measured evidence, but it remains neither a
causal provider diagnosis nor a throughput result. The result receipt is
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run02/result_receipt.md`.

The `troup.hominidae` stderr also contains a post-completion Python 3.14
Windows asynchronous cleanup warning (`Cancelling an overlapped future failed`;
`WinError 6`). No process survived, but this is an open unattended-reliability
finding. Investigate and verify the owning cleanup path before full-backlog
readiness; do not suppress the warning or call the runtime clean by default.

The supervisor has also been hardened offline: recovery checks recorded PID
command identity and output-root ownership, receipt writes flush/fsync before
atomic replacement, and the installer validates required files and parent
directories before registration. Focused verification is 56 supervisor tests,
17 health-checker tests, Python compilation, and PowerShell parser success.
The S4U registration permission boundary remains an OS-level prerequisite.

### Fresh current captioned smoke (2026-08-11)

The isolated smoke at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_smoke_run02/`
validated the planner correction and the normal route on 12 authoritative
pending captioned IDs. The planner now selects pending rows even when they
exist in the reference cache, then removes them only from copied staging
caches. All four arms completed `6/6` selected IDs with token-only auth
preflight passed, staging integrity `ok`, canonical fingerprints unchanged,
and no positive fallback/source-add/RPC9/source-addressability action. Full
receipt:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_smoke_run02/result_receipt.md`.

This result is `controls_valid_adaptive_not_exercised`, not throughput proof.
The Pro adaptive scheduler never exceeded three target workers because the
two IDs per account created one outer industrial batch; it emitted only
`backlog_below_scale_up` and `backlog_low`. Free accounts were fixed at three.
Do not promote the observed diagnostic rates, claim optimality, or authorize a
full backlog from this smoke. A future adaptive packet must make the scale-up
backlog reachable and require repeated clean control/adaptive soaks.

### Latest source-add fallback canary (2026-08-11)

The exact two-row canary at
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run03_after_content_run01/`
used the staged database and `--fallback-only` after immediate token-only auth
passed for all three canonical identities. Both selected source-add residuals
remained failed: one ended `no_transcript`, and one exhausted the bounded
Whisper deadline. The raw action audit found no NotebookLM source-add,
materialization, source-content, or content-fetch mutation. Neither row passed
the `500`-character promotion gate, so no promotion occurred. Receipt:
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run03_after_content_run01/result_receipt.md`.
Do not direct-replay RPC9, blanket-requeue this class, or treat this result as
an authentication or throughput result.

### Current captioned batch-1 scale-up attempt (2026-08-11)

The follow-up packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_batch1_run03/`
used 60 pending captioned IDs, 10 per account, and `batch_size=1` in an
attempt to make Pro adaptive scale-up reachable. The workload was below the
planner's conservative feasibility floor. Immediate token-only auth passed for all three
accounts, but both fixed controls failed their exact gates: pair-01 `27/30`
and pair-02 `29/30`. Pair-01 had three content-threshold residuals; pair-02
had one Pro `SourceNotFoundError`/`command_failed` row after a source-add
recovery event. Adaptive was correctly withheld. Receipt:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_batch1_run03/result_receipt.md`.

This is `control_invalid_adaptive_not_launched`, not a login failure, VPH
comparison, or optimality result. Do not direct-replay the four residual IDs;
use exact residual packets or a new mechanism packet before another live pair.

The current task registration audit is
`P:/.logs/multi_account_fetch/scheduler_canary_audit_20260811.md`. It verifies
the installed interactive-token, plan-only canary arguments only. It does not
prove logged-out execution or authorize replacing the task with
`--execute --until-empty`.
An isolated S4U `--execute` canary was then attempted under a separate task
name and was blocked before registration by Windows `Access is denied`
(`HRESULT 0x80070005`). The existing plan-only task was preserved and no
workload was launched. See
`P:/.logs/multi_account_fetch/20260811_scheduler_s4u_execute_canary_run01/result_receipt.md`.

A fresh registration-only recheck on 2026-08-12 used a new S4U task name,
state path, and output root. Windows again denied `Register-ScheduledTask`
before task creation; the fresh task/state/output paths were confirmed absent
and the production plan-only task was unchanged. This confirms the same OS
permission boundary, not a NotebookLM auth failure. Receipt:
`P:/.logs/multi_account_fetch/20260812_scheduler_s4u_registration_recheck_run01/result_receipt.md`.
Do not request another NotebookLM login for this boundary. The next required
step is operator/elevated registration for the exact user or an explicitly
approved password-backed principal; do not use `SYSTEM`, shared cookies,
legacy login, or `--no-sandbox`.

## Wiki semantic resynthesis reconciliation (2026-08-11)

The sole poisoned/deferred wiki item,
`4017aa6e-35fb-426d-bc53-34620bec405e`, is now closed by the exact bounded MMX
Stage-C checkpoint resume run16. The run reopened only that item, completed in
`1168.5s` under its `1200s` bound, and left no worker or synthesis child.
Queue state is `completed=47`, `failed=2`, `poisoned=0`,
`needs_resynthesis=0`, with no pending/in-progress work. Receipt:
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`.

Five pages passed normal validation with `llm_validated` quality and complete
four-hop provenance from 36 local transcripts. Citation coverage is only
`19/36` transcripts (`52.8%`), so this closes the poisoned queue state but does
not establish complete source coverage. The read-only historical manifest audit
still reports 13 gaps and zero exact-receipt repairs:
`P:/.logs/wiki-yt-queue/20260812/manifest_gap_audit_current_after_run16.json`.
The audit now also reports 8 degraded-fallback concept pages whose slugs are
absent from the current manifest; this is a cleanup warning, not permission to
delete pages or fabricate manifest entries.

## Current State Superseding Older Packets (2026-08-12)

The active database is `integrity_check=ok` with `complete=9,982`, `failed=197`,
and `pending=332,940`. The current read-only residual audit is
`P:/.logs/multi_account_fetch/20260812_residual_audit_after_source_add_canaries.json`.
It reports `25` command, `12` content-threshold, `2` external-cookie, `2`
empty-transcript, `2` no-transcript, `2` source-add, `1` fallback-quality,
`142` unavailable, and `9` `whisper_timeout` rows;
`unknown=0` and `51` rows still require a decision
packet.
The current exact packet set is
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_current_after_pacing/`.
It was rebuilt from the current audit after the source-add pacing control
abort; the older `after_run10` packet set is historical only. The current
pending-only residual-policy gate receipt is
`P:/.logs/multi_account_fetch/20260812_residual_policy_gate_pending_only_current_after_pacing/`.
It records `332,940` pending rows and `197` failed rows, and expires
`2026-08-13T12:00:00Z`.
The fresh candidate-only pacing validation at
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/`
passed exact token-only preflight and acquired the account gate for all
`18/18` Pro source-add attempts, but reproduced typed `ADD_SOURCE rpc_code=9`
for `ZHYqjD099Aw` after the gate. The Pro run ended `15/18` complete after
read-only reconciliation followed by terminal materialization status `3`.
The Free partitions were withheld after this abort and did not perform
NotebookLM work because their concurrent setup attempts encountered the
shared staging DB lock. This branch is source-add mechanism evidence only:
pacing remains disabled and does not authorize source-add replay, recovery, a
throughput claim, or full-backlog execution. Receipt:
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/result_receipt.md`.
It is valid only for the narrow policy that the supervisor drains `pending`
rows and leaves all `failed` rows deferred. It does not authorize recovery,
fallback promotion, a throughput claim, scheduler installation, or a full
backlog run.
The two current `source_add` rows have a further exact disposition packet at
`P:/.logs/multi_account_fetch/20260812_source_add_residual_closure_after_prior_fallback.md`.
They were already attempted through the bounded fallback-only route:
`yLSnkG9yLbA` exhausted its deadline and `w9cxJdazkEs` reached terminal
`no_transcript`. Treat both as deferred terminal residuals. Do not repeat the
same fallback mechanism or replay RPC9 without a new mechanism and packet.
The cross-run residual-attempt ledger is
`P:/.data/yt-is/unattended-backlog/residual-attempt-ledger.json`.
The guarded requeue command requires a unique attempt ID, mechanism ID,
falsifiable hypothesis, account scope, and decision packet. It rejects a
same-mechanism retry for an ID already present in the ledger.
The ledger is an admission guard for `scripts/requeue_exact_failed_manifest.py`,
not a quality proof and not coverage of the coordinator's separate in-run
industrial fallback queue. If SQLite changes but ledger finalization or the
post-transition read fails, the command writes a failure-bearing receipt and
fails closed; reconcile that receipt before another attempt.
Coordinator-owned children now enable the package-owned SQLite fallback queue
only when an explicit fallback route is selected. The queue lives at
`<account state root>/transcript-fallback-queue.sqlite`, preserves the exact
source URL and `skip_notebooklm` route, and reclaims prior claims after the
database lock is acquired. This covers queued and in-flight fallback work
across a supervisor restart without silently routing it through NotebookLM.
Direct standalone `csf-source` remains memory-only unless the durable queue
environment variables are explicitly supplied. This is restart-safety
persistence, not authorization for default fallback routing, source-add
replay, or full-backlog execution. See
`P:/.logs/multi_account_fetch/20260811_durable_fallback_queue_implementation_receipt.md`.
The bounded restart canary verified one terminated in-flight claim was
requeued once and finalized consistently as a typed failure in the queue and
staged batch DB. See
`P:/.logs/multi_account_fetch/20260811_durable_fallback_queue_restart_canary_run01/result_receipt.md`.
This does not establish a fallback success rate or authorize full-backlog
operation.

### Current identity-canary and throughput-harness status (2026-08-12)

The fresh identity canary run06 exposed a harness defect: post-run validation
compared mutable staging DB/cache files with their frozen pre-launch hashes,
and the executor did not stop later arms after every failed gate. The fix in
`P:/packages/yt-is/scripts/run_throughput_pair.py` keeps immutable packet,
settings, and manifest provenance checks while using post-run integrity/cache
checks for mutable staging files; it also stops all later arms after any failed
gate. The wrapper now produces the executable combined manifests itself; do
not execute a packet produced only by the lower-level staging planner.
`scripts/prepare_throughput_pair.py` is intentionally staging-only and its
`prepare` command never launches workers. The executable wrapper also returns
a failed validation result, rather than raising while indexing packet-owned
paths, when an executable packet is malformed. The planner/coordinator
boundary is covered by `47` focused tests.

Run07 used a fresh disjoint cohort. Exact token-only preflight passed for all
three accounts, source-add attempts succeeded with no RPC9, and the control
was correctly stopped at `7/9` after two `nlm_content_below_threshold` results
on `a.hominidae`. Adaptive and the second pair were not launched. Run06/07
are auth, identity, source-add telemetry, and executor-safety evidence only;
neither is throughput evidence. The durable diagnosis is
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_run02_run03_diagnosis.md`.

### Source-add initial-window mechanism branch

The latest Run09 analysis associated all observed `ADD_SOURCE rpc_code=9`
failures with the first 50-source window, but that association is not causal
proof. The opt-in candidate `YTIS_NLM_SOURCE_ADD_INITIAL_WINDOW_SIZE` is now
implemented in the shared batch path and is disabled by default (`0`). It only
reduces the first add window after a successful source-count probe proves an
empty notebook. Local verification passed. The run01 candidate canary applied
the mechanism on all three accounts and completed `150/150`, but the control
and candidate cohorts were disjoint (`0/50` overlap per account), so the causal
comparison is invalid and the mechanism remains unpromoted.

The exact control/candidate canary packet, gates, falsifier, and promotion rule
are at
`P:/.logs/multi_account_fetch/source_add_initial_window_decision_packet_current.md`.
Do not direct-retry RPC9, enable the candidate for production, or infer a
throughput improvement. A future paired experiment must prepare an immutable
cohort and isolate the two arms; two independent `--all-pending` invocations
are not a valid paired design. The result receipt is
`P:/.logs/multi_account_fetch/source_add_initial_window_canary_run01_result_receipt.md`.

The canaries consumed `300` pending rows, so the prior plan was archived at
`P:/.data/yt-is/unattended-backlog/state-stale-after-source-add-canaries.json`.
A fresh plan-only state was generated under
`P:/.logs/multi_account_fetch/unattended-refresh-after-source-add-canaries/`;
read-only health reports `planned` with `issues=[]`.

### Latest production-shaped throughput attempt: run09 (2026-08-12)

The packet and result receipt are
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/throughput_pair_packet.json`
and
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/result_receipt.md`.
Immediate exact-account token-only preflight passed for all three identities.
The pair01 batch-50 control then produced `13` fresh typed
`ADD_SOURCE rpc_code=9` failures across the three account stderr logs, so the
parent controlled-aborted the controls and adaptive was withheld. Pair01
staging reached `1,113/2,553` complete; pair02 was stopped before meaningful
work at `0/2,553`. Both throughput receipts are invalid and contain no valid
VPH. This is source-add recurrence evidence, not an auth failure or a reason
to direct-retry RPC9. Do not replay this cohort or authorize full-backlog work
from it.

The follow-up read-only distribution audit joined the 42 per-video typed RPC9
outcomes from the same raw run to the local `analysis_status` catalog. Twenty-
five of 25 videos from channel `UCHXy48aYSYeRaBuMOedwxAQ` failed across all
three accounts; the other 17 failures covered 12 channels. The run also used
18 distinct notebook IDs for 9 worker profiles, with no notebook ID shared
across profiles. This weakens shared-notebook ownership as the primary
explanation and makes source/provider addressability the leading hypothesis,
but it is not causal proof. Do not replay those IDs, fetch external metadata,
direct-retry RPC9, or enable a source filter without a fresh disjoint packet.
See `P:/.logs/multi_account_fetch/20260812_source_add_rpc9_distribution_after_run09.md`.

### Exact source-add fallback canary run10 and stale-plan refresh (2026-08-12)

Run10 is recorded at
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run10/result_receipt.md`.
It used an exact three-ID manifest, immediate token-only preflight for all
three canonical identities, and the explicit NotebookLM-free `--fallback-only`
route. The result was `partial`: `DV4EYDLeqBg` produced 645 Whisper
characters and passed the 500-character quality gate; `2vYu5CYAQtY` produced
three characters and `QbK4INVu9fI` was unavailable. Only the first ID was
promoted through a separate exact locked promotion receipt. The raw event scan
found zero NotebookLM source-add, materialization, or source-content actions.
Keep the default source-add route fail-closed; this does not repair RPC9,
authorize fallback promotion generally, or authorize full-backlog execution.

### Source-add fallback routing-gap repair (2026-08-12)

The fresh source-class canary also exposed a classification gap: a typed
`SourceAddError (rpc_code=9)` can be followed by read-only source recovery and
terminal materialization, while the worker persisted only
`Source materialization terminal error`. The explicit
`--route-source-add-failures-to-fallback` route could therefore miss the exact
row even though its raw event history proved the source-add failure.

`P:/packages/yt-is/csf/nlm_batch.py` now preserves the bounded typed source-add
error in the per-video failure reason for both recovered-terminal and
recovered-timeout paths. The existing explicit `Source add failed` predicate
then recognizes the row. This is a routing/classification repair only: it does
not retry RPC9, enable fallback by default, or establish throughput. Local
verification is `184` NLM batch tests plus `79` `csf-source` routing tests;
the decision packet is
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_routing_gap_fix_current.md`.
The routing half was live-validated at
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_routing_gap_canary_run01/`:
all `3/3` typed RPC9/materialization failures were admitted exactly once to the
explicit fallback route, with one `ADD_SOURCE` attempt per ID and no direct
RPC9 retry. Canonical databases/cache were unchanged, staging integrity and
cleanup passed, and no owned process remained. The run used a deliberately
short `30s` fallback deadline, so all three fallback attempts ended with
`transcript fallback deadline exhausted`; quality is still unproven and the
default route remains off. Do not replay those IDs.

The canary also found a provenance-loss defect: failed fallback finalization
replaced the original source-add/RPC9 admission reason with generic `unknown`
text. `P:/packages/yt-is/bin/csf-source` now preserves real upstream
provenance through both parallel and surgical fallback finalization while
excluding synthetic route labels. This local repair is covered by the focused
suite and has not yet had a fresh live quality validation. A future quality
canary needs a disjoint pending manifest, normal fallback deadline, raw-event
and durable-queue reconciliation, the existing 500-character gate, and the
same canonical-state/cleanup protections. The fresh quality canary at
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_quality_canary_run01/`
then passed routing and provenance with one typed RPC9 failure, one exact
fallback admission, one successful fallback completion, no direct retry,
preserved source-add reason in staged status and durable queue, staging
integrity and cleanup passed, and unchanged canonical hashes. The
transcript contained `301` characters, below the `500`-character gate, so the
result is `routing_pass_provenance_pass_quality_gate_failed_default_deferred`;
no promotion or default enablement followed. The retained reason on the
completed status row is intentional provenance preservation in
`csf/batch_status.py:set_status`. Do not replay either cohort or direct-retry
RPC9; a future policy decision needs class-level quality/cost evidence.

The exact promotion changed canonical counts by one row and made the prior
plan stale because it still selected `DV4EYDLeqBg` as pending. The old state
was archived at
`P:/.data/yt-is/unattended-backlog/state-stale-after-run10.json`; a fresh
plan-only state was generated under
`P:/.logs/multi_account_fetch/unattended-refresh-after-run10/` and installed
at `P:/.data/yt-is/unattended-backlog/state.json`. Read-only health now reports
`planned` with `issues=[]`. This is a plan reconciliation receipt, not live
execution proof. Any canonical promotion or exact status reconciliation must
be followed by archive, fresh plan generation, and health validation before a
live supervisor is started.

Three source-add fallback outputs passed the exact `500`-character promotion
gate and were reconciled into canonical state with the locked exact-result
promoter. The two apply receipts are
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run01_apply_receipt.json`
and
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run02_apply_receipt.json`.
The two remaining source-add rows are still failed and packet-required. The
earlier packet set from this stage is superseded by the final post-quality
packet set at
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_after_run10/`.

The latest source-addressability fallback-only canary is recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run02/result_receipt.md`.
One exact row recovered with a `13,928`-character Whisper transcript and was
promoted through the exact quality-gated promoter; one remained
`no_transcript`. This is partial class evidence only, not default fallback or
full-backlog authorization.

The subsequent exact run03 canary tested the three previously untested
source-addressability IDs and found all three unavailable across Selenium,
Whisper/audio, yt-dlp, and cookie-backed yt-dlp. The earlier `FUaqMRqbYvY`
result showed the same four-stage evidence. The guarded classification
reconciliation moved those four exact rows to terminal `unavailable` without
requeueing or completion. Receipt and backup:
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run03_after_source_add_run03/unavailable_reconciliation_receipt.md`.
The source-addressability class is closed. `QvxHBtYsDig` is a separate
`fallback_quality` result below the canonical promotion gate; this does not
authorize blanket retry or default fallback.

The latest content-threshold fallback-only canary is recorded at
`P:/.logs/multi_account_fetch/20260811_content_threshold_fallback_canary_run01/result_receipt.md`.
Three exact rows recovered through `ytdlp` with `22`, `46`, and `8,815`
characters, passed the existing 21-character quality gate, and were promoted
through separate exact receipts because their original failure reasons had
different source UUIDs. Immediate token-only auth, staging integrity, exact
reconciliation, process cleanup, and the no-NotebookLM-mutation scan passed.
The class is reduced to 12 rows. This is positive bounded sample evidence,
not class-wide recovery proof, default-route authorization, a throughput
result, or full-backlog authorization.

The latest bounded quality-observability canary at
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/` selected 400
pending IDs and reconciled 389 complete plus 11 failed. Immediate token-only
auth passed for all three canonical identities. Pro adaptive settings were
forwarded and emitted scale-down transitions only; the run did not prove
scale-up. Free profiles remained at fixed three workers. The normal
cache/NotebookLM path did not populate fallback-only quality fields, so this
run is not semantic-quality or VPH evidence. Receipt:
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/result_receipt.md`.

The next exact command-residual fallback canary at
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run04/`
processed `QOhOFjRLjWA`, `YUazGIwPwfI`, and `yJUq-obHXzw` in isolated staging.
It reconciled `3/3` rows to `complete/whisper`, produced non-empty cache text,
passed both SQLite integrity checks, matched all three token-only auth
identities, and emitted no NotebookLM source/add/materialization/content
actions. Canonical DB/cache fingerprints remained unchanged and no process
survived. This is a positive bounded recovery sample, not a general command
class fix, semantic-quality result, default fallback authorization, or
full-backlog authorization. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run04/result_receipt.md`.

A disjoint six-ID command-residual follow-up at
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run05/`
reconciled `4/6` staged rows to `complete/whisper` with non-empty cache text;
the remaining `2/6` reached the explicit 900-second fallback deadline. All
three immediate token-only auth identities passed, staging integrity was `ok`,
selected IDs reconciled exactly, and no source-add, materialization,
source-content, or content-fetch action appeared. Generic fallback
initialization `nlm_client_*`/auth-probe events are recorded separately and do
not prove NotebookLM mutation. Canonical DB/cache hashes were unchanged and
no process survived. This is partial class evidence with an expensive tail,
not a default-route promotion, throughput result, or full-backlog gate. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run05/result_receipt.md`.

After the canary, a fresh plan-only state was installed at
`P:/.data/yt-is/unattended-backlog/state.json`; the previous terminal plan is
preserved at
`P:/.data/yt-is/unattended-backlog/state-pre-quality-observability-canary-run01.json`.
The read-only health check returned `health_status=planned` with `issues=[]`.
The earlier 195-row source-add class was processed by the exact bounded
fallback manifest: `98` complete, `97` terminal/unknown, `0` pending, and `0`
missing. Receipt:
`P:/.logs/multi_account_fetch/20260810_source_add_residual_policy_current/recovery_run02_result_receipt.md`.

The six exact RPC9 rows from the 2026-08-11 unknown-cohort throughput control
were separately processed in isolated staging under
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run03/source_add_recovery_run01/`.
That bounded fallback-only recovery produced `2` recovered transcripts and `4`
terminal unavailable outcomes, with `0` pending, `0` missing, and `0` direct
NotebookLM source-action events. It closes that recovery branch only; it does
not prove RPC9 is fixed, promote fallback as the default route, or make the
invalid throughput pair valid VPH evidence. Receipt:
`.../source_add_recovery_run01/result_receipt.md`.

The fresh run13 unknown-caption throughput controls are also closed as
`control_invalid_adaptive_not_launched`:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/`.
Pair 01 completed `84/90` with six exact RPC9 source-add failures; pair 02
completed `78/90` with nine RPC9 failures and three separate command failures.
All three immediate token-only auth probes passed, and the adaptive arms were
withheld. No child VPH from this packet is valid promotion evidence.

The six pair-01 RPC9 rows were requeued only in isolated staging and processed
through exact `--fallback-only` manifests. Two produced non-empty transcripts
and four reached explicit `unavailable` terminal state; all six reached final
state, both staging databases passed integrity, and the fallback event scans
found zero NotebookLM source-add, materialization, or source-content actions.
This validates bounded recovery/classification for the exact cohort only. It
does not prove RPC9 is fixed, promote fallback by default, or authorize a new
throughput pair. See
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/source_add_failure_diagnosis_packet.md`
and
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/source_add_rpc9_recovery_run01/result_receipt.md`.

The current command-residual canary then processed the exact IDs
`bnXLDAGL2z8`, `A9Wy_9h1_Ro`, and `BwI1JgoT3pI` through the opt-in industrial
failure fallback route. All three token-only auth probes passed; the canonical
coordinator reconciled `3/3` complete rows, both databases passed integrity,
and the timestamp-ordered event scan found no NLM source/add/content events
after `industrial_failure_fallback_queued`. Cache lengths were `16`, `10,283`,
and `503`; the 16-character output is a quality caveat, not proof of semantic
quality. This validates only the exact canary and does not authorize the
remaining 25 command rows or default fallback routing. See
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run01/result_receipt.md`.

A five-ID isolated staging expansion was attempted for
`OFu07TgcoOk`, `ZYugF5TxgTc`, `pQfeoRzy45s`, `qhqHixwl0X8`, and
`wymC6XG9bms`. It produced two non-empty Whisper transcripts, one
`unavailable`, one `no_transcript`, and one fallback deadline exhaustion. Both
staging databases passed integrity and no NotebookLM action occurred, but the
outer launcher timed out at `1300s`, failed to publish a coordinator summary,
and left descendants that required exact cleanup. The route is therefore not
promoted and the remaining 20 command rows must not be requeued from this
result. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_canary_run02/result_receipt.md`.

Fresh fallback completions now persist `transcript_chars`, `transcript_words`,
`transcript_length_threshold_chars`, and `transcript_length_band` in both cache
metadata and `analysis_status.quality_metrics`, including when engagement
metadata is absent. This is an evidence improvement only: short output is not
automatically rejected, and `status=complete` is not semantic-quality proof.

The bounded quality-observability coordinator canary at
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/` selected 400
pending IDs and reconciled 389 complete plus 11 failed. Immediate token-only
auth passed for all three canonical identities. Pro adaptive settings were
forwarded and emitted scale-down transitions only; the run did not prove
scale-up. Free profiles remained at fixed three workers. Because these rows
completed through cache/NotebookLM paths, none of the 389 selected complete
rows populated the fallback-only quality fields. This is not semantic-quality
or VPH evidence. Receipt:
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/result_receipt.md`.

The earlier post-source-addressability-promotion packet set is superseded by
the final post-quality packet set at
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_after_run10/`.
The current set contains manifests and decision packets for `25` command,
`12` content-threshold, `2` cookie-source, `1` fallback-quality, `2`
source-add, and `9` Whisper-timeout rows. It is read-only and explicitly
records `live_authorized=false` and `database_mutated=false`; it prepares
exact scope but does not authorize any retry.

The source-content not-found branch was then tested in two isolated one-item
canaries. The implementation performs one bounded source-list presence probe
for the recognized spaced `SourceNotFoundError` form: presence admits the
existing local retry, absence suppresses local and queued retry, and unknown
fails closed. In the first canary the source was present after source-add
reconciliation, but four content attempts returned the same not-found error;
the row exhausted attempts and could not enter fallback because the selected
fallback dependency was unavailable. In the second canary the explicit
`--route-source-addressability-failures-to-fallback` switch preserved the
marker, queued exactly the selected ID, and performed no later NotebookLM
content action; fallback returned oEmbed HTTP 404 and classified the row as
terminal `unavailable`.

These receipts validate bounded retry admission and exact route partitioning,
not content recovery, an auth fix, a provider fix, default fallback policy, or
throughput. Keep the route opt-in and do not retry the unavailable item:

- `P:/.logs/multi_account_fetch/source_content_presence_retry_canary_20260811_run01/decision_packet.md`
- `P:/.logs/multi_account_fetch/source_content_addressability_fallback_canary_20260811_run02/decision_packet.md`

The older RPC9 reconciliation canary at
`P:/.logs/multi_account_fetch/source_add_rpc9_reconciliation_canary_20260811_run03/`
is not current-policy proof: it records one attempt despite positive presence,
but has no source revision or working-tree fingerprint. A later same-day
presence-aware artifact records four attempts and `attempts_exhausted`, and
the current unit regression locks that behavior. Keep the older result as
historical evidence; do not treat it as authentication failure or authorize
an RPC9 replay. See
`P:/.logs/multi_account_fetch/source_add_content_not_found_reconciliation_20260812.md`.

The current five-row `source_add` residual class was separately tested through
two disjoint exact fallback-only packets in isolated staging. Run01 recovered
one row; run02 recovered two and terminalized two (`no_transcript` and
fallback deadline exhausted). All three immediate token-only auth probes
passed, raw events showed no source-add/materialization/content action,
staging integrity and cleanup passed, and the canonical DB/cache hashes were
preserved. This is `3/5` partial bounded recovery evidence with a costly tail,
not default fallback promotion or full-backlog authorization. Receipts:

- `P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run01/result_receipt.md`
- `P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run02/result_receipt.md`

The first exact row from the six-row `source_addressability` residual class was
then tested through a fresh isolated fallback-only packet. The staged row
recovered in `19.598s` with a non-empty `33`-character / `5`-word Selenium
cache result; all three immediate token-only auth probes passed, raw events
showed no source-add/materialization/content action, staged integrity and
cleanup passed, and canonical DB/cache hashes were preserved. The output only
barely meets the existing minimum, so this is a bounded route success with a
semantic-quality caveat, not default fallback promotion or full-backlog
authorization. Receipt:

- `P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run01/result_receipt.md`

The throughput-pair harness now supports distinct per-account worker counts,
batch sizes, and adaptive policies. Effective settings are fingerprinted in
each packet and execution rejects settings-file drift. The offline validation
packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_distinct_settings_plan_run01/`
passed with `live_launch=false`; this enables a future Pro-vs-Free and
per-Free-account comparison but does not authorize or imply a live throughput
result.

The nine former deadline-unknown rows were then processed by one exact,
isolated fallback-only retry under
`P:/.logs/multi_account_fetch/20260810_source_add_residual_policy_current/deadline_unknown_retry_run01/`.
All `9/9` reached Whisper and timed out between `782.510s` and `823.481s`;
`0/9` produced a transcript, no row remained pending, and both isolated
databases passed integrity checks. The exact raw action scan found zero
NotebookLM source-add, materialization, or source-content events. This is a
negative fallback-mechanism result, not an auth or source-add result. A
DB-locked classification-only repair changed the canonical rows to
`whisper_timeout` while preserving `status=failed` and `last_stage=whisper`.
No automatic retry or fallback promotion is authorized. See
`.../deadline_unknown_retry_run01/result_receipt.md` and
`.../deadline_unknown_classification_repair_receipt_apply.json`.

The canonical plan state is
`P:/.data/yt-is/unattended-backlog/state.json`, refreshed at
`2026-08-11T00:36:30Z`; `check_unattended_backlog.py` returned
`health_status=planned` and `issues=[]`. Its plan artifacts are under
`P:/.logs/multi_account_fetch/unattended-refresh-20260811/chunk-0001/`.
The prior state is preserved as
`P:/.data/yt-is/unattended-backlog/state-stale-20260810-20260810-183830.json`.
This state is eligible for a bounded live chunk after same-window auth and
process preflight, but it is not a full-backlog authorization.

The latest authoritative wiki receipt supersedes the older run14 state:
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`
reports `completed=47`, `failed=2`, `poisoned=0`, `needs_resynthesis=0`,
`pending=0`, and `in_progress=0`. The exact semantic-debt item produced five
validated pages with complete four-hop provenance. The reconciled citation
coverage is `19/36` (`52.8%`), so this is not a claim of complete source
coverage. The historical manifest audit still reports `13` gaps and zero
exact-receipt repairs; no historical entries may be fabricated.

Read-only health is fail-closed. It requires a schema-valid supervisor state
and config, a current canonical database, and parity between configured
accounts, chunk assignments, manifests, and planned receipts. It recomputes
the manifest/database/selection fingerprints and rejects missing, malformed,
stale, or forged receipts; it does not skip a corrupt planned receipt or infer
health from aggregate counts alone. An empty or malformed state is
`needs_attention` and must not be treated as scheduler-ready.

Full-backlog authorization is stricter than a plan. The builder requires all
five `--gate name=passed` labels **and** one `--gate-evidence
name=PATH` JSON sidecar per gate. Each sidecar must declare
`schema_version=2`, the exact gate name, its gate-specific `evidence_kind`,
`decision=passed`, timezone-bearing `verified_at`/`expires_at`, and an
`evidence_path` plus matching SHA-256. The referenced artifact must be
structured JSON with `schema_version=1`, the same gate and evidence kind, and
the required claims for that gate: exact-account token-only success;
non-plan scheduler execution; zero surviving processes and good staged DB
integrity; residual-policy and packet fingerprints; or repeated valid
throughput evidence with canonical account coverage. The five artifacts must
be distinct. The authorization also fingerprints every raw evidence file. At
launch, the supervisor rechecks the sidecars, gate-specific artifact schema,
raw evidence fingerprints, expiry, database, account settings, and pending-ID
set. A readable Markdown file, a generic non-empty file, or a stale chat
assertion is not authorization evidence.

This schema validates the evidence contract and its file bindings; it does not
perform authentication, scheduler execution, cleanup, residual analysis, or
throughput measurement. Those gates must be produced independently before a
receipt is built.

The current gate audit is
`P:/.logs/multi_account_fetch/20260810_unattended_readiness_gate_audit_20260810.md`.
It remains `not_ready_for_unattended_full_backlog`: plan/health, bounded
cleanup, and isolated restart/resume are proven, but logged-out OS scheduler
execution, policy closure for the remaining non-terminal classes, a clean
throughput pair, and full-drain reconciliation are not. The fresh plan health
pass does not change those gates. Do not create or use a full-backlog
authorization receipt until all five independent gates pass.
`YtisUnattendedBacklog` is registered and verified in interactive-token,
plan-only mode by
`P:/.logs/multi_account_fetch/scheduler_execution_canary_run01/result_receipt.md`.
The S4U registration attempt was denied by Windows, so logged-out execution
remains unverified. The task must remain plan-only until the authorization
gates pass.

The supervisor supports an explicit selection contract for reproducible
bounded experiments. `--caption-state` selects `unknown`, `captioned`,
`no-caption`, or `any`; `--uncached-only` requires an explicit
`--uncached-reference-cache-db-path`. The selection mode and reference-cache
path are persisted in supervisor state and must match the child receipt. A
normal full-backlog invocation keeps the default `--all-pending` scope. These
flags make control/candidate cohorts auditable; they do not authorize a live
benchmark or full drain by themselves.

The corrected uncached captioned control run04 is also closed as
`control_invalid_adaptive_not_launched`: `264/270` completed and `6` failed,
including one RPC9 source-add precondition and five content-threshold rows.
It proved real NotebookLM source/content work in isolated staging, but the
adaptive arm was withheld because the control gate failed. Its one source-add
residual was tested through exact staged `--fallback-only` and classified
`unavailable` without NotebookLM mutation; the canonical row remained
unchanged. This is reliability evidence only and does not authorize a clean
throughput pair or full backlog.

The next executable throughput-pair control canary is also closed as
`control_invalid_adaptive_not_launched`:
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run07/`.
It completed `29/30` selected IDs. The sole failure was the known typed
non-retryable source-add `RPCError rpc_code=9` for `A1NrAlw1lHw` on
`brsthomson`; all three immediate token-only auth probes passed. The adaptive
arm and second pair were withheld, and the result is not usable VPH evidence.
The exact receipt is
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run07/pair-01/control_result_receipt.md`.
The throughput coordinator now writes an atomic arm receipt for partial or
runner-failed children, records observed counts separately from valid VPH, and
keeps promotion fail-closed.

The subsequent scheduler input-closure canary is recorded at
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run11/` with
receipt
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run11/result_receipt.md`.
Pair-01 used the same fresh 30-ID cohort for control and adaptive arms, with
10 items per account and `batch_size=1`; both reconciled `30/30` and passed
staging database/cache integrity. Control measured `662.963` combined VPH and
adaptive measured `1616.234`. Treat this as a bounded scheduler mechanism
canary only. It proves raw events can raise the target from three to four
workers after input closes while avoiding premature scale-down during open
input; it does not prove sustained optimal VPH, account-specific worker
settings, or full-backlog readiness.

Pair-02 was invalidated at `26/30` by four exact `SourceNotFoundError`
content-fetch failures (`Bw0I1M7gZ74`, `BHApw964CVQ`, `AQHlyGA2cZM`, and
`AS8evR1_1Qk`). The errors were not auth failures; no adaptive arm was
launched. Preserve them as a source-addressability/materialization residual
and do not replay them blindly.

The distinct-settings no-caption pair at
`.logs/multi_account_fetch/throughput_pair_20260811_no_caption_30_distinct_settings_plan_run01/`
is also closed as `negative_control_invalid_adaptive_not_launched`. Immediate
token-only auth passed for all three canonical identities, but pair-01
reconciled `85/90` and pair-02 `88/90` because seven exact typed RPC9
source-add failures occurred; three of those also produced
`SourceNotFoundError` during content handling. Adaptive arms were withheld,
and no VPH from either control is valid for a control-versus-adaptive claim.
The four exact RPC9 rows were tested once in isolated staged
`--fallback-only` recovery: `3/4` reached non-empty Whisper cache completion
and `1/4` exhausted its bounded deadline. Both staging databases passed
integrity and no NotebookLM action occurred in fallback mode. This validates
only bounded class-specific recovery; it does not fix RPC9, promote fallback
by default, or authorize a new same-shape pair or full backlog.

## Reliability Boundary: Whisper Fallback

The coordinator's per-item fallback deadline is an outer boundary. The
transcript worker now receives a child deadline with a 30-second result-writing
margin. Whisper audio selector attempts share one total download budget rather
than receiving independent 300-second deadlines, and transcription is capped by
the time remaining after audio. Once the deadline is exhausted, the chain stops
retrying or starting another provider and returns a classified terminal result.

This was exercised against three exact failed control IDs in an isolated copy:
two completed through Whisper; the third exposed a finalization race and was
then rechecked with a 120-second bounded deadline, ending in a terminal
`last_stage=whisper` timeout in `73.678s` with no outer fallback timeout or
pending row. This proves bounded-failure handling, not successful recovery for
every source and not default fallback promotion. The exact packet is
`P:/.logs/multi_account_fetch/20260810_uncached_control_adaptive_pair_run02/whisper_timeout_retry_decision_packet.md`.

## Historical Evidence Snapshot (2026-08-10)

The following section preserves earlier evidence for comparison. It is not the
current residual count; use the 2026-08-11 block above and the package handoff
for current state.

The latest authoritative database snapshot is `integrity_check=ok` with
`complete=9,184`, `failed=294`, and `pending=333,641`. The latest bounded
source-add policy canary processed 400 fresh rows and reconciled 392 complete
with 8 terminal failures. The fresh 30-row
coordinator-health canary
(`P:/.logs/multi_account_fetch/20260810_unattended_readiness_canary_run03/`)
reconciled `30/30 complete`, 10 per canonical account. All three exact
token-only auth identities matched, all 30 selected IDs have non-empty rows in
the canonical transcript cache, and cleanup reported verified notebook-list
postconditions with zero worker notebooks. Pro forwarded its configured
adaptive policy; both Free profiles used fixed three-worker settings. This
validates coordinator routing, locking, receipts, cache writes, and cleanup
for a bounded cohort; it does not authorize full-backlog execution, prove
logged-out scheduler execution, or establish maximum throughput. The detailed
receipt is
`P:/.logs/multi_account_fetch/20260810_unattended_readiness_canary_run03/result_receipt.md`.

The current read-only residual audit is
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit_current_20260810_133736.json`.
It classifies `195` source-add, `28` command, `15` content-threshold, `2`
external-cookie, `2` empty-transcript, `2` no-transcript, and `50` unavailable
rows. `240` rows still require a decision packet. Do not use older snapshots
elsewhere in this document as current state.

The isolated uncached control/adaptive preparation then ran the fixed control
only. The control is an invalid comparison member: `1,087/1,200` completed and
`113` failed, with `112` exact `Source add failed`/`rpc_code=9` rows and one
`command_failed` row. The adaptive candidate was not launched. The exact
control packet and raw evidence are under
`P:/.logs/multi_account_fetch/20260810_uncached_control_adaptive_pair_run01/`.

The 112 source-add rows were requeued only after exact failed-state validation
and processed through per-account `--fallback-only` manifests in an isolated
staging database. `101/112` completed with non-empty cache rows and `11` became
explicit terminal failures; both staged SQLite integrity checks passed and the
raw fallback event scan found zero NotebookLM mutation events. This validates
bounded recovery, not default promotion or throughput. The two invalid
transcript-worker JSON results remain a separate fallback reliability issue.
Keep the primary NotebookLM path and direct `rpc_code=9` no-replay rule
unchanged. Governing packet and receipt:
`P:/.logs/multi_account_fetch/20260810_source_add_control_recovery_run01/`.

The canonical non-secret account settings file is now present at
`P:/.data/yt-is/unattended-backlog/account-settings.json`. A fresh plan-only
supervisor run using that file selected `400` pending rows, reconciled all
three account settings, and passed the read-only health check with
`health_status=planned` and no issues. Its durable state is
`P:/.data/yt-is/unattended-backlog/state.json`, and its exact plan artifacts
are under `P:/.logs/multi_account_fetch/unattended/chunk-0001/`; the planned
account counts are `a.hominidae=134`, `brsthomson=133`, and
`troup.hominidae=133`. No child or auth preflight launched. The effective policy is Pro
(`a.hominidae`) adaptive-capable at `3..5` workers and batch size `50`, with
both Free profiles at three workers and batch size `50`. This verifies the
configuration and selection path only; it is not live execution evidence.

The current-policy plan/health path was revalidated offline in
`P:/.logs/multi_account_fetch/20260810_offline_plan_validation/`: `400` pending
rows were partitioned `134/133/133` across the three exact accounts, Pro
adaptive settings were forwarded, and explicit source-add fallback used the
`900s` deadline. Read-only health returned `health_status=planned` with
`issues=[]`; no auth or child ran. Reusing the older canonical state after a
configuration change was rejected fail-closed rather than silently resuming
under different settings. This is plan/health evidence only.

The next isolated control/adaptive pair run completed only its fixed control.
The exact `1,200`-ID cohort reconciled `1,137 complete`, `63 failed`, `0 pending`
with no missing IDs. All three token-only identities passed the immediate
preflight, and the fixed control used three workers and batch size `50` for
each account. Its parallel-account completed VPH was `1534.50`, calculated
from `1137 / 2667.454s * 3600`. The result is not a clean throughput baseline:
`231` fallback attempts ran, `48 transcript_chain_failed` events were present,
and the failed rows include two deadline-exhausted fallback outcomes and two
cookie-rotation failures. The packet therefore invalidated the comparison and
did not launch the adaptive candidate. Staging DB/cache integrity and process
cleanup passed, and all `1,137` completed selected IDs have non-empty isolated
cache rows. See
`P:/.logs/multi_account_fetch/20260810_uncached_control_adaptive_pair_run03/`.
This does not promote fallback, authorize full-backlog execution, or establish
maximum throughput.

The exact source-add fallback route has been validated as bounded recovery on
small cohorts, but default promotion remains deferred because the latest
12-row recovery produced `9` non-empty caches and `3` terminal unavailable
rows, and a larger residual cohort has not been reconciled under one current
policy packet. Direct `rpc_code=9` replay remains prohibited.

Current source-add boundary: the 2026-08-09 all-account run04 canary passed
token-only auth preflight and attempted each `rpc_code=9` add exactly once.
The post-error probe observed a `0 -> 1` source-count change for every
account, but the returned IDs were not addressable by `nlm source content`:
all three selected items failed with `SourceNotFoundError` on both content
attempts. See
`P:/.logs/multi_account_fetch/20260809_source_add_probe_canary_run04_decision_packet.md`.
The result proves no-replay behavior, not usable-source success. This branch
remains closed for direct source-add promotion. The six historical
Free-account rpc9 residuals were subsequently classified: four completed via
the bounded post-failure fallback canary, while two were independently
unavailable/private after the exact-manifest `--fallback-only` route. See
`P:/.logs/multi_account_fetch/20260810_source_add_fallback_only_run01/result_receipt.md`.
The new route emitted no source-add or NotebookLM content action, requires an
exact manifest, and is recovery-only; it does not silently change the default
route or authorize full backlog.

The larger 2026-08-10 all-account canary exposed 37 additional exact
`Source add failed` rows. The guarded fallback recovery completed 34 and
classified three terminal fallback failures (`unavailable` or an unclassified
direct-API failure). One row initially remained pending after the old
in-process Whisper transcription path ran past 25 minutes, but a subsequent
exact one-item fallback-only run completed it through Selenium. The
authoritative result receipt is
`P:/.logs/multi_account_fetch/20260810_source_add_fallback_recovery_run01/result_receipt.md`.
This branch remains partial: the unknown row still needs a discriminating
diagnostic, and the recovery route is not promoted to the default or full
backlog from this receipt alone. The live residual did not exercise the new
process-isolated Whisper worker.

A fresh route-policy canary then exercised
`--route-source-add-failures-to-fallback` on 400 new pending rows across all
three accounts. It reconciled `394 complete`, `6 failed`, and `0 pending`.
The six failures were all `command_failed` outcomes and were correctly not
admitted by the source-add-only predicate. One exact `Source add failed` row
was admitted to fallback and completed. The fallback event stream contained
no NotebookLM source-add, materialization, or source-content actions; child
receipts also reconciled `processed_count == pending_total` for every account.
This validates the narrow routing safety boundary, but not complete processing,
command-failure recovery, sustained throughput, or full-backlog readiness.
Keep the flag explicit and off by default, and do not use this partial canary
to authorize `--until-empty`.
The governing packet is
`P:/.logs/multi_account_fetch/20260810_source_add_fallback_policy_canary_run02/decision_packet.md`.

The follow-up policy canary run03 exercised the same exact admission after the
coordinator began forwarding a `900s` per-item fallback deadline. Of 400 fresh
pending rows, 40 exact source-add failures were admitted and all 40 reached
terminal fallback outcomes (33 Selenium successes, 1 Whisper success, and 6
unavailable). No fallback timeout, fallback failure, or NotebookLM mutation
occurred. The chunk as a whole was partial (`392 complete`, `8 failed`, `0
pending`), so the supervisor correctly returned nonzero. Keep the route
explicit and opt-in; do not infer that command failures or unavailable rows
belong in the same route, and do not use this canary to authorize
`--until-empty`. The raw receipt is
`P:/.logs/multi_account_fetch/20260810_source_add_fallback_policy_canary_run03/result_receipt.md`.

The post-fix fallback-only deadline canary run04 then processed an isolated
40-row source-add residual cohort. All 40 terminalized (`2` non-empty cache
rows, `38` `unavailable`, `0` pending), with no outer fallback timeout and no
NotebookLM mutation. Staging integrity was `ok`; canonical counts remained
`complete=9184`, `failed=294`, `pending=333641`. This validates bounded
fallback terminalization only. Since the run used explicit `--fallback-only`,
it did not exercise normal source-add admission; the route flag remains opt-in
and full-backlog authorization remains deferred. See
`P:/.logs/multi_account_fetch/20260810_source_add_deadline_postfix_canary_run04/result_receipt.md`.

The six `SourceNotFoundError` rows left by that canary were then recovered
through an exact-manifest `--fallback-only` run. All six reconciled to
`complete` at `last_stage=whisper` (`a.hominidae` 2/2, `brsthomson` 2/2,
`troup.hominidae` 2/2), and the raw event scan found no NotebookLM source-add,
materialization, or source-content actions. This closes the observed
source-addressability residual without replaying NotebookLM. It does not
promote fallback as the default route or authorize `--until-empty`; the
recovery packet is
`P:/.logs/multi_account_fetch/20260810_source_not_found_fallback_recovery_run01/decision_packet.md`.

The industrial-worker failure fallback was subsequently validated on three
exact failed IDs in
`P:/.logs/multi_account_fetch/20260810_industrial_failure_fallback_canary_run02_live/`.
All three canonical account probes passed; each failed industrial row was
requeued once, routed with `skip_notebooklm=true`, and ended `complete` at the
`whisper` stage. Elapsed times were 145s, 312s, and 563s. This validates
failure recovery and DB reconciliation, but not direct source-add repair,
acceptable full-backlog tail latency, or a default route. Keep
`--route-industrial-failures-to-fallback` explicit and off by default pending
a larger route-partitioned policy canary.

That bounded fallback canary has now passed for one fresh `has_captions=0` row
per account: all three produced non-empty Selenium transcripts, reconciled to
`complete`, and emitted no source-add events. See
`P:/.logs/multi_account_fetch/20260809_no_captions_fallback_canary_run01_decision_packet.md`.
Treat this as validation of the no-caption partition only. It does not
authorize the default route change, captioned/unknown processing, adaptive
scale-up, or full-backlog execution.

A separate three-item `has_captions=NULL`, uncached canary also completed via
NotebookLM with `status=ready` content receipts and no retry/auth failures:
`P:/.logs/multi_account_fetch/20260809_unknown_uncached_source_add_canary_run01_decision_packet.md`.
The evidence now supports route-partitioned progression, but a larger
partitioned throughput canary and residual policy are still required before
unattended full-backlog promotion.

The later route-partitioned adaptive candidate is also closed as
`candidate_invalidated_no_control`. It selected 1,200 `has_captions IS NULL`
rows and reconciled 994 complete and 206 failed; 166 were source-add failures,
25 command failures, and 15 content-threshold failures. Pro target-worker
telemetry never exceeded its initial three workers, so no adaptive comparison
was exercised and no fixed control ran. The packet is
`P:/.logs/multi_account_fetch/20260810_throughput_uncategorized_adaptive_pair_run01_decision_packet.md`.
The post-run residual audit is
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit_after_uncategorized_candidate.md`.
Do not run another same-shape pair. The exact 12-row source-add residual
`--fallback-only` canary has now completed with 9 non-empty cache results and
3 explicit unavailable terminal failures; no forbidden NotebookLM events
occurred. Keep default promotion deferred until fallback tail cost and
per-item budgets are measured. See
`P:/.logs/multi_account_fetch/20260810_source_add_residual_fallback_canary_run03/result_receipt.md`.

## Historical Residual Gate (2026-08-10; superseded)

The fresh three-account coordinator health canary passed for 30 exact pending
IDs (10 per canonical account): all 30 reconciled to `complete`, each account
observed its expected token-only identity, the canonical transcript cache has
30 non-empty rows, and the batch DB integrity check is `ok`. The Pro route
forwarded the configured bounded adaptive policy; both Free routes used fixed
three-worker settings. See
`P:/.logs/multi_account_fetch/20260810_unattended_canary_run01_decision_packet.md`.
This is a coordinator-health promotion only. It does not prove maximum VPH,
adaptive scale-up, poisoned synthesis, or authorize `--until-empty`.

The latest exact source-add recovery run is partial, not a full-backlog gate:
26 rows were processed through the explicit `--fallback-only` route, with 14
complete and 12 failed. Its result receipt is
`P:/.logs/multi_account_fetch/20260810_source_add_residual_recovery_run01/result_receipt.md`.
The route emitted no NotebookLM source-add/materialization/content actions and
must remain recovery-only.

The current authoritative residual audit is
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit.md`:
Classification version `unattended-residuals-v3` reports 35 failed rows as 29
terminal unavailable, 2 terminal no-transcript, 2 terminal empty-Whisper-
transcript, and 2 cookie-source rows blocked on external YouTube cookie state.
The six original content-threshold candidates were processed through a
dedicated fallback-only recovery: five completed with non-empty cache entries
and `x85tFCIc3Ps` is explicitly terminal no-transcript after the exact fallback
chain exhausted at direct API with subtitles disabled. The recovery receipt is
`P:/.logs/multi_account_fetch/20260810_content_threshold_recovery_result_receipt.md`.
The 17 command-failure candidates were processed through the opt-in
industrial-failure fallback expansion; 15 completed and 2 remained failed.
The exact result receipt is
`P:/.logs/multi_account_fetch/20260810_command_residual_expansion_result_receipt.md`.
This validates the route boundary but does not promote it to the default route
or authorize `--until-empty`. The two formerly null-reason rows were repaired
from exact raw events under the DB lock; the repair receipts are next to the
source-add recovery receipt. This classification repair changed no retry
state.

Before any `--until-empty` execution, every non-terminal class must have a
separate decision packet naming an exact manifest, falsifier, early-abort gate,
bounded retry/fallback policy, and reconciled postcondition. In particular,
command failures, content-threshold failures, source-add failures, poisoned
synthesis, and missing historical-manifest provenance are separate queues; do
not merge them into a generic retry pool. No unknown or other rows remain; the
two cookie-source rows remain a hard stop until a separately packeted external
cookie source is available, distinct from NotebookLM authentication.

The downstream wiki queue is also a separate quality boundary. Its latest
locked state is recorded in
`P:/.data/wiki/_state/nlm-sync/queue.json` and reconciled by
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`:
`completed=47`, `failed=2`, `poisoned=0`, and `needs_resynthesis=0`, with no
pending or in-progress work. The exact semantic-debt item was completed by a
bounded checkpoint resume and its five pages have complete four-hop
provenance. Citation coverage remains `19/36` (`52.8%`), not complete source
coverage. The two failed records and the `13` historical manifest gaps remain
separate audit boundaries; neither may be erased or fabricated.

The earlier 2026-08-10/11 timeout attempts are historical evidence only and
are superseded for current queue state by run16. Keep their bounded-timeout
receipts for provenance; do not interpret them as a current poisoned item or
as evidence that the latest checkpoint-resume result lacks semantic
validation.

The latest 400-row unattended canary is also only partial (`346/400` complete,
54 failed). It proves coordinator/receipt behavior, not full-backlog readiness.
One fallback item showed roughly 30 minutes of long-audio tail across failed
audio/Whisper attempts. Existing audio and Whisper stage deadlines are finite,
but the child-level four-hour timeout is not a sufficient per-item safety
policy. Full-backlog promotion therefore also requires bounded per-item tail
handling and a receipt that distinguishes item timeout, provider failure, and
worker/child timeout.

The two residual Whisper rows with the earlier yt-dlp `-f best` warning were
given one exact fallback-only canary after the selector fallback was covered by
tests. Both remained failed with age-restricted-video and rotated Firefox
cookie evidence. A read-only Chrome-cookie diagnostic failed before extraction
because yt-dlp could not copy the Chrome cookie database. The canary emitted no
NotebookLM actions and did not request interactive login. Its packet and
receipt are
`P:/.logs/multi_account_fetch/20260810_whisper_default_selector_canary_decision_packet.md`
and
`P:/.logs/multi_account_fetch/20260810_whisper_default_selector_canary_result_receipt.md`.
This is a `blocked_cookie_source` quarantine, not permission to retry or to
promote the selector change.

## Account Policy

The coordinator accepts an optional JSON file keyed by canonical account
identity. A checked-in starting point is
`P:/packages/yt-is/config/account-settings.example.json`; copy it to the
operator-owned data area before editing. Unspecified fields inherit the global
command-line defaults.
`batch_size` is the NotebookLM subbatch size, not the coordinator chunk size.

Throughput-pair packets that require adaptive scale-up now fail closed during
planning if the per-account workload cannot leave a scheduler backlog after
the initial dispatch and health window. The packet records the derived logical
batch floor and the assumption that `csf-source` dispatches up to four logical
batches per worker. This prevents a small smoke from being misreported as an
adaptive test; it does not guarantee live scale-up or authorize a benchmark.

The no-caption route is an explicit supervisor/coordinator option, not an
ambient requirement. After the bounded fallback canary is promoted for the
appropriate partition, pass `--route-no-captions-to-fallback` to the supervisor
(or `-RouteNoCaptionsToFallback` to the task installer). The coordinator
forwards it to every child and records the effective value in each summary;
the default remains off until a broader policy packet changes it.

Industrial worker failures have a separate explicit option:
`--route-industrial-failures-to-fallback`. It is effective only when transcript
fallback workers are available. The path requires an authoritative DB row with
`status=failed` and a source URL, requeues that exact ID once, and records
whether the fallback completed. Missing status/source evidence fails closed;
the default is off.

The narrower `--route-source-add-failures-to-fallback` option is reserved for
rows whose authoritative failure reason is exactly a source-add failure. It
does not issue another NotebookLM `ADD_SOURCE` mutation; it requeues the exact
row once and runs the exact-manifest fallback-only path. Keep it off for the
normal route until a route-partitioned canary and throughput packet promote it.

The separate `--route-source-addressability-failures-to-fallback` option is
reserved for rows whose authoritative failure reason contains the exact
`SourceNotFoundError`/source-not-found addressability class. It is deliberately
not a broad `command_failed` route and does not retry `ADD_SOURCE`,
materialization, or `nlm source content`. It requeues only the reconciled row
once and sends it through the exact-manifest fallback-only path. The supervisor,
coordinator, child environment, and receipts carry this flag explicitly. Keep
it off by default until a fresh policy canary demonstrates useful recovery and
acceptable tail behavior; a source-addressability recovery receipt alone does
not authorize `--until-empty`.

Coordinator-owned industrial workers have a finite default deadline of four
hours and are launched in an owned process group so a timeout can terminate
descendants before the assignment is requeued. Transcript fallback workers use
a bounded post-kill wait and pipe-handle reap: cleanup is `confirmed` only
when the second wait completes; an expired or failed cleanup is recorded as
`termination_unconfirmed` and remains a failed, non-promotable outcome. This
receipt confirms the owned process cleanup boundary, but does not constitute
platform-specific proof that every descendant has exited. Direct standalone
`csf-source` callers remain unbounded unless they set
`YTIS_INDUSTRIAL_WORKER_TIMEOUT_S`; unattended operation must use the
coordinator path. Adaptive worker quarantine is bounded: a failed worker can
be recreated after the configured cooldown for at most two recovery attempts;
a successful assignment resets that budget. An exhausted adaptive run must
emit a partial receipt with its unprocessed count rather than claim completion.

For a reviewed residual that must never re-enter NotebookLM, use
`--fallback-only` with an exact `--video-manifest`. The coordinator and child
both reject an unbounded fallback-only run. This route bypasses oEmbed only for
the exact manifest, records `fallback_only=true`, and does not change default
backlog routing. Treat unavailable/private outcomes as terminal evidence, not
as a reason to retry or request authentication.

```json
{
  "a.hominidae": {
    "workers_per_account": 3,
    "batch_size": 50,
    "adaptive_workers": true,
    "adaptive_min_workers": 1,
    "adaptive_max_workers": 5,
    "adaptive_scale_up_backlog": 2,
    "adaptive_scale_down_backlog": 0,
    "adaptive_cooldown_s": 60,
    "adaptive_health_window": 2
  },
  "troup.hominidae": {"workers_per_account": 3, "batch_size": 50},
  "brsthomson": {"workers_per_account": 3, "batch_size": 50}
}
```

The exact identity map is in `AGENTS.md`:
`a.hominidae` is Pro, and `troup.hominidae` and `brsthomson` are Free. The
coordinator launches each account in a separate process, so its batch-size
environment is account-scoped. Effective settings and the settings-file
fingerprint are recorded in the coordinator receipt.

## Offline Gate

1. Run the focused coordinator/supervisor/auth boundary tests and compile the
   changed Python files.
2. Run a fresh `--plan-only` supervisor invocation with the authoritative DB.
3. Reconcile every per-account manifest and receipt to pending DB rows. Do not
   treat selected rows as complete.
4. Immediately before a live canary, run the exact-account token-only preflight
   through `ensure_account_session(..., allow_bootstrap=False)`.
5. Normal operation must use canonical storage and the documented exact-account
   backup/master-token repair. It must not open a browser, use shared cookies,
   use legacy login, use `--no-sandbox`, or fetch external metadata.

Example plan-only command:

```powershell
python P:/packages/yt-is/scripts/run_unattended_backlog.py `
  --db-path P:/.data/yt-is/batch_status.sqlite `
  --state-path P:/.data/yt-is/unattended-backlog/state.json `
  --output-root P:/.logs/multi_account_fetch/unattended `
  --chunk-size 400 `
  --workers-per-account 3 `
  --account-settings P:/.data/yt-is/unattended-backlog/account-settings.json
```

## Live Progression

The supervisor is plan-only unless `--execute` is supplied. Use a fresh output
root for every new scope. Start with one bounded per-account canary. Promote
only when authentication, database/process locks, no-duplicate selection,
bounded retries, DB reconciliation, manifest receipts, and quality gates pass.

The 2026-08-10 adaptive-policy candidate must not be treated as a throughput
baseline: it was partial (`548/600` complete, `52` failed), its `51`
source-add failures were all known no-caption rows, and its Pro queue did not
exercise scale-up beyond three workers. The fixed control was intentionally
not launched. The explicit no-caption fallback route is still opt-in. Its
oEmbed false-terminal boundary is fixed, but the 12-row retest reached only
external private/unavailable/cookie failures and produced no transcript cache
entries. Use the two packeted canaries as routing evidence only; do not enable
the route globally or launch `--until-empty` from them.

After a successful canary, use a bounded execute invocation with an explicit
`--max-chunks`. `--until-empty` is reserved for an approved full-backlog run;
it is never an implicit scheduler default and now requires
`--account-settings` plus a current `--full-backlog-authorization` receipt.
The version-2 receipt must fingerprint the database, account settings, and exact
pending video-ID set (not only its count), prove exact-account auth, scheduler
execution, cleanup postconditions, residual policy, and throughput validation,
and list readable evidence artifacts. A partial, failed, blocked, stale, or
malformed receipt stops the supervisor and requires classification before a
retry. Source-add `rpc_code=9`, poisoned synthesis, and missing manifest
receipts each retain their own residual policy; they must not be silently
reclassified as auth failures or blindly replayed. A source-count increase
without a returned source ID and authoritative DB reconciliation is not a
source-add success.

## State And Recovery

- The supervisor state and its lock are durable under `P:/.data/yt-is/`.
- A second supervisor for the same database is blocked even when it uses a
  different state path.
- A chunk with a valid identity-checked summary is recovered without relaunch.
- A missing, malformed, mismatched, or internally inconsistent summary fails
  closed.
- Each launched chunk has an atomic `supervisor_runtime.json` receipt with a
  run ID, PID, heartbeat, and lease. A live owner blocks a second launch; a
  dead owner is classified as unexpired-lease or orphaned before any retry.
- The supervisor heartbeat interval is 30 seconds and its default timeout is
  22 hours, below the installer task's 23-hour execution limit. A timeout
  leaves both the runtime and timeout receipts for diagnosis.
- A nonzero coordinator exit never becomes a successful supervisor state.
- A `blocked` supervisor result exits nonzero; scheduler success is reserved for
  plan, bounded pause, and completed states.
- A terminal `completed` state is rechecked against the authoritative DB before
  it is reported again.
- Preserve stdout, stderr, manifests, summaries, timeout receipts, and raw
  event logs. Do not delete worker state or notebooks manually while a run may
  still own them.

Use the read-only health command after a restart or scheduler invocation:

```powershell
python P:/packages/yt-is/scripts/check_unattended_backlog.py `
  --db-path P:/.data/yt-is/batch_status.sqlite `
  --state-path P:/.data/yt-is/unattended-backlog/state.json
```

`healthy` means the recorded state and DB agree. `planned` means no live work
was launched. `needs_attention` means inspect the emitted issues and latest
receipt before resuming.
`active_runtime`, `orphaned_unexpired_lease`, and `orphaned_runtime` are
explicit recovery states. Do not relaunch an orphaned chunk from the same
output directory: reconcile the authoritative DB and exact per-account
receipts first, then start a new output root only under a new decision.

## Scheduling

Keep `YtisNlmAuthKeepalive` separate from the backlog task. Inspection is an
exact configuration check, so pass the arguments of the task that is actually
registered. The current verified task is the interactive-token, plan-only
canary:

```powershell
powershell -File P:/packages/yt-is/scripts/install_unattended_backlog_task.ps1 `
  -Inspect `
  -TaskName YtisUnattendedBacklog `
  -PythonExecutable C:\Python314\python.exe `
  -LogonType Interactive `
  -StatePath P:/.data/yt-is/unattended-backlog/scheduler-canary-state.json `
  -OutputRoot P:/.logs/multi_account_fetch/scheduler_execution_canary_run01 `
  -AccountSettingsPath P:/.data/yt-is/unattended-backlog/account-settings.json `
  -UserId "$env:USERDOMAIN\$env:USERNAME"
```

The bare `-Inspect -TaskName` form uses the installer defaults and is expected
to fail when the installed task intentionally points at another plan-only
state/output root. This is a verification mismatch, not evidence of a login
failure or a broken scheduler task.

Registration uses `MultipleInstances=IgnoreNew`, starts when available, is
battery-safe, has a finite execution limit, and verifies the exported task XML.
The installer defaults to `-LogonType S4U` for a logged-out token-only task on
this machine's local `P:` volume. Use `-LogonType S4U -UserId DOMAIN\\user`
explicitly, or use `-LogonType Password -Credential` when the scheduled
identity needs network credentials. `-LogonType Interactive` remains
available for foreground/diagnostic use but is not evidence of logged-out
operation. XML verification checks the principal user, logon type, run level,
action, working directory, and execution limit. A real S4U/password task
registration and logged-out canary still require operator/OS approval and are
not proven by repository tests.
The installer defaults to plan-only, one chunk, and no `--until-empty`. Use
`-Execute` and `-UntilEmpty` only after the live canary and full-backlog
authorization receipt authorize them; pass `-AccountSettingsPath` and
`-FullBacklogAuthorizationPath` for a full drain. A template is at
`P:/packages/yt-is/config/full-backlog-authorization.example.json`. The installer exposes the same explicit
`-RouteSourceAddFailuresToFallback` and
`-RouteSourceAddressabilityFailuresToFallback` policy switches as the Python
supervisor; leave both off unless their route-partitioned canaries are
promoted. Scheduler task registration is an operational change; verify the
task action and health receipt after registration.

When all five gates are independently evidenced, generate the version-2
authorization with the receipt builder. It reads the current database in
read-only mode, records the exact pending-ID fingerprint, rejects missing gates
or unreadable evidence, and refuses to overwrite an existing receipt. It does
not perform the gates or launch work; the resulting JSON still requires human
review before it is passed to `--until-empty`.

Full-backlog authorization is intentionally limited to the canonical ordered
account set `a.hominidae,troup.hominidae,brsthomson`; the builder and supervisor
reject subsets, unknown profiles, and reordered accounts. Experiments may use
subsets, but they cannot produce a full-drain authorization receipt.

```powershell
python P:/packages/yt-is/scripts/build_full_backlog_authorization.py `
  --db-path P:/.data/yt-is/batch_status.sqlite `
  --account-settings P:/.data/yt-is/unattended-backlog/account-settings.json `
  --accounts a.hominidae,troup.hominidae,brsthomson `
  --evidence P:/.logs/multi_account_fetch/<auth-receipt>.json `
  --evidence P:/.logs/multi_account_fetch/<scheduler-receipt>.json `
  --evidence P:/.logs/multi_account_fetch/<residual-policy>.md `
  --evidence P:/.logs/sharded_lane_series/<throughput-receipt>.md `
  --gate exact_account_auth=passed `
  --gate scheduler_execution=passed `
  --gate cleanup_postcondition=passed `
  --gate residual_policy=passed `
  --gate throughput_validation=passed `
  --expires-at 2026-08-11T12:00:00Z `
  --output P:/.data/yt-is/unattended-backlog/full-backlog-authorization.json
```

## Throughput Decision

Throughput optimization is a separate evidence gate. Compare Pro and Free
independently across worker counts, batch sizes, and adaptive policies using
fresh repeated control-versus-candidate soaks. Use sustained current-contract
VPH, stable repeatability, failure/retry/source-age tails, quality, and resource
limits. Reject high-water-only, tainted, duplicated, parallel-overlapped, or
tautological metrics. Report the best validated configuration and uncertainty,
not mathematical optimality.

The executable throughput-pair validator also requires identity-bearing event
logs. The shared JSONL logger attaches `run_id` and `account_profile` from the
coordinator environment, while preserving explicit event values. Action names
alone are insufficient: an old or copied event directory can contain the right
families without belonging to the packet's run.

## Completion

The backlog is complete only when the authoritative DB has no pending rows and
the final receipts reconcile every selected ID. Otherwise report each residual
as completed, retryable, blocked, poisoned, or irrecoverably missing with the
exact evidence and next action. Do not claim unattended readiness while a
required canary, reconciliation, scheduler verification, or residual policy
is merely planned.

## Residual Fallback Canary Evidence (2026-08-11)

The command residual fallback packets are under
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run02/`
and
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run03/`.
Run02 is configuration-limited: its `120s` per-item deadline left Whisper
`0.1s` after the earlier fallback stages and the reserved transcription
margin. Do not interpret that result as auth or provider failure.

Run03 used fresh staging, the exact same ID, and the normal `900s` fallback
deadline. It completed one row through Whisper in `257.065s`, produced a
non-empty `1125`-character cache entry, passed DB/cache integrity and receipt
reconciliation, and left no process behind. Its event trace contains no
NotebookLM source/add/materialization/content action, and canonical DB/cache
timestamps were unchanged. This is bounded route evidence only. It does not
authorize the fallback route globally, the other residual classes, or a full
backlog; semantic quality was not independently scored.
