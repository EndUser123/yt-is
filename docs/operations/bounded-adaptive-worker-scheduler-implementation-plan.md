# Bounded Adaptive Worker Scheduler Implementation Plan

Status: offline implementation complete. The pure policy, opt-in
`bin/csf-source` integration, assignment ledger, telemetry, and optional lane
configuration are implemented in the current worktree. No live NotebookLM run,
external fetch, staging, commit, or push is authorized by this document.

Adversarial review correction: dispatch is bounded by both available target
capacity and eligible worker identities, preventing queue loss after worker
quarantine. This is covered by
`tests/test_csf_source_fetch_timing.py::test_bounded_dispatch_slot_count_never_exceeds_eligible_workers`.
The review also required malformed worker summaries and missing health telemetry
to fail closed, and lane validation now rejects duplicate or cross-lane worker
profiles before launch. Scheduler path identities are normalized for Windows
case aliases before uniqueness checks.

Known limitations retained by design: coordination is per process and per
lane, and parent-process crash recovery is not implemented. Untrustworthy
worker results are explicitly requeued under the same stable batch identity;
ordinary content failures remain terminal. Parent-owned live validation remains
a separate decision.

Audience: a lower-cost implementation LLM working under a parent reviewer.

## Objective

Add an opt-in bounded adaptive scheduler for the industrial NotebookLM worker
pool. The scheduler may increase or decrease the number of active worker slots
between safe batch boundaries, while preserving the current fixed-worker
behavior when adaptive mode is not enabled.

The first implementation goal is correctness, observability, and safe
transitions. It must not claim or assume that adaptive scaling improves
sustained VPH. Any throughput comparison is a later, parent-authorized
experiment with its own decision packet.

## Current facts to preserve

These facts were checked against the current source and are the starting
constraints, not assumptions to rewrite:

1. `csf/sharded_lane_series.py:89-105` treats `workers` as a startup value and
   requires enough NotebookLM profiles for that count.
2. `csf/sharded_lane_series.py:208-218` selects worker profiles from the
   configured list or profile prefix. Profile identity is part of the worker
   contract.
3. `csf/sharded_lane_series.py:634-685` passes the fixed lane worker count
   into the benchmark command.
4. `bin/csf-source:2576-2607` already groups queued batches across available
   worker slots.
5. `bin/csf-source:2609-2686` launches an isolated worker subprocess with a
   worker ID, NotebookLM profile, notebook title, state path, and environment.
6. `bin/csf-source:2753-2801` computes free slots from the startup `workers`
   value and creates the executor with that same fixed ceiling.
7. `bin/csf-source:3639-3675` drains industrial work until the batch queue and
   active futures are empty. The existing slot reuse is dynamic assignment,
   not runtime scale-up.
8. The wiki queue design at
   `P:/.data/wiki/concepts/queue-of-work-pattern-for-nlm-to-wiki.md:73-101`
   is a reference for hot-reloading work capacity between items. Its current
   `queue_sync.py` implementation is not a correctness oracle: it has no
   queue-specific tests and does not provide a complete lease/reclaim model.
9. The package trust-floor reviews identify unresolved shared-retry correctness
   defects. Do not combine this scheduler with a shared-retry repair or use a
   scheduler run to make industrial optimality claims.

## Scope

### In scope

- A pure scheduler policy/state module with no subprocess or NotebookLM calls.
- An opt-in integration into the industrial worker dispatch path.
- Explicit minimum, initial, and maximum active worker counts.
- Scale-up and scale-down only at batch/worker completion boundaries.
- Prevalidated worker identity capacity: profile, browser, notebook, and state
  root must be unique for every possible slot.
- Transition and ownership telemetry.
- Unit, integration, and conservation tests using fake workers and synthetic
  queues.
- Offline replay or simulation against existing summaries where useful.
- A follow-up decision packet template for a future adaptive-vs-static test.

### Out of scope

- Changing the default fixed-worker behavior.
- Automatic creation, copying, or repair of auth profiles.
- Changes to `csf/nlm_worker_auth.py` or the canonical auth storage path.
- Changes to shared-retry semantics, retry budgets, or fallback worker counts.
- Killing an active worker to scale down.
- Creating new browser profiles or NotebookLM notebooks on demand.
- Restarting a crashed parent coordinator or providing a durable cross-process
  queue. The implementation must classify an unparseable worker result
  explicitly and requeue it only under the existing stable batch identity; it
  must never silently create a new logical assignment.
- Any live NotebookLM benchmark or external metadata/API fetch.
- Claiming a higher VPH from unit tests, simulation, or historical artifacts.
- Staging, committing, pushing, deleting raw artifacts, or mutating databases.

## Required scheduler contract

Use these terms consistently in code, events, tests, and documentation:

- `min_workers`: lower bound for active capacity.
- `initial_workers`: active target at run start.
- `max_workers`: hard upper bound for the run and number of prevalidated slots.
- `target_workers`: desired active capacity selected by policy.
- `active_workers`: slots with an in-flight worker future.
- `draining_workers`: slots allowed to finish their current assignment but not
  eligible for a new assignment.
- `available_workers`: eligible slots not active or draining.
- `queued_batches`: batches not yet assigned to a worker.

Validation must fail closed when:

- any worker count is less than one;
- `min_workers > initial_workers` or `initial_workers > max_workers`;
- the profile list has fewer than `max_workers` entries;
- profile IDs are duplicated;
- state roots or notebook titles are duplicated;
- adaptive mode is requested without an explicit maximum or a proven default;
- a transition would exceed the lane/account capacity.

The existing `workers` argument must retain its current meaning when adaptive
mode is absent. Do not silently reinterpret existing lane files.

## Backward-compatible configuration shape

Use this shape unless the Phase 0 caller trace proves that a different layer
owns these values:

CLI flags for the industrial fetch command:

```text
--adaptive-workers                 opt-in switch; absent means fixed mode
--adaptive-min-workers N           default 1
--adaptive-max-workers N            required with adaptive mode
--adaptive-scale-up-backlog N      default 2
--adaptive-scale-down-backlog N    default 0
--adaptive-cooldown-s S            default 60
--adaptive-health-window N         default 2 completed worker results
```

The existing `--workers N` remains the initial worker target. In adaptive mode
it must satisfy `1 <= workers <= adaptive-max-workers`. The defaults above are
conservative test defaults, not performance claims. Every value must be copied
into the run configuration snapshot and telemetry.

For lane JSON, add only optional fields so existing files keep fixed behavior:

```json
{
  "workers": 1,
  "adaptive_workers": false,
  "adaptive_min_workers": 1,
  "adaptive_max_workers": 1,
  "notebooklm_profiles": ["profile-01"]
}
```

When `adaptive_workers` is true, `adaptive_max_workers` is required to be at
least `workers`, and the profile list must contain at least that many unique
entries. The lane runner must pass the opt-in flag and maximum through every
caller layer only after the Phase 0 trace identifies those layers. Do not add
an independent second launcher or infer missing profiles.

The policy should return explicit reason codes such as:

`disabled`, `initial_target`, `backlog_high`, `health_missing`,
`recent_failure`, `cooldown`, `at_max`, `backlog_low`, `at_min`,
`drain_active_slot`, and `quarantine_failure`.

## Policy rules

The policy must be deterministic and conservative:

1. Start at `initial_workers`.
2. Change capacity by at most one worker per decision.
3. Enforce a cooldown between transitions.
4. Scale up only when all are true:
   - queued work is above the configured backlog threshold;
   - current workers are busy or the queue is persistently growing;
   - the recent window has no disqualifying auth, source-age-cliff, or worker
     launch failures;
   - `target_workers < max_workers`.
5. Scale down only when all are true:
   - backlog is below the low-water threshold for the required number of
     observations;
   - the candidate slot is not active, or is explicitly marked draining;
   - `target_workers > min_workers`.
6. Never scale on a single noisy row. Use a bounded observation window and
   hysteresis between high- and low-water thresholds.
7. Missing health telemetry must block scale-up, not be interpreted as health.
8. A worker that is already active is never killed by a scale-down decision.
9. A failed worker slot is quarantined until its failure is classified and the
   parent policy explicitly permits reuse.
10. Static mode must not call the adaptive policy or emit adaptive transition
    events that alter existing worker assignment.

The default thresholds may be conservative and configurable, but every
threshold must be named in the run summary. Do not choose thresholds from a
single historical VPH result.

## State machine

Each prevalidated slot has one of:

`available -> starting -> active -> draining -> available`

Failure transitions are:

`starting -> quarantined` and `active -> quarantined`.

Rules:

- `starting` is emitted before subprocess launch and becomes `active` only
  after the launch is accepted by the coordinator.
- `draining` cannot receive a new batch.
- A completed future releases its slot only after its result has been recorded
  and its assignment has been accounted for.
- A subprocess failure must produce an explicit terminal assignment result or
  an explicit requeue decision. It must never disappear from accounting.
- Slot identity cannot be reused by a second future while the first future is
  present in the coordinator's ownership map.

## Telemetry contract

Add structured events without changing the existing throughput metric:

- `adaptive_scheduler_initialized`
- `adaptive_scale_decision`
- `adaptive_worker_starting`
- `adaptive_worker_started`
- `adaptive_worker_draining`
- `adaptive_worker_stopped`
- `adaptive_worker_quarantined`
- `adaptive_assignment_claimed`
- `adaptive_assignment_completed`
- `adaptive_assignment_failed`

Every transition event must include at least:

`run_id`, `lane`, `worker_id`, `target_workers`, `active_workers`,
`queued_batches`, `reason`, `policy_version`, and a monotonic timestamp.

Assignment events must include a stable assignment ID and the batch IDs. The
reducer/test helper must be able to prove:

`input batches = completed + terminal_failed + requeued + still_in_flight`

at every checkpoint and at finalization. A summary that cannot establish this
conservation equation is invalid for adaptive-mode decisions.

## Implementation phases

### Phase 0: preflight and baseline, read-only

Before editing:

1. Read the package `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, auth architecture,
   root-cause program, and the current experiment-loop contract.
2. Trace the actual caller chain from the fetch CLI to `csf-source`, the worker
   launch builder, and `dev.worker_pool.worker_main`.
3. Record the current fixed-mode behavior and existing tests. Do not infer a
   function signature from a grep hit; read the signature and callers.
4. Run only focused offline tests needed to establish the baseline. Do not
   launch NotebookLM, authenticate, fetch metadata, or modify raw artifacts.

Stop with `needs_fix` if the call graph or worker identity contract cannot be
shown from source.

### Phase 1: pure policy module

Preferred files:

- Create `csf/adaptive_worker_scheduler.py`.
- Create `tests/test_adaptive_worker_scheduler.py`.

The module must be independent of subprocesses and filesystem state. Define
small typed records for a snapshot, slot state, decision, and transition. The
policy should accept a snapshot and return a decision; it must not mutate the
caller-owned queue or launch workers.

Required tests:

- valid and invalid count ranges;
- duplicate profile/state/notebook identity rejection;
- initial target is respected;
- one-step scale-up only when all gates pass;
- no scale-up with missing health or recent disqualifying failures;
- cooldown prevents repeated scale-up;
- high-water/low-water hysteresis prevents oscillation;
- one-step scale-down marks a non-active slot draining;
- active work is never killed or reassigned;
- target never exceeds max or falls below min;
- deterministic reason codes for every decision.

### Phase 2: coordinator integration, opt-in only

Preferred file:

- Modify `bin/csf-source` only after Phase 1 tests pass.

Keep the existing fixed path unchanged. Add an explicit adaptive option and
thread the following values through the already-existing command path rather
than creating a second worker launcher:

- adaptive enabled;
- initial/min/max worker counts;
- policy thresholds and cooldown;
- policy version or configuration snapshot.

The integration is per lane: when the sharded runner starts multiple lane
processes, each process owns its own adaptive scheduler, ledger, worker
identity namespace, and health window. There is no cross-lane or
cross-account coordination. Keep those boundaries explicit and do not add a
shared coordinator until per-lane invariants and tests prove that such a
change is necessary.

Use an executor capacity of `max_workers`, but submit work only to slots whose
state is eligible under `target_workers`. Reuse the existing free-slot and
dispatch-group machinery. The adaptive policy decides eligibility; it does
not change batch contents or retry behavior.

At every future completion:

1. record the worker result and assignment accounting;
2. update slot health;
3. evaluate one policy decision;
4. apply only a legal transition;
5. dispatch new work only after transition state is recorded.

Do not change `worker_main.py` unless Phase 0 proves a required identity or
result field is missing. If it must change, keep the change limited to
transition/assignment telemetry and add a semantic test for the field.

### Phase 3: lane/config plumbing

Only after the `csf-source` coordinator is correct:

- Add optional adaptive fields to `LaneConfig` and its JSON loader.
- Preserve existing lane files byte-for-byte in behavior.
- Require `notebooklm_profiles` capacity for `max_workers`, not merely the
  initial count, in adaptive mode.
- Propagate lane, account class, profile list, state root, and notebook prefix
  without creating duplicate identities.
- Add tests for Pro and Free lanes independently, asymmetric min/max values,
  malformed configs, and expected worker-shape reporting.

Do not wire adaptive mode into the benchmark series by default. It should be
an explicit opt-in field/flag and must be visible in the environment snapshot.

### Phase 4: offline validation and documentation

Run:

```powershell
python -m pytest tests/test_adaptive_worker_scheduler.py -q
python -m pytest tests/test_csf_source_fetch_timing.py tests/test_sharded_lane_series.py -q
python -m py_compile csf/adaptive_worker_scheduler.py bin/csf-source csf/sharded_lane_series.py
git diff --check
```

Add a synthetic integration test that feeds a fixed batch queue through a fake
worker launcher and verifies:

- static mode has the old assignment sequence;
- adaptive mode scales up and down only at legal boundaries;
- no batch is duplicated or lost;
- failed and requeued assignments are distinguishable;
- every transition event has the required fields;
- a missing transition or conservation record invalidates the summary.

Update the operations documentation with the state machine, configuration
fields, default-off behavior, and known limitations. Do not update the test
registry with a VPH winner because this phase produces no live throughput
result.

### Phase 5: parent-owned validation decision packet, do not execute

After implementation and offline tests, prepare a decision packet containing:

- exact code/harness change;
- static control command and adaptive candidate command;
- fresh output roots;
- worker shape and account/profile matrix;
- raw event and summary paths to inspect;
- falsifiers for duplicate/lost work, identity reuse, transition omissions,
  health-gate bypass, and VPH regression;
- smoke early-abort gates;
- promotion rule that requires valid artifacts and a predeclared comparison;
- explicit statement that no live run was executed by the implementation goal.

The parent decides whether to authorize a live validation. The implementer must
return `Parent handoff: decision_required` rather than launching it.

## Adversarial review checklist

Before reporting `ready_for_parent_review`, try to falsify these claims:

| Claim | Required attack | Falsifier |
|---|---|---|
| Static mode is unchanged | Compare command construction, slot selection, and focused regression tests | Any changed assignment or default flag behavior |
| Scale-up is safe | Simulate backlog growth, a worker finishing during a transition, and duplicate launch attempts | Duplicate slot, identity reuse, or unaccounted batch |
| Scale-down is safe | Drain an active slot and inject new work during the drain | Active work killed or reassigned |
| Accounting is trustworthy | Force launch failure, timeout, result parse failure, and requeue | Conservation equation does not balance |
| Health gates are real | Omit health fields and inject source-age/failure events | Scheduler scales up on missing or disqualifying evidence |
| Adaptive policy is stable | Run repeated snapshots around both thresholds | Oscillation or repeated transitions inside cooldown |
| VPH conclusions are valid | Inspect metric layer and parallel-overlap handling | Scheduler telemetry is mistaken for a throughput decomposition |

Include a claim ledger in the final handoff:

`Claim | Type | Evidence | Verification | Confidence | Falsifier | Action allowed`

Any remaining inference authorizes more evidence only, not a live run or a
claim that adaptive scaling is optimal.

## Final implementer handoff format

The simpler LLM must finish with:

- Decision: `ready_for_parent_review`, `needs_fix`, `blocked`, or
  `decision_required`.
- Objective and phases completed.
- Files read and files changed.
- Exact commands and exit statuses.
- Tests and semantic behaviors proven.
- Synthetic artifacts created, if any.
- Static-mode compatibility result.
- Claim ledger and adversarial review findings.
- Explicit confirmation: no live NotebookLM run, external fetch, raw artifact
  mutation, stage, commit, or push.
- Remaining risks and the smallest next action.
