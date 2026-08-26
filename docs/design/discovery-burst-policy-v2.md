# Discovery burst-policy-v2 (SHADOW) — design

Status: implemented as an explicitly selectable SHADOW policy on
2026-08-25. burst-policy-v1 REMAINS the production default until v2
passes a completely new unseen formal holdout. This document is the
durable, target-name-free design record (the private calibration
artifacts live outside git under
`P:/.data/yt-is/ef/concept-discovery-calibration/`).

## Problem with burst-policy-v1

Formal holdout-v4 (consumed; training-only forever) verdict FAIL:
candidate recall 0.714 but emerging recall 0.000/42, policy separation
0.0. Root defects established mechanically:
1. `source_types >= 2` normalizes acquisition modality (notebooklm,
   ytdlp, selenium, whisper all map to `youtube`), not publisher
   independence — it structurally blocked every single-modality target.
2. Fixed 30-day hard windows made all 12 candidate misses (<2
   observations in every window) and were perturbation-fragile.
3. The flat count/ratio/channel conjunction family cannot separate
   targets from matched negative controls at useful recall
   (preregistered 216-config grid, plan bb1c0299...: max emerging recall
   0.238; NO_SIMPLE_POLICY_SUPPORTED).

## Calibration lineage

- Consumed holdout-v4, TRAINING_DIAGNOSTIC_ONLY.
- Simple-family calibration: plan sha256 bb1c0299... →
  NO_SIMPLE_POLICY_SUPPORTED (publication aec340a5 lineage).
- Stateful bakeoff: plan sha256 a04ee198... →
  BAYESIAN_EPISODES_SUPPORTED (publication da53eba7).
  Selected configuration (all 5 grouped CV folds):
  window 60d vs prior 180d, multiplier 1.5, threshold 0.80, channel
  floor 1, persistence episodes. Full-v4 training: emerging recall
  0.5476, control emerging 0.0714, separation 0.4762; OOF: 0.5295 /
  0.0717 / 0.4578.
- Donors: Kleinberg via a local port of hitalex/pybursts (MIT; rejected
  as primary — never qualified in any fold); BOCD rejected (sparse
  streams). See docs/handoffs/interest-intelligence/
  discovery-burst-model-donors.md.

## Candidate rule

For each DISTINCT supporting EU of age d days: weight = 2^(-d/30).
Candidate iff sum(weight) >= 1.5 AND lifetime distinct EUs >= 2.
Duplicates never increase support (distinct EU counted once at its
earliest mention). Training: recall 0.9048 at 79.1 mean candidates per
checkpoint (v1: 0.714 at 67.0).

## Bayesian model

Counts = distinct EUs. Recent window 60 days (exposure 2.0), baseline
the preceding 180 days (exposure 6.0), 30-day time unit. Independent
prior lambda ~ Gamma(0.5, 0.5) per rate — FIXED, no empirical-Bayes
fitting. Signal positive iff P(lambda_recent > 1.5 * lambda_baseline |
evidence) >= 0.80 AND independent channels >= 1 (channel_id = publisher
identity in the recent window). The signal is computed UNGATED by the
candidate rule (episodes open on the first positive signal); the
candidate gate controls concept/monitoring persistence. `source_types`
is an AUDIT FEATURE ONLY — never a gate in v2.

## Numerical method

P(X > mY) for X ~ Gamma(a_r, rate b_r), Y ~ Gamma(a_b, rate b_b):
with c = m*b_r/b_b, P = 1 - I_{c/(1+c)}(a_r, a_b) (beta-prime
relationship; scipy.special.betainc). Validated against the calibrated
256-node Gauss-Legendre quadrature (with y=t^2 substitution): max
absolute error 6.6e-13 across all v4 training points, the full
(k, k_base) sweep to 40, and boundary cases at 0.70/0.80/0.99; ZERO
classification-decision differences. Closed form adopted
(NUMERICAL_METHOD = "beta-incomplete-closed-form"). This was numerical
equivalence only — no parameter changed.

## Episode state machine

Persistence is the decisive component (ablation: persistence OFF →
control emerging rate 0.246, failing the <= 0.20 axis).
- OPEN: first positive Bayesian signal (episode row in the EXISTING
  registry trend_episodes table; no new state store).
- PROMOTE to emerging: positive at two consecutive available policy
  evaluations no more than 30 days apart, OR posterior >= 0.99 AND
  channels >= 2.
- CONTINUE: positive signal OR posterior >= 0.70.
- COOL: two consecutive negative evaluations (episode closed with
  state 'cooled'; historical episodes never deleted).
Duplicate evaluations at the same as_of are idempotent (same-date evals
are ignored for transition purposes; deterministic episode ids).

## Source/channel semantics

`independent_source_count` = distinct channel_id (publisher) among
recent-window distinct EUs. `source_diversity` = normalized
acquisition-modality count (audit only). Acquisition modality never
masquerades as publisher independence.

## Registry mapping

Existing tables only. Concepts: metadata carries v2_support,
v2_candidate, v2_lifetime, v2_k_recent, v2_posterior, v2_evals (last
two evaluation records) plus the v1-style audit fields. Episodes:
started_at (open), peak_at (max posterior), last_active_at, ended_at,
state, recent_rate (k_recent/exposure), baseline_rate, acceleration
(posterior probability), source_diversity (audit),
independent_source_count (channels), novelty_score (unchanged
first_seen<=90d rule), evidence_json, policy_version
"burst-policy-v2". Ranking: v1's world_signal score is PRESERVED (v2
ranking calibration is OPEN); lifecycle promotion never depends on it.

## Replay semantics

Everything is a pure function of observations dated <= as_of (no future
evidence can influence a replay). Identical ordered replay sequence →
identical concepts and episodes (deterministic ids). Idempotent re-scan
at the same as_of. v1 and v2 replay independently in separate
registries. DOCUMENTED asymmetry: persistence is scan-history dependent
— a one-shot scan at date D is NOT equivalent to a sequential replay
ending at D (the latter can promote via consecutive checkpoints). This
is intentional; the formal evaluator always replays the full
checkpoint sequence.

## Shadow-before-promotion rule

scan_internal(policy_version=...) accepts "burst-policy-v1" (default)
and "burst-policy-v2" explicitly; unknown versions raise. The CLI
(--policy-version) defaults to v1. The formal evaluator pins
burst-policy-v2 from its freeze receipt and NEVER uses the runtime
default. Promotion to default happens only after an unseen formal
holdout PASS, by separate architect decision.

## Formal promotion protocol

Freeze (evaluator-v3 + burst-policy-v2 + all material code hashed,
including python/numpy/scipy versions and the numerical method) →
FRESH CURATOR creates a new unseen holdout → DIFFERENT FRESH EVALUATOR
consumes it with --label FORMAL → PASS promotes v2 to default; FAIL
returns to architecture without contaminating the gate. Consumed
holdout-v4 can never serve as promotion evidence.
