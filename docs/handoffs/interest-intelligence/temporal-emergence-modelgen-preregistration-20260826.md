---
agent: zcode
host: yt-is (package)
status: FROZEN
frozen_before_model_outcomes: true
experiment: temporal-emergence-modelgen-v1
contract: episode-semantics-contract-20260826.md
---

# Preregistration — Temporal Emergence Model-Generation Bakeoff v1

Frozen 2026-08-26 BEFORE any model-family outcome was computed or
inspected. Data status honored: the consumed evaluator-v4 case-control set
(42 positives; 123 VALID paired explicit negatives + 1
NEGATIVE_CONTROL_INSUFFICIENT) is TRAINING_DIAGNOSTIC ONLY — never formal,
never promotion evidence, never a new holdout. No new holdout is curated or
consumed in this session.

## Arms (fixed)

ARM A — V2 REFERENCE. Existing burst-policy-v2 unchanged, replayed at the
six evaluator checkpoints (T-30, T, T+7, T+14, T+30, T+60) with stateful
consecutive-evaluation promotion. Reference failure behavior only.
Acceptance for the harness itself: must reproduce the established v4
diagnostic aggregate (positive emerging recall 0.833, explicit-negative
emerging rate 0.581) within rounding of the artifact, proving replay
equivalence before anything else is believed.

ARM B — POST-TRIGGER CONFIRMATION. Opening = burst-policy-v2 signal
crossing under the contract's PRECISE definition
(`episode_opened_at`, trigger_evidence_cutoff = opened_at). Promotion to
CONFIRMED_EMERGENCE requires post-open evidence only (strictly
> opened_at), within a bounded confirmation deadline from the variant
matrix below. Pre-open evidence NEVER enters confirmation. Deadline expiry
-> candidate expires unconfirmed (episode may re-arm only via the v2 cool
rule then a fresh positive crossing; the new open resets the cutoff).

ARM C — EXPLICIT EPISODE STATE MODEL. Same opening and confirmation core
as B, represented as an explicit event-driven state machine with fields:
episode_opened_at, trigger_evidence_cutoff (= opened_at),
confirmation_after (= opened_at, strict), confirmed_at, last_support_at,
cooling_started_at, closed_at. Transitions are evidence/event-driven;
deterministic as-of replay by construction (pure function of obs <= t).
This arm tests whether explicit state adds anything beyond B's predicate:
CONTINUING persistence (stays confirmed until COOLING) and CLOSED
lifecycle accounting. If its metrics equal B's on every preregistered
axis, B's simpler form is preferred (packet criterion 7).

ARM D — TWO-SIGNAL MODEL. Separates dimensions: burst_strength =
v2 signal at opening (burst axis); persistence = an INDEPENDENT detector
over the post-open stream alone: >= 2 distinct post-open EUs inside some
trailing-30d window contained in [opened_at, opened_at + 60d]. A concept
with high burst + low persistence stays unconfirmed. Tests whether the
two-dimensional factorization discriminates better than single-predicate
confirmation.

## Confirmation variant matrix (bounded; no open-ended search afterward)

| Variant | Confirmation predicate (post-open evidence only) |
|---|---|
| CONF-EU1-W30 | >= 1 post-open EU, deadline open+30d |
| CONF-EU2-W60 | >= 2 post-open EUs, deadline open+60d |
| CONF-BUCKETS-W120 | >= 2 post-open EUs in distinct calendar months, deadline open+120d |
| CONF-POSTERIOR-EXCL-W30 | Gamma-Poisson posterior computed over post-open counts only (same prior/baseline machinery) >= continue_threshold 0.70, evaluated at open+30d |
| CONF-CHANNELNEW-W30 | CONF-EU1-W30 plus the confirming EU originates from a channel_id absent among pre-open recent-window EUs |

Considered and NOT tested as separate arms (boundedness; each is
expressible as a combination/limit of the above axes): publisher-count
hard gates (tested as the corroboration SENSITIVITY dimension instead),
posterior-continues-above-signal-threshold-excluding-trigger (subsumed by
CONF-POSTERIOR-EXCL with threshold 0.80 reported secondarily), candidate-decay
recursions (D30 decay remains the shared candidate substrate, unchanged
across arms).

## Metrics (all preregistered; identical definitions across arms)

Per arm over the training-diagnostic cohorts:

1. positive candidate recall (candidate at any checkpoint by T+60);
2. positive confirmed-emergence recall;
3. explicit-negative candidate rate;
4. explicit-negative confirmed-emergence rate (SELECTIVITY AUTHORITY);
5. separation = (2) - (4); Wilson 95% intervals on rates;
6. median confirmation delay (days, open -> confirmed_at);
7. mean candidate lifetime (checkpoints in CANDIDATE_EPISODE);
8. counts: episodes opened / confirmed / expired-unconfirmed;
9. perturbation10 / perturbation20 retention at T+30 prefix replay
   (candidate + confirmed retained, deterministic seed = sha256(subject id));
10. publisher-corroboration sensitivity: rerun each selected-family
    variant requiring >= 2 distinct KNOWN publishers among post-open
    confirming EUs; report delta only (never silently gated);
11. replay determinism: full pipeline run twice -> byte-identical outcome
    artifacts (sha256 equality).

Segmentations (REQUIRED - prove mechanism fixed, not aggregate shifted):

- pre-anchor activity strength: terciles of trigger mass k_recent(open);
- post-anchor activity: buckets {0 post-open EUs by T+60} vs {>=1};
- publisher count (KNOWN publishers lifetime, UNKNOWN excluded): 1 vs 2+;
- channel count (lifetime): 1 vs 2+;
- evidence age: terciles of (anchor_T - first EU date) span.

Synthetic discriminating fixtures (mechanical acceptance BEFORE corpus):
CASE P (historical activity + genuinely new post-open support) vs CASE N
(identical historical activity + none): every non-A arm MUST confirm P
and NOT confirm N per its own variant definition; Arm A expected to
promote both (documented failure reference). Plus: future-leak fixture
(decisions invariant to evidence dated after evaluation time); duplicate
publisher fixture (cloned EU, same channel/publisher -> CHANNELNEW does
not corroborate; plain counting variants may count it as continued
coverage); undated-evidence fixture (dropped identically everywhere).

Counterfactual suite on representative diagnostic episodes: remove
post-open support -> confirmation disappears; retain trigger evidence
only -> episode opens, never confirms; add post-open support ->
confirmation occurs; move trigger-period EUs into post-open period ->
directional change toward confirmation; duplicate same-publisher
evidence -> no false independent corroboration under CHANNELNEW;
independent-publisher addition -> behavior reported under sensitivity
dimension; replay at old as-of after adding future support -> zero leak.

## Decision mapping (preregistered bars)

A family is SUPPORTED only if ALL hold (packet criteria made numeric):

- D1 mechanism: negative confirmed-emergence rate <= 0.50 x Arm A's
  0.581 AND <= 0.35 absolute ("materially falls");
- D2 positives useful: positive confirmed recall >= 0.50 absolute;
- D3 separation > Arm A's 0.253 (point estimates);
- D4 perturbation20 confirmed-retention >= 0.5;
- D5 replay determinism exact; no look-ahead (fixture must pass);
- D6 semantics simpler/equal in clarity to v2's accidental threshold
  behavior (judged structurally in review; recorded);
- D7 improvement not attributable to individual target names: result
  holds when each single subject with the largest absolute negative-rate
  contribution is dropped one-at-a-time (n-1 stability).

Outcome selection: pick the SIMPLEST family meeting all bars with the
largest negative-rate drop among simple ties (ties broken by fewest
variants/components). If B qualifies and C/D add no axis beyond B,
outcome is POST_TRIGGER_CONFIRMATION_SUPPORTED; if the explicit state
layer (C) is what carries durability/replay requirements not expressible
as B's predicate, EPISODE_STATE_MODEL_SUPPORTED; if the two-signal
factorization (D) alone meets bars that B fails, TWO_SIGNAL_
MODEL_SUPPORTED. NO_NEW_MODEL_SUPPORTED if every family fails any bar;
INSUFFICIENT_EVIDENCE if cohort support degrades below sufficiency
(<40 valid negatives).

Implementation consequence if supported: SHADOW ONLY as
`episode-confirmation-v1` (name chosen because the architecture is no
longer fundamentally a "burst policy"), extending the existing
trend_episodes/state-event substrate; deterministic ids; append-only
state events; idempotent; as-of replay; no mutation of burst-policy-v1;
production default UNCHANGED; no Interest/Recommendation changes.

## What this experiment must NOT do

No tuning of v2 thresholds; no new holdout curation or consumption; no
formal ledger writes; no production default change; no Interest inference
or Recommendation changes; no concept extraction or label modifications;
no E4 discord date-policy work.
