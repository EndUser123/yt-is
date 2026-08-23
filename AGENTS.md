# AGENTS.md

## Overview

`yt-is` (YouTube Intelligence System) is a transcript ingestion pipeline with
an account-scoped multi-worker path. Current backlog size and readiness must be
read from the authoritative `P:/.data/yt-is/batch_status.sqlite` receipt; do
not rely on the older 140,000-video estimate.

### Platform surface (2026-08-21 — supersedes nothing below; the drain contracts remain in force)

yt-is is now a multi-source intelligence platform, not only a transcript
pipeline. Every subsystem below is shipped and runtime-verified.

**Sources & connectors.** YouTube (NLM drain + Whisper fallback) plus
connectors: Reddit, Hacker News, RSS blogs (full-text via trafilatura),
Twitter/X (RSSHub routes, token TWITTER_AUTH_TOKEN in P:/.env), GitHub
(gh CLI, READMEs+releases), Discord (DHT archive ingest; bot API parked).
One registry drives them all: `csf/connectors.py`; `ytis sync` runs every
available connector and indexes what's new. Connector docs land in
transcript_cache with source tags and are embedded into the Evidence
Fabric by `ef/ingest_connectors.py` (watermark `connector_indexed_watermark`
in `P:/.data/yt-is/ef/state.json`; source aliases incl. rss/github/chs).

**Web service.** `python -m ef.warm_query_service` on 127.0.0.1:6391
serves: / (search; idle state with suggestions + topic momentum),
/home, /digest (daily brief + 7-day), /sources (add/remove feeds and
subreddits via POST), /review (channel enable/disable), /reddit, /discord,
/entities, /ask (NL Q&A), /status (health), /trends, /topics. Search
federates CHS conversation history (`_chs_search`). Pages are same-origin
by design — never link them as file:// (Chromium local-network rules).
Q&A provider chain: `ef/qa.py`, env-orderable via YTIS_QA_PROVIDERS
(default codex,agy,openrouter,gemini); CLIs need node dirs on PATH
(per-user install at ~/AppData/Local/Programs/nodejs).

**Agent service restarts (standard, 2026-08-22).** `ef_warm_query` and
`ef_qdrant` carry a scoped SDDL grant giving the user account exactly
start/stop/query rights (`(A;;SWRPWPRC;;;<user-sid>)` appended to the
service DACL), so agent shells on this machine may restart these two
services directly, no UAC. Any NEW NSSM/LocalSystem service installed
for this workspace must add the same grant in its install procedure —
see the wiki concept `windows-scoped-service-restart-delegation`.
Machine-wide operations (firewall rules, HKLM, other services) still
require an operator elevated terminal.

**Service-definition durability (2026-08-23).** The live WinSW install at
`P:\.data\winsw\` is invisible to Git (blanket `.data/*` ignore). Durable
copies of both service XMLs live at `deploy/winsw/`, and the idempotent
admin repair script (squatter kill + DACL re-grant + real-row verify) at
`scripts/ytws-warm-fix.ps1`. After any service reinstall or live-XML edit,
update the durable copy in the same change, and re-run the repair script
from an admin terminal (a WinSW reinstall wipes the DACL grant above).
General rule: anything needed to rebuild production — service definitions,
admin/repair scripts, install procedures — is committed to this repo at
creation time; `%TEMP%`, Downloads, and chat-session artifacts are not
durable homes.

**Automation (Task Scheduler, all windowless pythonw — no .cmd wrappers,
operator constraint: console windows steal focus).** 03:00 YtisDhtCapture
(DHT app + capture browser + ingest), 05:00 YtisIndexIncremental (paced
daemon, single-instance pid guard), 06:00 YtisContentSync (YouTube phase
runs in PARALLEL with all light connectors via run_script_threaded; then
EF ingest, topic assignment, metadata + title self-healers, digest).
Twitter routes are paced 75s apart with 900s backoff (per-token limits).

**Self-healers.** Title backfill (oEmbed + API fallback, terminal
unavailables marked), channel metadata backfill (50 IDs/call), topic
assignment (nearest-centroid, assigned_at = transcript capture time).

**Destructive ops.** `scripts/purge_channels.py` — dry-run by default,
receipts under `.logs/purge/`, execute only on explicit operator
instruction; never coupled to the review-page block toggle.

**DB conventions.** Shared SQLite stores have many concurrent writers:
every write path uses busy_timeout=30000 and a retry-on-locked wrapper
(see `_retry_locked` in any connector sync). Qdrant runs as a PID-owned
HTTP server (:6390 HTTP, :6392 gRPC since 2026-08-23 — gRPC on 6391
crash-looped ef_warm_query at bind after the loopback change; the warm
query service owns :6391 HTTP; EF server config is authoritative).

**Dependencies outside the repo.** RSSHub at `P:/tools/rsshub`
(launcher start.cmd; Node >=22.12 required, no experimental flag),
DHT desktop app `P:/tools/dht/` (external binary — stays in tools).
DHT capture automation lives IN this repo at `scripts/dht-capture/`
(playwright login profile + tracking-script.js + channels.txt; the
profile and tracking-script.js are gitignored — they hold credentials).
DHT archives are COLD ORIGINALS at `G:/backups/dht/` (canonical since
2026-08-22; discovery order G: → P:/.data/dht → P:/.data/yt-is/dht →
Documents → Downloads; unchanged archives fingerprint-skip, never prune).

### Runtime log-root contract

New direct, supervised, and throughput runs must write under the package-owned
`P:/packages/yt-is/.logs/` tree. Multi-account output belongs under
`P:/packages/yt-is/.logs/multi_account_fetch/`; benchmark evidence belongs under
`P:/packages/yt-is/.logs/sharded_lane_series/`. The older
`P:/.logs/multi_account_fetch/` tree is a legacy compatibility root for
historical receipts and existing supervisor state. Do not move or delete a
state-owned run root during migration; when a state file records an existing
output root, restart against that exact root. Use `python -m csf.cleanup_staging
--dry-run` to inspect both roots before any cleanup.
The shared `csf_logging` fallback also resolves to package-local `.logs` even
when a caller starts an absolute script from another working directory.
Account workers must emit `nlm_client_account_binding_checked` and fail closed
on a runtime email, `authuser` route, or storage-path mismatch; static auth
storage preflight alone is not runtime RPC-routing proof.

The fresh selector-loop health canary at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_selector_loop_health_canary_run01/`
passed for all three exact accounts with no `WinError 6` teardown warning.
Treat it as bounded runtime-health evidence only; it does not authorize a
throughput claim or full-backlog run. Its receipt and claim ledger are the
authoritative evidence for this mitigation.

The subsequent production-shaped health canary at
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_production_shape_health_canary_run01/`
selected 30 current pending IDs and completed `10/10` on each exact account.
It immediately verified the expected account emails, used Pro adaptive `3..5`
and fixed three-worker Free settings, parsed 348 raw events without errors,
and left no active yt-is process. Its receipt is bounded health and policy
wiring evidence only; it does not authorize sustained-VPH claims, failed-row
retry, or full-backlog execution.

### Current operational pointers (2026-08-12, reconciled after the source-add fallback-only canary and exact promotion)

The current package-local reconciliation supersedes the older pointers in this
section:
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/goal_completion_audit_after_source_add_canary.md`.
Its current residual audit is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/residual_audit_after_canary.json`;
the current packet set is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/retry_packet_set_after_canary/`;
and the current non-authorizing policy gate is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/residual_policy_after_canary/residual_policy_receipt.json`.
The canonical snapshot is `integrity_check=ok`, with
`complete=10,359`, `failed=253`, and `pending=332,507`. One exact
source-add fallback recovery was quality-gated and promoted; failed rows
remain excluded from automatic retry. The older paths below are preserved
historical baselines, not launch authority.

Read `HANDOFF.md` and
`docs/operations/unattended-backlog-operation.md` for the current state before
acting. The following older packet paths are retained as historical baseline
evidence only; use the current package-local paths in the override above.
The pre-canary database snapshot was `integrity_check=ok`,
`complete=10,358`, `failed=254`, `pending=332,507` after the bounded partial
unattended chunk; its historical residual audit was
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/residual_audit.json`.
The pre-canary exact residual packet set, rebuilt after the partial chunk and
the source-add pacing control abort, was
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/retry_packet_set/`.
The pre-canary residual-policy gate receipt was
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/policy_gate/residual_policy_receipt.json`.
It proves only that the supervisor may drain the current `pending` scope while
all `failed` rows remain explicitly deferred; it is not a recovery, quality,
throughput, or full-backlog authorization receipt. It was built by
`scripts/build_residual_policy_gate.py`, which fails closed on stale audit,
packet-scope drift, missing packets, or unclassified failures.
The package-local packet/gate snapshot is current for the database at
generation time. It authorizes only a pending-only drain policy and expires at
the timestamp recorded in the gate receipt; rebuild it after any DB change or
expiry. The older `P:/.logs/multi_account_fetch/20260812_*residual*` artifacts
remain historical evidence and must not be used as launch authority. The fresh
plan-only supervisor state is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/unattended_plan/state.json`.
`scripts/check_unattended_backlog.py` reports `health_status=planned` and no
issues for that state. It selected 400 current pending rows after the partial
chunk; it did not launch workers or mutate the database. The partial chunk
receipt is
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_production_canary_reconciliation_run01/unattended_plan/chunk-0002/result_receipt.md`.
The source-add event reducer and current completion-conditioned report are
`P:/packages/yt-is/scripts/analyze_source_add_rpc9.py` and
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_post_unattended_chunk_partial_run01/source_add_rpc9_analysis.md`.
They are offline association evidence only; they do not authorize replay,
fallback promotion, or a throughput run.
The current package-local reconciliation for the larger unattended-readiness
goal is the post-canary receipt named at the top of this section:
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run01/goal_completion_audit_after_source_add_canary.md`.
The older same-purpose receipts under
`P:/packages/yt-is/.logs/multi_account_fetch/20260812_goal_completion_audit_current.md`
and `P:/.logs/multi_account_fetch/` are historical compatibility evidence
only; do not use them as current launch authority.
The cross-run residual-attempt ledger is
`P:/.data/yt-is/unattended-backlog/residual-attempt-ledger.json`; it is seeded
from `65` exact applied requeue receipts covering `324` unique IDs. Future
requeue admission must use `scripts/requeue_exact_failed_manifest.py` with a
unique attempt ID, mechanism ID, falsifiable hypothesis, account scope, and
decision packet; same-mechanism overlap fails closed. This ledger records
retry admission, not successful transcript quality.
The coordinator's opt-in in-run industrial fallback queue is durable when a
coordinator-owned child supplies `YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED=1`
and a state-root `YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH`. It uses the
package-owned `csf.durable_fallback_queue` SQLite contract, reclaims prior
claims after the database-scoped run lock is held, and preserves the exact
source URL plus `skip_notebooklm`. A restart may recover queued or in-flight
fallback work, but it never sends that row back through NotebookLM.
Standalone `csf-source` remains memory-only unless those explicit variables are
present. Completed and terminal-failed queue rows are never resurrected;
cross-run residual retries still require the current exact packet set and
ledger-backed requeue command.
The bounded restart canary passed the failure-path recovery contract: one
in-flight claim was recovered after process termination, retried once, and
finalized consistently as a typed failure in both the queue and staged batch
DB. Its receipt is
`P:/.logs/multi_account_fetch/20260811_durable_fallback_queue_restart_canary_run01/result_receipt.md`.
This is not a fallback success-rate estimate, source-add fix, or full-backlog
authorization.
The canonical unattended plan is
`P:/.data/yt-is/unattended-backlog/state.json` and currently passes read-only
health as `planned` with `issues=[]`; this is not full-backlog authorization.
After the run10 exact promotion, the stale pre-promotion plan was preserved at
`P:/.data/yt-is/unattended-backlog/state-stale-after-run10.json` and a fresh
plan-only state was generated from the current databases under
`P:/.logs/multi_account_fetch/unattended-refresh-after-run10/` before being
installed at the canonical state path. Any exact canonical promotion or
status reconciliation can invalidate selected-row preconditions; archive the
old state, replan from the current authoritative databases, and rerun the
read-only health check before launching live work. Never overwrite a stale
state in place.
The latest bounded source-add recovery is
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_canary_run10/result_receipt.md`.
It passed exact token-only preflight for all three identities and used
`--fallback-only` without NotebookLM mutation. One of three staged results met
the 500-character quality gate and was promoted exactly; the other two remain
excluded. This is not default fallback authorization, RPC9 repair, throughput
evidence, or full-backlog authorization.
The subsequent routing/provenance repair and live canaries are governed by
`P:/.logs/multi_account_fetch/20260812_source_add_fallback_routing_gap_fix_current.md`.
Routing was validated on `3/3` exact typed RPC9/materialization failures and a
fresh normal-deadline quality canary routed `1/1` and completed while preserving
the original reason, but its transcript was only `301` characters against the
`500`-character promotion gate. Keep the explicit route opt-in and default-off;
do not replay either cohort or direct-retry RPC9.
The two rows in the current `source_add` residual manifest are now explicitly
closed to another same-mechanism fallback attempt. `yLSnkG9yLbA` already
exhausted the bounded fallback deadline and `w9cxJdazkEs` already reached
terminal `no_transcript` in the prior exact canaries. The closure packet is
`P:/.logs/multi_account_fetch/20260812_source_add_residual_closure_after_prior_fallback.md`.
Do not reopen either ID, and do not direct-replay RPC9, unless a new mechanism
and a fresh reviewed packet exist.
The latest throughput attempt is recorded at
`P:/.logs/multi_account_fetch/20260812_batch50_any_throughput_pair_run09_plan/result_receipt.md`.
It is `invalidated_control_rpc9_adaptive_withheld`: immediate exact-account
token-only preflight passed, but pair-01 control produced `13` fresh typed
`ADD_SOURCE rpc_code=9` failures across all three accounts. The controls were
controlled-aborted, adaptive was withheld, and no VPH is valid. Do not
interpret the packet as an auth failure, worker/batch result, or full-backlog
authorization. Run08 remains historical bounded terminal-guard evidence.
The fresh same-cohort source-add mechanism attempt is recorded in the
run03 packet at
`C:/Users/brsth/AppData/Local/Temp/yt-is-source-add-pair-run03-plan/decision_packet.md`
and its durable offline diagnosis at
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_run02_run03_diagnosis.md`.
The exact-account token-only preflights passed, then the pair-01 control
produced a nonce-matched RPC9 event before the candidate ran. Treat it as
negative control mechanism evidence, not an auth or throughput result; do not
rerun the same shape or enable the initial-window setting by default.
The opt-in account-pacing mechanism was then tested from a fresh paired packet
at
`P:/.logs/multi_account_fetch/20260812_source_add_account_pacing_pair_run02/`.
The exact token-only preflight passed for all three accounts, but
`pair-01/control` hit a nonce-matched RPC9 for `a.hominidae` video
`ZHYqjD099Aw` at source position 14 after `42` successful adds. The runner
aborted and withheld the candidate and pair-02, so the pacing hypothesis is
unevaluated and no VPH is valid. Do not replay the cohort or enable pacing by
default. The gate implementation is still opt-in and locally verified by
`9` gate tests, `182` batch tests, and `47` throughput-pair tests; a gate
failure is terminal and cannot schedule a source-add retry or notebook reset.
The result receipt is
`P:/.logs/multi_account_fetch/20260812_source_add_account_pacing_pair_run02/result_receipt.md`.
That unevaluated paired packet was followed by a fresh candidate-only pacing
run at
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/`.
All `18/18` Pro source-add attempts acquired the account gate with
`gate_pacing_s=2.0`, but `a.hominidae` video `ZHYqjD099Aw` still produced
`SourceAddError` with `rpc_code=9` after the gate. The existing read-only
reconciliation observed the created source, then materialization ended in
terminal status `3`; the run finished `15/18` complete with two
`nlm_content_below_threshold` failures and one terminal materialization
failure. This falsifies account pacing as an RPC9 fix for this cohort. The
two Free launches were withheld after the Pro abort; their concurrent setup
attempts stopped at the shared staging-DB lock before any NotebookLM work.
Receipt:
`P:/.logs/multi_account_fetch/20260812_source_add_pacing_candidate_only_run02/result_receipt.md`.
Keep pacing disabled by default, do not replay this cohort, and do not treat
the result as VPH or full-backlog evidence. A future source-add branch needs
a narrower provider/identity mechanism and a new reviewed packet.
The follow-up offline join found `42` per-video typed RPC9 outcomes in the
same run; `25/25` videos from one local catalog channel failed across all three
accounts, while the other `17` failures covered `12` channels. The run also
used `18` distinct notebook IDs for `9` worker profiles, with no cross-profile
notebook reuse. Treat source/provider addressability as the leading hypothesis
and shared notebook ownership as lower priority, not as a proven cause. Do not
replay those IDs, fetch external metadata, direct-retry RPC9, or enable a
source filter without a fresh disjoint packet. Details:
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_distribution_after_run09.md`.
The source-add path now also emits per-attempt identity/timing events around
the existing typed mutation call (`nlm_batch_source_add_attempt_started` and
`nlm_batch_source_add_attempt_completed`). This is evidence-only telemetry,
covered by the `182`-test `test_nlm_batch.py` suite; it is not an RPC9 fix,
cross-process lock, or throughput result.
The fresh identity canary at
`C:/Users/brsth/AppData/Local/Temp/yt-is-source-add-identity-canary-run06/`
passed all three exact token-only auth preflights and completed pair-01
control, but its result was invalidated by a harness bug that compared
post-run mutable staging DBs to pre-run fingerprints. The runner fix now
skips only those mutable DB/cache hashes during post-run validation and stops
all later arms after any failed gate. See the durable diagnosis at
`P:/.logs/multi_account_fetch/20260812_source_add_rpc9_run02_run03_diagnosis.md`;
the run is not throughput evidence and must not be reused.
Its fresh replacement at
`C:/Users/brsth/AppData/Local/Temp/yt-is-source-add-identity-canary-run07/`
again passed all three exact token-only preflights and all observed source-add
attempts, with no RPC9. The control was partial only because two
`nlm_content_below_threshold` results occurred on `a.hominidae`; the corrected
executor withheld adaptive and pair-02. This is auth/source-add-path and
executor-safety evidence, not VPH evidence or full-backlog authorization.
The lower-level `scripts/prepare_throughput_pair.py` command is staging-only
and never launches workers; use `scripts/run_throughput_pair.py` to build the
executable packet and perform the separate explicit execution step. Executable
receipt validation now fails closed without raising on malformed packet
structure; the planner/coordinator boundary is covered by `47` focused tests.
The latest scheduler audit is
`P:/.logs/multi_account_fetch/scheduler_canary_audit_20260811.md`; the
installed Windows task is still only an interactive-token, plan-only canary.
An attempted separate S4U execute canary was blocked before task creation by
Windows `Register-ScheduledTask` access denied (`HRESULT 0x80070005`); see
`P:/.logs/multi_account_fetch/20260811_scheduler_s4u_execute_canary_run01/result_receipt.md`.
The 2026-08-12 registration-only recheck used a fresh task/state/output
identity and was blocked the same way before task creation; see
`P:/.logs/multi_account_fetch/20260812_scheduler_s4u_registration_recheck_run01/result_receipt.md`.
Do not reinterpret this OS permission boundary as NotebookLM authentication
failure or work around it with `SYSTEM`, shared cookies, or legacy login.
The separate application execute/restart/resume proof is
`P:/.logs/multi_account_fetch/20260810_scheduler_restart_resume_canary_run04/result_receipt.md`.
Never infer that either a plan-only task receipt or a bounded application
canary authorizes `--until-empty` or a throughput claim.
The consolidated gate ledger is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_reconciliation.md`;
the adversarial review is
`P:/.logs/multi_account_fetch/20260811_unattended_readiness_adversarial_review.md`.

### Latest source-ready gate validation run06 (2026-08-11)

Run06 supersedes run05 as the latest fresh source-readiness observation. Its
packet and result receipt are
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_scaleup_run06/decision_packet.md`
and
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_scaleup_run06/result_receipt.md`.
The immediate exact-account token-only preflight passed for all three profiles.
Across 108 selected IDs, 107/107 successful waits had exact expected/ready IDs,
empty missing/not-ready sets, and status `2`; all 111 content-fetch starts
followed a successful exact wait, including four retry-pass starts. One Pro
source remained present with status `3` and timed out after `606.235s`; the
exact video was quarantined without extraction. Both controls were partial
(`50/54` and `53/54`), so both adaptive arms were withheld and VPH is invalid.
No adaptive transition was observed. No RPC9, `SourceNotFound`,
`source_add_failed`, or `ADD_SOURCE` marker was found; the single source-add
recovery event is not proof of a provider fix. Do not rerun this cohort or
interpret it as a throughput, worker-setting, full-backlog, or optimality
result.

The older source-add RPC9 reconciliation artifact at
`P:/.logs/multi_account_fetch/source_add_rpc9_reconciliation_canary_20260811_run03/`
is historical and lacks a runtime source fingerprint. Its one-attempt
`not_retryable` result must not be interpreted as a current retry-policy or
authentication failure. The later presence-aware canary recorded four
attempts and `attempts_exhausted`; the current regression test preserves that
bounded behavior. See
`P:/.logs/multi_account_fetch/source_add_content_not_found_reconciliation_20260812.md`.

### Terminal source-status fail-fast guard (2026-08-11)

The run06 status-`3` observation exposed a production wait-path defect: the
installed `notebooklm` runtime defines `SourceStatus.ERROR = 3`, but the old
readiness loop treated every non-ready status as indefinitely transient and
could poll for the full 600-second timeout. `csf/nlm_batch.py` now detects
status `3` (including string and nested encodings), records
`source_materialization_terminal_error`, raises the distinct
`NotebookSourceMaterializationTerminalError`, quarantines only the affected
sub-batch, and continues later sub-batches. Status `1`/`5` still poll, and
the sharded runner invalidates terminal materialization failures as source
evidence rather than throughput evidence. This is a reliability/latency
guard, not a measured VPH improvement or full-backlog authorization.

The isolated live canary at
`P:/.logs/multi_account_fetch/terminal_source_status_guard_canary_20260811/`
confirmed the production artifact. Exact-account token-only preflight passed;
the known status-`3` source stopped after one poll in `0.426s`, was quarantined
without a content-fetch event, and the next selected source completed. Staging
integrity, cleanup, and process termination passed; canonical DB/cache
fingerprints remained unchanged. Receipt:
`P:/.logs/multi_account_fetch/terminal_source_status_guard_canary_20260811/result_receipt.md`.
Because the canary used `batch_size=1`, it proves runner-level continuation,
not a later sub-batch inside one same-call add operation. RPC9 was observed and
remains a separate open source-add issue. This canary authorizes neither VPH
interpretation, worker-setting changes, nor full-backlog execution.
Focused and full package verification: `176` `tests/test_nlm_batch.py` tests,
`44` `tests/test_sharded_lane_series.py` tests, compilation, and diff hygiene
passed. Do not claim the live 600-second path is revalidated until a fresh
bounded canary observes the changed code.

### Latest source-ready gate canary (2026-08-11)

The authoritative run05 packet is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_canary_run05/`.
Immediate token-only preflight passed for `a.hominidae`, `troup.hominidae`,
and `brsthomson`. Pair01 control/adaptive completed `54/54`; pair02 control
completed `53/54` after one exact source remained present but not READY for
`604.553s`, and pair02 adaptive was correctly withheld. Across the `161`
successful waits, exact expected/ready IDs and `status=2` evidence were present
on every success, and every matching content-fetch start followed its wait.
The timeout was classified as `Source materialization timeout` for
`9WfjJl2JGoE`; its continuation event reported `halted=false` and quarantined
the row. This is partial mechanism evidence, not a valid VPH comparison,
production worker-setting authorization, full-backlog authorization, or an
authentication result. The result receipt is
`P:/.logs/multi_account_fetch/throughput_pair_20260811_source_ready_gate_canary_run05/result_receipt.md`.
Do not rerun the same cohort. A future live branch must name a new mechanism
and packet; if it needs live continuation-after-timeout evidence, place the
timeout before at least one later selected sub-batch.
Read both before proposing a new live arm or changing scheduler policy.

Fallback completion and semantic quality are separate facts. The coordinator
may reconcile a non-empty fallback transcript as `complete`, but every current
fallback completion now records `transcript_chars`, `transcript_words`, and an
explicit length band in cache metadata and `analysis_status.quality_metrics`.
Treat a short band as a quality-review obligation, not proof of a bad source or
permission to retry blindly; the 16-character `Jingle Bells` canary result is
the motivating warning and is not a general quality-rate estimate.

The current post-gate `source_add` residual class has now had two isolated,
NotebookLM-free fallback-only canaries:
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run01/` and
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run02/`.
Across the exact five-row class, three staged rows recovered with non-empty
Whisper cache output and two ended as explicit `no_transcript` or fallback
deadline-exhausted failures. Immediate token-only auth passed for all three
identities, raw events showed no source-add/materialization/content mutation,
and canonical DB/cache hashes were preserved. This is partial bounded recovery
evidence with a costly tail, not default-route promotion, direct RPC9 retry
authorization, throughput evidence, or full-backlog readiness.

The three source-add fallback outputs that met the explicit `500`-character
quality gate were subsequently reconciled into canonical state by the exact
promotion utility. The apply receipts are
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run01_apply_receipt.json`
and
`P:/.logs/multi_account_fetch/20260811_fallback_promotion_source_add_successes_20260811/run02_apply_receipt.json`.
The earlier source-add logical postcondition was `complete=9,677`, `failed=201`, and
`pending=333,241`, with both SQLite integrity checks `ok`; the three promoted
IDs are absent from the new packet-required residual audit. File hashes are
not used as the logical-write proof because SQLite WAL checkpoints can leave a
database file hash unchanged after a committed row update.

The current `source_addressability` fallback-only evidence is recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run02/`.
One of two exact rows recovered through Whisper with `13,928` characters and
was promoted only through the exact dry-run/apply utility; the other remained a
typed `no_transcript` failure. All three immediate token-only auth probes
passed, raw events contained no source-add/materialization/content actions, and
staged integrity and cleanup passed. The canary is partial class evidence, not
default fallback authorization, throughput evidence, or full-backlog readiness.
The complete result receipt is
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run02/result_receipt.md`.

The latest content-threshold fallback canary is
`P:/.logs/multi_account_fetch/20260811_content_threshold_fallback_canary_run01/result_receipt.md`.
Three exact current residuals recovered through the existing fallback-only
route with `22`, `46`, and `8,815` characters, passed the existing
21-character quality gate, and were promoted through three exact dry-run/apply
receipts. Immediate token-only auth passed for all three identities; staging
integrity, receipt reconciliation, process cleanup, and the no-NotebookLM-
mutation scan passed. The residual class is reduced from 15 to 12, but this is
only a positive bounded sample: do not blanket-requeue the remaining 12,
change the default route, or infer full-backlog readiness.

The latest exact source-add fallback canary is
`P:/.logs/multi_account_fetch/20260811_source_add_fallback_canary_run03_after_content_run01/result_receipt.md`.
It re-tested the two remaining exact `Source add failed` rows through the
NotebookLM-free fallback route after immediate token-only auth passed for all
three canonical identities. `w9cxJdazkEs` ended `no_transcript`; `yLSnkG9yLbA`
exhausted the bounded Whisper fallback and ended deadline-exhausted/unknown.
Neither met the `500`-character promotion gate, so no promotion occurred.
The raw action audit found no source-add, materialization, source-content, or
content-fetch mutation. This is negative exact-canary evidence, not proof that
the whole class is unrecoverable and not authorization for direct RPC9 replay.

The four exact fallback outcomes with four-stage unavailability evidence were
then reconciled as terminal `unavailable` through the guarded classification
utility. The dry-run/apply receipts, SQLite backup, raw event references, and
postcondition are recorded at
`P:/.logs/multi_account_fetch/20260811_source_addressability_fallback_canary_run03_after_source_add_run03/unavailable_reconciliation_receipt.md`.
The current source-addressability class is closed; `QvxHBtYsDig` is now the
sole `fallback_quality` row, the current unavailable class is `142`, and `51`
rows require decision packets.
This is exact-ID terminal classification evidence, not blanket inference for
future SourceNotFoundError rows.

## NLM authentication — READ BEFORE TOUCHING ANY NLM-AUTH CODE

**Canonical doc:** `docs/operations/nlm-auth-architecture.md` (read this first).

The active auth model is one canonical storage file per exact external
identity, backed up as a same-named file in the local bare repo at
`C:\Users\brsth\.ytis-nlm-auth-backup\` (no network remote). The authoritative
map in `csf/nlm_auth_check.py` is:

| Identity | Expected email | Canonical storage |
|---|---|---|
| `a.hominidae` | `a.hominidae@gmail.com` | `P:/.data/yt-is/nlm-auth/storage_state.json` |
| `troup.hominidae` | `troup.hominidae@gmail.com` | `P:/.data/yt-is/nlm-auth/storage_state_troup_hominidae.json` |
| `brsthomson` | `brsthomson@hotmail.com` | `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json` |

Active launchers perform an exact-account preflight through
`csf.nlm_auth_headless.ensure_account_session(..., allow_bootstrap=False)`.
A healthy account is only probed; a missing/corrupt canonical file may be
restored from its exact backup, and an expired file may be repaired from its
matching durable master token. Active coordinators and workers never launch a
browser or wait for a human. First-time bootstrap is exclusively the explicit
`bin/csf-nlm-auth` operation. Never copy cookies between identities. The
operator/scheduled-maintenance path
`python -m csf.nlm_keepalive --log-file P:/.data/yt-is/nlm-auth/keepalive.log`
repairs each mapped account only through the exact-account backup/master-token
path, probes it, and backs up every healthy account separately. It never opens
a browser; first-time bootstrap remains the explicit `bin/csf-nlm-auth`
operation. A nonzero exit is actionable scheduler health, not permission to
reopen the legacy auth branch.

### Authentication is not the default explanation for a run failure

Do not reopen the solved authentication branch because a runner message
contains the words `auth`, `source`, or `login`. First inspect the exact lane
config, the same-window `probe_account_session()` receipt, and the raw lane
events. A successful account-specific probe plus `nlm_auth_storage_probe_ok`
events establishes that the account reached the active client path; it does not
prove throughput, but it rules out treating a later source-add failure as an
authentication failure.

### Canonical multi-account fetch lifecycle

An account having zero existing Gemini Notebook notebooks is not a reason to
skip it. The production coordinator is
`scripts/run_multi_account_fetch.py`: it selects a bounded exact-video scope,
partitions it into per-account manifests, preflights every exact account, and
invokes the existing `bin/csf-source fetch` once per account. Each child has an
account-scoped state root and descriptive worker-notebook prefix. The existing
worker path creates notebooks on demand, reuses each worker's notebook within
the run, and deletes it during worker shutdown; the coordinator must not use a
pre-existing-notebook inventory as its work queue.

The coordinator resolves one `--db-path` and passes that exact path to every
child through `YTIS_BATCH_STATUS_DB_PATH`. It also holds a database-scoped
interprocess lock from selection through post-child reconciliation. Do not
remove either guarantee: selecting in one database while children update
another can silently produce false completion counts, and overlapping
coordinators can launch duplicate work.

Direct live `--all-pending` execution is bounded to `400` rows. A larger
all-pending scope is accepted only from a supervisor-created
`supervisor_runtime.json` ownership marker with the matching database and
output root; use `scripts/run_unattended_backlog.py` for that path. Plan-only,
bounded canaries, and exact-manifest runs remain valid. This is a scope guard,
not full-backlog authorization.

The direct `bin/csf-source fetch` entry point acquires the same DB-scoped lock
before selection and external work. Coordinator-owned children receive a
parent/run/database ownership envelope and are the only allowed bypass, so a
legacy or manually launched fetch cannot race the coordinator. Keep the shared
lock implementation in `csf/fetch_run_lock.py`; do not recreate a second lock
path in a caller.

The adaptive scheduler health window is a window of completed results, but
results from worker slots above the current `target_workers` are excluded
before scale-up evaluation. Do not change this to an in-flight-worker filter:
the coordinator records health only after a worker future completes, and that
would prevent legitimate scale-up. The regression is
`tests/test_adaptive_worker_scheduler.py::test_health_from_slot_above_current_target_cannot_authorize_scale_up`.
This offline correction does not authorize adaptive live work or full-backlog
execution; current scheduler registration and source-add quality gates still
govern readiness.

### Unattended receipt and runtime identity hardening (2026-08-12)

The unattended supervisor derives expected per-account worker, batch, and
adaptive settings from the validated account-settings loader before accepting
a child summary. A child cannot make an incorrect policy valid by repeating it
in both its summary and account receipt. The regression is
`tests/test_run_unattended_backlog.py::test_summary_cannot_self_authorize_wrong_account_policy`.

The read-only health checker verifies a live runtime PID against the recorded
coordinator entrypoint and output root. A reused or unrelated PID is reported
as `runtime_process_mismatch`; an uninspectable live PID is reported as
`runtime_process_inspection_failed`. PID liveness alone is not an active-run
proof. This remains an offline trust-boundary correction and does not prove
Task Scheduler execution, adaptive restart persistence, live VPH, or
full-backlog readiness.

Do not report a multi-account run as complete from child exit codes alone. A
child can exit `0` after `source_add_failed` or below-threshold outcomes. Read
the selected video IDs back from `batch_status.sqlite` and classify the run as
`completed`, `partial`, `failed`, `blocked`, `planned`, or `no_work`. A `blocked`
run is a coordinator-level refusal, normally lock contention or failed
account preflight; its JSON summary is the authoritative receipt and no child
should have launched. The coordinator writes that summary even when lock or
preflight prevents work from starting. Source-add,
materialization, and content-threshold failures are operational outcomes to
diagnose and retry from the retained manifest; they are not permission to
reopen the legacy login path. Preserve manifests, receipts, and event logs;
worker notebooks and per-run worker state are disposable and must be cleaned
by the existing fetcher lifecycle.

### Scheduler restart/resume and cache isolation canaries (2026-08-10)

The supervisor restart contract is now validated for a bounded isolated run,
but this is not OS Task Scheduler or logged-out proof. `run05` verified that
the completed child receipt records the explicit staged transcript cache path;
`run06` then killed the active supervisor tree in `chunk-0002`, found no
matching descendants, ran the exact token-only auth preflight again, recovered
the chunk once, archived its partial `accounts`/`manifests`/`receipts`, and
completed all six selected IDs. Both staged SQLite databases passed
`integrity_check=ok`; the canonical cache was not the configured write target.
Receipts and raw evidence:
`P:/.logs/multi_account_fetch/20260810_scheduler_restart_resume_canary_run05/`
and
`P:/.logs/multi_account_fetch/20260810_scheduler_restart_resume_canary_run06/`.

The durable supervisor now pins `transcript_cache_db_path` in state, passes
`--transcript-cache-db-path` to every coordinator child, validates the child
receipt against that path, and rejects pre-pinning state files rather than
trusting an ambient cache environment. A fresh launch still rejects an
occupied output root; one owned recovery may quarantine partial artifacts into
a sibling archive. This proves bounded restart/resume and cache isolation, not
full-backlog drain, scheduled-task execution, or throughput optimality.

### Historical full-backlog gate snapshot (2026-08-10; superseded)

The fresh three-account coordinator health canary passed: 30 exact pending
IDs (10 per canonical account) reconciled to `complete`, all three token-only
account probes observed the expected email, the canonical transcript cache has
30 non-empty rows, and the batch DB integrity check is `ok`. The Pro command
used its configured bounded adaptive policy; both Free commands used fixed
three-worker settings. The packet is
`P:/.logs/multi_account_fetch/20260810_unattended_canary_run01_decision_packet.md`.
This promotes coordinator health only; it does not prove sustained VPH,
adaptive scale-up, poisoned synthesis, or full-backlog readiness.

The captioned adaptive candidate that followed the health canary is closed as
`candidate_invalidated_cache_only_no_control`, not as throughput evidence. It
selected 200 rows, but all 200 authoritative rows had `last_stage=cache`, all
200 canonical cache entries pre-existed with `source=notebooklm`, and the raw
run contained only `transcript_cache_reconciled` per-video events. No
NotebookLM source-add/materialization/content workload ran, and the fixed
control was correctly not launched. Receipt:
`P:/.logs/multi_account_fetch/20260810_throughput_captioned_adaptive_pair_run01/result_receipt.md`.
Future pairs must prove uncached IDs and intended live event families before
computing VPH or adaptive benefit.

Executable throughput-pair validation also requires the JSONL event logs to
carry the coordinator run/account envelope. Coordinator child environments
propagate the packet run ID into `YTIS_INDUSTRIAL_RUN_ID`; `bin/csf-source`
preserves that identity instead of replacing it with a child UUID. Then
`csf.csf_logging` adds `run_id` from `YTIS_INDUSTRIAL_RUN_ID` and
`account_profile` from `YTIS_NLM_ACCOUNT_PROFILE` without overwriting explicit
event fields. Do not
weaken the validator back to action-name-only checks: an event directory can
be stale or copied while still containing the expected action names.

The downstream `wiki-yt` queue is a separate quality boundary. The older
2026-08-10 paragraph below is historical and superseded by the exact run16
receipt. Current queue state is `completed=47`, `failed=2`, `poisoned=0`,
`needs_resynthesis=0`, `pending=0`, and `in_progress={}`. The semantic-debt
item was completed by the bounded checkpoint-resume path; its receipt is
`P:/.logs/wiki-yt-queue/20260811/semantic-resynthesis-4017-mmx-run16-result_receipt.md`.
Its citation coverage remains `19/36` (`52.8%`), so this is not complete-source
coverage. The two profileless `0 pages` failures remain unclassified and are
not authorized for retry; the current reconciliation is
`P:/.logs/wiki-yt-queue/20260811/wiki_failed_residual_reconciliation_20260812.md`.
The separate historical manifest audit still reports 13 gaps and zero exact
receipt recoveries at
`P:/.logs/wiki-yt-queue/20260811/manifest_gap_audit_after_run16.json`.
Page/output presence without an exact worker/profile/attempt receipt is not
repair proof.

### Historical backlog evidence (2026-08-10; superseded)

The active DB is `P:/.data/yt-is/batch_status.sqlite`; the latest verified
canonical snapshot is `integrity_check=ok`, `complete=9,184`, `failed=294`,
and `pending=333,641`. The latest 400-row source-add policy canary reconciled
`392/400` complete and `8` terminal failures; `40` exact newly observed
source-add failures were routed to fallback and all reached terminal outcomes.
This is valid bounded routing/receipt evidence, not full-backlog or throughput
evidence.

The current canonical residual audit is the read-only packet generated at
`2026-08-10T19:37:46Z`:
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit_current_20260810_133736.json`:
`195` source-add, `28` command, `15` content-threshold, `2` external-cookie,
`2` empty-transcript, `2` no-transcript, and `50` unavailable failures; `240`
rows require a decision packet. Older residual snapshots are historical and
must not be used as current state. Direct `rpc_code=9` replay remains
prohibited; only the exact, packeted fallback route may process source-add
rows.

The subsequent adaptive-policy candidate is closed as a diagnostic
`partial_source_add_gate_failed`, not throughput evidence:
`P:/.logs/multi_account_fetch/20260810_throughput_adaptive_policy_run01_decision_packet.md`.
It completed `548/600` rows and failed `52`; `51` source-add failures were
all authoritative `has_captions=0` rows with `rpc_code_9_failed_precondition`.
The fixed control was correctly not launched. Pro adaptive scale-up was not
exercised beyond three workers because the candidate queue exposed only four
batches. Do not compare its per-account rates to a sustained VPH ceiling.

The later route-partitioned adaptive candidate is closed as
`candidate_invalidated_no_control`, not throughput evidence. It selected 1,200
`has_captions IS NULL` rows and reconciled `994` complete and `206` failed;
the new failures were `166` source-add, `25` command, and `15` content-
threshold outcomes. Pro emitted adaptive decisions but never targeted above
its initial three workers, so the fixed control was correctly not launched.
The governing packet is
`P:/.logs/multi_account_fetch/20260810_throughput_uncategorized_adaptive_pair_run01_decision_packet.md`.
Do not rerun that pair or claim adaptive benefit. Source-add residual recovery
is separately packeted and uses exact manifests plus `--fallback-only`. The
12-row canary completed with 9 non-empty cache results and 3 explicit
unavailable terminal failures, with no NotebookLM source-add, materialization,
or content events. Keep default promotion deferred until fallback tail cost
and per-item budgets are measured; this does not authorize `--until-empty`.
See
`P:/.logs/multi_account_fetch/20260810_source_add_residual_fallback_canary_run03/decision_packet.md`.

The following residual audits are historical snapshots, not the current
canonical state:
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit_after_no_caption_retests.md`.
It reports `87` failed rows: `39` source-add candidates, `41` unavailable,
`2` cookie-source, `2` empty-transcript, `2` no-transcript, and `1` command.
The source-add/no-caption recovery branch is recorded in
`P:/.logs/multi_account_fetch/20260810_no_caption_fallback_canary_run01_decision_packet.md`
and run 02. The narrow oEmbed bypass fix in `bin/csf-source` is verified, but
the 12-row retest reached private/sign-in, unavailable, Selenium, and rotated
cookie failures without producing transcripts. Keep
`--route-no-captions-to-fallback` explicit and off by default; do not ask for
NotebookLM login from these residuals.

That older snapshot was superseded by the current 294-row audit above. The
older classification receipt is
`P:/.logs/multi_account_fetch/20260810_unattended_residual_audit.md`.
The current classification version reports 35 failed rows: 29 terminal
`unavailable`, 2 terminal `no_transcript`, 2 terminal `empty_transcript`, and
2 `cookie_source` rows blocked on external YouTube cookie state. The six
original content-threshold candidates produced five non-empty cache
completions; the remaining `x85tFCIc3Ps` row is now explicitly classified as
terminal `no_transcript` after its exact fallback chain exhausted at direct
API with subtitles disabled. The recovery receipt is
`P:/.logs/multi_account_fetch/20260810_content_threshold_recovery_result_receipt.md`.
This is a classification receipt, not retry authorization. The two
`cookie_source` rows remain a hard stop until a separately packeted cookie
source becomes available; no NotebookLM authentication repair is implied.

The latest exact source-add recovery receipt from the earlier residual branch
is
`P:/.logs/multi_account_fetch/20260810_source_add_residual_recovery_run01/result_receipt.md`.
It processed 26 rows through explicit fallback-only recovery and completed
14; it did not promote fallback to the default route. Before `--until-empty`
or scheduled full-backlog execution, each non-terminal class must have its own
exact manifest, decision packet, falsifier, bounded policy, early-abort gate,
and reconciled postcondition. Do not merge source-add, command,
content-threshold, poisoned-synthesis, or historical-manifest residuals into a
generic retry pool. The observed long-audio fallback tail also requires a
per-item budget/receipt policy; the four-hour child timeout alone is not proof
of unattended safety.

The command-failure expansion receipt is
`P:/.logs/multi_account_fetch/20260810_command_residual_expansion_result_receipt.md`.
It validates the opt-in industrial-failure fallback boundary on 17 exact IDs,
but remains a partial recovery and does not authorize enabling the route for
the full backlog. The remaining non-terminal rows are still separate queues:
unknown `4` and the two `no_transcript`/other rows. The audit and every
residual packet must be regenerated after a live recovery branch; stale counts
in older handoffs are historical only.

The explicit `--route-source-addressability-failures-to-fallback` policy is a
separate, opt-in recovery route for authoritative `SourceNotFoundError` or
source-not-found failures after the source-add/content path. It admits only
that narrow failure class, requeues each exact ID once, and sends it through
the exact-manifest fallback-only path; it never retries NotebookLM source-add,
materialization, or source-content operations. Keep the route off by default
until its policy canary is reviewed. A token-only account probe plus a source
addressability failure is not an authentication failure and must not trigger a
browser login request. The supervisor/coordinator receipt flag must match the
child execution setting; any mismatch is a failed receipt, not a success.

The sharded runner now classifies invalidation evidence in both the exception
text and the JSON failure record:

- `auth_or_profile_artifacts` means investigate account/profile state.
- `source_add_or_materialization_artifacts` means investigate the cohort,
  source-add, mapping, or materialization path; do **not** ask the operator to
  sign in again on that evidence alone.
- `mixed_auth_and_source_artifacts` requires separating the two classes before
  choosing an action.

The 2026-08-08 `candidate6_telemetry_validation_run03` run is the reference
example: `a.hominidae` and `troup.hominidae` passed the immediate canonical
probes, both lanes emitted auth-storage probe success events, and the run was
invalidated by the same six source-add failures in both lanes. Its `0.00 VPH`
is an invalidation placeholder, not a throughput result. `brsthomson` was not
part of that lane config and remains a separate account state; never use it to
block the two accounts that were actually probed.

**Do NOT** touch the following thinking you are "fixing" auth without reading
the canonical doc first:
- The legacy sibling-copy/family-refresh paths in `csf/nlm_worker_auth.py`;
  its account-specific CDP launcher is retained only for first-time bootstrap
  and is not the active run-time auth boundary.
- The nlm CLI cookie store at `~/.notebooklm-mcp-cli/` (no longer primary)
- The account-owned bootstrap browser roots configured in
  `csf/nlm_worker_auth.py`; `brsthomson` intentionally reuses the verified
  `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json.browser_profile`
  root, while Pro and Free1 use their dedicated roots.

**Do NOT** delete any canonical `P:/.data/yt-is/nlm-auth/storage_state*.json`
file or its matching backup.
If you see a login prompt, inspect the exact account probe and repair result;
do not fall back to the legacy `notebooklm login` or shared/default Chrome
profile. Use the package-owned command for the exact identity:

    python P:/packages/yt-is/bin/csf-nlm-auth --profile a.hominidae

If no durable master token exists, the only operator-dependent step is a
one-time attach to a loopback CDP endpoint from a browser context signed into
only that exact account. Start the command first; it waits for the exact
account to appear while the operator completes sign-in in that dedicated
window:

    python P:/packages/yt-is/bin/csf-nlm-auth --profile a.hominidae --cdp-url http://127.0.0.1:18870

The command rejects remote CDP hosts, ambiguous multi-account contexts, and
`--all` plus `--cdp-url`. After this one-time bootstrap, normal renewal is
headless and token-only. Never use `--no-sandbox`, copy browser cookies, or
attach a shared/default profile as an auth workaround.

## Handoff topology — this package handoff is not the whole project

`P:/packages/yt-is/HANDOFF.md` is the package-local operational reference. It
is not the sole project handoff and must not be used to infer that all yt-is
work is complete. Before cross-repo, cache, wiki-yt, or visual-pipeline work,
read the applicable handoff chain under `P:/docs/handoffs/`:

1. `yt-is-nlm-to-wiki-integration-20260730/HANDOFF.md` is the parent
   integration objective: yt-is as the canonical YouTube transcript cache and
   the forward-sync path into wiki-yt.
2. `yt-is-nlm-to-wiki-fixes-20260730/HANDOFF.md` is its implementation child:
   F2 cache-first/feed-forward shipped; F1 and F3 remain separately deferred.
3. `wiki-yt-architecture-decisions-20260730/HANDOFF.md` records the locked
   architecture decisions and their next actions.
4. `yt-is-progressive-visual-analysis-20260804/HANDOFF.md` is a separate open
   visual-pipeline workstream with U-05 through U-09 still to implement.

Older `nlm-to-wiki-v3`, bulk-ingest, and notebook-consolidation handoffs are
historical inputs unless a current handoff explicitly reopens them. When
counts or next actions conflict, verify the current code, databases, and
artifacts, then update the governing handoff; do not silently choose the
package-local summary. The throughput/auth investigation is a separate
diagnostic branch and does not close the integration or visual workstreams.

## Worktree lifecycle policy

`worktree-policy.toml` at the package root defines the worktree lifecycle
settings (main branch, naming pattern, worktree root, backup tag prefix).
Loaded by `cc-skills-sdlc/skills/go/scripts/worktree_lifecycle.py::load_policy`.

The PreToolUse hook that enforces the managed worktree root lives at the
**user level**, not in this package:
`P:/.claude/hooks/worktree_root_policy_PreToolUse.py`, wired in
`~/.claude/settings.json` (`hooks.PreToolUse` matcher `Bash`). It blocks
`git worktree add` whose target is not under the configured
`WORKTREE_ALLOWED_ROOT` (default `P:/.worktrees/`) — bypass with
`GO_WORKTREE_SAFETY_BYPASS=1`. The user-level home defeats upstream #79111
(subdirectory launches fail-open for project-root settings.json), which is
exactly when worktree ops happen. See
`P:/.data/wiki/concepts/worktree-root-policy-hook-design-2026-07` for the
design rationale, and `claude-code-hooks-bug-landscape-2026-07` for the
upstream-gap snapshot.

For the policy-validated CLI, see
`P:/packages/.claude-marketplace/plugins/cc-skills-sdlc/skills/go/scripts/worktree_cleanup.py`
(PR 4 of `P:/docs/worktree-lifecycle-design.md`).

## Industrial trust floor (read before integrity/multi-worker work)

**Implementation backlog (contracts):**  
`docs/operations/root-cause-program.md`

**Evidence / acceptance (finding IDs, not the plan):**  
`docs/operations/review-2026-07-17-grok-deep2.md`  
`docs/operations/critical-review-2026-07-17.md`

| Rule | Detail |
|------|--------|
| Plan by **contract** (C1–C5) | Not by raw CON-/INT-/COR- ticket order alone |
| Findings = acceptance | Each contract lists IDs; close under C# when falsifier tests pass |
| Cache / shared-retry trust | Until **C1+C2** closed (or explicit waiver), do not treat industrial shared cache or multi-worker shared-retry as trustworthy for “optimal” claims |
| Dual paths | Prefer path monopoly; do not leave serial vs shared outcome algebras diverging |

## Fresh Agent Throughput Gate

Before proposing or launching any NotebookLM throughput benchmark, read:

- `docs/operations/throughput-optimization-llm-contract.md`
- `docs/operations/templates/throughput-decision-packet.md`
- `docs/operations/hot-path-throughput-next-test-plan.md`
- `docs/operations/test-registry.md`
- `docs/operations/observability-contract-checklist.md`

Use the installed `evidence-driven-experiment-loop` skill to record the active
authority, metric, claims, gates, verification, adversarial review, and next
action. Run its validator before delegating throughput work, authorizing a live
run, or reporting `ready_for_parent_review`.

Do not launch a live throughput run from chat memory alone. Complete the decision packet first. If the packet cannot name the raw artifacts, current control, falsifier, early-abort gate, and promotion rule, do offline attribution, a harness fix, or a code fix instead.

## Throughput Reasoning Guardrails

Fresh agents must treat prior LLM summaries as claims to verify, not as ground truth. Before changing code, launching a benchmark, or declaring a result ready, inspect the current repo files and raw artifacts named by the claim.

For sustained-VPH work:

- Use the current repo contract: combined hot-path VPH is the throughput metric; exclude Whisper recovery from sustained throughput.
- Treat `docs/operations/hot-path-throughput-next-test-plan.md`, `docs/operations/test-registry.md`, and `HANDOFF.md` as the current planning chain. Re-read them before acting on an older chat or packet.
- Do not run same-shape benchmarks to "see what happens." A live run needs a new code/harness mechanism or a completed decision packet with raw artifacts, falsifier, abort gate, and promotion rule.
- Separate historical high-water results from current-contract results. Do not call the current observed leader "optimal" unless the evidence proves optimality.
- Label every performance claim with its layer: row, source, video, worker, lane, stage, run, or combined run.
- Prefer offline attribution and artifact analysis before live benchmark work.

For evidence-table and metadata analysis:

- Do not globally dedupe by `video_id` unless the question is explicitly video-level. Throughput burden usually needs a source-level or observation-level key.
- For sharded-lane evidence rows, preserve at least `run_label`, `stage`, `lane`, `batch_index`, `worker`, `profile`, `video_id`, and `source_id` when grouping observations.
- Treat `pass_name` explicitly. If primary and retry rows are merged, say so; phase-level analysis must be built from raw rows or a key that includes `pass_name`.
- Do not sum elapsed fields blindly. Check whether `command_elapsed_s_total`, `command_elapsed_s_max`, and `command_elapsed_s_count` are row-level, cumulative, or already aggregated before calculating burden.
- Report median and p95, not only averages. If medians are similar but p95 differs, call it a tail-latency signal, not a general elapsed-time improvement.
- Apply sample gates to every band used for a signal. Unknown or under-sampled bands must be excluded or marked insufficient.
- Metadata sidecars created from YouTube Data API or other external fetches are exploratory/tainted unless generated by the benchmark or explicitly validated. Do not use tainted metadata alone to justify a live benchmark.

Useful next-step language:

- "partial exploratory signal" means continue offline validation or instrumentation.
- "benchmark justified" requires canonical evidence plus a decision packet.
- "ready" means tests/verification passed and stale packets/docs do not contradict the current result.

## Completion and Handoff Discipline

Do not call work complete because fields, files, packets, or tests exist. Completion requires the requested behavior or evidence contract to work on the rows, events, code paths, and artifacts where it is supposed to discriminate behavior.

## Pre-Completion Self-Review Gate

Before saying work is done, complete, fixed, ready, or safe to hand off, perform a self-review pass that tries to find what is wrong with your own work.

Required checks:

- Re-read the original objective and list each requested requirement.
- Map each requirement to the exact file, test, artifact, or command output that proves it is satisfied.
- Look for contradictions between final summary, docs, tests, packets, git status, and raw artifacts.
- Search for stale claims, stale numbers, stale filenames, and old run IDs introduced or left behind by the work.
- Check that tests prove behavior, not just schema, field presence, or JSON serializability.
- Check edge cases around identity keys, grouping keys, retry paths, partial failures, empty data, and duplicated IDs.
- Verify that no prohibited side effect occurred, such as an unapproved live benchmark, external metadata fetch, notebook deletion, or raw artifact mutation.
- Run the required verification commands for the task type.
- If any issue remains, do not report completion. Report `needs_fix` with the exact blocker and next action.

Final responses must include a `Self-review result` line:

- `Self-review result: no blocking issues found` only if the checks above were performed and passed.
- `Self-review result: needs_fix` if any contradiction, missing verification, partial implementation, or unproven requirement remains.

Do not outsource self-review to the user or parent agent. If a parent reviewer can find an obvious contradiction, stale count, missing semantic test, or unverified claim from the files you changed, your completion report was premature.

## Claim Ledger for Throughput Decisions

For non-trivial reviews, proposals, decision packets, benchmark interpretations, or mechanism investigations, include a compact claim ledger before final handoff. This is required for `yt-is` throughput work because previous branches have found real flaws and then overcorrected into new unverified causal stories.

Use columns like:

`Claim | Type | Evidence | Verification method | Confidence | Falsifier | Action allowed`

Allowed claim types:

- `verified_fact`: directly proven by code, artifact, or command output.
- `measured_metric`: directly re-derived from raw artifacts.
- `inference`: plausible explanation, not directly proven.
- `hypothesis`: candidate mechanism needing a discriminating test.
- `historical_context`: older result or prior decision, not current authority.
- `unsupported`: must not drive action.

Do not promote an `inference` or `hypothesis` into a decision as if it were verified. If adversarial review falsifies an old claim, correct that claim first. Any replacement explanation must be classified separately and given its own falsifier. If the replacement remains an inference, the allowed next action is evidence gathering, not implementation or live benchmark authorization.

## Parent Handoff Boundary

When working as a delegated or target agent, do not decide to continue into the next goal yourself. Hand off to the parent reviewer when the assigned objective reaches any terminal state:

- `ready_for_parent_review`: requested work is implemented or the requested decision packet is complete, self-review passed, and verification commands were run.
- `needs_fix`: self-review found a blocker, contradiction, missing semantic coverage, or unproven requirement.
- `blocked`: required data, credentials, environment, or user authorization is missing.
- `decision_required`: the next action would change scope, launch a live run, fetch external data, delete/mutate raw artifacts, commit/stage changes, or choose between competing mechanisms.

The final response must make the handoff state explicit:

- `Parent handoff: ready_for_parent_review`
- `Parent handoff: needs_fix`
- `Parent handoff: blocked`
- `Parent handoff: decision_required`

Do not tell the user that parent review is unnecessary. If the delegated objective is complete, return the evidence packet and stop. The parent reviewer decides whether to re-verify, commit, launch another agent, approve a live benchmark, or assign the next goal.

## Bounded Branch Execution

Delegated goals may include multi-step branches, but the allowed action budget must be explicit before work starts.

For any goal that can run live systems, benchmarks, external fetches, migrations, deletes, or other costly actions:

- State the exact number of authorized live or costly actions.
- Use a fresh output root for every run or generated artifact unless overwrite is explicitly authorized.
- Preserve prior run roots and raw artifacts.
- Do not run adjacent experiments because the first result is interesting.
- If a live run exposes a code or instrumentation bug, fix and verify the bug only if the goal allows code edits; do not automatically run another live validation unless the goal explicitly budgets it.
- Keep verdict layers separate: instrumentation validity, throughput result, cohort effects, environment/tooling health, and decision readiness.
- At each branch end, either continue only within the authorized branch or return `Parent handoff: decision_required`.

A final response for a branched goal must state:

- Authorized live or costly actions used vs allowed.
- Output roots created or modified.
- Which branch was taken and why.
- Which branches were not taken.
- Whether another live or costly action is needed and who must approve it.

## Blocker Triage

Before reporting `blocked`, classify the blocker precisely:

- `domain_blocked`: the target system or code failed after known recipes were tried.
- `environment_blocked`: credentials, auth, network, filesystem, browser, process state, or service state is invalid.
- `tool_blocked`: the agent shell, CLI, plugin, or command runner cannot execute or capture output reliably.
- `decision_blocked`: the next action needs user or parent approval.
- `data_blocked`: required artifacts, logs, inputs, or fixtures do not exist.
- `scope_blocked`: the required fix is outside the allowed write or action scope.

Do not report a broad blocker until you have:

- Searched repo-local runbooks, handoffs, troubleshooting docs, and prior decision packets relevant to the failure.
- Checked whether the same problem has a documented recipe or previous fix.
- Captured the exact command, exit code, stdout/stderr, and expected artifact/log side effects.
- Tried a minimal sanity command to determine whether the execution tool itself is working.
- Tried the platform-native shell or command form when shell syntax may be the issue.
- Separated "the task failed" from "my launcher/tooling failed."
- Identified the smallest next action that would unblock progress.

If the user says "this was solved before", stop the current diagnosis and search local docs and artifacts for the solved path before continuing.

A blocked final response must include:

- Blocker class.
- Evidence inspected.
- Known recipes checked.
- Commands attempted with exit codes.
- Why alternate explanations were rejected.
- Smallest next unblock action.
- `Parent handoff: blocked`.

Example: for Gemini Notebook auth/profile failures, check
`docs/operations/nlm-auth-architecture.md` before claiming interactive auth is
required; the historical rerun recipe is not the active auth authority.

## Time-Sensitive Preflight

If a preflight depends on expiring state such as auth, leases, browser sessions, locks, service health, temporary files, ports, or process state, do not treat an earlier pass as current.

For time-sensitive preflights:

- Record the exact check time.
- State what can expire or drift.
- Run the preflight immediately before the dependent action.
- If too much time or a turn boundary has passed, re-run the preflight.
- Do not report "ready to launch" unless the preflight was checked in the same execution window as the launch command.
- If the preflight passes but the dependent action is not launched, report `Parent handoff: decision_required`, not `ready`.

Example: Gemini Notebook account state can expire quickly. Run the exact-profile
`csf-nlm-auth` probe/repair immediately before the dependent action, not in an
earlier turn. If no durable master token exists, the one-time dedicated-CDP
bootstrap must complete before a live smoke; do not use shared/default Chrome.

## Ignored Artifact Reporting

`git status --short` is not enough when the task writes ignored paths such as `.logs/`.

If you create or modify ignored artifacts, list them explicitly even when git status is clean. Include:

- Exact path.
- Whether it was created or modified.
- Why it was written.
- Whether it should remain untracked or ignored.
- Whether a tracked doc or registry should point to it.

Do not say "no files changed" if ignored artifacts were written. Say: "Tracked git status is clean; ignored artifacts changed: ..."

For instrumentation work:

- Schema presence is not enough. If a field is expected to answer a question, tests must prove it is non-null or meaningfully populated on the relevant path.
- If a field is intentionally `None` on a path, state why. Mark the implementation partial if that path is required by the contract.
- Tests must assert semantics, not only field shape or JSON serializability.
- Do not narrow scope silently. If the requested contract includes queue timing, retry timing, and per-attempt breakdown, completing only one category is partial unless the parent explicitly approves the narrower scope.
- Do not call a result "ready for live run" while any discriminator field remains unimplemented, always-null, or untested on the relevant path.

For handoffs:

- Do not say "committed", "staged", or "clean" unless verified with `git status --short` and `git log`.
- If the worktree is dirty, list the exact modified and untracked files.
- If a summary contains contradictions, resolve them before final output.
- Prior LLM reports are claims, not evidence. Verify against current files, raw artifacts, tests, and git state.

### Historical Industrial Transition (April 2026)

The following bullets are historical context from commit `bea672f`; they are
not the current unattended-operation contract and must not be treated as
verified current behavior without checking the active source path:

- **Persistent Staging:** `NLMIndustrialScraper` was intended to reuse a
  staging notebook for up to 300 videos.
- **Automated Triage:** an earlier design described an industrial/sequential
  split around `BACKLOG_THRESHOLD = 50`.
- **Deep Discovery:** an earlier `source_enumerator.py` design included full
  playlist enumeration beyond the RSS window.
- **Self-Healing:** an earlier design described a `BatchScheduler` retry
  window. The current coordinator does not provide a verified 24-hour
  unattended retry loop; inspect `scripts/run_multi_account_fetch.py` and its
  receipts before making that claim.

For the active mass-ingestion path, use
`scripts/run_multi_account_fetch.py` with the authoritative DB path and
account-scoped manifests. Use `--plan-only` to prepare and revalidate a large
scope without launching a child; a `planned` receipt is not a completed fetch.

## Architecture

```
User Input → Skill Invocation → CLI Script / Python → Transcript Sources → SQLite Cache
```

## Skills

### `/yt-is` — YouTube Channel Management

Check all tracked YouTube channels for new videos and manage your channel list.

**Entry point**: `bin/yt-is` (wraps `bin/csf-source`) for channel management.
For canonical multi-account mass ingestion, use
`scripts/run_multi_account_fetch.py`, not an existing-notebook inventory.

**Commands:**
- `sync` — Check all tracked channels for new videos (RSS + gap detection + API). **Long-running: ~1-2s per channel.** For 1,298 channels this is 20-40 minutes. Set `timeout: 1800000` (30 min) on `run_terminal_command`, or run in background with no timeout. Never use the default 120s timeout — it kills a working process mid-sync.
- `list` — List all tracked channels with metadata
- `add <url>` — Add a new channel or playlist to track
- `fetch` — Download pending transcripts via the full fallback chain (oEmbed → yt-dlp → yt-dlp+cookies → direct API → NotebookLM → Selenium → Whisper)

**Escalation Chain:**
1. oEmbed reachability probe — cheap early skip for removed/private videos
2. yt-dlp (WEB client) — fastest, works for most public videos
3. yt-dlp with cookies — for age-restricted videos
4. direct API — cheap terminal/no-transcript discriminator
5. NotebookLM Industrial — best for backlog and clean transcripts
6. Selenium Firefox — fallback for bot-check failures
7. Whisper — audio fallback

**Key files:**
- `bin/yt-is` — CLI entry point
- `bin/csf-source` — Backend implementation
- `csf/source_enumerator.py` — RSS + API enumeration
- `csf/batch_status.py` — SQLite storage (`channel_metadata`, `analysis_status` tables)

**Dependencies:**
- `yt-dlp>=2024.0.0`
- Firefox (Selenium fallback)
- `YOUTUBE_API_KEY` (for gap resolution)

### `/yt-nlm` — NotebookLM Transcript Extraction

Extract YouTube transcripts using NotebookLM's batch notebook workflow.

**Entry point**: `csf/transcript.py` via the NotebookLM batch path inside `bin/csf-source fetch`

**Why batch over ephemeral:**
- **Ephemeral (deprecated)**: 1 notebook per video — wastes NotebookLM slots, slow
- **Batch**: Up to 300 YouTube sources per notebook — reuses a single notebook

**Workflow:**
1. Create batch notebook: `nlm notebook create "batch_transcript_{id}"`
2. Add sources: `nlm source add <nb-id> --youtube <url1> --youtube <url2> ... --wait`
3. Get content: `nlm source content <source-id>` (returns raw JSON with `{"value": {"content": "..."}}`)
4. Delete notebook: `nlm notebook delete <nb-id> --confirm`

**Auth and runtime contract:**
- The active path uses `csf.nlm_auth_headless.ensure_account_session` with the exact account profile. Do not revive the legacy `nlm login`, `uv tool install`, or shared cookie-store recovery path for active work.
- Normal renewal is token-only. The scheduled `python -m csf.nlm_keepalive --log-file P:/.data/yt-is/nlm-auth/keepalive.log` path never opens a browser. If a matching durable master token is absent, use the one-time package-owned `python P:/packages/yt-is/bin/csf-nlm-auth --profile <exact-profile>` bootstrap and then return to the token-only path.
- Before any benchmark trial: clear stale worker notebooks through the existing worker-notebook cleanup path, then let the worker process prewarm its notebook before timed batches start.
- Browser/process cleanup must be scoped to yt-is-owned runtime state only. Do not kill Chrome, Edge, or other browser processes by executable name. Only stop processes that can be tied to the active yt-is run by an explicit PID recorded by the harness or by a command line rooted under a configured yt-is browser profile such as `P:\.data\yt-is\browser\notebooklm-pro`, `P:\.data\yt-is\browser\notebooklm-free`, or another lane `browser_profile_root` from the active lane config. If ownership is ambiguous, leave the browser running and inspect the run logs/profile roots first.

**Key files:**
- `csf/transcript.py` — `_fetch_via_notebooklm_batch()` with auth recovery
- `csf/cache.py` — `set_cached_transcript()` for database caching

**Dependencies:**
- `nlm` CLI (NotebookLM command-line interface)
- NotebookLM Pro/Plus account (300 source limit per notebook)

## CLI Tools

### `yt-is`

Channel management CLI. Delegates to `csf-source` backend.

```powershell
yt-is sync                  # Check all tracked channels
yt-is list                  # List all tracked channels
yt-is add <url>             # Add a channel
yt-is fetch                 # Download pending transcripts
```

### `csf-source`

Backend implementation for all channel and transcript operations.

```powershell
csf-source list              # List tracked sources
csf-source add <url>         # Add a source
csf-source check <source>    # Check one source for new videos
csf-source check-all        # Check all sources
csf-source sync <source>    # Process pending videos for a source
csf-source fetch            # Download pending transcripts
```

When launching from a shell inside the repo, prefer `python bin/csf-source ...` so the command does not depend on PATH.

## Data Flow

```
/yt-is sync
    │
    ├─► RSS fetch (15 most recent per channel)
    ├─► Gap detection (new videos not in local DB)
    └─► API resolution (YouTube Data API with publishedAfter cursor)
            │
            ▼
    batch_status.sqlite: analysis_status (pending)
            │
            ├─► /yt-is fetch ──► python bin/csf-source fetch ──► transcripts.sqlite
            │                              └─► full fallback chain
            │
            └─► /yt-nlm ──► NotebookLM batch ──► transcripts.sqlite
                        │
                        ▼
            Combined markdown batches → CKS / Obsidian / analysis tools
```

## Storage

- **batch_status.sqlite** — Video tracking
  - `channel_metadata`: tracked channels, playlist IDs, last_checked
  - `analysis_status`: video_id, status (pending/complete/failed), last_stage, failure_reason
    - A `complete` row freezes `last_stage` and `failure_reason` against later
      retry writers. A failed-to-complete fallback may retain the original
      failure reason as provenance; residual/retry queries must scope by
      `status='failed'`, not by the presence of `failure_reason` alone.
- **transcripts.sqlite** — Cached transcripts (video_id, lang, source, content)

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `YOUTUBE_API_KEY` | — | YouTube Data API v3 key (for gap resolution) |
| `YTIS_NLM_MAX_SOURCES_PER_NOTEBOOK` | 300 | Max YouTube sources per NotebookLM notebook |

### External Transcript Provider

Register a custom transcript provider:

```python
from yt_is.csf.transcript import register_external_transcript_provider

def my_provider(video_id: str, prefer_lang: str | None):
    # Return (success: bool, transcript: str | None, error: str | None)
    return True, "transcript content", None

register_external_transcript_provider(my_provider)
```

Called after all built-in methods fail, before returning final failure.

## Troubleshooting

### "No new videos found" after sync

The RSS feed only returns 15 most recent videos. If your tracked videos are older than that, the sync reports no new videos — even if there are unprocessed pending videos from prior syncs.

### Gemini Notebook auth expired

Run the package-owned exact-account preflight/repair path. Do not use `nlm login` or copy cookies from the normal Chrome profile:

```powershell
python P:/packages/yt-is/bin/csf-nlm-auth --profile <exact-profile>
python -m csf.nlm_keepalive --log-file P:/.data/yt-is/nlm-auth/keepalive.log
```

The first command is operator-dependent only when that account has no durable master token; subsequent renewal is non-interactive. A scheduled keepalive exit of `2` or `3` is an actionable auth-health failure, not permission to reopen the legacy auth branch.

### Transcript fetch fails for all methods

Check:
1. Video has captions (YouTube Studio → Subtitles)
2. Video is not age-restricted or region-blocked
3. `YOUTUBE_API_KEY` is set for gap resolution
4. Firefox is installed (for Selenium fallback)
