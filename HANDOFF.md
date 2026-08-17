# yt-is Handoff

**This is the package-local operational reference for `yt-is`.** For active work
streams, see the integration handoff chain at the bottom of this file.

Last updated: 2026-08-12 (hot-path throughput loop outcome: ceiling
classification subject to a blocked no-patch lever; current-state
reconciliation after invalidated
throughput validation run01, source-add
fallback-only canary and exact promotion, production-shaped
health canary run01, bounded unattended chunk 0002 partial,
fallback recovery, source-add initial-window canary run01, residual audit and
plan refresh, exact canonical promotion, source-add fallback canary run10,
terminal-guard validation run08, run09 RPC9 invalidation, source-add pacing
control abort, current residual-policy refresh, source-add fallback routing
canary run01, fallback provenance repair, source-add fallback quality canary
run01, source-class isolation canary run01, source-class isolation canary
run02, S4U registration recheck, and
unattended supervisor durability hardening).

### Hot-path throughput loop outcome (2026-08-12)

The hot-path optimization loop reached the blocked-gap end state with no live
benchmark launched: the operational leader is cadence01 at `3636.16` combined
hot-path VPH (`793/7/800`, zero `source_age_cliff`, command time `2652.777s`,
clean smoke at `3759.6` and valid soak, `status=ok`, `throughput_valid=true`,
`3+3`, `home_300mb`) — the highest current-contract home-network result with a
clean profile; not proven optimal, control variance unresolved. The
numerically higher projection-60 run02 soak at `3788.53` (`794/6/800`) is a
diagnostic soak, not a control: measured from an ungated launch whose smoke
promotion gate failed (`2788.30` VPH, `67` `source_age_cliff`, `72` failures),
single unpaired sample from a closed branch. The historical non-reproduced
high-water mark `5572.04` stays separate. Classification:
`ceiling_subject_to_blocked_gap` — every tested lever has explicit
negative/invalidated evidence (margin20/25/15, projection-60 gated rerun,
retry-queue-fix, local-retry projection, first-window cap, rotation-180,
shared retry, warmup-state, auth intervals, worker balance, agecap-200, active/
extract windows, cadence tuning), and the sole remaining lever (batch-1
old-window `nlm source content` latency) is blocked by the no-patch design
packet. What's left: a new code mechanism narrower than the existing
projection/retry guard path that reduces old-window command latency or
source-age accumulation directly. What's blocked: any hot-path live benchmark
until that mechanism exists and passes the design packet's test coverage, then
one bounded run with an adjacent current-control in the same cohort/time
window. Decision packet:
`docs/operations/packets/batch1-old-window-source-content-latency-2026-08-12.md`
(`no run — packet failed`); per-lever dispositions and ceiling row in
`docs/operations/test-registry.md` "Hot-path throughput loop outcome
(2026-08-12)".

## Current authoritative snapshot (2026-08-12)

The following state supersedes older dated sections in this file. Historical
packets remain useful evidence, but their counts and readiness verdicts are not
current unless they are named below.

The active database `P:/.data/yt-is/batch_status.sqlite` currently reports
`integrity_check=ok`, `complete=10,359`, `failed=253`, and `pending=332,507`
after the exact promotion from the source-add fallback canary below.
The current package-local requirement-by-requirement audit for the active Codex
goal is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/goal_completion_audit_after_source_add_canary.md`.
It is a current reconciliation receipt, not a full-backlog authorization. The
older similarly named receipt at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_goal_completion_audit_current.md`
is the pre-canary baseline; the similarly named receipt at
`P:/.logs/multi_account_fetch/20260812_ytis_goal_completion_audit_after_run10.md`
is preserved as historical compatibility evidence and is not the current
source of truth.

### Throughput validation run02 invalidation and log-root contract (2026-08-12)

The fresh packet at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_throughput_validation_run02_unknown/throughput_pair_packet.json`
is invalidated throughput evidence. Exact token-only preflight passed for all
three accounts, but pair-01 control encountered worker-level
`source list`/source-add failures before adaptive or pair-02 launched. The
failure was concentrated in worker-03 (`53` RPC5 rows for `a.hominidae` and
one RPC9 row for `troup.hominidae`); no valid VPH was produced. Do not replay
this cohort. The current diagnosis is a worker/session/notebook-readiness
boundary, not proof that static auth storage or account login failed. The
installed client statically supports email-valued `authuser` routing, but the
live RPC route was not captured, so no routing repair is claimed yet. New
clients now emit `nlm_client_account_binding_checked` with redacted-safe
identity metadata and fail closed if the runtime client email, `authuser` route,
or storage path disagrees with the exact account profile.

New yt-is runtime logs belong under `P:/packages/yt-is/.logs/`. Multi-account
children explicitly set `INTELLIGENCE_STREAM_LOG_DIR` to their package-local
experiment event directory, and the shared logging fallback now resolves from
the package path rather than the process current directory. `P:/.logs/` is
legacy compatibility evidence only; retain it for historical cleanup and do
not use it as the default for new runs. Durable auth/database state remains
under `P:/.data/yt-is/`.

### Source-add fallback canary and exact promotion (2026-08-12)

The current package-local result is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/source_add_fallback_canary_result.md`.
The three-ID fallback-only canary passed immediate token-only auth for
`a.hominidae`, `troup.hominidae`, and `brsthomson`, recovered
`C7yod85fqCs` through Whisper with `9,408` characters, and left
`w9cxJdazkEs` as `no_transcript` and `yLSnkG9yLbA` as a bounded Whisper
timeout/deadline exhaustion. No direct NotebookLM source-add action occurred.
The recovered row passed a separate dry-run/apply promotion gate and is now
canonical `complete`; the other two remain `failed / Source add failed`.
The rebuilt current audit, packet set, and non-authorizing policy gate are in
the same experiment directory under `residual_audit_after_canary.json`,
`retry_packet_set_after_canary/`, and `residual_policy_after_canary/`.
This is a positive bounded recovery result for one exact ID, not permission to
enable fallback by default, replay RPC9, or claim full-backlog/VPH readiness.

### Invalidated throughput validation run01 (2026-08-12)

The fresh packet at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_throughput_validation_run01/throughput_pair_packet.json`
was not a valid throughput result. It used `caption_state=any` with the
no-caption fallback route disabled; all `2,553` IDs in `pair-01` had
`has_captions=0`. The control arm hit one typed RPC9 source-add failure for
`iXw4qwy5Ld4` on `brsthomson` worker-01, so the coordinator aborted control,
did not launch adaptive or pair-02, and marked VPH invalid. The canonical row
remains pending because the arm used isolated staging databases. The complete
classification is in
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_throughput_validation_run01/invalidation_decision.md`.
Do not replay this same shape; use a captioned/unknown NotebookLM cohort or
explicitly route known no-caption items to fallback.
The direct throughput-pair planner now rejects `no-caption` and `any` cohort
states because its packet has fallback disabled; fallback-dependent selection
belongs to the backlog runner, where the route is explicit and receipt-backed.

The promotion helper's first receipt used main-file-only hashes that were not
WAL-safe; this was corrected in
`scripts/promote_exact_fallback_results.py` with focused regression tests.
The logical postcondition and direct current row/cache/integrity checks remain
the authoritative proof for this promotion.
The pending-only residual packet/gate snapshot named in older sections is
historical; the current package-local snapshot is recorded below and must be
rebuilt after any further database change or expiry.

### Pre-canary residual reconciliation and plan refresh (2026-08-12)

The following artifacts are the pre-canary baseline after bounded unattended
chunk 0002. They remain useful historical evidence but are not current launch
authority:

- Audit: `P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/residual_audit.json`
- Packet set: `P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/retry_packet_set/`
- Pending-only gate: `P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/policy_gate/residual_policy_receipt.json`
- Plan-only supervisor state: `P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/unattended_plan/state.json`

That baseline reported `254` failed rows and `pending=332,507`. The current
post-promotion audit is the package-local receipt named in the authoritative
snapshot above; it reports `253` failed rows, `pending=332,507`, and one exact
quality-gated fallback recovery. Rebuild the packet/gate after any database
change or gate expiry; do not reuse either old baseline as launch authority.

### Bounded unattended chunk 0002 partial (2026-08-12)

The live result receipt is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_production_canary_reconciliation_run01/unattended_plan/chunk-0002/result_receipt.md`.
It ran 400 current pending IDs with the exact account policy, completed
`343/400`, and classified `57/400` as typed source-add `RPCError` code 9
failures. All three token-only account preflights matched the expected
identities; this was not an authentication failure. The supervisor stopped
with a partial result, left no active worker, and did not authorize a repeat.
This is operational health evidence, not a valid sustained-VPH comparison or
full-backlog authorization. Do not rerun the failed IDs or the same chunk
shape until a new source-add mechanism packet and exact retry policy exist.

The raw source-add events were reduced offline without external calls by
`P:/packages/yt-is/scripts/analyze_source_add_rpc9.py`. The current report is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/source_add_rpc9_analysis.md`
and its JSON twin. It counts `400` completed source-add rows for `400` distinct
videos: `343` `ok` and `57` typed `RPCError` code `9` failures. The
completion-conditioned distributions attribute those failures as `17` to
`a.hominidae`, `23` to `brsthomson`, and `17` to `troup.hominidae`; worker
counts are `22/21/14`. These are associations from one bounded cohort, not a
causal provider diagnosis, retry authorization, or live-throughput result.
The reducer's focused tests pass (`5`), and its report intentionally keeps
attempt-row and distinct-video denominators separate.
The governing decision packet is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/source_add_rpc9_decision_packet.md`;
it keeps RPC9 non-retryable and authorizes no same-shape replay.

### Wiki queue and historical manifest state

The authoritative wiki queue currently reports `pending=0`, `in_progress=0`,
`poisoned=0`, `needs_resynthesis=0`, `completed=47`, and `failed=2`.
The three previously poisoned synthesis items already have final semantic
records and validated current pages; do not reopen them. The two remaining
failed records have `0 pages` and no exact worker/profile/attempt receipt, so
they remain deferred rather than being retried or fabricated.
The fresh read-only manifest audit is
`P:/.logs/wiki-yt-queue/20260812/manifest_gap_audit_current_after_goal_continuation.md`.
It reports `13` historical manifest gaps (`12` output/provenance without an
exact receipt and `1` with no local output), `8` unmanifested degraded pages,
and `recovery_eligibility=ineligible_read_only_audit`. No manifest/page/queue
mutation is authorized from that audit alone.

### Production-shaped health canary run01 (2026-08-12)

The bounded live canary receipt is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_production_shape_health_canary_run01/result_receipt.md`.
It selected 30 current pending IDs, ten per exact account, and ran with the
configured account policy: Pro adaptive `3..5`, both Free accounts fixed at
three workers, `batch_size=50`, parallel accounts, and fallback routes off.
All three token-only preflights observed the expected account email and all
30 selected IDs reached canonical `complete`. Raw reconciliation parsed 348
JSONL events with no parse errors, RPC9/failure fields, non-empty stderr, or
teardown warning; no yt-is worker process remained. This is bounded health and
policy-wiring evidence only, not sustained-VPH or full-backlog authorization.

Staging SQLite cleanup is implemented in
`P:/packages/yt-is/csf/cleanup_staging.py` and documented at
`P:/packages/yt-is/docs/operations/staging-artifact-cleanup.md`. Use
`python -m csf.cleanup_staging --dry-run` before any applied sweep; the
default scans the package-owned
`P:/packages/yt-is/.logs/multi_account_fetch` root plus the legacy
`P:/.logs/multi_account_fetch` compatibility root. New supervised and direct
runs use the package-owned root. Existing supervisor state keeps its recorded
legacy output root on restart so path migration cannot orphan a live chunk.
The canonical batch DB, browser profile root, receipts, and durable fallback
queue are protected; the one-hour activity guard is intentional.

### Status and fallback provenance invariant

`analysis_status` now freezes `last_stage` and `failure_reason` when a row is
already `complete`, including through the bulk `set_status_batch` writer. A
failed-to-complete fallback may intentionally retain the original source-add
failure as audit provenance. Residual/retry tooling must therefore select
`status='failed'`; a non-null `failure_reason` on a completed row is not a
retry candidate. Regression coverage is in
`P:/packages/yt-is/tests/test_batch_status.py`.

### Adaptive health-window capacity boundary (2026-08-12)

The adaptive scheduler's health window remains a window of completed worker
results, as specified by the implementation plan. A result from a worker slot
above the current `target_workers` is excluded before the window is evaluated;
otherwise a slot that was disabled or drained could authorize a new scale-up
step. The check preserves the normal completion-boundary behavior: filtering
by currently in-flight workers would deadlock scale-up because health samples
are recorded only after a worker future completes. The regression is
`test_health_from_slot_above_current_target_cannot_authorize_scale_up` in
`P:/packages/yt-is/tests/test_adaptive_worker_scheduler.py`.

Verification after this correction: the adaptive scheduler/coordinator suite
passed `33` tests, the affected source/coordinator/status/backlog suite passed
`239` tests, compilation passed, and `git diff --check` passed. This is an
offline safety correction; it is not live adaptive evidence, a VPH result, or
full-backlog authorization.

The fresh full package run after the source-add promotion-observability fix
passed `1,575` tests with `4` expected OCR skips because `easyocr` is not
installed (`1,579` collected). This is the current offline regression receipt
and does not change the live-readiness boundary below.

### Source-class isolation canary (2026-08-12)

The fresh bounded canary at
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run01/`
used 24 pending IDs from the canonical local catalog, split evenly between the
Run09 high-risk channel `UCHXy48aYSYeRaBuMOedwxAQ` and a clean control channel.
Each of `a.hominidae`, `troup.hominidae`, and `brsthomson` processed four IDs
from each class with one worker, `batch_size=1`, serialized staging databases,
and account pacing disabled. Exact token-only auth passed immediately before
each child.

The high-risk class produced `11/12` failures versus `0/12` for the control;
all 11 were typed `SourceAddError` RPC code 9. The staging databases remained
integrity-clean and canonical hashes were unchanged. This is a fresh source-
class association with complete per-account attribution, not proof of a
causal provider defect, not a VPH measurement, and not permission to enable a
source filter or issue a direct RPC9 retry. A disjoint repeat with its own
falsifier is required before any promotion decision. Do not replay these IDs.

The governing packet is
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run01/decision_packet.md`.

The required disjoint repeat completed at
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run02/`.
It used another 24 pending IDs with the same three exact accounts, one worker
per account, `batch_size=1`, serialized staging, and immediate token-only
preflight before each account arm. The high-risk channel class produced
`12/12` typed RPC9 source-add failures; the clean control produced `0/12`
RPC9 failures and `11/12` complete outcomes, with its one failure being
`nlm_content_below_threshold`. Raw reconciliation found no outside IDs,
duplicate attempt identities, auth errors, or surviving Python processes;
staging integrity passed and canonical DB/cache hashes were unchanged.

This reproduces a strong source-class association across the account mix but
still does not prove causality, authorize a default source filter, authorize
direct RPC9 replay, promote fallback, or establish VPH. The governing result
receipt is
`P:/.logs/multi_account_fetch/20260812_source_class_isolation_canary_run02/result_receipt.md`.
The `troup.hominidae` child also emitted a post-completion Python 3.14 Windows
`Cancelling an overlapped future failed` / `WinError 6` warning. It did not
invalidate this attribution. The traceback identifies the Windows Proactor
pipe-teardown path; `csf/nlm_client.py` now applies an explicit Windows
selector loop as the narrow package-owned mitigation instead of the deprecated
process-wide policy. The change is covered offline, but the historical live
warning remains unrevalidated until a fresh bounded canary; do not treat this
as proof that unattended readiness is complete.

### Windows NotebookLM client teardown correction (2026-08-12)

`NLMSyncClient` now uses an explicit `asyncio.SelectorEventLoop` on Windows
for all owned loops. This follows the installed `notebooklm-py` runtime's own
Windows workaround while avoiding its deprecated global policy API. It is a
targeted mitigation for the observed teardown warning, not yet a live root-
cause proof. It does not change auth, source-add retry, account routing, or
NotebookLM behavior.
The focused client/worker suite passed `40` tests; compilation and
`git diff --check` passed. A fresh exact-account canary must still inspect
stderr for recurrence before this is promoted to a live-runtime health gate.

### Selector-loop health canary run01 (2026-08-12)

The fresh bounded canary at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_selector_loop_health_canary_run01/`
immediately preflighted `a.hominidae`, `troup.hominidae`, and `brsthomson`
with bootstrap disabled. It selected three fresh uncached pending IDs,
serialized one-worker `batch_size=1` children, and used the package-owned log
root. All three rows completed, all three source-add attempts returned `ok`,
and no `rpc_code=9` occurred.

Raw reconciliation found three unique manifest IDs, matching final DB status,
`PRAGMA integrity_check=ok`, coordinator run-ID propagation in every event
stream, zero account stderr bytes, no `WinError 6`/overlapped-future warning,
and no surviving yt-is worker process. The one-hour staging grace correctly
left the just-finished root intact. The detailed receipt and claim ledger are
at `P:/packages/yt-is/.logs/multi_account_fetch/20260812_selector_loop_health_canary_run01/result_receipt.md`.

This validates the selector-loop mitigation for this bounded runtime path only.
It does not fix source-add RPC9, establish VPH, prove adaptive scale-up, or
authorize fallback or full-backlog operation.

### Unattended receipt and runtime identity hardening (2026-08-12)

The supervisor no longer treats child-reported `account_settings` as
authoritative. It derives the expected per-account worker, batch, and adaptive
policy from the validated configured settings and compares both the summary and
child execution settings against that result. This closes a
self-consistent-but-wrong receipt path. The focused supervisor suite passed
`52` tests; the regression is
`P:/packages/yt-is/tests/test_run_unattended_backlog.py::test_summary_cannot_self_authorize_wrong_account_policy`.

The read-only health checker now verifies a live runtime PID's command line
contains the recorded coordinator entrypoint and output root. A reused or
unrelated PID yields `runtime_process_mismatch`; an uninspectable live PID
yields `runtime_process_inspection_failed`. The focused checker suite passed
`17` tests. The supervisor recovery path now applies the same ownership rule
instead of treating any live numeric PID as active: it checks the coordinator
command line and output root, and records a process creation timestamp when the
host permits it. Atomic JSON and text receipts flush and `fsync` their
temporary file before replacement. The task installer rejects
missing/unreadable database, cache, authorization/settings files, missing
state/output parents, and a missing supervisor script before registration.
The focused supervisor suite passed `57` tests, with Python compilation and
PowerShell parser verification also passing. These corrections remain
offline-only and do not establish Task
Scheduler execution, adaptive restart persistence, live throughput, or
full-backlog authorization.

The exact current residual audit is
`P:/.logs/multi_account_fetch/20260812_residual_audit_after_source_add_canaries.json`:
`25` command, `12` content-threshold, `2` external-cookie, `2`
empty-transcript, `2` no-transcript, `2` source-add, `1` fallback-quality,
`142` unavailable, and `9` `whisper_timeout` rows;
`unknown=0` and `requires_decision_packet=51`.
The two current `source_add` IDs are not fresh fallback candidates. The exact
prior canaries already classified `yLSnkG9yLbA` as bounded-fallback deadline
exhausted and `w9cxJdazkEs` as terminal `no_transcript`. The current residual
closure packet is
`P:/.logs/multi_account_fetch/20260812_source_add_residual_closure_after_prior_fallback.md`.
Keep both failed/deferred; do not repeat the same fallback mechanism or replay
RPC9 without a new mechanism and reviewed packet.
The refined offline RPC9 distribution review is
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_distribution_after_run09.md`.
It identified an initial-window hypothesis but did not by itself authorize a
patch, replay, or throughput run. The narrow opt-in mechanism is now implemented
and locally verified; its current decision packet is
`P:/.logs/multi_account_fetch/source_add_initial_window_decision_packet_current.md`.
The setting remains disabled by default. The live run01 canary applied the
candidate cleanly but used a disjoint control/candidate cohort, so it produced
no valid causal or throughput result. Its receipt is
`P:/.logs/multi_account_fetch/source_add_initial_window_canary_run01_result_receipt.md`.
The packet does not authorize direct RPC9 replay, candidate promotion, or
full-backlog operation.

The fresh same-cohort run03 replacement then hit a nonce-matched RPC9 in the
pair-01 control for `troup.hominidae` before the candidate ran. Exact
token-only preflight passed for all three identities; the control was
terminated with `termination_confirmed=true` and `vph_valid=false`. The
candidate was withheld, so the smaller-window mechanism remains unevaluated.
The durable diagnosis is
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_run02_run03_diagnosis.md`;
this is negative mechanism evidence, not an auth or throughput result.
The next opt-in account-pacing packet at
`P:/.logs/multi_account_fetch/20260812_source_add_account_pacing_pair_run02/`
also passed all three exact token-only preflights, then aborted
`pair-01/control` on a nonce-matched RPC9 for `a.hominidae` after `42`
successful adds. The pacing candidate and pair-02 were withheld. This is a
fresh control recurrence with no causal pacing result; do not replay the
cohort, direct-retry RPC9, or enable the gate by default. The implementation
is covered by `9` process/functional gate tests, `182` batch tests, and `47`
throughput-pair tests. See the packet result receipt for exact paths and
counts.
The follow-up candidate-only pacing run at
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/`
then tested the gate directly on a fresh `18`-video Pro scope. Exact token-only
preflight passed; all `18/18` source-add attempts acquired the `2.0s` account
gate, but `ZHYqjD099Aw` still produced typed `ADD_SOURCE rpc_code=9`. The
existing read-only reconciliation saw one listed source and then the source
reached terminal materialization status `3`; the run ended `15/18` complete.
This falsifies pacing as the RPC9 fix for this cohort. The Free partitions
were withheld after the Pro abort, and their concurrent setup attempts were
blocked by the shared staging DB lock before NotebookLM work. Receipt:
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/result_receipt.md`.
Keep the gate disabled by default; do not replay the same cohort or claim a
throughput result. The next source-add branch must change the provider/identity
mechanism and carry a new falsifier, abort gate, and promotion rule.
The follow-up offline distribution audit then joined the `42` per-video typed
RPC9 outcomes to the local `analysis_status` catalog without external fetches:
`25/25` videos from channel `UCHXy48aYSYeRaBuMOedwxAQ` failed across all three
accounts, while the remaining `17` failures were spread across `12` channels.
The same run used `18` distinct notebook IDs for `9` worker profiles, with no
ID shared across profiles (each profile rotated two notebooks across its two
source windows). This weakens shared-notebook ownership as the leading cause
and elevates source/provider addressability as the next offline-supported
hypothesis. It remains a hypothesis: no source replay, external metadata
fetch, default filter, or direct RPC9 retry is authorized. The detailed packet
is `P:/.logs/multi_account_fetch/20260812_source_add_rpc9_distribution_after_run09.md`.
The source-add path now emits per-attempt identity/timing events around the
existing typed mutation call (`nlm_batch_source_add_attempt_started` and
`nlm_batch_source_add_attempt_completed`). The change is covered by the full
`182`-test `test_nlm_batch.py` suite and is observability-only; it does not
claim to fix RPC9, prove cross-process notebook uniqueness, or authorize a
throughput run.

### Source-add fallback routing-gap repair (2026-08-12)

The fresh source-class canary showed that a typed `SourceAddError` with
`rpc_code=9` can be followed by read-only source-ID recovery and then terminal
materialization. Before this repair, `csf/nlm_batch.py` collapsed that chain to
`Source materialization terminal error`, so the explicit
`--route-source-add-failures-to-fallback` predicate could not recognize it.
The code now preserves the bounded typed error in the per-video failure
message for both terminal materialization and materialization-timeout paths;
the existing `Source add failed` marker makes the opt-in route addressable.
The repair does not retry RPC9 and does not change the default-off policy.
It is locally verified by `184` `test_nlm_batch.py` tests and `79`
`test_csf_source_fetch_timing.py` tests. The decision packet is
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_routing_gap_fix_current.md`.
The routing half was then live-validated on a fresh disjoint three-ID staging
canary at
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_routing_gap_canary_run01/`:
all `3/3` typed RPC9/materialization failures were admitted exactly once to the
explicit fallback route, with one `ADD_SOURCE` attempt per ID and no direct
RPC9 retry. Canonical databases and cache were unchanged, staging integrity
and cleanup passed, and no owned process remained. The run used a deliberately
short `30s` fallback deadline, so all three fallback attempts ended
`transcript fallback deadline exhausted`; fallback quality remains unproven and
the default route remains off. Do not replay those IDs.

That canary also found that failed fallback finalization replaced the original
source-add/RPC9 admission reason with generic `unknown` text in the staging
status and durable queue. `bin/csf-source` now preserves real upstream
provenance through both parallel and surgical fallback finalization while
excluding synthetic route labels. The added regression coverage is included in
the `79` focused tests above. The canary is classified
`routing_pass_quality_unproven_provenance_loss_fixed_locally`; a future quality
canary needs a fresh disjoint manifest, normal fallback deadline, and the
existing 500-character gate. The fresh quality canary at
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_quality_canary_run01/`
then used a new pending ID, isolated staging, the normal `900s` deadline, and
immediate exact token-only auth. It passed routing and provenance: one typed
RPC9 failure, one exact fallback admission, one successful fallback
completion, no direct retry, preserved source-add reason in staged status and
durable queue, staging integrity and cleanup passed, and canonical hashes were
unchanged. Its transcript was only `301` characters, below the existing `500`
character promotion gate, so no promotion occurred. The current
classification is `routing_pass_provenance_pass_quality_gate_failed_default_deferred`;
the default route remains off and this does not authorize full-backlog
operation or throughput claims. The retained failure reason on the completed
status row is intentional provenance preservation in
`csf/batch_status.py:set_status`.
The fresh identity canary at
`C:/Users/brsth/AppData/Local/Temp/yt-is-source-add-identity-canary-run06/`
passed exact token-only auth preflight for all three accounts and completed
pair-01 control, but was invalidated by a runner defect: post-run validation
treated mutable staging DB changes as provenance drift, and the old executor
launched pair-02 control after the failed gate. The fix in
`scripts/run_throughput_pair.py` separates immutable artifact validation from
mutable staging validation and stops all later arms after any failed gate.
The run is not throughput evidence; see
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_run02_run03_diagnosis.md`.
The fresh replacement run07 in
`C:/Users/brsth/AppData/Local/Temp/yt-is-source-add-identity-canary-run07/`
passed all three exact token-only preflights and all observed source-add
attempts without RPC9. Its control was correctly stopped after two
`nlm_content_below_threshold` results on `a.hominidae`; no adaptive or second
pair ran. Treat this as auth/source-add-path and executor-safety evidence only,
not a VPH result or full-backlog authorization.
The lower-level `scripts/prepare_throughput_pair.py` command is staging-only
and never launches workers. The wrapper `scripts/run_throughput_pair.py` is the
only path that adds executable packet metadata and performs the separate
explicit execution step; malformed executable receipts now fail closed without
raising during validation. The planner/coordinator boundary is covered by
`47` focused tests.

### Logged-out Windows scheduler boundary (2026-08-12)

The installed `YtisUnattendedBacklog` task remains the verified interactive-
token, plan-only canary. A fresh registration-only recheck attempted a
separate `YtisUnattendedBacklogS4UPlanCanary` identity with a new state/output
root and `-LogonType S4U`; Windows returned `Register-ScheduledTask: Access is
denied` before task creation. The fresh task, state path, and output root were
all confirmed absent, and the existing task was not changed. Receipt:
`P:/.logs/multi_account_fetch/20260812_scheduler_s4u_registration_recheck_run01/result_receipt.md`.

This is an OS permission boundary, not a NotebookLM authentication failure.
It does not authorize a full drain. The next step requires an elevated or
operator-managed Windows registration context for the exact user (or an
explicitly approved password-backed principal); do not request another
NotebookLM login or substitute `SYSTEM`, shared cookies, legacy login, or
`--no-sandbox`.

### Coordinator cache-path receipt hardening (2026-08-12)

`scripts/run_multi_account_fetch.py` now carries an explicit transcript-cache
path through prepared account specs, child environments, and every coordinator
summary. A direct caller can no longer produce a receipt naming the ambient
cache when it supplied an isolated cache. The regression boundary is
`tests/test_run_multi_account_fetch.py` (`62 passed`), including a
discriminating ambient-versus-explicit path test. This is receipt/path
integrity hardening; it does not change the active live run or authorize full
backlog execution.

The current exact packet set regenerated from that audit after the pacing
control abort is
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_current_after_pacing/`.

### Current residual-policy refresh (2026-08-12)

The earlier residual packet set and pending-only gate were stale relative to
the current database snapshot. The exact current audit was re-materialized
without changing SQLite at
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_current_after_pacing/`.
The matching pending-only policy gate is
`P:/.logs/multi_account_fetch/20260812_residual_policy_gate_pending_only_current_after_pacing/`.
Its receipt records `332,940` pending rows and `197` failed rows, keeps every
failed row deferred, and expires `2026-08-13T12:00:00Z`. The packet builder
reported `database_mutated=false`; the gate builder accepted only the exact
audit and packet-set fingerprints. This is current residual-policy evidence
only. It does not authorize failed-row recovery, fallback promotion,
throughput claims, scheduler registration, or a full-backlog run.

### Offline readiness-guard hardening (2026-08-12)

The direct coordinator now refuses live `--all-pending` scopes over `400` rows
unless the supervisor supplies a matching `supervisor_runtime.json` ownership
marker for the same database and output root. Plan-only, bounded, and exact
manifest paths remain available; this is a scope guard, not full-backlog
authorization.

Executable throughput-pair validation now binds the packet to its canonical
database/cache, in-root arm artifacts, exact receipts, summaries, selected IDs,
and event-log roots. JSONL event records receive non-sensitive `run_id` and
`account_profile` envelope fields from the coordinator environment, and
executable validation fails when those identities are absent or mismatched.
The identity propagation boundary was adversarially checked: coordinator child
launches now set `YTIS_INDUSTRIAL_RUN_ID` to the packet run ID, and
`bin/csf-source` preserves that value instead of replacing it with a fresh
child UUID. This prevents valid future throughput artifacts from failing
provenance validation because explicit adaptive events carry a different run
identity.

Full-backlog gate evidence is now a version-2 sidecar contract. Each gate needs
a distinct structured gate-specific artifact with required claims; a generic
non-empty file or one artifact reused for multiple gates is rejected. This
validator checks the evidence contract and bindings only; it does not perform
auth, scheduler execution, cleanup, residual analysis, or throughput work.

Verification after these changes: the full package suite reports `1503 passed,
4 skipped`; the identity/provenance-focused suites report `162 passed`, and the
wiki-yt suite reports `95 passed`; compilation and touched-document diff checks
pass. No live
fetch, auth bootstrap, scheduler registration, external fetch, stage, commit,
or push occurred. The readiness boundary therefore remains unchanged:
`full_authorization=false` and logged-out scheduler execution is unverified.

The pending-only residual-policy gate receipt is
`P:/.logs/multi_account_fetch/20260812_residual_policy_gate_pending_only_current_after_pacing/`.
It is a narrow `residual_policy=passed` evidence artifact for draining pending
rows while leaving all `197` failed rows deferred. It authorizes no retry,
fallback promotion, quality conclusion, throughput claim, scheduler change,
or full-backlog execution.
The cross-run retry-admission ledger is
`P:/.data/yt-is/unattended-backlog/residual-attempt-ledger.json`.
It contains `65` historical applied requeue receipts covering `324` unique
IDs. Future exact requeues must provide a unique attempt ID, mechanism ID,
falsifiable hypothesis, account scope, and decision-packet fingerprint; the
guard rejects same-mechanism overlap with a prior attempt.
This ledger guards the reviewed exact-requeue command only; it is not a
quality proof and does not cover the coordinator's separate in-run industrial
fallback queue. If the database changes but ledger finalization or the
post-transition read fails, the command writes a failure-bearing receipt and
fails closed pending reconciliation.
The coordinator-owned opt-in fallback queue now persists admitted worker
failures in a SQLite queue under each account/run state root. On startup, after
the database-scoped run lock is held, prior claims are returned to `queued` and
the exact `video_id`, source URL, `skip_notebooklm` route, and failure class are
recovered. Completion and terminal failure are idempotent, and completed rows
are never resurrected. Standalone `csf-source` remains memory-only unless the
explicit durable-queue environment variables are supplied. This improves
restart recovery but is not a blanket fallback policy, source-add fix, quality
proof, or full-backlog authorization. The implementation receipt is
`P:/.logs/multi_account_fetch/20260811_durable_fallback_queue_implementation_receipt.md`.
The bounded restart canary then recovered one terminated in-flight claim,
replayed it once through the NotebookLM-free route, and finalized the typed
failure consistently in the durable queue and staged batch DB. Its receipt is
`P:/.logs/multi_account_fetch/20260811_durable_fallback_queue_restart_canary_run01/result_receipt.md`.
This proves the failure-path restart contract for the opt-in queue only; it is
not a fallback success-rate estimate or full-backlog authorization.
The 98 source-add recoveries are complete; the remaining 97 source-add
manifest rows are terminal unavailable or deadline-exhausted unknown outcomes,
not a permission to blindly requeue or replay `rpc_code=9`.

### Source-ready gate recovery canary run05 (2026-08-11)

The run05 packet and result receipt are
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_canary_run05/decision_packet.md`
and
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_canary_run05/result_receipt.md`.
Immediate token-only preflight passed for all three exact identities. The
changed source-readiness path produced exact READY evidence on `161/161`
successful waits, with no wait/content event-order violations. Pair01 control
and adaptive completed `54/54`; pair02 control was partial at `53/54` because
`9WfjJl2JGoE` remained present but status `3` for `604.553s`, and pair02
adaptive was withheld by the control gate. The timeout continuation classified
the exact row and did not extract it. This is partial mechanism evidence only:
it is not a valid throughput comparison, worker-setting recommendation,
authentication diagnosis, full-backlog authorization, or optimality proof.
The timeout was the final selected item, so live evidence of a timeout followed
by a later selected sub-batch remains open; the focused unit test covers that
control-flow shape.

### Source-ready gate validation run06 (2026-08-11)

Run06 is the latest fresh source-readiness packet:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_scaleup_run06/`.
Immediate token-only preflight passed for `a.hominidae`, `troup.hominidae`,
and `brsthomson`. The 108-ID captioned cohort produced 107 exact successful
READY waits and one Pro timeout. Every successful wait had matching expected
and ready IDs, empty missing/not-ready sets, and source status `2`; all 111
content-fetch starts followed a successful wait. The timeout source stayed at
status `3` for `606.235s`, was classified with the exact source ID and video,
and was quarantined without extraction.

Both controls were partial: pair01 `50/54` and pair02 `53/54`. The runner
withheld both adaptive arms, and no adaptive transition or valid VPH result
exists. Canonical hashes/integrity, auth identities, staging integrity, worker
cleanup, and process cleanup passed. A single source-add recovery observation
occurred, but no RPC9/`SourceNotFound`/`source_add_failed` marker appeared; do
not call source-add repaired. Do not rerun the cohort or use run06 to change
worker/batch settings or authorize full-backlog operation. See the run06
`result_receipt.md` claim ledger for the remaining evidence boundary.

### Source-ready gate scale-up run07c (2026-08-12)

The fresh packet and authoritative receipt are
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_scaleup_run07c/throughput_pair_packet.json`
and
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_scaleup_run07c/result_receipt.md`.
Immediate exact-account token-only preflight passed for all three identities,
and the canonical DB/reference-cache hashes remained unchanged. Pair01
control was partial at `53/54`, so its adaptive arm was correctly withheld.
Pair02 control completed `54/54`; pair02 adaptive was partial at `53/54`
because `brsthomson` video `u9D1A8vSn0A` reached source materialization status
`3` after one poll and was quarantined. Therefore no combined control/adaptive
VPH comparison is valid.

This run did prove one bounded mechanism fact: the Pro adaptive event stream
raised its target from `3` to `4` on `backlog_high` and started `worker-04`.
The Free accounts stayed fixed at three workers. This confirms the existing
per-account dynamic scheduler path is executable, but does not establish that
four workers is optimal or authorize production settings. The status-`3`
guard also failed fast and emitted no
`nlm_batch_source_content_fetch_started` event for the terminal source. Do not
replay this cohort or treat its partial-arm rates as VPH.
The next live branch, if authorized by a new packet, must use a fresh disjoint
cohort and require complete control and adaptive arms before comparison.

### Terminal-guard validation run08 (2026-08-12)

The packet and authoritative receipt are
`P:/.logs/multi_account_fetch/throughput_pair_20260812_terminal_guard_validation_run08/throughput_pair_packet.json`
and
`P:/.logs/multi_account_fetch/throughput_pair_20260812_terminal_guard_validation_run08/result_receipt.md`.
Immediate exact-account token-only preflight passed for `a.hominidae`,
`troup.hominidae`, and `brsthomson`. The parent launch command was issued once;
the run root contains the resulting control artifacts. Pair01
control was partial at `50/54` and pair02 control was partial at `53/54`, so
both adaptive arms were correctly withheld. There is no valid VPH comparison
and no worker-setting or full-backlog authorization.

The run positively validates the terminal status guard on a fresh path:
`ZHYqjD099Aw` emitted `nlm_batch_source_materialization_wait_terminal_failure`
and `nlm_batch_subbatch_materialization_error_continuing`; no
`nlm_batch_source_content_fetch_started` event was emitted for that terminal
source and later selected work continued.
The other incomplete items were content-quality failures. This was not an
authentication failure. Do not replay this cohort; the next live comparison,
if separately authorized, needs a fresh eligible cohort and complete control
and adaptive arms. The read-only arm validator was also hardened so invalid
partial arms expose `vph=null` and an empty per-account VPH mapping, preventing
diagnostic partial rates from being mistaken for promotable throughput.

### Production-shaped batch-50 pair run09 (2026-08-12)

The packet and durable result receipt are
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/throughput_pair_packet.json`
and
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/result_receipt.md`.
Immediate exact-account token-only preflight passed for `a.hominidae`,
`troup.hominidae`, and `brsthomson`. Pair01 control then produced `13` fresh
typed `ADD_SOURCE rpc_code=9` failures across the three account stderr logs
(`6/4/3`), so the parent controlled-aborted the controls and the coordinator
withheld both adaptive arms. Pair01 staging reached `1,113/2,553` complete;
pair02 was stopped before meaningful work at `0/2,553`. Both throughput
receipts are invalid (`vph_valid=false`), and no VPH, worker setting, batch
setting, or full-backlog authorization follows.

This is fresh source-add recurrence evidence, not an authentication failure.
Do not replay the cohort or direct-retry RPC9. The next allowed source-add
branch needs a new mechanism decision packet with an exact canary, falsifier,
abort gate, and promotion rule.

The detailed source-add packet is
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/source_add_rpc9_decision_packet.md`;
the existing source-content analyzer parsed `21` term files and `7,094` events
with zero parse errors. The relevant focused/coordinator test set passed
`254` tests in `53.65s` after this reconciliation.

### Source-add fallback-only canary run10 and exact plan refresh (2026-08-12)

The run10 packet and receipt are
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run10/decision_packet.md`
and
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run10/result_receipt.md`.
It selected exactly one final run09 `Source add failed` row per account and
passed immediate token-only preflight for `a.hominidae`, `troup.hominidae`, and
`brsthomson`. The explicit `--fallback-only` route emitted `76` raw events and
zero source-add, materialization, or source-content mutations. `DV4EYDLeqBg`
produced a 645-character Whisper transcript and was the only result to pass
the 500-character quality gate; it was promoted through the exact locked
promoter. `2vYu5CYAQtY` produced three characters and `QbK4INVu9fI` was
classified unavailable, so neither was promoted. This is partial exact-row
recovery evidence, not a default fallback policy, RPC9 fix, throughput result,
or full-backlog authorization.

The promotion changed canonical counts from `9,681/197/333,241` to
`9,682/197/333,240` (`complete/failed/pending`) with batch/cache integrity
`ok`/`ok`. Because the promoted ID was selected by the older plan, that plan
was archived at
`P:/.data/yt-is/unattended-backlog/state-stale-after-run10.json`. A fresh
plan-only state was generated from the current databases under
`P:/.logs/multi_account_fetch/unattended-refresh-after-run10/`, installed at
`P:/.data/yt-is/unattended-backlog/state.json`, and rechecked as
`health_status=planned` with `issues=[]`. This establishes plan reconciliation
only; no unattended live execution was launched. Treat every existing plan as
stale after an exact canonical promotion or status reconciliation, archive it,
replan from the authoritative databases, and pass health again before launch.

### Terminal source-status fail-fast guard (2026-08-11)

Run06 showed one source with installed-runtime status `3` (`SourceStatus.ERROR`)
that remained in the old readiness poll for `606.235s`. The production path now
recognizes terminal status `3` before sleeping, records
`source_materialization_terminal_error`, raises
`NotebookSourceMaterializationTerminalError`, quarantines the affected source,
and continues later sub-batches. Processing/preparing statuses `1`/`5` retain
the polling path. The sharded evidence guard recognizes this as source
materialization invalidation, not a throughput success. `176` package tests and
`44` sharded-lane tests passed. The fresh live canary below measured the new
guard; this did not authorize a throughput claim or full-backlog run.

The isolated live confirmation is recorded at
`P:/.logs/multi_account_fetch/terminal_source_status_guard_canary_20260811/result_receipt.md`.
Immediate exact-account preflight passed. The known status-`3` source failed
fast after one poll (`0.426s`), emitted no content-fetch event, and the next
selected source completed; staging integrity, canonical fingerprints, cleanup,
and process termination passed. The run used `batch_size=1`, so it proves
runner-level continuation only, not a later sub-batch within the same add call.
RPC9 recurred and remains a separate source-add branch. No VPH, adaptive
worker-setting, or full-backlog authorization follows from this canary.

### Adaptive scale-up cohort feasibility check (2026-08-11)

A plan-only attempt to prepare a two-pair captioned control/adaptive comparison
with `851` items per account failed closed before staging:

```text
ValueError: insufficient deterministic cohort: need 5106, found 270
```

The canonical pending database currently has `270` captioned rows, `6,167`
unknown-caption rows, and `326,804` known no-caption rows. The `851` floor is
not arbitrary: with the current account settings (`3` initial workers,
`batch_size=50`, health window `2`, four industrial batches per worker, and
scale-up backlog `2`), `adaptive_workload_requirements()` requires `18`
logical batches, or `(18 - 1) * 50 + 1 = 851` items per account. The required
two-pair cohort is therefore `5,106` rows. This attempt created no staging
root, launched no live work, and changed no canonical state.

The experiment input at
`P:/.data/yt-is/unattended-backlog/account-settings.json` explicitly sets
`batch_size=50` for all three accounts and enables adaptive workers only for
`a.hominidae`; the other two Free accounts remain fixed by default. Passing
`--batch-size 1` alongside that file does not override those explicit
per-account values. A future batch-1 scheduler-observability packet must omit
the conflicting file or provide explicit per-account overrides, and a future
batch-50 captioned throughput packet needs a larger eligible cohort or a
different, explicitly labeled source state. This feasibility result is not a
live throughput result and authorizes no run. For the mixed account set, do not
add the global `--adaptive-workers` flag: it applies adaptive policy to every
selected account and is rejected by the fixed Free settings. The account
settings file is the authoritative mixed-policy path.

The resulting all-account batch-1 scheduler packet is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_captioned_adaptive_all_accounts_batch1_plan_run07/`.
It explicitly targets adaptive workers for all three exact identities while
keeping the fixed control at three workers. Its decision packet is
`throughput_decision_packet.md`; the packet is now closed as
`negative_control_invalid_adaptive_withheld`.
The packet requires the typed `ensure_account_session(...,
allow_bootstrap=False)` probe immediately before launch and treats worker
transition, assignment, and cleanup evidence as the only promotion target. It
does not authorize a VPH, batch-size, or full-backlog conclusion.

The packet was then exercised once under the existing Codex goal. The exact
three-account token-only probes passed, but both fixed controls were partial:
pair 01 completed `50/54` and pair 02 completed `53/54`. The five failures were
four `nlm_content_below_threshold` rows and one `command_failed` row with
`SourceNotFoundError`; the executor withheld both adaptive arms. The result is
recorded at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_captioned_adaptive_all_accounts_batch1_plan_run07/result_receipt.md`.
It is a negative control/cohort-quality result, not an auth result and not a
VPH result. Canonical state remained `complete=9,681`, `failed=197`,
`pending=333,241`, with integrity `ok`. Do not replay this same shape without a
new quality/cohort mechanism and packet.

### All-account adaptive batch-1 scheduler canary passed (2026-08-11)

The follow-up packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_captioned_excluded_residuals_adaptive_all_accounts_batch1_plan_run08/`
used a fresh captioned cohort with five known residual IDs excluded only from
the benchmark selection. The immediate typed token-only preflight passed for
`a.hominidae`, `troup.hominidae`, and `brsthomson`; all four arms completed
`54/54`, staging DB/cache integrity and selected-cache completeness passed, the
canonical DB/cache hashes and counts were unchanged, and no child process
survived completion.

The adaptive arm emitted target workers `[3,4]` for each of the three exact
accounts in both pairs. Combined diagnostic VPH was `2474.038` versus
`1953.690` for pair 01 and `2599.938` versus `2041.888` for pair 02. These are
valid bounded scheduler/cohort measurements, not sustained production VPH,
proof that four workers is optimal for any account, or a full-backlog
authorization: the run used `batch_size=1`, a small captioned-only cohort, two
pairs, and explicit exclusions. A larger repeated control/adaptive soak at the
intended production batch size remains deferred until a sufficiently eligible
cohort and decision packet exist. This result is not an auth result and does
not authorize changing the production account settings.

### Larger adaptive batch-1 repeat closed invalid (2026-08-11)

The follow-up packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_captioned_excluded_residuals_adaptive_all_accounts_batch1_repeat_run09/`
used `30` captioned IDs per account per pair and passed the immediate
token-only preflight for all three exact accounts. Pair 01 control and
adaptive both completed `90/90`; adaptive recorded `[3,4]` for every account
and measured `2814.430` diagnostic VPH versus `2351.456` for control.

Pair 02 control failed closed at `89/90`: `troup.hominidae` video
`p0jZ_cV9ZmA` ended `nlm_content_below_threshold`. The executor correctly
withheld pair 02 adaptive. Canonical DB/cache hashes, counts, and integrity
remained unchanged; no process survived and stderr was empty. The authoritative
receipt is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_captioned_excluded_residuals_adaptive_all_accounts_batch1_repeat_run09/result_receipt.md`.
This is source/content cohort-quality evidence, not an auth result and not a
valid repeated throughput comparison. Do not replay the exact row or rerun
this cohort. The pair-01 scheduler transition remains bounded evidence only;
production worker settings, sustained VPH, and full-backlog authorization
remain open.

### Fresh captioned Pro scale-up canary passed (2026-08-11)

The next fresh packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_fresh_captioned_scaleup_batch1_run10b/`
passed the immediate token-only preflight for all three exact accounts and
completed all four control/adaptive arms at `42/42` selected IDs. The fixed
controls measured diagnostic VPH `2036.665` and `2058.852`; the adaptive arms
measured `2055.800` and `2083.018`. Both adaptive Pro event trees recorded
raw target workers `{3,4}` and started `worker-04`; both Free accounts stayed
fixed at workers 1-3 as configured. Source-content rows were `168/168`
`ready` with command return code `0`, and all staging integrity, selected-cache,
canonical-fingerprint, and cleanup gates passed.

The authoritative receipt is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_fresh_captioned_scaleup_batch1_run10b/result_receipt.md`;
the coordinator validator returned `status=passed` for four receipts. This is
valid bounded scheduler evidence only: the cohort is captioned-only, two
14-item pairs, and `batch_size=1`. It does not establish sustained VPH,
account-specific worker optimality, production settings, or full-backlog
readiness. Keep canonical settings unchanged and use a new packet for repeated
intended-shape soaks.

The current source-add recovery receipt is
`P:/.logs/multi_account_fetch/20260810_source_add_residual_policy_current/recovery_run02_result_receipt.md`.
It records `98` complete, `97` failed, `0` pending, `0` missing, database
integrity `ok`, zero forbidden NotebookLM source-action events, and the exact
manifest/database fingerprints. The default source-add fallback route remains
opt-in until its policy is independently authorized; terminal unavailable and
unknown rows remain classified residuals.

The consolidated current readiness reconciliation is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_reconciliation.md`.
It is the shortest current gate ledger: exact auth and bounded cleanup pass,
while the residual policy is now bounded only for pending-only draining, valid
throughput, logged-out scheduler execution, and full-backlog drain remain
open. It is not an authorization receipt.
The adversarial review of the current claims is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_adversarial_review.md`.

The three source-add fallback results that met the explicit `500`-character
gate were reconciled into canonical state using the exact, locked promotion
utility. Apply receipts:
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run01_apply_receipt.json`
and
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run02_apply_receipt.json`.
The two remaining source-add rows stay failed and packet-required. The
pre-content-canary post-source-addressability audit and packet set were
`P:/.logs/multi_account_fetch/20260811_post_promotion_source_addressability_fallback_run02_residual_audit.json`
and
`P:/.logs/multi_account_fetch/20260811_residual_retry_packet_set_post_source_addressability_fallback_run02/`.
The current post-promotion audit is recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run03_after_source_add_run03/post_quality_reconciliation_residual_audit.json`.

The exact source-addressability fallback canary run02 is recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run02/result_receipt.md`.
`Y_t3eO9xptQ` recovered with a `13,928`-character Whisper transcript and was
promoted by the locked exact promoter; `FUaqMRqbYvY` was later classified as
terminal `unavailable` from four-stage raw evidence. The canary itself was fallback-only and emitted no source-add,
materialization, or source-content actions. The current canonical snapshot is
now `complete=9,681`, `failed=197`, `pending=333,241`; this does not authorize
default fallback routing or full-backlog operation.

### Content-threshold fallback canary run01 (2026-08-11)

The exact canary at
`P:/.logs/multi_account_fetch/20260811_content_threshold_fallback_canary_run01/`
selected three of the pre-canary 15 content-threshold residuals across both
source groups. The existing fallback-only route recovered all three with
`22`, `46`, and `8,815` transcript characters, and all passed the existing
21-character quality gate. Immediate token-only auth passed for all three
canonical identities; staging integrity, exact receipts, process cleanup, and
the no-NotebookLM-mutation action scan passed. Three separate exact promotion
receipts applied the recovered rows to canonical state. The class is now 12
rows, but this is only positive bounded sample evidence: the remaining rows
must not be blanket-requeued, and default fallback/full-backlog authorization
remains open.

### Source-add fallback canary run03 (2026-08-11)

The exact canary at
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run03_after_content_run01/`
re-tested the two remaining source-add residuals through the existing
NotebookLM-free fallback route. Immediate token-only auth passed for
`a.hominidae`, `troup.hominidae`, and `brsthomson`; both staged rows remained
failed. `w9cxJdazkEs` ended `no_transcript` after the direct transcript route,
and `yLSnkG9yLbA` exhausted the bounded Whisper fallback after more than 801
seconds. Raw action logs contained no source-add, materialization,
source-content, or content-fetch mutation. Neither row met the `500`-character
promotion gate, so no canonical promotion was attempted. Receipt:
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run03_after_content_run01/result_receipt.md`.
This narrows the exact evidence but does not prove class-wide unrecoverability,
authorize direct RPC9 replay, or authorize default fallback routing.

### Exact terminal-unavailable reconciliation (2026-08-11)

The four exact rows `FUaqMRqbYvY`, `bCjXn5NA-FQ`, `-nJIgUTc4N8`, and
`Dz32gmAeb1I` had four-stage unavailable evidence and no successful fallback
output. The guarded reconciliation changed only their failure reason from the
stale source-addressability aggregate to terminal `unavailable`; it did not
requeue or complete them. The SQLite backup, dry-run/apply receipts, raw event
references, and regenerated audit are recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run03_after_source_add_run03/unavailable_reconciliation_receipt.md`.
The source-addressability class is now closed; `QvxHBtYsDig` is a distinct
`fallback_quality` residual. Current `unavailable=142` and packet-required rows
are `51`.

### Unknown-caption throughput pair run02 closed invalid (2026-08-11)

The bounded pair at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/`
is closed as `control_invalid_adaptive_not_launched`. Both fixed controls
performed real isolated work, but pair 01 reconciled `86/90` and pair 02
reconciled `79/90`; both controls encountered fresh typed source-add RPC9 or
post-add `SourceNotFoundError` failures. Immediate token-only auth passed for
all three canonical identities before launch, so this is not an auth result.
The coordinator withheld both adaptive arms, and the observed `1866.623` and
`1362.427` completed-items/hour values are not valid control/adaptive VPH or
optimality evidence. Staged SQLite integrity and child cleanup passed; failed
selected items correctly caused the selected-cache completeness gate to fail.
The authoritative receipt is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/result_receipt.md`.
The raw recurrence classification is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_30_live_gate_run02/source_add_recurrence_packet.md`.
Do not replay these RPC9 rows directly; use only an exact isolated
fallback-only recovery packet if that residual branch is reopened. Do not
launch another throughput pair until a fresh control cohort passes the exact
completion, cache, and failure gates.

### Fresh current captioned smoke and planner correction (2026-08-11)

The fresh isolated smoke at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_smoke_run02/`
was launched after fixing `scripts/prepare_throughput_pair.py`: authoritative
pending rows are now eligible even when they are present in the read-only
reference cache, because selected IDs are removed from each copied staging
cache before launch. The focused planner tests pass (`11 passed`), and the
packet records this selection semantics.

The smoke passed the exact-account token-only preflight for `a.hominidae`,
`troup.hominidae`, and `brsthomson`. All four arms processed `6/6` selected
IDs with child return code `0`, staging DB/cache integrity `ok`, non-empty
selected cache output, unchanged canonical DB/cache fingerprints, and zero
positive fallback/source-add/RPC9/source-addressability actions. Receipt:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_smoke_run02/result_receipt.md`.

Decision: `controls_valid_adaptive_not_exercised`. The controls are valid
diagnostic controls, but each account had only one outer industrial batch.
Pro adaptive emitted target workers `3` then `2`, never a target above `3`;
the Free accounts remain fixed at `3`. Diagnostic arm rates are retained only
in the receipt and are not sustained VPH, adaptive-win, or optimality
evidence. A future adaptive packet must use enough per-account work to create
the configured scale-up backlog and must use repeated clean soaks before any
throughput conclusion.

### Current captioned batch-1 scale-up attempt closed invalid (2026-08-11)

The next packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_batch1_run03/`
used 60 fresh pending captioned IDs, 10 per account, and `batch_size=1` in an
attempt to make Pro adaptive scale-up observable. The workload was below the
planner's conservative feasibility floor. Immediate token-only auth passed
for all three canonical identities. Both fixed controls nevertheless failed:
pair-01 reconciled `27/30` and pair-02 `29/30`; adaptive was withheld by the
control-first gate. Pair-01 had three `nlm_content_below_threshold` residuals;
pair-02 had one Pro `SourceNotFoundError`/`command_failed` residual after a
source-add recovery event. Receipt:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_batch1_run03/result_receipt.md`.

This closes the packet as `control_invalid_adaptive_not_launched`. It is not an
auth result, VPH result, adaptive result, or optimality evidence. Do not replay
the four exact residuals directly. The next useful branch is exact residual
classification/policy work or a new mechanism packet, followed by a fresh
clean control before throughput comparison. The completed offline
reclassification is recorded at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_objective_current_captioned_batch1_run03/batch1_residual_decision_packet.md`;
it leaves all four rows packet-required and authorizes no retry or live arm.

### Adaptive workload planner guard (2026-08-11)

The throughput coordinator now performs an offline feasibility check before it
creates any staging artifacts. When an adaptive packet requires observable
scale-up, it derives a conservative logical-batch floor from the configured
initial workers, health window, scale-up backlog, NotebookLM batch size, and
the `csf-source` four-batches-per-worker dispatch contract. An undersized
packet fails closed with the required item count instead of launching a
control whose adaptive arm cannot exercise its gate. Feasibility is not a
performance promise: live worker health and the control-first gates still
decide whether an adaptive arm runs. The implementation is in
`scripts/prepare_throughput_pair.py` and is enabled by
`scripts/run_throughput_pair.py`; focused planner/coordinator verification is
`27 passed` with clean compilation and diff checks.

### Scheduler canary audit (2026-08-11)

`YtisUnattendedBacklog` is verified only in its existing interactive-token,
plan-only canary configuration. The exact task XML and inspector result are
recorded at
`P:/.logs/multi_account_fetch/scheduler_canary_audit_20260811.md`.
An attempted separate S4U `--execute` canary was blocked before task creation
by Windows `Register-ScheduledTask` access denied (`HRESULT 0x80070005`); the
existing plan-only task was preserved. Receipt:
`P:/.logs/multi_account_fetch/20260811_scheduler_s4u_execute_canary_run01/result_receipt.md`.
The application supervisor's separate bounded execute/restart/resume behavior
is proven on six isolated staged IDs by
`P:/.logs/multi_account_fetch/20260810_scheduler_restart_resume_canary_run04/result_receipt.md`.
Logged-out OS-task execution, `--until-empty`, and a full-backlog authorization
receipt remain unproven. Older sections saying the task was
unregistered are historical and are superseded by this audit; they do not
authorize changing the installed task.

### Wiki semantic debt closed by exact checkpoint resume (2026-08-11)

The exact poisoned/deferred notebook
`4017aa6e-35fb-426d-bc53-34620bec405e` was completed by the bounded MMX
checkpoint-resume run16. The worker used the existing Stage-C checkpoint,
reopened only that one item, and finished in `1168.5s` under the `1200s`
bound. Queue state is now `completed=47`, `failed=2`, `poisoned=0`,
`needs_resynthesis=0`, with no pending or in-progress work. The authoritative
receipt is
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`.

The result contains five `llm_validated`, `complete_4_hop` concept pages from
36 local transcripts; the normal validator passed all five pages. The report
records `38` citations covering `19/36` transcripts (`52.8%`), so semantic
completion does not mean complete source coverage. The 13 historical manifest
gaps were audited again and remain unrepairable without exact receipts:
`P:/.logs/wiki-yt-queue/20260811/manifest_gap_audit_after_run16.json`.

### Bounded quality-observability canary (2026-08-11)

The fresh one-chunk coordinator run at
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/` selected
`400` pending IDs and reconciled `389` complete plus `11` failed. Immediate
token-only auth passed for all three canonical identities. Pro forwarded the
configured adaptive policy and emitted only scale-down transitions (`3 -> 2 ->
1`) after input closure; no scale-up transition occurred. Both Free profiles
used fixed three-worker settings. The result is a bounded reliability canary,
not a throughput or full-backlog result; its receipt is
`P:/.logs/multi_account_fetch/quality-observability-canary-run01/result_receipt.md`.

The selected completions used cache/NotebookLM paths, so the fallback-only
transcript quality fields were absent from all 389 selected complete rows. The
canary therefore does not validate fallback quality-field population or
semantic quality. The canonical plan was refreshed in plan-only mode; the old
state is preserved at
`P:/.data/yt-is/unattended-backlog/state-pre-quality-observability-canary-run01.json`,
and `check_unattended_backlog.py` now returns `health_status=planned` with
`issues=[]` for the new canonical state.

### Current command-residual fallback canary (2026-08-11)

The exact three-ID command-class canary at
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run04/`
reconciled `3/3` staged rows to `complete/whisper` through explicit
`--fallback-only` processing. The selected IDs were `QOhOFjRLjWA`,
`YUazGIwPwfI`, and `yJUq-obHXzw`; all three account children returned `0`, all
three immediate token-only auth identities matched, both staging SQLite
databases passed integrity, and cache outputs were non-empty. No NotebookLM
source/add, materialization, or content action appeared, and the canonical
database/cache remained unchanged. This extends the prior one-item positive
canary but is still bounded fallback evidence: it does not prove the command
class is solved, score semantic quality, authorize blanket fallback routing,
or open full-backlog operation. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run04/result_receipt.md`.

The disjoint six-ID follow-up at
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run05/`
reconciled `4/6` staged rows to `complete/whisper` with non-empty cache text;
`2/6` reached the explicit 900-second fallback deadline and remained failed.
The immediate token-only auth check passed for all three mapped identities,
both staging SQLite databases passed integrity, and every selected ID matched
its child receipt and final staging status. The action-level scan found no
source-add, materialization, source-content, or content-fetch action. Generic
`nlm_client_*` initialization/auth-probe events were present but are not
NotebookLM mutation evidence. Canonical DB/cache hashes remained unchanged and
no canary process survived. This is partial class evidence with a costly tail,
not a command-class fix, default fallback authorization, throughput result, or
full-backlog authorization. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run05/result_receipt.md`.

The six exact RPC9 rows from the 2026-08-11 unknown-cohort throughput control
were separately processed in isolated staging under
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run03/source_add_recovery_run01/`.
The bounded fallback-only result is `2` recovered transcripts and `4`
terminal unavailable outcomes, with `0` pending, `0` missing, and `0` direct
NotebookLM source-action events. This closes that recovery branch only; it does
not prove RPC9 is fixed, promote fallback as the default route, or make the
invalid throughput pair usable as VPH evidence. The authoritative receipt is
`.../source_add_recovery_run01/result_receipt.md`.

### Fresh run13 control and RPC9 recovery (2026-08-11)

The larger unknown-caption control/adaptive packet at
`P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/`
is closed as `control_invalid_adaptive_not_launched`. Pair 01 reconciled
`84/90` and had six exact `ADD_SOURCE` `RPCError rpc_code=9` failures. Pair 02
reconciled `78/90` and had nine exact RPC9 failures plus three separate
`command_failed` rows. All three immediate token-only auth probes passed; the
adaptive arms were withheld. No arithmetic VPH from these partial controls is
valid promotion evidence.

The six pair-01 RPC9 rows were then requeued only in the pair's isolated
staging database and sent through two exact `--fallback-only` manifests. The
bounded recovery produced `2` non-empty transcripts and `4` explicit
`unavailable` terminal outcomes, with `0` pending, `0` missing, intact staging
SQLite databases, and `0` NotebookLM source/materialization/content events.
This closes the recovery/classification branch only. It does not prove that
provider RPC9 is fixed, promote fallback as the default route, or authorize a
new throughput pair. Diagnosis and receipts:

- `P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/source_add_failure_diagnosis_packet.md`
- `P:/.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run13/source_add_rpc9_recovery_run01/result_receipt.md`

The source-add code still correctly avoids blind RPC9 replay. The next
source-add work requires a new provider-side mechanism or discriminating
evidence; do not request another browser login from this result.

### Historical residual policy packet set (2026-08-12, after run10 promotion)

The post-quality read-only audit at that point was materialized into exact,
class-separated manifests and decision packets by
`scripts/build_residual_retry_packets.py` at
`P:/.logs/multi_account_fetch/20260812_residual_retry_packet_set_after_run10/`.
It contains `25` `command`, `12` `content_threshold`, `2` `cookie_source`,
`1` `fallback_quality`, `2` `source_add`, and `9` `whisper_timeout` IDs.
Every manifest is bound to the failed DB status and final audit fingerprint
observed at generation time; the builder reports `live_authorized=false` and
`database_mutated=false`. The earlier pre-quality packet set is historical
and must not be used as a current retry scope.

The historical pending-only residual-policy gate receipt is
`P:/.logs/multi_account_fetch/20260812_residual_policy_gate_pending_only_after_run10/`.
It verifies the then-current audit and packet fingerprints and explicitly
deferred all `197` failed rows while exposing only the `333,240` pending scope to a
future drain. It does not recover any failed row or authorize the full
backlog.

This closes the offline residual inventory and exact-scope preparation, not the
live policy gate. Command rows still need a reviewed fallback canary, quality
threshold rows need a separate quality-preserving mechanism, Whisper timeout
rows have a negative prior bounded retry, and cookie rows require changed
external cookie state. No class may be blanket-requeued from the packet set.

### Per-account throughput harness hardening (2026-08-11)

`scripts/prepare_throughput_pair.py` and `scripts/run_throughput_pair.py` now
accept an optional per-account settings JSON mapping. The packet stores the
normalized effective settings and fingerprints them; execution fails closed if
the settings file contents differ from the packet. Distinct initial worker
counts, batch sizes, and mixed adaptive/fixed policies are supported, and
adaptive target-worker gates are derived from the packet rather than assuming
`a.hominidae`/three workers. The isolated real plan
`P:/.logs/multi_account_fetch/throughput_pair_20260811_distinct_settings_plan_run01/`
completed offline with `live_launch=false`; its packet passed provenance
validation. Focused harness/scheduler verification is `122 passed` with clean
compilation and diff checks. This makes distinct Pro/Free measurement
expressible; it does not measure which settings are fastest or authorize a
live pair.

### Current command residual canary (2026-08-11)

Three exact current `command` rows were requeued through the class-guarded
manifest and run with the canonical DB/cache and
`--route-industrial-failures-to-fallback`:
`bnXLDAGL2z8`, `A9Wy_9h1_Ro`, and `BwI1JgoT3pI`. All three canonical token-only
auth probes passed immediately before launch. The coordinator and all three
children completed with `3/3` terminal `complete` rows, no pending or missing
IDs, non-empty cache rows of `16`, `10,283`, and `503` characters, and `ok`
SQLite integrity for both databases.

The timestamp-ordered raw-event scan found one
`industrial_failure_fallback_queued` event per ID with `skip_notebooklm=true`
and zero NLM source/add/content events after fallback admission. This is
validated opt-in recovery for the exact three-row canary, not a fix for the
NotebookLM command/source-add mechanism, not a VPH result, and not automatic
authorization for the remaining `25` command rows. One output is only
`["Jingle Bells"]` (16 characters), so `complete` must not be treated as
semantic-quality proof. Packet and receipt:

- `P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run01/decision_packet.md`
- `P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run01/result_receipt.md`

A five-ID isolated staging expansion of this route was then attempted with
`OFu07TgcoOk`, `ZYugF5TxgTc`, `pQfeoRzy45s`, `qhqHixwl0X8`, and
`wymC6XG9bms`. Two rows produced non-empty Whisper cache entries, one became
`unavailable`, one became `no_transcript`, and one exhausted the fallback
deadline. Both staging databases passed integrity and the event scan found no
NotebookLM actions, but the outer launcher timed out at `1300s` while a child
was active, failed to publish its coordinator summary, and left descendants
that required exact process cleanup. This is a negative cleanup-boundary
result, not authorization for the remaining `20` rows or default fallback
routing. Receipt:
`P:/.logs/multi_account_fetch/20260811_command_residual_canary_run02/result_receipt.md`.
Any broader retry requires a new coordinator timeout/cleanup mechanism and a
new packet.

### Throughput-pair closure and source-add recovery (2026-08-11)

The distinct-settings no-caption pair plan
`P:/.logs/multi_account_fetch/throughput_pair_20260811_no_caption_30_distinct_settings_plan_run01/`
was executed only after immediate token-only auth probes passed for
`a.hominidae`, `troup.hominidae`, and `brsthomson`. Both fixed-control arms
completed only partially: pair 01 reconciled `85/90` and pair 02 `88/90`.
The failures included seven exact typed source-add `RPCError rpc_code=9`
events; three of those rows later exposed `SourceNotFoundError` during content
processing. The control gate therefore failed, both adaptive arms were
withheld, and neither diagnostic rate in the receipt is valid VPH evidence.
Do not infer a throughput ranking or auth failure from this packet.

The four exact RPC9 rows were then processed through isolated staged
`--fallback-only` recovery. Three reached terminal `complete` with non-empty
Whisper cache entries; one exhausted its bounded Whisper deadline. Staging
SQLite integrity passed, no NotebookLM source/add/content action occurred in
the fallback route, and canonical state was not changed. This is a partial
class-specific recovery result, not proof that RPC9 is fixed, not authorization
for default fallback routing, and not a reason to replay the four rows again.
Receipts:

- `P:/.logs/multi_account_fetch/throughput_pair_20260811_no_caption_30_distinct_settings_plan_run01/result_receipt.md`
- `P:/.logs/multi_account_fetch/throughput_pair_20260811_no_caption_30_distinct_settings_plan_run01/rpc9_recovery/source_add_pair01/result_receipt.md`

No new throughput pair is justified from this same shape without a changed
source-add/fallback mechanism or a separately justified clean cohort and a
fresh decision packet.

### Fallback quality observability (2026-08-11)

The exact command-residual canary exposed a real contract gap: fallback
completion required only a non-empty transcript, and one `complete` row had a
16-character `["Jingle Bells"]` result. This is a quality warning, not proof
that the source or provider is wrong.

The fallback completion paths in `bin/csf-source` now persist deterministic
`transcript_chars`, `transcript_words`,
`transcript_length_threshold_chars`, and `transcript_length_band` evidence in
both cache metadata and `analysis_status.quality_metrics`, even when YouTube
engagement metadata is absent. The change is observability-only: it does not
reject short output, alter routing, or authorize retries. Focused transcript
and source-CLI tests pass. Future fallback promotion or full-backlog quality
gates must consume this distribution and define a separately justified policy;
do not infer semantic quality from `status=complete` alone.

### Source-content addressability canaries (2026-08-11)

The source-content retry branch now performs one bounded, read-only source-list
presence probe when a content command returns a recognized spaced
`SourceNotFoundError` form. A confirmed source ID admits the existing bounded
local retry; a confirmed absence suppresses both local retry and retry-queue
admission; an unknown probe fails closed for the spaced form. The implementation
and regression tests are in `csf/nlm_batch.py` and `tests/test_nlm_batch.py`.

The first live canary used one exact `a.hominidae` item in isolated staging.
Source-add final-count reconciliation recovered the source, and the presence
probe correctly found it and admitted the bounded retry. The content endpoint
then returned the same spaced not-found error across four attempts with about
seven seconds of local retry sleep; the row exhausted attempts and was not
queued because the selected fallback dependency was unavailable. This is a
negative result for source-list presence as a recovery predictor, not evidence
of an authentication problem. Receipt:
`P:/.logs/multi_account_fetch/source_content_presence_retry_canary_20260811_run01/decision_packet.md`.

The second canary enabled the explicit
`--route-source-addressability-failures-to-fallback` route for one exact item.
The final error preserved the recognized `SourceNotFoundError` marker, the
coordinator queued exactly that ID to fallback, and no post-route NotebookLM
content action occurred. Fallback then returned oEmbed HTTP 404 and recorded a
terminal `unavailable` result. This validates narrow marker preservation and
route partitioning, not fallback success, a provider fix, default-route
authorization, or throughput. Receipt:
`P:/.logs/multi_account_fetch/source_content_addressability_fallback_canary_20260811_run02/decision_packet.md`.

Do not broaden the retry count, enable this route by default, retry the
unavailable item, or authorize the full backlog from these canaries alone.

### Source-add/content-not-found provenance reconciliation (2026-08-12)

The older RPC9 reconciliation canary at
`P:/.logs/multi_account_fetch/source_add_rpc9_reconciliation_canary_20260811_run03/`
records one content attempt and `retry_exit_reason=not_retryable` even though
its source-presence probe was positive. Its event stream has no source
revision or working-tree fingerprint, so this cannot be attributed to the
current checkout. A later same-day presence-aware canary records four
attempts and `attempts_exhausted` for the same failure class. The current
working tree now has a regression test proving that positive presence admits
bounded retries and persistent failure exhausts the local budget. Preserve
the older row as historical evidence; do not call it an auth failure, replay
RPC9, broaden retry defaults, or authorize throughput from it. Reconciliation
packet:
`P:/.logs/multi_account_fetch/source_add_content_not_found_reconciliation_20260812.md`.

### Deadline-unknown fallback retry run01 (2026-08-11)

The nine former `unknown: transcript fallback deadline exhausted` rows were
retried once through an exact isolated `--fallback-only` manifest after all
three token-only account probes passed. All `9/9` reached Whisper and timed
out between `782.510s` and `823.481s`; `0/9` produced a transcript, the
isolated cache remained empty for these IDs, and both isolated SQLite
databases passed integrity checks. The raw action scan found zero exact
NotebookLM source-add, materialization, or source-content actions, and no
owned yt-is process remained after cleanup.

This is a negative mechanism result, not an authentication or source-add
result. A guarded classification-only repair changed the canonical rows from
the aggregate deadline string to
`timeout: whisper transcription timed out; bounded fallback retry exhausted`,
preserving `status=failed` and `last_stage=whisper`. The rows now classify as
`whisper_timeout` / `bounded_quality_retry_candidate`; no automatic retry,
Whisper deadline increase, fallback promotion, or full-backlog authorization
follows. Receipts and raw evidence:
`P:/.logs/multi_account_fetch/20260810_source_add_residual_policy_current/deadline_unknown_retry_run01/result_receipt.md`
and
`P:/.logs/multi_account_fetch/20260810_source_add_residual_policy_current/deadline_unknown_classification_repair_receipt_apply.json`.

The canonical unattended plan was refreshed at `2026-08-11T00:36:30Z`. Its
read-only health is `planned` with `issues=[]`:
`P:/.data/yt-is/unattended-backlog/state.json`, with plan artifacts under
`P:/.logs/multi_account_fetch/unattended-refresh-20260811/chunk-0001/`.
The previous stale state is preserved as
`P:/.data/yt-is/unattended-backlog/state-stale-20260810-20260810-183830.json`.
The new plan selects `400` pending rows: `a.hominidae=134`,
`brsthomson=133`, and `troup.hominidae=133`; Pro is configured for adaptive
`3..5` workers and both Free profiles use fixed three workers. This is a
healthy plan, not live execution or full-drain authorization.
The latest fresh coordinator canary is
`.logs/multi_account_fetch/20260810_unattended_readiness_canary_run03/`:
it reconciled `30/30` complete, 10 per canonical account, with all three
token-only auth identities correct, `30/30` non-empty canonical cache rows,
and verified cleanup postconditions. Pro forwarded the configured adaptive
policy; both Free profiles used fixed three-worker settings. This promotes the
coordinator/receipt/cache/cleanup path only; it does not authorize unattended
full-backlog execution, prove logged-out scheduler execution, or establish
maximum throughput. The detailed receipt is
`.logs/multi_account_fetch/20260810_unattended_readiness_canary_run03/result_receipt.md`.

The exact account auth preflight passed for all three mapped identities during
the recovery window: `a.hominidae`, `troup.hominidae`, and `brsthomson`.
Auth is not the explanation for the residual classes above; do not request a
new browser login from source-add, command, or fallback evidence alone.

The 2026-08-11 executable throughput-pair control canary is closed as
`control_invalid_adaptive_not_launched`. It performed real work against the
fresh unknown-caption cohort, but reconciled `29/30` selected IDs because
`brsthomson` had one exact `ADD_SOURCE` `RPCError rpc_code=9` for
`A1NrAlw1lHw`. The immediate token-only auth preflight passed for all three
accounts, and the other `brsthomson` items completed, so this is not an auth
failure. The typed source-add policy correctly skipped direct RPC9 replay.
The adaptive arm and pair 02 were not launched; no VPH from this arm is valid
for a control-versus-adaptive comparison. The raw result receipt is
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run07/pair-01/control_result_receipt.md`.
The coordinator now persists partial/runner-failed arm receipts atomically
instead of raising before recording them; this is a reliability hardening,
not throughput evidence.

### Scheduler input-closure canary run11 (2026-08-11)

The scheduler `input_closed` fix was exercised in
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run11/`.
Pair-01 used the same fresh 30-ID cohort for fixed control and adaptive arms,
with `10` items per canonical account and `batch_size=1`. Both arms passed
`30/30` with staging database/cache integrity `ok`. The control measured
`662.963` combined VPH over `162.905s`; adaptive measured `1616.234` over
`66.822s`.

This is valid bounded mechanism evidence, not a sustained-throughput
promotion: the cohort is small, the batch size is deliberately diagnostic,
and the adaptive arm has not been repeated on a larger queue. Raw adaptive
events show `input_open` prevented premature scale-down while the stream was
still producing work, then `backlog_high` raised the target from `3` to `4`
after input closed. This proves target-worker scale-up can occur; it does not
prove that `4` or `5` workers is optimal for any account.

Pair-02 control is invalid (`26/30` complete, `4` failed) because of exact
`SourceNotFoundError` content-fetch failures for `Bw0I1M7gZ74`,
`BHApw964CVQ`, `AQHlyGA2cZM`, and `AS8evR1_1Qk`. These are source
addressability/materialization residuals, not auth failures; its adaptive arm
was correctly withheld. The durable receipt is
`.logs/multi_account_fetch/throughput_pair_20260811_unknown_plan_run11/result_receipt.md`.
No VPH from pair-02 is valid, and no full-backlog or maximum-throughput
authorization follows from run11.

### Corrected uncached control run04 (2026-08-10)

The prior captioned adaptive candidate was cache-only. A corrected isolated
run selected `270` exact pending `has_captions=1` IDs, removed only those IDs
from separate staging caches, and proved `0` selected staging cache rows
before launch while the canonical cache remained unchanged. The fixed control
then performed real NotebookLM work and reconciled `264/270` complete with
`6` failures (`1` RPC9 source-add precondition and `5`
`nlm_content_below_threshold`). It produced `274` content-fetch completions,
`304` source-content command completions, and `6/6` materialization waits;
staging integrity and cleanup passed. Its diagnostic combined useful VPH was
`2880.37`, but the control is an invalid comparison member, so the adaptive
arm was correctly not launched. This does not promote adaptive workers,
source-add behavior, full-backlog execution, or maximum throughput.

The exact packet, receipt, and raw artifacts are under
`.logs/multi_account_fetch/20260810_captioned_uncached_control_adaptive_pair_run04/`.
The one staged RPC9 residual was requeued only in staging and sent through
exact `--fallback-only`; it ended `unavailable` at `direct_api` with no
NotebookLM action. The canonical row remains unchanged and pending. Direct
RPC9 replay remains prohibited.

The current full-backlog gate audit remains `not_ready_for_unattended_full_backlog`:
`.logs/multi_account_fetch/20260810_unattended_readiness_gate_audit_20260810.md`.
The package tests are green (`1385 passed, 4 skipped`), and the bounded
restart/resume contract is now proven by the isolated `run06` canary below.
Residual policy closure, a clean throughput pair, logged-out OS scheduler
execution, and a full drain/reconciliation remain unproven. Do not build or
use a full-backlog authorization receipt yet. `YtisUnattendedBacklog` is now
registered and verified in interactive-token, plan-only mode by the isolated
canary at
`P:/.logs/multi_account_fetch/scheduler_execution_canary_run01/result_receipt.md`.
S4U registration was denied by Windows (`Access is denied`), so logged-out
execution remains an open gate. The task must not be changed to execute/full
drain until the five independent authorization gates pass.

The current-policy plan/health path was separately revalidated without live
work under `.logs/multi_account_fetch/20260810_offline_plan_validation/`:
`400` pending rows partitioned as `a.hominidae=134`, `brsthomson=133`, and
`troup.hominidae=133`; Pro adaptive settings were forwarded, source-add
fallback was explicit with a `900s` deadline, and health returned
`health_status=planned` with `issues=[]`. Reusing the older canonical state
after a configuration change was rejected fail-closed; it was not overwritten.
This remains plan/health evidence only, not live readiness.

### Scheduler restart/resume and cache-path validation (2026-08-10)

The supervisor cache-isolation defect found in the earlier restart canary is
fixed. `scripts/run_unattended_backlog.py` now records the effective
`transcript_cache_db_path` in immutable supervisor state, forwards it explicitly
to `run_multi_account_fetch.py`, and rejects a child summary whose cache path
is missing or differs. State written before this field existed fails closed;
it is not silently resumed under the canonical cache. Focused verification is
`47 passed`, compilation clean, and `git diff --check` clean.

The supervisor also preserves an explicit selection contract for bounded
throughput work: `--caption-state` selects a caption cohort, while
`--uncached-only` requires an explicit read-only reference cache and validates
the matching mode/path in the child receipt. Normal full-backlog operation
continues to use the default all-pending scope.

`run05` completed six staged rows and its child receipt named the staged cache.
`run06` is the valid restart/resume canary: six staged rows were pending, the
active `chunk-0002` supervisor tree was terminated, the immediate exact
token-only auth preflight passed for `a.hominidae`, `troup.hominidae`, and
`brsthomson`, and one recovery attempt completed all `6/6` rows. The recovery
archive contains the interrupted accounts/manifests/receipts tree; staged
batch and transcript caches both pass SQLite integrity; the process scan
reports no matching descendants. These are bounded scheduler and isolation
results only. They do not prove OS Task Scheduler/logged-out operation, a full
backlog drain, or maximum throughput. Evidence:
`.logs/multi_account_fetch/20260810_scheduler_restart_resume_canary_run06/`.

### Uncached control/adaptive pair run03 (2026-08-10)

The fixed control completed against the isolated frozen `1,200`-ID cohort,
with `1,137 complete`, `63 failed`, and `0 pending`. All three exact token-only
auth identities matched immediately before launch; each account used fixed
three workers and batch size `50`. The combined completed-throughput estimate
was `1534.50` VPH (`1137 / 2667.454s * 3600`, using the maximum parallel account
elapsed). This is a partial reliability/control measurement, not a clean
hot-path result: `231` fallback attempts completed, raw events include
`48 transcript_chain_failed` outcomes, and the selected failures include two
deadline-exhausted fallbacks and two cookie-rotation failures.
All `1,137` completed selected IDs have non-empty rows in the isolated
transcript cache.

The packet's fallback-failure gate therefore invalidated the comparison and
the adaptive candidate was not launched. Both staging SQLite integrity checks
passed and no run processes remained after coordinator exit. This does not
promote source-add fallback, authorize full-backlog execution, or establish
maximum throughput. Governing packet and receipt:
`P:/.logs/multi_account_fetch/20260810_uncached_control_adaptive_pair_run03/`.

### Whisper fallback deadline hardening (2026-08-10)

The invalid uncached control run exposed three source-add fallback items that
spent the full 900-second outer deadline inside Whisper audio/transcription.
`csf/transcript.py` now gives all yt-dlp audio selectors one shared total
budget, passes the coordinator child deadline into the transcript worker,
budgets transcription from the remaining time, and stops retrying/starting
later providers when the deadline is exhausted. `bin/csf-source` leaves a
30-second margin for terminal result serialization. Focused verification is
`160 passed` across `tests/test_transcript.py` and
`tests/test_csf_source_fetch_timing.py`, plus compilation and diff hygiene.

The isolated three-ID retest produced two Whisper successes and one outer
timeout before the final short-circuit change. The subsequent one-ID bounded
120-second retest finished in `73.678s` with a terminal `whisper` timeout,
no pending row, and no outer fallback timeout. Treat this as reliability
hardening evidence, not fallback default-promotion or throughput evidence.
The governing packet and raw artifacts are under
`P:/.logs/multi_account_fetch/20260810_uncached_control_adaptive_pair_run02/`.
The source-add fallback route remains explicit and default-off; the adaptive
throughput arm remains blocked by the invalid control.

The supervisor now refuses `--until-empty` unless the caller supplies the
authoritative account settings file and a current, fingerprinted
`--full-backlog-authorization` receipt. That receipt must prove exact-account
auth, scheduler execution, cleanup postconditions, residual policy, and
throughput validation, bind the exact pending video-ID set as well as its count,
and include readable evidence paths and an expiry. The authorization schema is
version 2; old count-only receipts are intentionally rejected. The example is
`config/full-backlog-authorization.example.json`; no such live authorization
exists yet. Generate a receipt only through
`scripts/build_full_backlog_authorization.py`, after all five gates have
independent evidence; the builder does not perform or imply those gates.

## Current unattended gate (2026-08-10)

The 600-row adaptive-policy candidate is closed as a partial diagnostic, not a
throughput comparison: `548/600` complete and `52` failed. All `51`
source-add failures were `has_captions=0` with `rpc_code=9`; the fixed control
was not launched, and Pro adaptive scale-up did not exceed three workers.
See `.logs/multi_account_fetch/20260810_throughput_adaptive_policy_run01_decision_packet.md`.

The follow-up route-partitioned adaptive candidate is closed as
`candidate_invalidated_no_control`, not as a throughput result:
`.logs/multi_account_fetch/20260810_throughput_uncategorized_adaptive_pair_run01_decision_packet.md`.
It selected 1,200 `has_captions IS NULL` rows and reconciled 994 complete and
206 failed with no missing IDs. The candidate added 166 `Source add failed`,
25 `command_failed`, and 15 `nlm_content_below_threshold` rows. Pro adaptive
telemetry recorded targets only at 1, 2, and 3 workers, so scale-up was not
exercised and the fixed control was not launched. The current residual audit
is `.logs/multi_account_fetch/20260810_unattended_residual_audit_after_uncategorized_candidate.md`.
That 12-row fallback-only recovery canary is now complete: 9 rows produced
non-empty cache entries and 3 were explicit unavailable terminal failures.
All account probes, receipts, DB integrity, and no-NotebookLM-event gates
passed. Keep default promotion deferred because fallback tail cost and
per-item budgets are not yet established. Packet and receipt:
`.logs/multi_account_fetch/20260810_source_add_residual_fallback_canary_run03/decision_packet.md`
and `result_receipt.md`.

The isolated 1,200-row fixed control for the uncached adaptive comparison has
now completed but is an invalid comparison member: `1,087/1,200` complete and
`113` failed. `112` failures were exact `Source add failed`/
`rpc_code=9_failed_precondition` outcomes and one was `command_failed`; all
three immediate token-only auth probes passed. The adaptive candidate was not
launched. The 112 source-add rows were then requeued only through exact
failed-state receipts and processed with isolated `--fallback-only` manifests:
`101/112` produced non-empty transcript cache rows and `11` became explicit
terminal failures. Both staged SQLite integrity checks passed and zero raw
NotebookLM mutation events occurred. This validates bounded recovery only;
fallback default promotion, adaptive comparison, and full-backlog authorization
remain blocked. Governing packet and receipt:
`.logs/multi_account_fetch/20260810_source_add_control_recovery_run01/`.

The exact follow-up retest at
`.logs/multi_account_fetch/20260810_source_add_worker_protocol_retest_run01/`
closed the two malformed transcript-worker result cases. It also fixed and
validated leading-hyphen video-ID argument handling and normalized
`direct_api no_transcript` to the explicit `no_transcript` class. One exact row
completed with a non-empty Selenium cache; the other was explicitly classified
as `no_transcript`; no malformed worker result or NotebookLM mutation occurred.
Fallback default promotion remains deferred for its independent budget,
terminal-policy, retry-limit, quality, and tail-cost decision.

The exact no-caption fallback branch found and fixed an oEmbed false-terminal
boundary. The first 12-row attempt stopped at HTTP 403 before the real chain;
the code fix now bypasses oEmbed for explicit no-caption fallback and the
run-02 retest reached yt-dlp/direct API/Selenium/Whisper with no NotebookLM
events. All 12 were nevertheless terminal external-content failures because
the selected videos were private/unavailable or required rotated YouTube
cookies. Keep `--route-no-captions-to-fallback` opt-in/off by default; this
branch does not authorize `--until-empty` or a full-backlog run.

Current residual audit:
`.logs/multi_account_fetch/20260810_unattended_residual_audit_after_no_caption_retests.md`.
The 12 run-02 IDs are terminal and must not be requeued without a new content
or cookie decision packet. NotebookLM authentication passed immediately before
both live attempts and is not the residual cause.

## Downstream wiki quality debt (2026-08-11)

The canonical wiki queue is a separate downstream state machine at
`P:/.data/wiki/_state/nlm-sync/queue.json`. The latest authoritative receipt,
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`,
reports `schema_version=2`, `completed=47`, `failed=2`, `poisoned=0`,
`needs_resynthesis=0`, `pending=0`, and `in_progress=0`. The exact
`4017aa6e-35fb-426d-bc53-34620bec405e` semantic debt was cleared by a bounded
Stage-C checkpoint resume: five pages were produced, all five passed normal
validation, and all five have complete four-hop provenance. This supersedes
the older run12/run14 statements below; those remain historical negative
evidence and must not be read as current queue state.

The latest run does not prove complete source coverage: its reconciled citation
coverage is `19/36` (`52.8%`). Keep that quality caveat visible. The separate
historical manifest audit still reports `13` gaps, `12` with output but no
exact worker/profile/attempt receipts and one with no local output, and
`manifest_recovery_eligible_count=0`. No historical entry may be fabricated or
repaired from output alone. Receipt:
`P:/.logs/wiki-yt-queue/20260811/manifest_gap_audit_after_run16.json`.

## Latest live validation receipt: Candidate 6 run03 (2026-08-08)

The live telemetry validation run was executed with fresh output root
`P:/packages/yt-is/.logs/sharded_lane_series/candidate6_telemetry_validation_run03_current`.
The immediate canonical probes passed for the two configured lanes:
`a.hominidae` (Pro) and `troup.hominidae` (Free). `brsthomson` was not in the
lane configuration and is a separate account state.

The run is **invalidated**, not an authentication failure and not valid VPH
evidence. Both lane processes exited `0`, but the coordinator found
`source_add_failed` artifacts for the same six source IDs in both lanes and
failed closed. The resulting combined `0.00 VPH` is an invalidation
placeholder. Candidate 6 telemetry was still emitted: field population and
retry-exit distribution passed; the overshoot phase split is partial/useful
evidence and is not a promotion pass.

Detailed receipt:
`.logs/sharded_lane_series/candidate6_telemetry_validation_run03_result.md`.
Do not ask the operator to authenticate again from this result alone. The next
run requires a narrow source/cohort decision packet and a source-add gate; the
separate Windows decoding exception in the lane stderr should also be tracked
as a harness issue, not folded into the auth diagnosis.

## Current source-add and unattended boundary (2026-08-09)

The current all-account bounded canary is recorded in
`.logs/multi_account_fetch/20260809_source_add_probe_canary_run04_decision_packet.md`.
All three exact account probes passed immediately before source work. The
narrow rpc9 handling attempted each add once and prevented blind replay. A
post-error source-list probe observed one source and a `0 -> 1` count change
for each account, but each returned source then failed `nlm source content`
with `SourceNotFoundError` on both content attempts. This is a source/provider
addressability result, not an auth failure. Source-count growth is not usable
source proof. Do not replay the three failed IDs or launch the full backlog
until a separately authorized mechanism or fallback path produces usable
transcripts and reconciled receipts.

The earlier run02 packet remains historical evidence for the original six
code-9 failures:
`.logs/multi_account_fetch/20260809_adaptive_canary_run02_source_add_decision_packet.md`.
`csf/nlm_batch.py` retains the narrow no-replay rule for code 9; unclassified
add errors retain one bounded retry. Do not reinterpret either result as an
authentication failure or request another login from these artifacts.

The existing no-caption fallback was then validated on one fresh pending
`has_captions=0` item per account. All three produced non-empty Selenium
transcripts, reconciled to `complete`, and emitted no source-add events. Full
receipt and gates:
`.logs/multi_account_fetch/20260809_no_captions_fallback_canary_run01_decision_packet.md`.
This establishes a promising safe route for the no-caption partition only; it
does not prove captioned/unknown source handling, adaptive scale-up, sustained
throughput, or full-backlog readiness.

## Latest bounded throughput canaries (2026-08-11)

The batch-10 policy-floor packet
`P:/.logs/multi_account_fetch/throughput_pair_20260811_batch10_policy_floor_run01/`
was invalid: both controls were partial (`508/513` and `501/513`) because of
source-add/source-addressability failures, and both adaptive arms were
withheld. A replacement packet excluded exactly those `17` failed IDs:
`P:/.logs/multi_account_fetch/throughput_pair_20260811_batch10_clean_cohort_run02/`.
It was also invalid: controls completed `512/513` and `509/513`, with five
`SourceNotFoundError` rows and no source-add failures; adaptive was again
withheld. Immediate token-only preflight passed for `a.hominidae`,
`troup.hominidae`, and `brsthomson` before both runs, and staging integrity
passed. These are source/content-addressability findings, not authentication
failures or valid VPH evidence. Do not rerun either packet or claim a worker,
batch, or optimality result from them. Receipts:

- `P:/.logs/multi_account_fetch/throughput_pair_20260811_batch10_policy_floor_run01/result_receipt.md`
- `P:/.logs/multi_account_fetch/throughput_pair_20260811_batch10_clean_cohort_run02/result_receipt.md`

The first exact captioned-partition check also exposed a cache reconciliation
gap: industrial manifest items with an existing usable transcript cache were
counted as cached but left `analysis_status=pending`. `bin/csf-source` now
marks those live rows `complete` with `last_stage=cache` and emits
`transcript_cache_reconciled`; the focused and full source-timing tests pass.
The fresh exact-manifest validation reconciled three cached captioned rows and
emitted no source-add events. Actual uncached captioned source-add behavior is
still unmeasured.

An exact three-item `has_captions=NULL`, uncached canary then completed all
three through NotebookLM: each source add completed once and each content
receipt was `status=ready`. Full packet:
`.logs/multi_account_fetch/20260809_unknown_uncached_source_add_canary_run01_decision_packet.md`.
The current evidence therefore supports route partitioning by source shape,
not a blanket source-add failure diagnosis. A larger route-partitioned
throughput canary is still required before choosing unattended defaults.

### Industrial worker-failure fallback validation (2026-08-10)

The fixed-worker industrial path was repaired so the exact logical batch
payload is retained even when adaptive scheduling is disabled. With the
explicit `--route-industrial-failures-to-fallback` flag, DB-confirmed worker
failures are requeued once and sent to transcript fallback with
`skip_notebooklm=true`; they are not replayed through the failing NotebookLM
content operation.

The bounded validation is recorded at
`.logs/multi_account_fetch/20260810_industrial_failure_fallback_canary_run02_decision_packet.md`.
All three canonical account probes passed and all three exact IDs finished
`complete` with `last_stage=whisper`. Account elapsed times were 145s, 312s,
and 563s, so this is a validated recovery path with a material tail, not a
throughput win or a default-route authorization. Direct `ADD_SOURCE`
`rpc_code=9` addressability remains unresolved and the route flag stays
explicitly off by default.

### Coordinator health canary (2026-08-10)

The fresh three-account coordinator canary passed for 30 exact pending IDs:
10 each for `a.hominidae`, `troup.hominidae`, and `brsthomson`. All 30 rows
reconciled to `complete`, all three token-only probes observed the expected
email, the canonical transcript cache contains 30 non-empty rows, and the
batch DB integrity check is `ok`. The Pro route forwarded bounded adaptive
settings; both Free routes used fixed three-worker settings. The decision
packet is `.logs/multi_account_fetch/20260810_unattended_canary_run01_decision_packet.md`.

This promotes coordinator health and exact-account reconciliation only. It
does not prove sustained VPH, adaptive scale-up, poisoned synthesis, or
full-backlog readiness. The two Free child logs contain a Python 3.14 Windows
overlapped-future warning; it is recorded as a reliability follow-up, not
silently treated as a clean runtime.

### Captioned adaptive pair candidate invalidation (2026-08-10)

The follow-up captioned adaptive candidate is invalidated as
`candidate_invalidated_cache_only_no_control`. All 200 selected rows reported
complete, but authoritative DB reconciliation showed `last_stage=cache` for
every row; all 200 canonical `transcript_cache` rows already existed with
`source=notebooklm`; and the raw run contained 200
`transcript_cache_reconciled` events but no source-add, materialization,
source-content, or command events. The single adaptive decision event does not
prove scale-up. The fixed control was not launched, and this run is not VPH or
adaptive-policy evidence. Receipt:
`.logs/multi_account_fetch/20260810_throughput_captioned_adaptive_pair_run01/result_receipt.md`.

Any future throughput pair must prove an uncached cohort before launch and
require the intended live event family before calculating useful throughput.

### Source-add residual recovery and current disposition (2026-08-10)

The six exact Free-account source-add residuals are now classified without
reopening authentication:

- Four troup IDs completed through the earlier post-failure fallback canary.
- `hW6FfYA6ios` and `keFH7JwVAvI` were tested through the new exact-manifest
  `--fallback-only` route with immediate token-only preflight for
  `troup.hominidae` and `brsthomson`.
- The new route emitted no NotebookLM content/materialization or
  `ADD_SOURCE` action. `hW6FfYA6ios` is private/unavailable and
  `keFH7JwVAvI` is unavailable; both remain explicit terminal `failed` rows.

Packet and result receipt:
`.logs/multi_account_fetch/20260810_source_add_fallback_only_run01/decision_packet.md`
and
`.logs/multi_account_fetch/20260810_source_add_fallback_only_run01/result_receipt.md`.

`--fallback-only` is a bounded recovery mechanism, not the default route. It
requires an exact manifest, bypasses NotebookLM and the oEmbed terminal
short-circuit only for that manifest, and now avoids unnecessary NotebookLM
worker prewarm/cleanup when the queue contains only fallback work. The exact
six-row branch is closed as `4 recovered, 2 terminal unavailable`; no blind
retry or login is justified from these results.

### Source-add fallback recovery run 01 (2026-08-10)

The larger unattended canary selected exactly 37 rows whose authoritative
database state was `failed` with `failure_reason=Source add failed`. The
guarded exact-manifest fallback recovery completed 34 rows and classified
three terminal failures. One row initially remained pending after the old
in-process Whisper transcription path exceeded 25 minutes without a terminal
event; a subsequent exact one-item fallback-only run completed it through
Selenium in 22.838s. The run is partial, not a promotion.

Result receipt:
`.logs/multi_account_fetch/20260810_source_add_fallback_recovery_run01/result_receipt.md`.

The residuals are `f_saCFbPe4c` and `OtuoOWsnLDg` (`unavailable`) and
`qeHxr59VUJw` (unclassified direct-API no-transcript failure). The former
pending row `S2jF501Laq8` is now `complete` through the exact fallback-only
route; its live result used Selenium, so the new process-isolated Whisper path
was not live-exercised. The unknown row needs a discriminating diagnostic. No
direct NotebookLM source-add replay or authentication request is justified.

The new `csf/whisper_worker.py` process boundary and the finite
coordinator-owned industrial-worker deadline address the observed unattended
hang surface. They are reliability hardening, not throughput evidence or
full-backlog authorization.

The adaptive scheduler now also recovers quarantined worker capacity after its
cooldown, with a bounded two-attempt recovery budget reset by successful work.
Incomplete industrial outcomes are emitted as `fetch_completed` with
`status=partial`, `unprocessed_count`, and an explicit failure reason rather
than as a false completed receipt. This is offline-tested hardening; it is not
live throughput evidence.

### Source-add fallback policy canary run 02 (2026-08-10)

The fresh 400-row canary for the explicit
`--route-source-add-failures-to-fallback` policy reconciled `394 complete`,
`6 failed`, and `0 pending`: `a.hominidae` completed 132/134,
`troup.hominidae` 131/133, and `brsthomson` 131/133. All three token-only
account probes passed immediately before launch, and every child receipt
reported all selected rows processed.

The six terminal failures were all `command_failed`, so the exact
source-add-only predicate correctly did not route them. One exact RPC9
`Source add failed` row was admitted to fallback and completed. The fallback
event stream contained no NotebookLM source-add, materialization, or source
content actions. This validates routing isolation and safe fallback admission,
not complete processing, command-failure recovery, sustained throughput,
adaptive scale-up, or full-backlog readiness. Keep the route flag explicit and
off by default; do not launch `--until-empty` from this result.

Packet and raw evidence:
`.logs/multi_account_fetch/20260810_source_add_fallback_policy_canary_run02/decision_packet.md`
and `.logs/multi_account_fetch/20260810_source_add_fallback_policy_canary_run02/live/chunk-0001/`.

The six `SourceNotFoundError` rows left by that canary were subsequently
requeued with six per-ID receipts and recovered through exact-manifest
`--fallback-only` runs. All six are now `complete` at `last_stage=whisper`:
`a.hominidae` 2/2 in 71.244s, `brsthomson` 2/2 in 60.568s, and
`troup.hominidae` 2/2 in 15.399s. The fallback event scan found no NotebookLM
source-add, materialization, or source-content actions. This closes the
observed source-addressability residual without replaying NotebookLM, but it
does not promote fallback to the default route, establish throughput, or
authorize `--until-empty`.

### Source-add fallback policy canary run 03 (2026-08-10)

The fresh 400-row canary after durable `900s` timeout propagation reconciled
`392 complete`, `8 failed`, and `0 pending`: `a.hominidae` completed 131/134,
`troup.hominidae` 132/133, and `brsthomson` 129/133. The supervisor returned
nonzero because the chunk was partial; this is the intended fail-closed result.

Exactly `40` newly observed source-add failures entered the fallback route.
All `40` were started and terminally completed: `33` Selenium successes,
`1` Whisper success, and `6` explicit unavailable failures. No fallback
timeout or fallback-failed event occurred, and no fallback event emitted a
NotebookLM source-add, materialization, or source-content action. The eight
selected failures were separate terminal outcomes: two `command_failed` and
six `unavailable`.

This validates the exact source-add fallback boundary and its 900-second
per-item deadline for this cohort. It does not validate a blanket fallback
for command/unavailable failures, default promotion, full-backlog execution,
or maximum throughput. Keep `--route-source-add-failures-to-fallback`
explicit and off by default. The governing receipt is
`P:/.logs/multi_account_fetch/20260810_source_add_fallback_policy_canary_run03/result_receipt.md`.

Recovery packet and raw evidence:
`.logs/multi_account_fetch/20260810_source_not_found_fallback_recovery_run01/decision_packet.md`.

The durable coordinator path now carries an explicit
`--route-source-addressability-failures-to-fallback` option for this exact
failure class. The predicate admits only authoritative `SourceNotFoundError`
or source-not-found rows, requeues each exact ID once, and routes it through
fallback-only without replaying NotebookLM. Supervisor state, coordinator
summaries, child environments, plan receipts, and execution settings all carry
and validate the flag. Focused source/coordinator/supervisor tests pass. The
flag remains off by default; no fresh policy canary has yet promoted it or
authorized `--until-empty`.

### Source-add deadline post-fix fallback-only canary run04 (2026-08-10)

The isolated 40-row follow-up exercised the nested Whisper deadline hardening
through explicit `--fallback-only`. All 40 exact source-add residual IDs
terminalized: 2 produced non-empty cache rows (`ytdlp=1`, `whisper=1`) and 38
were classified `unavailable` at `direct_api`; no row remained pending, no
outer fallback timeout occurred, and no NotebookLM source-add/materialization/
content action was emitted. Failed-item elapsed was p50 `84.031s`, p95
`99.773s`, max `101.068s`; the two-success tail reached `791.397s`.

The isolated staged DB ended `complete=9186`, `failed=292`, `pending=333641`
versus the unchanged canonical `9184/294/333641`; both integrity checks were
`ok`. This validates bounded fallback terminalization and preserves the
deadline fix, but it does not validate normal source-add admission because the
effective route flag was false under `--fallback-only`. Keep
`--route-source-add-failures-to-fallback` explicit and off by default; do not
authorize full-backlog execution or claim a throughput result from this run.
The governing packet and receipt are
`P:/.logs/multi_account_fetch/20260810_source_add_deadline_postfix_canary_run04/decision_packet.md`
and
`P:/.logs/multi_account_fetch/20260810_source_add_deadline_postfix_canary_run04/result_receipt.md`.

### Source-add residual recovery run 01 (2026-08-10, current receipt)

The exact combined source-add manifest contained 26 rows selected from the
authoritative database after guarded requeue validation. The fallback-only run
completed 14 rows and failed 12 (`9` unavailable, `3` initially unclassified);
the final database state was `pending=0` for the manifest. It emitted no
NotebookLM source-add, materialization, or source-content actions. This is a
partial recovery result, not a default-route or full-backlog promotion.

Governing artifacts:
`.logs/multi_account_fetch/20260810_source_add_residual_recovery_run01/decision_packet.md`,
`result_receipt.md`,
`classification_repair_receipt.json`, and
`classification_repair_remaining_run01.json`.

The two previously null-reason rows were repaired only after exact raw-event
evidence was found and while holding the canonical DB fetch lock:
`J-TUNeiLmfs` is `no_transcript` at `whisper`, and `qeHxr59VUJw` is
`Source add failed` with `rpc_code=9` at `source_add`. No retry was launched by
the repair. The current read-only residual audit is
`.logs/multi_account_fetch/20260810_unattended_residual_audit.md` with
classification version `unattended-residuals-v3`: 35 failed rows remain,
including 29 terminal unavailable rows, 2 terminal no-transcript rows, 2
terminal empty-Whisper-transcript rows, and 2 cookie-source rows blocked on
external YouTube cookie state. The six original content-threshold candidates
were processed through their dedicated fallback-only recovery: five completed
with non-empty cache entries; `x85tFCIc3Ps` is explicitly classified as
terminal no-transcript after its direct-API/subtitles-disabled outcome. The
result receipt is
`.logs/multi_account_fetch/20260810_content_threshold_recovery_result_receipt.md`.
No unknown or other rows remain; the two cookie-source rows remain blocked and
must not be confused with NotebookLM authentication.

The run also exposed an operational tail: one long-audio fallback item spent
about 30 minutes across failed audio/Whisper attempts before completion. The
existing stage deadlines are finite, but a full-backlog promotion still needs
an explicit per-item tail policy and receipt classification; the four-hour
child deadline alone is too coarse to establish unattended safety.

The remaining `qeHxr59VUJw` source-add residual was then given its own exact
fallback-only probe. It ended with repeated `yt-dlp`/audio evidence that the
video is unavailable, emitted no NotebookLM events, and was normalized to a
terminal `unavailable` row under the DB lock. The packet and receipts are
`.logs/multi_account_fetch/20260810_source_add_residual_recovery_run01/qeHxr59VUJw_decision_packet.md`,
`qeHxr59VUJw-requeue-apply.json`, and
`qeHxr59VUJw-classification-repair.json`. The current audit therefore has no
remaining `source_add`, `command`, unknown, or other class: 2 cookie-source
rows are externally blocked, 2 rows are terminal no-transcript, 2 are
terminal empty-Whisper-transcript, and 29 are terminal unavailable. The six
original content-threshold candidates are closed by the dedicated recovery
receipt, with one explicitly classified terminal no-transcript.
The 17-row
command-residual expansion receipt is
`.logs/multi_account_fetch/20260810_command_residual_expansion_result_receipt.md`.
It confirms that every selected ID was admitted with `skip_notebooklm=true`
and that no NotebookLM source-add/materialization/content event occurred
after fallback admission. The result remains `partial_recovery`: the
unavailable row and the empty-Whisper-transcript row are not silently retried,
and the route remains opt-in.

The two residual rows whose earlier Whisper failure was the yt-dlp `-f best`
warning were given a bounded exact fallback-only canary after the selector
fallback was unit-tested. Both remained failed because the live stderr showed
age restriction plus rotated Firefox YouTube cookies. A read-only Chrome
cookie diagnostic failed at yt-dlp cookie-database copy before extraction. The
run emitted no NotebookLM actions and did not request interactive login. The
current packet and receipt are
`.logs/multi_account_fetch/20260810_whisper_default_selector_canary_decision_packet.md`
and `.logs/multi_account_fetch/20260810_whisper_default_selector_canary_result_receipt.md`.
This branch is `blocked_cookie_source`; do not requeue those IDs without a new
cookie-source decision packet.

## Multi-account retry receipt: troup.hominidae (2026-08-09)

The exact 20-item retry passed the canonical token-only preflight and used the
account-scoped typed industrial route. It did not complete a transcript: 17
items failed during `ADD_SOURCE` with provider `rpc_code=9`, and 3 reached
extraction but returned `nlm_content_below_threshold`. No legacy profile error,
auth failure, or transcript fallback occurred; no VPH result was measured.

The child returned a structured result with `failed=20`, but the old worker
counted failures without writing `analysis_status`, so the coordinator first
saw all 20 as pending. A guarded reconciliation verified the exact manifest and
all-20-pending precondition, then marked 17 rows `source_add_failed` and 3 rows
`nlm_content_below_threshold` for retry. The durable fix in
`dev/worker_pool/worker_main.py` now persists explicit and omitted failures in
both serial and double-buffered paths. `csf/nlm_batch.py` also preserves safe
underlying source-add cause type and RPC code in future telemetry.

Receipt and packet:
`.logs/multi_account_fetch/20260809_retry_41b/live_result.md` and
`.logs/multi_account_fetch/20260809_retry_41b/decision_packet.md`.
Do not request authentication again from this result. Do not rerun these IDs
without a new source-add decision packet and early-abort gate.

## Active work stream

### 2026-08-09 offline operational validation

The current coordinator and downstream cache-first path were validated without
external work. Receipt and claim ledger:
`P:/docs/handoffs/yt-is-forward-sync-offline-validation-20260809/HANDOFF.md`.

The authoritative batch DB is `P:/.data/yt-is/batch_status.sqlite`; the
package-local `.data/yt-is/batch_status.sqlite` is stale. The default six-day
uncategorized scope is currently empty, so any future bounded operation must
name an explicit scope. A 20-item `--all-uncategorized` dry-run produced exact
three-account manifests and reconciled `pending=20` without external calls or
DB mutation. This is operational-readiness evidence, not a live result.

The active work is documented in the `docs/handoffs/` chain:

### Subsequent bounded live validation: Free accounts (2026-08-09)

The exact-account preflight passed for `troup.hominidae` and `brsthomson`.
The bounded 20-ID live run completed `14/20` and failed six items at
NotebookLM `ADD_SOURCE` with `rpc_code=9`; it is a source-add result, not an
auth result. Do not request authentication or retry those IDs without a new
source-add decision packet. Full receipt:
`P:/docs/handoffs/yt-is-free-account-live-validation-20260809/HANDOFF.md`.

### Reliability hardening follow-up (2026-08-09)

Two offline-only reliability fixes are now verified. `csf/csf_logging.py`
accepts descendants of the workspace-level `<current-drive>:\.logs` root in
addition to its existing cwd/home roots, so an explicit `P:\.logs\...` child
event directory is no longer silently redirected to the package-local `.logs`.
Traversal protection remains `resolve()` plus `is_relative_to()`. Focused
verification: `tests/test_csf_logging.py` — 3 passed.

The wiki-yt synthesis path now fails closed when a concept has no citations,
an empty claim/excerpt, or an unmapped/ambiguous source reference. Map-reduce
head fallback is classified as `synthesis_degraded`, and queue records retain
that, `citation_invalid`, or `synthesis_backend_exhausted` rather than
reporting a generic pipeline failure. Partial synthesis still cannot advance
the wiki manifest or rename a notebook. Concurrent sync workers now use a
locked reload/merge manifest writer; confirmed maintenance repairs use the same
lock and must run queue-exclusive. Focused verification: wiki-yt synthesis
tests — 22 passed; queue/auth tests — 12 passed; full wiki suite — 44 passed;
manifest concurrency tests — 2 passed; Python compilation passed. These changes
authorize no new live fetch and do not retry the known source-add failures.
Detailed handoff: `P:/docs/handoffs/yt-is-wiki-synthesis-quality-gate-20260809/HANDOFF.md`.

### Current wiki-yt queue and manifest reconciliation (2026-08-09)

The current queue state is `pending=0`, `in_progress=0`, `completed=43`
records (`39` distinct IDs), `failed=2`, `poisoned=0`, and
`failure_history=6`. The three previously poisoned items were promoted through
the approved degraded fallback path; semantic re-synthesis remains deferred.
The two failed IDs remain profileless `0 pages` records and are absent from all
three current canonical account inventories. They are blocked as stale/unowned
records, not an auth failure; the ownership packet is
`P:/.logs/wiki-yt-queue/20260809/wiki_failed_residual_resolution_20260810.md`.
No queue worker processes remain.

The durable wiki queue retry path now fails closed if any active failed record
lacks an exact canonical account profile. It no longer falls back to a legacy
queue-level value such as `codex`, which could misroute a retry. The queue
retry regression tests pass (`56` tests in the full wiki suite).

Thirteen older queue completion records remain outside the manifest because
they have no unambiguous current worker/profile receipt; they were not
fabricated. Twelve have transcript/provenance evidence only and one has only a
queue completion record. None is eligible for safe manifest recovery. See the
full claim ledger and exact IDs at
`P:/docs/handoffs/yt-is-wiki-queue-live-20260809/HANDOFF.md`.

The follow-up historical-gap audit is now precise and read-only at
`P:/.logs/wiki-yt-queue/20260809/historical_manifest_gap_audit_current.md`.
Of the 13 older queue IDs outside the manifest, 12 have exact transcript
frontmatter plus concept provenance but no preserved worker/profile receipt;
`d66afb5b-35cb-4e89-bd51-3b120e15d643` has only a completed queue record and no
local output evidence. None is eligible for manifest recovery. Do not infer a
manifest row from queue status, title, transcript presence, or the audit
packet itself. The auditor and tests are
`P:/.agents/skills/wiki-yt/scripts/audit_manifest_gaps.py` and
`P:/.agents/skills/wiki-yt/tests/test_manifest_gap_audit.py`.

1. **`P:/docs/handoffs/yt-is-nlm-to-wiki-integration-20260730/HANDOFF.md`** — parent.
   Making yt-is the single canonical YouTube transcript store and driving the
   forward-sync path into wiki-yt. This parent handoff owns the current
   integration counts and next actions; do not rely on stale numbers repeated
   in this package file.

2. **`P:/docs/handoffs/yt-is-nlm-to-wiki-fixes-20260730/HANDOFF.md`** — child.
   F2 (cache-first + feed-forward) shipped. F1 (wiki-query Stop hook) and F3
   (orphan resolver) deferred.

3. **`P:/docs/handoffs/wiki-yt-architecture-decisions-20260730/HANDOFF.md`** — child.
   Five locked architecture decisions (NotebookLM primary, cache-first shipped,
   Stage 0 rejected, wiki-yt rename, non-lossy metadata pipeline).

4. **`P:/docs/handoffs/yt-is-progressive-visual-analysis-20260804/HANDOFF.md`** —
   separate open workstream. It is ready to implement U-05 through U-09 for
   split transcript/visual pools, idempotent ingestion, OCR-driven profile
   promotion, legacy-status cutover, and final tests/docs.

Older NLM bulk-ingest, consolidation, and v3-refactor handoffs remain useful
background but are not current authority unless one of the active handoffs
reopens them. The throughput/auth receipts above are a separate diagnostic
branch; they do not mean the integration or visual workstreams are complete.

## Databases

Two transcript databases exist — know which one you're working with:

| Database | Location | Rows | Purpose |
|----------|----------|------|---------|
| **Primary** | `P:/.data/yt-is/transcripts.sqlite` | ~10,072 | The active cache. Integration imports land here. |
| **Stale package-local** | `P:/packages/yt-is/.data/yt-is/transcripts.sqlite` | 369 | Old dev DB. Do not use for new work. |

Before any operation, verify which DB your code targets via `YTIS_TRANSCRIPT_CACHE_DB_PATH`.

## Key files

- `csf/nlm_config.py` — NotebookLM batch size, source cap, materialization timeout, auth policy defaults
- `csf/nlm_batch.py` — worker-owned notebook rotation and source-add subbatch sizing
- `bin/csf-source` — preflight routing split, worker-run orchestration, logging
- `csf/transcript.py` — oEmbed probe, direct_api classification, Whisper fallback, negative-cache persistence
- `csf/batch_status.py` — transcript cache / negative cache / status persistence
- `csf/cache.py` — `get_cached_transcript_by_video_id()` (added by F2 forward-sync)
- `scripts/title_bridge.py` — shared title→video_id bridge (extracted from importer)
- `scripts/import_nlm_transcripts.py` — one-time backfill importer (nlm-to-wiki → yt-is cache)
- `tests/test_csf_source_fetch_timing.py` — routing regression tests
- `tests/test_transcript.py` — direct_api and Whisper regression tests
- `tests/test_shared_modules.py` — 31 tests for csf/urls.py, csf/paths.py, csf/clusters.py

## Import workflow operationalization

The import-workflow operationalization is now on `main` in the reviewed
cherry-picked commits `e75af02` and `deb26ba`. The former review branch
`codex/yt-is-import-operationalization` (tip `ae4952f`) was verified clean and
tree-equivalent before it was retired; `main` is now the sole active worktree.

- `scripts/build_video_selection_manifest.py` builds deterministic manifests
  from local `analysis_status` rows only.
- `bin/csf-source fetch --video-manifest PATH` selects exact IDs. Add
  `--selection-receipt PATH` for an atomic selection snapshot, and
  `--verify-selection-receipt PATH` to fail closed if the manifest or relevant
  status rows changed; live manifest fetches still require an explicit
  `--limit`.
- `scripts/reconcile_video_imports.py` lists unfinished `video_import` runs or
  reconciles one run against `analysis_status` without writing either DB. Import
  provenance records the effective batch-status DB path; the CLI uses that path
  unless `--batch-db` is supplied explicitly, and fails closed on unavailable DBs.
- Design and acceptance evidence: `docs/operations/import-workflow-next-design.md`
  and `docs/proposal_for_review.md`.

No live fetch, external API call, NotebookLM action, or raw-artifact mutation
was performed as part of this implementation. The current main worktree also
preserves the pre-existing modification to
`.logs/term_5bd58f58.jsonl`; do not reset, stage, or delete it. The canonical
transcript integration remains in progress: resolve the unmatched transcripts,
implement forward-sync, and sync the unchecked channels listed above. The
throughput investigation remains dormant and does not establish an optimal VPH.

## Bounded adaptive worker scheduler (offline implementation)

The implementation plan and validation packet are:
[`docs/operations/bounded-adaptive-worker-scheduler-implementation-plan.md`](P:/packages/yt-is/docs/operations/bounded-adaptive-worker-scheduler-implementation-plan.md).
[`docs/operations/bounded-adaptive-worker-scheduler-decision-packet.md`](P:/packages/yt-is/docs/operations/bounded-adaptive-worker-scheduler-decision-packet.md).
The opt-in implementation provides bounded worker scale-up/down at safe batch
boundaries, stable assignment accounting, and transition telemetry. A review
found and fixed a queue-loss edge case: dispatch now removes no more batches
than there are eligible worker identities. The current fixed-worker path
remains authoritative. Malformed worker summaries, missing health telemetry,
duplicate or cross-lane worker labels, and canonical account mismatches fail
closed. Untrustworthy worker results are requeued under the same stable batch
identity; ordinary content failures remain terminal. The latest offline
hardening centralizes that requeue operation and fails closed if a failed
future has no assignment ID or batch payload, instead of claiming recovery
that cannot be proven. The focused adaptive/sharded/load-ladder suite passes
`118` tests, and the full `tests/test_nlm_batch.py` regression passes `151`.
This is offline correctness evidence, not a live performance result.

### 2026-08-09 coordinator and worker-persistence hardening

The current offline hardening closes two reliability gaps around the canonical
coordinator. `scripts/run_multi_account_fetch.py` now revalidates an exact
retry manifest after manifest preparation, records selected IDs that disappear
from the database during reconciliation, and never reports completion from a
child exit code when selected rows remain pending or missing. Its receipts
also record the adaptive-worker policy when that explicit opt-in is used.
`dev/worker_pool/worker_main.py` normalizes omitted or malformed worker results,
persists every claimed ID, and requeues claimed work when a worker process
fails before producing trustworthy results. `csf/nlm_batch.py` keeps generic
source-add telemetry typed and bounded rather than copying arbitrary exception
text into durable artifacts.

The complete affected offline regression boundary passed `310` tests on
2026-08-09, including coordinator, auth, worker-persistence, source-add,
sharded-runner, and adaptive-scheduler suites. This verifies code paths and
receipt behavior only; it does not validate a live source-add outcome or prove
an adaptive VPH improvement. An independent adversarial follow-up review found
no remaining blocker, HIGH, or MEDIUM findings. Fixed-mode coordinator children
also strip ambient adaptive environment variables, and durable coordinator and
NotebookLM command diagnostics redact credential-shaped values.

The active auth contract uses exact account identities and canonical storage:
`a.hominidae` -> `P:/.data/yt-is/nlm-auth/storage_state.json`,
`troup.hominidae` -> `.../storage_state_troup_hominidae.json`, and
`brsthomson` -> `.../storage_state_brsthomson.json`. Worker labels such as
`a.hominidae-worker-01` are routing names only and never select auth state.
The active run path calls `ensure_account_session()`: it probes healthy state,
restores only an unusable canonical file from the matching backup, repairs an
expired session from its exact durable master token, and fails closed before
source work if repair is unavailable. The legacy CLI/profile-sync/CDP family
commands are not the active run path, and cookies are never copied between
identities. Active coordinators and workers pass `allow_bootstrap=False`; only
the explicit `bin/csf-nlm-auth` command may perform one-time browser bootstrap.

The canonical coordinator and direct `bin/csf-source fetch` path use the
shared DB-scoped lock in `csf/fetch_run_lock.py`. The coordinator holds it
through selection, child execution, and reconciliation. Direct
`bin/csf-source fetch` invocations acquire the same lock and fail closed on
contention. Coordinator children receive an exact parent/run/database
ownership envelope so they do not deadlock on the lock already held by their
coordinator. Legacy analysis/benchmark entry points remain separate and must
not be used as concurrent production fetchers.

The durable one-time bootstrap entry point is
`python P:/packages/yt-is/bin/csf-nlm-auth --profile <exact-profile>`. If the
matching master token does not exist and the dedicated family is not already
signed in, attach a user-owned loopback CDP context containing only that exact
account with `--cdp-url`; the command rejects remote endpoints, ambiguous
multi-account contexts, and `--all --cdp-url`. After bootstrap, normal renewal
is headless and token-only. `python -m csf.nlm_keepalive` remains the
account-aware maintenance path that probes and backs up all three identities
separately.

**Current auth boundary (verified 2026-08-08 15:18):** all three exact
identities now have durable master tokens and passed the package CLI's
token-only probe in one `--all` run: `a.hominidae` (Pro), `troup.hominidae`
(Free), and `brsthomson` (Free2). The account-aware keepalive then completed
with exit `0` and backed up all three matching canonical storage files. This
supersedes the earlier same-day blocker below; retain that text as historical
context, not current status.

The one-time bootstrap lesson is durable: the existing account-owned Chrome
profile `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json.browser_profile`
is the verified Free2 bootstrap source. A fresh `browser/notebooklm-free-2`
profile was blank and was not the correct sign-in source. `DEFAULT_FAMILIES`
now points Free2 at the established profile, while the active run remains
token-only. The interactive verifier also waits through the transient
"authentication expired/invalid" response from a newly unsigned context, but
only when the explicit bootstrap command requests interactive mode.

The latest narrow source discovery receipt is
`P:/tmp/ytis-checkpoint-discovery-narrow-20260807.json`; it completed with
`decision=proceed_with_discovery`, no conflicts, and no walk errors. The
canonical active auth owners are `csf/nlm_auth_check.py`, `csf/nlm_client.py`,
and `csf/nlm_keepalive.py`. Interactive bootstrap used `notebooklm-py 0.8.0`,
which recognizes the current Gemini Notebook host; the installed runtime was
verified separately from the repository's `requirements.txt`.

**Unattended keepalive receipt (verified 2026-08-08):** the package-owned
installer `scripts/install_nlm_keepalive_task.ps1` registers
`YtisNlmAuthKeepalive` to invoke `C:\Python314\python.exe -m
csf.nlm_keepalive --log-file P:/.data/yt-is/nlm-auth/keepalive.log` directly
from this package on a daily 03:00 trigger. The final manual run completed
with exit `0`: token-only repair/probe passed for all three identities and the
matching backups were pushed to the local bare backup repository. The
registered task XML remains verified: direct Python action,
`StartWhenAvailable=true`, battery start/continue enabled, idle-end stop
disabled, and `MultipleInstancesPolicy=IgnoreNew`.

The Pro capture initially failed before writing credentials because `gpsoauth`
was absent from the active Python environment; the dependency is now installed
and declared through `notebooklm-py[headless]`. The exact account-specific
bootstrap path then captured Pro, Free1, and Free2 without copying cookies. The
Free2 capture used the already-authenticated
`storage_state_brsthomson.json.browser_profile` root after the newly-created
`notebooklm-free-2` root proved blank. All three exact-profile probes and the
final keepalive now pass. The user-facing Chrome tab is not an interchangeable
auth boundary, and no further sign-in is required while the durable master
tokens remain healthy.

The sharded runner now exposes `--cohort-shape` explicitly. Historical runs
retain the `captioned` default. The conditional live smoke should point at the
existing frozen captioned cohort under
`candidate6_telemetry_validation_run02_current/cohort.json`, use
`--cohort-shape captioned`, and set the inner `YTIS_NLM_BATCH_SIZE=1`; this
keeps the input deterministic without regenerating metadata.

### Historical auth incident (superseded)

The immediate canonical Pro preflight on 2026-08-06 failed closed with
`Authentication expired or invalid` and a redirect to Google authentication.
The subsequent operator bootstrap was initially confused by the Gemini
Notebook host rebrand and an older `notebooklm-py` runtime. After upgrading to
`notebooklm-py 0.8.0` and signing into each exact account, the three probes and
the all-account keepalive passed as described above. Do not treat the old
failure text as a current blocker or revive the deprecated `ytis-*` profiles.

The earlier CLI-auth failure is historical, not a current launch instruction:
it produced no output roots and no NotebookLM source work. The statement in
that historical receipt that auth readiness was no longer the blocker applies
only to the 2026-08-07 state and is superseded by the current auth boundary
above. The only permitted live validation remains the parent-owned minimal
smoke after an immediate successful probe, with no throughput or VPH conclusion
until its decision packet gates pass.

Post-merge verification on `main` (2026-08-05): the focused batch-status
selection tests passed `14` with `34` deselected; the import, manifest,
reconciliation, playlist, and `csf-source` test set passed `41`; compilation,
`git diff --check`, and the three CLI help checks all passed. No live or
write-producing workflow was run.

## Routing split (still active)

By default, `no_captions` and unknown-caption items go to NotebookLM, while
live/streamed/premiere items go to `transcript_fallback`. The explicit
`--route-no-captions-to-fallback` option changes only the no-caption partition.
The separate `--route-industrial-failures-to-fallback` option handles only
DB-confirmed failures returned by an industrial worker: it requeues exact IDs
once and sends them to fallback without replaying NotebookLM. Both options are
opt-in and require their own canary and throughput decision packet.

The fallback tail reaches Whisper for `yt-dlp = ok` videos with no captions.
Audio download includes `--js-runtimes node` when `node` is available, which
solves the YouTube `n` challenge on the fallback path. Successful fallback
transcripts are cached in the primary DB.

## Backup commands (before risky operations)

Before any risky sweep or cleanup:
```bash
python P:/packages/yt-is/bin/csf-backup-transcripts    # snapshots transcripts.sqlite
python P:/packages/yt-is/bin/csf-backup-channel-state  # snapshots batch_status.sqlite
```

Staging DB pattern (for long runs before promotion):
```bash
# Set env to staging DB, run, then promote
YTIS_TRANSCRIPT_CACHE_DB_PATH=P:/.data/yt-is/transcripts-staging.sqlite
python P:/packages/yt-is/bin/csf-promote-transcripts   # blocking, fail-closed

YTIS_BATCH_STATUS_DB_PATH=P:/.data/yt-is/batch-status-staging.sqlite
python P:/packages/yt-is/bin/csf-promote-channel-state  # blocking, fail-closed
```

Legacy URL→channel_id backfill:
```bash
python P:/packages/yt-is/bin/csf-migrate-channel-ids
```

## Debugging / logging rules

- **Read [DEBUGGING_PLAYBOOK.md](P:/packages/yt-is/DEBUGGING_PLAYBOOK.md) first.**
- Do not trust the JSONL trace alone. Several important warnings surface only in live stderr/stdout.
- When threading a new field through a wrapper, verify the callee signature before assuming it works.
- Treat the worker result file as the source of truth for completed work. Stdout summaries can be stale.
- For throughput questions, prefer completed-worker totals and stage timings over scan-progress rates.
- `YTIS_SCAN_STATUS_INTERVAL_S` controls heartbeat cadence for `yt-is sync` and fetch scans.
- Most useful live signals: `fetch_worker_finished`, `worker_completed`, `worker_batch_metrics`,
  `worker_source_profile_totals`, `negative_cache_reason_counts`, `add_cmd_elapsed_s` vs
  `materialization_wait_elapsed_s`.
- `active_workers: 0` in transcript-fallback logs is expected; that lane is not the industrial
  NotebookLM worker pool.

## Throughput investigation (dormant — read before resuming)

The throughput benchmarking investigation is **dormant, not resolved**. Before
resuming any throughput work:

1. Do not launch another same-shape benchmark. A code/harness change + fresh
   decision packet is required first.
2. The current leader (`3788.53` combined hot-path VPH) is NOT proven optimal
   (smoke promotion gate failed). Do not cite it as a proven ceiling.
3. Read these before any throughput decision:
   - [Throughput Optimization LLM Contract](P:/packages/yt-is/docs/operations/throughput-optimization-llm-contract.md)
   - [Throughput Decision Packet Template](P:/packages/yt-is/docs/operations/templates/throughput-decision-packet.md)
4. Leading hypothesis: batch-1 old-window `nlm source content` latency, with
   retry-heavy rows and Free batch_01/batch_02 as the main hotspots.

## Session bootstrap checklist

Read these before starting any yt-is work:

- [HANDOFF.md](P:/packages/yt-is/HANDOFF.md) (this file)
- [CODEX_MEMORY.md](P:/packages/yt-is/CODEX_MEMORY.md)
- [DEBUGGING_PLAYBOOK.md](P:/packages/yt-is/DEBUGGING_PLAYBOOK.md)
- [NLM Auth Architecture](P:/packages/yt-is/docs/operations/nlm-auth-architecture.md)
- The active integration handoff (see "Active work stream" above)

Fast verification:
```bash
python -m py_compile P:/packages/yt-is/bin/csf-source P:/packages/yt-is/csf/transcript.py P:/packages/yt-is/csf/batch_status.py
PYTHONPATH=P:/packages/yt-is python -m pytest P:/packages/yt-is/tests/test_transcript.py P:/packages/yt-is/tests/test_csf_source_fetch_timing.py -q
```

## Cross-package data source: wiki transcripts

`P:/.data/wiki/sources/transcripts/` contains full verbatim YouTube transcripts
exported by the wiki-yt skill via `nlm source content`. Format: one `.md` file
per source, named `<source_id>.md` (NotebookLM UUID). Each file has frontmatter
(`source_id`, `title`, `notebook_id`, `url`, `type`, `exported`) followed by
the complete transcript text.

YouTube source `url` is `null` (NotebookLM doesn't expose it); title-based
matching via `title_bridge.py` closes the provenance gap.

Scale: ~5,070 YouTube transcripts. Integration with yt-is is in progress (see
active work stream above).

## Current operational multi-account fetch path (2026-08-09)

The earlier wiki-yt existing-notebook queue was the wrong path for yt-is mass
ingestion. It was stopped after exporting 328 transcripts from 11 Pro notebooks;
those exports are retained as separate artifacts, but they are not evidence that
the yt-is downloader completed work. The two Free accounts having zero existing
notebooks was incorrectly treated as "no work". That is not the production
lifecycle.

The canonical coordinator is now
`P:/packages/yt-is/scripts/run_multi_account_fetch.py`. It snapshots a bounded
pending scope from `P:/.data/yt-is/batch_status.sqlite`, partitions exact video
IDs into per-account manifests, preflights `a.hominidae`, `troup.hominidae`,
and `brsthomson`, then invokes the existing `bin/csf-source fetch` per account.
Each child receives an exact account identity, account-scoped worker state root,
and descriptive prefix. The existing fetcher creates worker notebooks on demand,
reuses them within a run, and deletes them during worker shutdown. A zero
notebook inventory is therefore a normal starting state, not a skip condition.
The coordinator resolves the selected batch database once and passes that exact
path to every child through `YTIS_BATCH_STATUS_DB_PATH`; this is required for
staging databases and must remain part of the launch contract. It also holds a
database-scoped interprocess lock from pending-row selection through final
database reconciliation, so overlapping coordinators cannot select and mutate
the same rows concurrently. Use `--lock-timeout-s` only when waiting is
intentional; the default is fail-fast with a structured `blocked` summary.

For planning a large scope, use `--plan-only`, not `--dry-run`. Plan-only
writes and reload-validates each account manifest and an atomic selection
receipt, rechecks that every selected row is still `pending`, and launches no
child or auth preflight. The authoritative full-backlog plan completed on
2026-08-09 at
`P:/.logs/multi_account_fetch/20260809_full_backlog_plan02_current/`:
`337,033` pending rows, partitioned across `a.hominidae` (`112,345`),
`troup.hominidae` (`112,344`), and `brsthomson` (`112,344`), with zero missing
rows, zero non-pending rows, and zero duplicate IDs. Its summary is
`multi_account_fetch_summary.json` and its status is `planned`, not completed.
The earlier `full_backlog_dry_run01_current` attempt is retained as a timeout
diagnostic; its old dry-run mode launched children and is not the planning
path. A plan receipt authorizes neither authentication nor live processing.

The coordinator now exposes the already-implemented bounded adaptive scheduler
as an explicit opt-in. The existing `--workers-per-account` command remains
fixed-worker mode. To allow each account child to scale from the initial worker
count up to a per-account ceiling, pass `--adaptive-workers` and an explicit
`--adaptive-max-workers`; the coordinator forwards min/max, backlog, cooldown,
and health-window settings to `bin/csf-source`, and records the policy in
`multi_account_fetch_summary.json`. Adaptive identity capacity is created per
account using the same descriptive profile, notebook, and state-root naming
contract. This is an operational wiring and offline-correctness improvement,
not evidence that adaptive scaling improves sustained VPH; a live comparison
still requires its own parent-owned decision packet.

Example opt-in command (not launched here):

```powershell
python P:/packages/yt-is/scripts/run_multi_account_fetch.py `
  --limit 150 `
  --workers-per-account 3 `
  --adaptive-workers `
  --adaptive-max-workers 5 `
  --parallel-accounts `
  --all-uncategorized `
  --output-root P:/packages/yt-is/.logs/multi_account_fetch/adaptive_canary_run01
```

The bounded industrial canary was:

```powershell
python P:/packages/yt-is/scripts/run_multi_account_fetch.py `
  --limit 150 `
  --workers-per-account 3 `
  --parallel-accounts `
  --all-uncategorized `
  --output-root P:/packages/yt-is/.logs/multi_account_fetch/20260808_industrial_canary_run01
```

The three exact account probes passed immediately before launch. All three
children exited cleanly and each created an account-owned worker notebook; the
worker shutdown logs show `delete=true`, a notebook delete attempt, and state
clearance for `a.hominidae-worker-01`, `troup.hominidae-worker-01`, and
`brsthomson-worker-01`. The later global cleanup count of zero means the worker
shutdown had already removed them; it does not mean no notebook was created.

The canary result is **partial**, not complete: 109/150 selected rows are now
`complete` and 41 remain `pending`. The pending outcomes are attributable to
NotebookLM `ADD_SOURCE`/`source_add_failed` and
`nlm_content_below_threshold` events; no legacy login events appeared. The
coordinator now reads the database after children exit and reports
`completed`, `partial`, `failed`, `blocked`, `planned`, or `no_work` instead of
trusting a zero child exit code. A `blocked` summary records whether the lock or
account preflight stopped the run and proves that no child was launched. Do not
launch the full backlog until the retained canary
manifests and failure mix have a retry policy; the next safe operation is a
bounded retry of the 41 exact pending IDs through the same coordinator.

The authoritative canary summary is
`P:/packages/yt-is/.logs/multi_account_fetch/20260808_industrial_canary_run01/multi_account_fetch_summary.json`.
The earlier wiki-yt queue artifacts remain at `P:/.logs/wiki-yt-queue/20260808/`
and should not be used as the yt-is fetch control plane.

The exact retry is now prepared offline under
`P:/packages/yt-is/.logs/multi_account_fetch/20260809_retry_41/`. Read its
`decision_packet.md` before any live action. It contains the 12/9/20
per-account manifests, exact commands, abort gates, falsifiers, and the rule
that only all-41 database completion promotes the retry. The three current
token-only account probes and three dry-run coordinator validations passed on
2026-08-09; the live retry itself remains `ready_for_parent_decision` and was
not launched in this preparation pass.

## Historical residual fallback canaries (pre-final residual audit)

The canary narratives below are retained as evidence, not as the current
residual scope. The packet set in force during these historical canaries was
`P:/.logs/multi_account_fetch/20260811_residual_retry_packet_set_post_quality_reconciliation/`;
the source-addressability class has since been reconciled and the packet set
now contains only the six classes recorded in its `residual_retry_packet_set.json`.

The historical residual audit and exact retry packet set cited by these
canaries are under
`P:/.logs/multi_account_fetch/20260811_residual_retry_packet_set_post_source_addressability_fallback_run02/`.
The command-class packet was tested against one exact ID without touching the
canonical databases. Run02 used a `120s` fallback deadline and is
configuration-limited: earlier fallback stages consumed the budget and Whisper
received `0.1s`, so it is not evidence of a provider or auth failure. Its
decision packet is
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run02/decision_packet.md`.

Run03 repeated the exact ID in fresh SQLite staging with the normal `900s`
fallback deadline. It completed through `whisper` in `257.065s`, produced a
non-empty `1125`-character cached transcript, reconciled the staged row to
`complete`, passed both SQLite integrity checks, left no owned process, and
recorded no NotebookLM source/add/materialization/content action. The packet
is
`P:/.logs/multi_account_fetch/20260811_command_residual_current_canary_run03/decision_packet.md`.
This proves one bounded fallback recovery, not a default-route or
full-backlog promotion; semantic quality was not independently scored and the
run was explicitly `--fallback-only`.

### Current source-add fallback-only canaries

The current five-row `source_add` residual class was tested through two
disjoint exact manifests in isolated staging:

- Run01 recovered `0Zeu2X-5280` in `577.9s` through Whisper with a non-empty
  `23,765`-character cache row.
- Run02 recovered `CqPs0oCci0Y` (`7,954` chars) and `S7F6tYAd60Q` (`664` chars);
  `w9cxJdazkEs` ended `no_transcript`, and `yLSnkG9yLbA` exhausted the
  `900s` fallback deadline without output.

Both runs passed immediate token-only auth for `a.hominidae`,
`troup.hominidae`, and `brsthomson`; raw-event scans found no source-add,
materialization, or source-content action; staged integrity and cleanup passed;
and the canonical DB/cache remained unchanged. The combined result is
`3/5` bounded recoveries with an expensive tail. Keep fallback opt-in and do
not replay RPC9 or treat these canaries as throughput/full-backlog evidence.
Receipts:
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run01/result_receipt.md`
and
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run02/result_receipt.md`.

### Current source-addressability fallback-only canary

The first exact row from the current six-row `source_addressability` class was
tested at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run01/`.
`QvxHBtYsDig` recovered in `19.598s` through the explicit fallback-only route;
staged status is `complete`, `last_stage=selenium`, with a non-empty `33`
character / `5` word cache row. The raw-event scan found no source-add,
materialization, or source-content action; all three immediate token-only auth
probes passed; staged integrity and cleanup passed; and canonical DB/cache
hashes were preserved. This is one bounded route success with a material
quality caveat because the output barely exceeds the existing minimum. It does
not promote fallback, authorize the remaining five IDs, or establish semantic
quality, throughput, or full-backlog readiness.
Receipt:
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run01/result_receipt.md`.

## 2026-08-15 whole-package review + critical fixes (agent: zcode)

Whole-package review from testing worktree `P:/.worktrees/yt-is-testing-review-20260815`
(branch `test/ytis-review-20260815`). Four commits landed on this branch:

- `0fe3dba` security: untracked `.browser/` (15,095 files incl. Network/Cookies,
  Login Data, Trust Tokens) from the index — the profile was pushed to
  `origin/main`. Working tree untouched. Operator runbook for session rotation
  and optional history scrub:
  `docs/operations/browser-credential-exposure-remediation-2026-08-15.md`.
  **Session rotation is still required; history still contains the blobs.**
- `fa8d7d3` checkpoint: preserved the previously uncommitted operational layer
  (10 csf modules, `bin/csf-nlm-auth`, 17 coordinator/supervisor scripts,
  24 test modules, requirements with notebooklm-py/playwright).
- `dd0be86` fix: coordinator now exits 0 for a terminalized partial
  (all selected rows terminal, no process failure, new `process_failure`
  summary field). Previously the supervisor's continue-on-terminalized-failure
  gate was unreachable and every such chunk stopped the supervisor.
- `9c65cbe` fix: `_pid_is_alive` uses `psutil.pid_exists` — on Windows
  `os.kill(pid, 0)` delivers CTRL_C_EVENT and returns True for dead PIDs,
  so orphaned/lease-expired health branches were unreachable and a check
  could interrupt a live coordinator.
- `2a57839` fix: `DurableFallbackQueue` write paths roll back on error;
  a mid-claim exception previously left an open transaction on the shared
  connection that the next commit applied as a partial claim.

Also: `dev/`, `skills/`, `analyses/` were found deleted from the working tree
by outside activity mid-session and restored from HEAD. Not yet addressed
(review backlog): main's committed suite is stale/red; medium data-layer
defects (unbounded IN() in `_get_status_batch`/`delete_cached_transcripts`,
f-string ATTACH in `batch_status.py`, busy_timeout-after-WAL, per-write
wal_checkpoint, csf_logging listener death on I/O error, supervisor unbounded
`communicate()` after timeout kill); docs drift (oEmbed documented but
default-off, README chain stale); dead code (bin/csf-ingest broken import,
off-chain fetchers); repo bloat (.logs 3,119 files, bin/node_modules 4,048,
root test-output litter).

<!-- BEGIN worktree-status (auto-generated; do not edit) -->
All worktrees relative to `main`. Generated by `handoff_sync.sync`.

| Path | Branch | Behind main |
|------|--------|----------------:|
| `P:/packages/yt-is` | `main` | 0 |

<!-- END worktree-status -->

<!-- BEGIN security-incident-20260815 (agent: zcode; do not edit block marker) -->
## Security incident 2026-08-15: browser-profile leak — remediation status

**Incident:** live Chromium profile (`.browser/notebooklm/`, 15,095 files incl.
`Network/Cookies`, `Login Data`, `Trust Tokens`) committed 2026-04-23
(`c720775`/`8bbf096`, committer "Claude Sonnet 4.6") and pushed to
github.com/EndUser123/yt-is. Root cause: `.browser/` gitignore rule landed 72
min AFTER the commit (and gitignore is inert on tracked files anyway); the
gitleaks pre-commit hook didn't exist until 2026-07-17; regex scanning cannot
see binary SQLite secrets regardless. `csf/yt_is_data.db` leaked via extension
mismatch (policy said `*.sqlite`, artifact was `.db`).

**Done (this session, all on `main`, pushed):**
- `2eeb910` — untracked `.browser/` + `csf/yt_is_data.db`; added `*.db`
  gitignore rule. Verified: `git ls-tree -r main --name-only .browser | wc -l`
  → 0 on local and `origin/main`.
- `477e77a` — pre-commit hook hardened with SECTION 0 fail-closed path deny
  list (`.browser/`, `*.db`, Chromium credential filenames, `nlm-auth/`;
  deletions allowed so untracking stays possible). Tracked copy at
  `scripts/git-hooks/pre-commit` + CONTRIBUTING install step. Verified by
  probes: force-added `.db` blocked; deletion-only commit passes.
- `f1655bd` — `docs/security/credential-rotation-runbook-20260815.md`
  (operator steps: enumerate NLM accounts, sign-out-all, re-auth via
  `bin/csf-nlm-auth`) + `docs/security/tracked-but-ignored-report-20260815.md`
  (1,553 files; 687 are curated sharded-lane evidence, ~860 untrack
  candidates; report only).

**Open (gated on operator):**
1. **Credential rotation** — Google identity `troup.hominidae` ("Troop")
   ROTATED by operator 2026-08-15; profile cookies touch Google hosts only,
   so session-cookie exposure is covered. Residual: Login Data holds 150
   saved logins (plaintext usernames + origins; passwords DPAPI-encrypted).
   Recommended: rotate router admin (`AdminBruce` @ 192.168.0.1),
   `brsthomson@hotmail.com` (DocuSign/Firefox/wrobot — reuse signal),
   `account.alberta.ca`, and the torrent-site passwords at operator
   discretion. Also delete plaintext profile copies still on disk in
   `P:/.worktrees/yt-is-overnight` and
   `P:/.worktrees/yt-is-autonomous-overnight-20260808` (live checkout copy
   already removed).
2. **History scrub — `main` DONE (2026-08-15 07:35, zcode); checkpoint refs
   REMAIN.** `git filter-repo --invert-paths --path .browser --path
   csf/yt_is_data.db` run in a fresh mirror clone; rewritten `main`
   force-pushed to origin (`f1655bd` → `a2d7161`). Verified: 502 commits
   before and after; zero commits touch the scrubbed paths in any rewritten
   ref; new tip tree hash `58dc979` identical to old tip — current content
   unchanged. Full pre-scrub backup (all refs, restorable):
   `P:/tmp/yt-is-pre-scrub-backup-20260815.bundle` — keep until satisfied,
   then delete manually. **Old→new hash map for the 2026-08-15 remediation
   commits: `2eeb910`→`72f7ee8`, `477e77a`→`6f39667`, `f1655bd`→`a2d7161`;
   all pre-2026-04-23 hashes unchanged, everything between is rewritten.**
   **Tag gap found and fixed 2026-08-15 (zcode, operator-challenged "did
   the scrub take?"):** four `refs/tags/backup/*-2026-07-18` tags on origin
   descended from the leak and kept the blobs reachable after the main
   push (initial ref enumeration used `git branch -r`, which hides tags —
   enumerate with `git ls-remote`). Rewritten tag versions force-pushed
   (`f6fab82→7454d78`, `2a3077f→b4f08c0`, `65d2df7→6a7bace`,
   `67dae94→8042669`), verified by cross-repo diff: only the 15,096
   scrubbed paths differ, 0 outside. Local tags force-synced. Cold fresh
   clone confirms: main 0, all four tags 0, checkpoint branch 2 (the
   known deferral — the ONLY remaining remote exposure; API 200 on
   c720775 is reachability via that branch, not just cache).
   REMAINING (pass 2, after checkpoint session completes and branch
   reconciles): rewrite `codex/yt-is-overnight-checkpoint` (local tip +
   `origin/…` @ 7c5caae shares the blobs) by re-filtering the THEN-CURRENT
   state (the stale rewrite sitting in `P:/tmp/yt-is-scrub-mirror` does NOT
   include this branch's recent commits — do not reuse it); force-push;
   reset worktrees; `git gc` the live repo to purge local old objects;
   rewrite/delete the LOCAL-ONLY leak-carrying tags first found 2026-08-15:
   `backup/codex-yt-is-autonomous-overnight-20260808-20260815-072958`,
   `backup/codex-yt-is-overnight-20260815-072958` (both c98a17b),
   `pre-delete-batch_size_series-20260507_050347` (9f4662d),
   `pre-delete-live-probe-allow-20260719_163614` (ad5f096) — the other
   five `pre-delete-*` tags predate the leak and are clean; then optionally
   ask GitHub Support to clear cached pre-scrub commits/forks (re-probe
   `api.github.com/.../commits/c720775` after pass 2 — 410/404 means done).
3. **`nlm-auth.lock`** — tracked (`f5d398b`), NOT ignored; confirm intended
   before any `rm --cached`.
4. **~860 tracked-but-ignored files** — cleanup commit after confirming
   sharded-lane curation intent (see report).

Note: the checkpoint branch also carries `0fe3dba` (an earlier untrack of the
same files, never pushed) — expect a trivial .gitignore conflict when the
branches reconcile.
<!-- END security-incident-20260815 -->

## Security incident 2026-08-15: resolution (agent: zcode, checkpoint branch)

Follow-up to the block above, which was staged before the operator acted:

- **Open item 1 (credential rotation) is DONE and verified.** Operator
  signed out all Google-side sessions 2026-08-15 and completed the one-time
  `troup.hominidae` bootstrap after its master token was rejected.
  Final `python -m csf.nlm_keepalive`: all three identities pass token-only
  repair/probe, exit 0, backups pushed (07:21). Every session in use was
  minted post-sign-out. Full triage and resolution record:
  `docs/operations/browser-credential-exposure-remediation-2026-08-15.md`
  (this branch).
- The checkpoint branch also carries the review/fix session: preserved
  operational layer (`fa8d7d3`), H1/H2/H3 defect fixes
  (`dd0be86`/`9c65cbe`/`2a57839`), all with regression tests.
- The staged `.review_findings_adversarial_performance.json` (two P1s on
  `run_throughput_pair.py`: abort does not prove the process tree is dead;
  source-add abort markers not scoped to run/account) is committed as
  evidence — those findings are still open and unfixed.
- `csf/yt_is_data.db` re-staged here by accident was unstaged; `*.db`
  ignore rule mirrored from main's `2eeb910` to stop recurrence.
- Still open: history scrub (item 2 above; branch must reconcile first),
  the ~860 tracked-but-ignored cleanup (item 4), and pushing this branch.


<!-- BEGIN scrub-pass2-instruction (agent: zcode, 2026-08-15 ~10:15; addressed to the checkpoint-branch session) -->
## TO THE CHECKPOINT-BRANCH SESSION: end-of-session reconciliation (2 steps)

Your branch descends from pre-scrub history: `main` was history-rewritten
2026-08-15 (`f1655bd`→`a2d7161`, leaked `.browser/` + `csf/yt_is_data.db`
removed from all commits; all four `backup/*` remote tags rewritten too).
Your local commits are fine — they never touch the leaked paths — but if
you push this branch as-is, GitHub re-exposes the leaked blobs via your
history. Do this at your natural end instead:

1. **Squash-merge onto the rewritten main, then push main.** From a temp
   worktree under `P:/.worktrees/` (NOT this checkout):
   `git worktree add P:/.worktrees/<name> main` → in it:
   `git merge --squash codex/yt-is-overnight-checkpoint` → resolve the
   trivial `.gitignore` conflict if any (keep both rules) → commit with a
   message crediting your branch's work → `git push origin main`.
   Squash brings your diff without your history, so main stays blob-free.
2. **Delete the remote checkpoint ref** (only after step 1 is pushed):
   `git push origin --delete codex/yt-is-overnight-checkpoint`.
   Do NOT push this branch afterwards — any push from un-rewritten history
   resurrects the leaked blobs on GitHub.

Leave the local branch/tags/`gc` alone — the zcode session (hourly gate
automation active) handles the tail cleanup: 4 leak-carrying local tags,
reflog expire + gc, GitHub API re-probe. If you cannot do step 1, just say
so in this handoff and stop committing; the gate will detect the quiet
state and the operator will direct the fallback.

Context: security-incident-20260815 block above; wiki concept
`verify-history-rewrite-by-tree-hash-equality`.
<!-- END scrub-pass2-instruction -->

<!-- BEGIN security-incident-20260815-cutover (agent: zcode, 2026-08-16 ~13:00) -->
## Incident CLOSED at the git level — cutover execution record

Operator-directed cutover 2026-08-16 ~12:20-13:00 (fix session stopped,
"commit everything first"):

- `5a3c32a` WIP preservation on the checkpoint branch (session working
  state). NOT committed, preserved on disk only: `.logs/multi_account_fetch/`
  (fetch receipts contain token-shaped auth material — gitleaks-blocked,
  34 findings/6 files; now gitignored) and playwright npm installs
  (reproducible from committed package-lock).
- `753a0aa` squash-merge of the checkpoint branch onto scrubbed main
  (~120 commits of session work rescued). Squash keeps main's blob-free
  history. Conflicts: 15 both-modified + 61 add/add resolved to the
  checkpoint side (canonical dev line); CONTRIBUTING.md union; one silent
  auto-merge artifact fixed (csf/cache.py `_normalize_metadata` line —
  caught by tree-diff vs checkpoint tip; criss-cross casualty of
  rewritten-vs-original graph ancestry). Verified: squash tree == checkpoint
  tip + exactly 4 main-only files.
- Deleted: remote+local `codex/yt-is-overnight-checkpoint`, 4 leak-carrying
  local tags, `refs/original/*` backup refs (2 — leftover of an earlier
  rewrite attempt), test worktree/branch. Live checkout now on main
  @ 753a0aa. Two overnight worktrees had already been removed by the fix
  session itself (its on-disk profile copies died with them).
- Purged: reflog expire + gc — leak commits c720775/8bbf096/5a0303d all
  `cat-file -e` fail locally; `.git` shrank 875M → 17M.
- Cold-clone verification: ZERO commits touching `.browser` or
  `csf/yt_is_data.db` reachable from ANY remote ref (6 refs: main, HEAD,
  4 rewritten backup tags).
- REMAINING (hygiene only): GitHub API still serves c720775 (HTTP 200) as
  an unreachable cached object — file a GitHub Support request to GC it
  (template: remove exposed credentials from cached commits after history
  rewrite; include repo + SHA). Check for forks while at it.
- Old→new map for today's commits: 2eeb910→72f7ee8, 477e77a→6f39667,
  f1655bd→a2d7161 (pass-1 rewrite); 5a3c32a (checkpoint WIP, branch now
  deleted — content lives in 753a0aa).
- STILL OPEN from the incident list: `nlm-auth.lock` intent (tracked, not
  ignored); ~860 tracked-but-ignored cleanup; delete
  P:/tmp/yt-is-pre-scrub-backup-20260815.bundle (last copy of leaked
  material) + P:/tmp/yt-is-scrub-mirror when satisfied.
<!-- END security-incident-20260815-cutover -->

<!-- BEGIN incident-cleanup-20260816 (agent: zcode) -->
## Incident cleanup complete (2026-08-16 evening)

- Pre-scrub bundle + coldcheck clone deleted — NO local copy of the leaked
  material remains on this machine.
- Fork check: **0 forks** (repo is PUBLIC — support request still
  worthwhile to purge cached commit views of c720775).
- `114ecf3e`: untracked 1,554 tracked-but-ignored files (687 sharded-lane
  runtime exhaust, 39 batch_size_series, 3 .claude-state/tdd — one held an
  `hmac_secret`, now untracked — ~820 misc logs) + empty
  `.data/yt-is/locks/nlm-auth.lock` (new ignore rule for locks dir).
  `git ls-files -i -c --exclude-standard` → **0**. Checkout dirty entries
  now 12 (runtime receipts/playwright, deliberately on-disk-only).
- ONLY remaining incident item: operator files the GitHub Support request
  (cached c720775). Everything else closed.
<!-- END incident-cleanup-20260816 -->
