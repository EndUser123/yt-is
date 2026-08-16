# Bounded Adaptive Worker Scheduler Decision Packet

Status: `historical_offline_record; live_smoke_not_authorized`. This packet
records the durable adaptive implementation and its original parent-owned
smoke design. Its historical live command is not current authorization. The
current operational authority is `P:/packages/yt-is/HANDOFF.md`, which records
that canonical auth is healthy but the source-add boundary remains unresolved;
do not launch the historical smoke or another source retry from this packet.

## Current supersession (2026-08-09)

The coordinator now exposes the existing adaptive scheduler through
`scripts/run_multi_account_fetch.py` as an explicit opt-in. Fixed mode remains
the default and strips ambient adaptive environment variables before launching
children. Adaptive mode requires an explicit per-account maximum and records
the full policy in the coordinator receipt. Coordinator exact-set
revalidation, missing-ID reconciliation, atomic summary receipts, worker
result normalization, and credential-shaped diagnostic redaction are covered
by the current affected offline boundary (`310` tests). These are correctness
and operational improvements only; no adaptive VPH or source-add improvement
has been established.

## Active identity naming correction (2026-08-06)

Active lane metadata now separates the exact external account identity from
unique worker routing labels:

- `a.hominidae` maps to `P:/.data/yt-is/nlm-auth/storage_state.json` and
  `a.hominidae@gmail.com`.
- `troup.hominidae` maps to
  `P:/.data/yt-is/nlm-auth/storage_state_troup_hominidae.json` and
  `troup.hominidae@gmail.com`.
- `brsthomson` maps to
  `P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json` and
  `brsthomson@hotmail.com`.
- Active worker labels are descriptive routing names derived from the account,
  for example `a.hominidae-worker-02`; they never select auth state.

The active typed client selects the exact mapped storage file and fails closed
on missing, invalid, expired, or mismatched state. Each worker subprocess
owns its client/event loop, state path, notebook title, and logical client
namespace. Historical CLI profile names and raw logs are not active evidence.

## Superseded historical CLI blocker (2026-08-06)

An earlier attempt checked deprecated CLI worker profiles and returned exit `2`
with `ClientAuthenticationError`:

```text
ytis-pro-worker-01..04   exit=2  ClientAuthenticationError
ytis-free-worker-01..04 exit=2  ClientAuthenticationError
```

This evidence is retained as historical context only. It does not authorize
CLI sync, refresh, cookie copying, or a conclusion about the canonical typed
client. The current preflight is `probe_account_session()` against the mapped
canonical storage file; no smoke is allowed until that probe passes.

## Current canonical preflight (2026-08-06)

The required read-only Pro probe was run immediately before any smoke attempt:

```text
probe_account_session('a.hominidae', worker_id='coordinator')
ok=False
reason=session_probe_failed:ValueError:Authentication expired or invalid;
redirected to https://accounts.google.com/<redacted>
```

The canonical storage file exists, is structurally valid, and contains the
expected `a.hominidae@gmail.com` identity. The session itself is expired or
invalid. This is an auth/preflight blocker, not an adaptive result. No login,
refresh, cookie copy, CLI sync, source work, or smoke was attempted after the
probe failed. The operator must repair this exact account outside the run and
rerun the read-only probe; only then is the one minimal Pro smoke authorized.

The offline smoke input is prepared from the existing frozen cohort at
`.logs/sharded_lane_series/candidate6_telemetry_validation_run02_current/`:
`cohort.a_hominidae_pro.json` is `cohort_shape=captioned`, contains `8,942`
items, and all inspected items are captioned. The planned smoke consumes only
four items, with inner batch size `1`, so the intended adaptive transition is
`2 -> 3`; no cohort regeneration or metadata fetch is needed.

## 2026-08-06 historical validation attempt

Decision: `blocked_before_launch` under the superseded CLI-auth contract. No
comparison process was launched and no NotebookLM source work or throughput
quota was consumed.

Verified preflight facts:

- Both historical lane files were valid JSON lists with two lanes, four named
  labels per lane, and an initial fixed target of three workers per lane.
- The control output root
  `.logs/sharded_lane_series/adaptive_worker_comparison_control_run01_current`
  and candidate output root
  `.logs/sharded_lane_series/adaptive_worker_comparison_candidate_run01_current`
  did not exist after the attempt.
- `nlm login --check --profile` with auto-update disabled failed for all eight
  deprecated profiles required by those lane files.
- HTTP/TCP connectivity to `notebooklm.google.com` was available, so this is
  not evidence of a general network outage.
- The old Free worker metadata named the wrong account. That is irrelevant to
  the canonical account-file contract and must not be repaired by copying CLI
  profiles.
- The canonical sync and dedicated CDP refresh attempts timed out; their
  verified process trees were cleaned. No residual matching auth or dedicated
  browser processes remain.

This was an auth/preflight blocker, not a negative adaptive-performance result.
The adaptive implementation and its offline tests remain valid; the next
authorized action is one Pro-only canonical smoke if the immediate probe
passes. The Free accounts are unavailable while their canonical files are
absent.

## Implemented change

- `csf/adaptive_worker_scheduler.py`: typed bounded policy, worker identity
  validation, draining/quarantine state, and batch assignment ledger.
- `bin/csf-source`: explicit opt-in adaptive industrial dispatch, stable batch
  assignment IDs, scale decisions, launch/timeout/result failure handling, and
  structured transition/assignment telemetry. Adaptive failure recovery now
  restores the exact queued batch objects through one fail-closed helper.
- `csf/sharded_lane_series.py`: optional adaptive lane fields and environment
  propagation. Existing lane files remain fixed-mode by default.
- `csf/nlm_batch.py`: canonical account workers no longer import the legacy
  CLI family-auth module at startup; compatibility callers load it lazily.

The existing `--workers` value remains the initial target. Adaptive mode
requires an explicit `--adaptive-max-workers` value and never changes fallback
worker counts.

## Offline evidence

Commands run:

```powershell
python -m pytest tests/test_adaptive_worker_scheduler.py tests/test_adaptive_worker_coordinator.py -q
python -m pytest tests/test_csf_source_fetch_timing.py tests/test_sharded_lane_series.py -q
python -m pytest tests/test_nlm_batch.py -q
python -m py_compile bin/csf-source csf/adaptive_worker_scheduler.py csf/sharded_lane_series.py
git diff --check
```

Current verification passes `25` adaptive scheduler/coordinator tests, `43`
source-timing tests, and `43` sharded-lane tests. The combined focused and
regression run passed `111` tests. Compilation and diff checks passed. The
`csf-source fetch --help` output exposes the adaptive flags. No live result or
VPH conclusion follows from these tests.

The full `tests/test_nlm_batch.py` regression also passes `151` tests,
including the fresh-process assertion that canonical import does not load
`csf.nlm_worker_auth`.

The offline evidence is synthetic coordinator coverage over the real policy and
ledger boundaries. Existing local summaries were not replayed because they do
not contain adaptive scheduler transition or assignment events; no new run
was created to manufacture such data.

## Critical-review correction

The adversarial review found a queue-loss edge case in the coordinator: target
capacity could exceed the number of eligible worker identities after a worker
was quarantined, so queue removal could outpace assignment. The dispatcher now
uses the smaller of available capacity and eligible slot count. The regression
test is `test_bounded_dispatch_slot_count_never_exceeds_eligible_workers` in
`tests/test_csf_source_fetch_timing.py`.

Current focused verification after that correction (including the latest
recovery regression):

```text
43 passed: tests/test_csf_source_fetch_timing.py
25 passed: tests/test_adaptive_worker_scheduler.py tests/test_adaptive_worker_coordinator.py
py_compile: passed
git diff --check: passed
```

This correction improves correctness but does not provide live throughput
evidence. The latest focused suite also covers recovery when a future loses
its slot-ownership record: the coordinator fails closed rather than claiming
that an un-restorable assignment was requeued.

## Additional adversarial corrections

- `_load_worker_summary` now rejects valid JSON values that are not objects;
  malformed worker output becomes an explicit health failure instead of
  reaching coordinator `.get(...)` calls and crashing.
- Adaptive health classification now fails closed when
  `content_fetch_status_counts_total` is missing or non-dict. Auth,
  `source_age_cliff`, and launch signals remain disqualifying.
- Lane configuration now rejects duplicate explicit profiles within a lane and
  overlapping worker profiles across lanes before launch.
- Scheduler identity validation normalizes Windows state/client paths before
  comparing them, so case-only path aliases cannot pass as distinct slots.

Semantic tests cover malformed result values, missing health telemetry,
disqualifying health reasons, empty queues, duplicate batch IDs, mixed terminal
and requeued outcomes, and profile-isolation failures.

## Claim ledger

| Claim | Type | Evidence | Confidence | Falsifier | Action allowed |
|---|---|---|---|---|---|
| Adaptive policy stays within configured bounds and changes one step at a time | verified_fact | scheduler tests and source integration | High | transition outside min/max or multi-step live transition | parent review |
| Queue removal cannot exceed eligible worker identities | verified_fact | bounded dispatch helper and regression test | High | live event shows unassigned batch IDs | parent review; live validation only with authorization |
| Malformed or missing health output cannot authorize scale-up | verified_fact | parser/classifier tests and worker schema inspection | High | live scale-up after missing health fields | parent review; live validation only with authorization |
| Worker identities are isolated across configured lanes and Windows path aliases | verified_fact | lane validation, profile-overlap tests, and normalized path test | High | live duplicate profile/state/browser namespace | parent review; live validation only with authorization |
| Adaptive scaling improves sustained VPH | unsupported | no live run performed | None | controlled control-versus-adaptive comparison | no performance claim allowed |

## Parent-owned validation command

The only currently authorized live action is one minimal Pro-only canonical
smoke, and only after the read-only canonical session probe passes immediately
before launch. Do not run the historical CLI sync, refresh, cookie-copy, or
profile-check commands below; those belong to the superseded auth contract.
Free-account validation is blocked until its canonical storage file exists and
passes the same probe.

```powershell
$env:PYTHONPATH = 'P:/packages/yt-is'
$env:YTIS_NLM_AUTO_UPDATE = '0'

# Read-only canonical preflight. This must be run immediately before the
# smoke; it does not log in, refresh, copy, or mutate auth state.
python -c "from csf.nlm_client import probe_account_session; print(probe_account_session('a.hominidae', worker_id='coordinator'))"

python P:/packages/yt-is/bin/csf-sharded-lane-series `
  --lane-config P:/packages/yt-is/.logs/sharded_lane_series/adaptive_worker_pro_smoke_lanes.json `
  --output-root P:/packages/yt-is/.logs/sharded_lane_series/adaptive_worker_pro_smoke_run01_current `
  --cohort-json P:/packages/yt-is/.logs/sharded_lane_series/candidate6_telemetry_validation_run02_current/cohort.json `
  --cohort-shape captioned `
  --limit 4 `
  --batch-size 4 `
  --run-environment-label home_300mb `
  --reusable-pipeline-mode serial `
  --preserve-worker-state-root

```

Before executing it, create the fresh Pro-only lane file and point the command
at the existing frozen captioned cohort above. Do not regenerate the cohort or
fetch metadata. It must use the exact account `a.hominidae`, descriptive worker
labels such as `a.hominidae-worker-01..03`, unique per-worker state roots and
notebook titles, and adaptive settings with initial `2`, maximum `3`, and
scale-up backlog `2`. Set `YTIS_NLM_BATCH_SIZE=1` in the lane environment so
four source assignments can exercise a `2 -> 3` scale-up; the sharded runner's
`--batch-size` alone does not control the inner `csf-source` batch size. This
smoke validates launch, identity isolation, queue accounting, and scale-up
only. It is not a control comparison, throughput benchmark, or VPH claim.

## Required raw evidence

Inspect the fresh `fetch_invoked`, `adaptive_scheduler_initialized`,
`adaptive_scale_decision`, `adaptive_worker_*`, and `adaptive_assignment_*`
events plus the final `fetch_completed` selection metadata. Confirm:

- the smoke uses the existing frozen captioned cohort input scope; this run
  is not a control-versus-adaptive comparison;
- target capacity stays within min/initial/max;
- no slot or profile identity is reused while active;
- active work completes during draining and is never cancelled;
- every assignment has a stable batch ID and a terminal disposition;
- `input_batches = completed + terminal_failed + requeued + still_in_flight`;
- missing health, timeout, launch failure, and parse failure do not scale up;
- fixed-mode behavior remains covered by offline regression tests; do not infer
  a live fixed-mode result from this smoke;
- throughput is evaluated only from the established metric layer, not from
  scheduler timing or parallel-overlapped sums.

## Early abort and smoke acceptance

Abort on duplicate/lost batch IDs, missing required telemetry fields, identity
reuse, target bounds violation, active-work cancellation, unbalanced accounting,
or any auth/environment/worker-shape mismatch. Accept this smoke only as a
live-path and telemetry validation if those gates pass. It does not promote an
adaptive configuration, authorize a throughput claim, or replace a future
current-control comparison.

## Known limitations

Coordination is per process and per lane. Parent-process crash recovery is not
implemented. Untrustworthy worker results (launch, timeout, auth, missing
health, or parse failures) are explicitly requeued under the same stable batch
identity; ordinary content failures and source-age cliffs remain terminal and
must not trigger scale-up. Live validation and any VPH decision remain
parent-owned.
