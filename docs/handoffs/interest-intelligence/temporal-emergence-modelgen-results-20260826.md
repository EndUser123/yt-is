---
agent: zcode
host: yt-is (package)
experiment: temporal-emergence-modelgen-v1
decision: NO_NEW_MODEL_SUPPORTED
review: fresh-context SHIP_WITH_NOTES (2026-08-26)
---

# Results — Temporal Emergence Model-Generation Bakeoff v1

Discovery slice B ONLY. Consumed evaluator-v4 case-control set used as
TRAINING_DIAGNOSTIC (42 scorable positives; 124 explicit negatives
paired to scorables). No formal run, no ledger write, no new holdout,
production default unchanged.

Frozen first: `episode-semantics-contract-20260826.md` +
`temporal-emergence-modelgen-preregistration-20260826.md` (committed
5d93fe2c BEFORE any outcome existed). Amendment A1 (in-file) records two
disclosed mechanics: parity re-anchoring to the same-day frozen-evaluator
rerun after catalog drift, and completion of the preregistered re-arm
clause, both before their respective outcomes were inspected.

## Harness acceptance (parity)

| Reference | positive emerging recall | explicit-negative emerging rate |
|---|---|---|
| Published morning artifact (eval-…T074407) | 0.8333 (35/42) | 0.581 (72/124) |
| Same-day frozen-evaluator rerun on current catalog (eval-…T202847) | 0.8571 (36/42) | 0.6048 (75/124) |
| This harness (of record) | 0.8571 (36/42) | 0.6048 (75/124) |

Per-subject equality vs the same-day frozen-evaluator rerun: 0/124
negative mismatches; candidate recall identical 42/42. Morning-vs-current
deltas are CATALOG DRIFT (eu/kg re-ingestion during 2026-08-26), named
per-sid in PARITY_DRIFT_PUBLISHED_VS_CURRENT_NEGATIVES (VRAM, Google
Drive, Margin requirement, Microsoft 365, Somalia-reversed, and target
"Claude Code"). A live registry replay of VRAM independently reaches
emerging at T+60 today, confirming drift rather than engine divergence.

## Aggregates by arm (of-record run; re-arm accounting; T+60 horizons)

Arm A reference: recall 0.8571 / negative 0.6048 / separation 0.2523.

| Family | pos confirmed | neg confirmed | separation | median delay d |
|---|---|---|---|---|
| armB EU1-W30 | 0.7381 (31/42) | 0.5081 (63/124) | 0.2300 | 134 |
| armB EU2-W60 | 0.5952 (25/42) | 0.3548 (44/124) | 0.2404 | 155 |
| armB BUCKETS-W120 | 0.5714 (24/42) | 0.4113 (51/124) | 0.1601 | 112 |
| armB POSTERIOR-EXCL-W30 | 0.0714 (3/42) | 0.1210 (15/124) | −0.0495 | 1358* |
| armB CHANNELNEW-W30 | 0.4286 (18/42) | 0.3226 (40/124) | 0.1060 | 115.5 |
| armC (=EU1-W30 + state machine) | 0.7381 | 0.5081 | 0.2300 | 134 |
| armD two-signal | 0.3571 (15/42) | 0.0968 (12/124) | 0.2604 | 30 |

*POSTERIOR-EXCL delay is a reporting artifact under re-arm chains
(measured from FIRST opening, confirmation may land on a later attempt);
the family fails every bar regardless.

First-pass single-shot accounting (no re-arm; superseded by Amendment A1
completion but reported for the frontier): neg rates collapsed to
0.040–0.169 while positive recall sat at 0.310–0.381. Together the two
regimes bracket the trade-off: strictness buys selectivity and starves
recall; loosening restores recall and re-admits negatives. No point in
the frozen variant set dominates v2 on BOTH axes with margin.

Episode counters (positives): opened 42/42; confirmed by >=1 of the five
B variants 32/42; expired-unconfirmed-by-any-B 10/42 (the aggregate.json
episodes_confirmed / expired_unconfirmed counters are EU1-W30-scoped:
31 confirmed / 11 expired; reviewer-noted scoping, retained verbatim);
Replay determinism: byte-identical double run.
Perturbation20 prefix retention: armD confirmed 13/15 = 0.867; armA
emerging 32/35 = 0.914. Leave-one-out analytic envelopes computed per
family (stability_leave_one_out in aggregate.json).

Known minor debts (reviewer findings, none decision-flipping):
apply_decision_bars carries an always-true D4 placeholder;
separation bar D3 compares against the LIVE arm A separation of the run
(0.2523) rather than the frozen-morning constant 0.253 — direction-
immaterial here but should be pinned explicitly in any successor packet;
sensitivity-boundary-exits.json was assembled analytically outside the
script.

## Synthetic discriminating fixtures (all PASS before corpus work)

CASE P confirms under EU1/EU2/CHANNELNEW; CASE N never confirms under any
variant; Arm A promotes BOTH cases (documented reference failure);
future-leak inert (inject_future transform); duplicate-publisher clones
never create corroboration (causal check 0 defect flips across 14
subjects); undated evidence dropped identically; unknown publishers stay
UNKNOWN.

## Why the mechanism fix still yields NO_NEW_MODEL_SUPPORTED

The trigger-reuse failure IS mechanically fixed (fixtures prove it;
segment postopen_activity shows nearly all subjects have SOME post-open
evidence: 41/42 positives, 123/124 negatives — presence alone separates
nothing; its DENSITY and channel-novelty carry all signal). But under the
frozen numeric bars,

- D1 material negative drop AND D2 recall >= 0.50 AND D3 separation > A
  are satisfied simultaneously by NO family:
  - high-recall variants (EU1/EU2/BUCKETS/C) fail D1 (neg 0.35–0.51);
  - selective variants fail D2 (CHANNELNEW 0.429, armD 0.357).

Criteria 1 (mechanism fixed), 5 (deterministic replay), 6 (clearer
semantics), 7 (n-1 stability) hold for several families; criteria 2–4
cannot be co-satisfied inside this model generation on these labels.
This matches the earlier blinded semantic audit: raw Tier-C persistence
labels diverge from time-local emergence semantics, so ANY strictly
time-local definition trades against this ground truth.

## Decision

NO_NEW_MODEL_SUPPORTED (preregistered mapping applied verbatim).
burst-policy-v3 / episode-confirmation shadow implementation NOT built.
Production default remains burst-policy-v1. New unseen formal holdout
still pending; holdout-v4 remains training-only forever.

## Recommendation fork for architect (data-grounded, not gate-flavored)

O1. PRECISION profile now: if Discovery Radar wants fewer-false-emerging
ranking diagnostics without lifecycle promotion, armD's profile
(pos 0.357 / neg 0.097 / sep +0.008 over v2, perturb-retained 0.867) is
shippable as pure SHADOW read-model scoring later — no ground truth
change required.
O2. Ground-truth semantics revision first: the label-vs-semantics
divergence (blinded audit 1/4 positives genuine) caps ANY time-local
model; a label-repair packet plausibly moves the reachable frontier more
than another policy family.
O3. Evidence-density gap: median confirmation delays (~4 months) show the
corpus rarely carries fresh post-trigger coverage; if early emergence
matters operationally, that argues for acquisition sources with denser
valid-time coverage, not threshold reshaping.

## Artifacts

P:/.data/yt-is/ef/concept-discovery-calibration/temporal-emergence-modelgen-v1/
(aggregate.json incl determinism + LOO + perturbation; metric-rows.json
166 rows; segments.json; counterfactuals.json; decision-bars.json;
run-config.json w/ contract/prereg shas; sensitivity-boundary-exits.json).
Reference receipts: P:/tmp/tm-parity-eval/eval-20260826T202847-NON_BLIND_DIAGNOSTIC/
(frozen-evaluator rerun; receipt-pinned).
