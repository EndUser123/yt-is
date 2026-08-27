---
agent: zcode
host: yt-is (package)
status: FROZEN
frozen_before_model_outcomes: true
experiment: temporal-emergence-modelgen-v1
---

# Episode Semantics Contract — Temporal Emergence (slice B)

Frozen 2026-08-26 BEFORE any model-family bakeoff outcome was computed or
inspected. This contract is the meaning layer for the bakeoff preregistered
in `temporal-emergence-modelgen-preregistration-20260826.md`. If a
mechanism cannot express these distinctions, it is rejected regardless of
its numbers.

## Scope

TEMPORAL CONCEPT EMERGENCE only (Discovery slice B). Not overall Discovery.
Preserved distinctions carry from the architect reconciliation:
Concept != Interest; world signal != personal relevance; durable memory !=
durable salience; temporal emergence != recommendation.

## State semantics

An entity's emergence episode is a STATEFUL PROCESS over time, not a
scalar. The states:

| State | Meaning | Mechanical condition |
|---|---|---|
| BURST_DETECTED | Evidence indicates unusual current activity relative to its own baseline. | Gamma-Poisson rate-change posterior >= signal threshold with channel floor (the existing burst-policy-v2 signal). |
| CANDIDATE_EPISODE | A temporal episode has OPENED at a specific instant. | First time BURST_DETECTED becomes true; that instant is `episode_opened_at`. |
| CONFIRMED_EMERGENCE | NEW evidence arriving STRICTLY AFTER `episode_opened_at` confirms continued emergence. | Confirmation predicate evaluated ONLY over evidence with evidence_time > episode_opened_at (see preregistration variants). |
| CONTINUING | A confirmed episode keeps receiving sufficient post-open support. | Confirmed AND signal still positive (or continue-threshold hold), otherwise falls to COOLING. |
| COOLING | Support has weakened. | Two consecutive non-positive evaluations (v2 cooling rule), state-driven not snapshot-only. |
| DORMANT / CLOSED | Episode no longer active. | Confirmation deadline expired unconfirmed, or COOLING persisted -> episode closed (existing trend_episodes close). |

Explicitly NOT collapsed into one scalar: an entity may be
BURST_DETECTED without CANDIDATE_EPISODE being confirmable, and
CANDIDATE_EPISODE without CONFIRMED_EMERGENCE. No single score expresses
all six states.

## THE INVARIANT (load-bearing)

Evidence used to OPEN an episode may not by itself CONFIRM it.

Formally: the confirmation predicate at any time t receives as input only
evidence with `evidence_time > episode_opened_at` (`trigger_evidence_cutoff
= episode_opened_at`). Pre-open evidence may keep the detector positive;
it can never satisfy the confirmation predicate alone.

This repairs the established v2 failure mechanism
(V2_SELECTIVITY_FAILURE_CONFIRMED 2026-08-26): v2's recent-60d window at
anchor checkpoints still contains the pre-anchor mass that CAUSED the
episode trigger, so two consecutive positive evaluations complete before
post-anchor silence matters. Historical support that opened the episode
was reused as confirmation that the episode continued.

## Valid-time policy (unchanged from production)

- Authoritative evidence time = published/source time
  (`substr(COALESCE(NULLIF(eu.published_at,''), eu.captured_at),1,10)`),
  exactly as production `_entity_observations`.
- Undated evidence (both fields empty) is EXCLUDED by production today;
  every arm inherits this exclusion identically and reports it. No arm
  silently assigns ingestion/captured time as event time for undated rows.
- E4 Discord date-policy remains out of scope; discord rows use whatever
  valid time production already gives them. No E4 fix here.

## Episode-open definition

Two semantics are distinguished and both reported:

1. PRECISE (evaluator replay): `episode_opened_at` = earliest date t such
   that BURST_DETECTED(t) is true, where t ranges over the exact boundary
   set of the piecewise-constant signal function
   ({first_evidence_date} ∪ {first_evidence_date + recent_window_days}).
   This is the most temporally precise definition supported by existing
   evidence; the signal is constant between boundaries, so nothing between
   two consecutive boundary dates can change the answer.
2. CHECKPOINT-SAMPLED (production cadence): `episode_opened_at` = the
   first evaluation day where BURST_DETECTED is true when evaluations run
   on scan days only. Precision is bounded by scan cadence.

The bakeoff arms open episodes under the PRECISE definition and
additionally report the checkpoint-sampled variant, demonstrating whether
the production cadence matches the precise definition (required proof item
for any supported family).

## Look-ahead prohibition

Every state decision at time t depends only on evidence with
`evidence_time <= t`. No future evidence may alter whether an earlier
checkpoint was considered confirmed when replayed as-of that checkpoint.
Tested explicitly (future-leak fixture), asserted deterministically.

## Independence semantics (publisher corroboration)

Independent-publisher identity uses the Concept/KG E1 accounting semantics
(`publisher_identity`: discord collapses to guild/server; aggregator/newsletter
sources and missing ids are `__UNKNOWN__`; YouTube-class modalities share
their UC channel id so acquisition modality never masquerades as publisher
diversity).

- Publisher corroboration is a MODEL DIMENSION TO TEST, never an axiom,
  and never a silent hard gate in the base confirmation definitions.
- Explicit UNKNOWN publisher identity remains UNKNOWN: unknown-identity
  evidence contributes toward generic confirmation predicates but can
  NEVER count as publisher corroboration (it does not corroborate).
- Same-publisher repeats are allowed to satisfy plain counting variants
  ("repeated coverage by one outlet" is real continued emergence); the
  corroboration dimension is measured separately.

## What would falsify this contract

A model that mechanically requires post-open evidence for promotion yet
still shows explicit-negative confirmed-emergence rates statistically
indistinguishable from positive confirmed-emergence recall would show the
contract is not capturing the phenomenon and must be revised before any
freezing of implementation.
