---
agent: zcode
host: yt-is (package)
session: sess_22f9a598-5a68-4270-bfc3-7ab947ed97b7
experiment: temporal-emergence-modelgen-v1 REPLAY (corrected evidence time)
corrected_time_decision: NO_NEW_MODEL_SUPPORTED
causal_interpretation: TIME_CORRECTION_NO_MATERIAL_EFFECT
status: CORRECTED_TIME_REPLAY_READY_FOR_FRESH_REVIEW
review: NOT performed (fresh reviewer required)
---

# Results — Temporal Emergence Corrected-Time Replay (2026-08-27)

Replay of the FROZEN model-generation experiment
(`temporal-emergence-modelgen-preregistration-20260826.md`, arms
unchanged, thresholds unchanged, no new labels) after the production
evidence-time correction. Not a new model search. Production default
remains burst-policy-v1; no arm promoted.

## Replay integrity (frozen in commit e264ffcd)

- Substrate: `1f72ee16` (local main == origin/main); repair lineage
  `49e7ba72 -> 2b873f2c -> e2a95acf -> ad218261` verified ancestral.
- Harness, its tests, the frozen evaluator, and burst-policy-v2 are
  git-blob IDENTICAL to the original-run commit `761b688a`. The ONLY
  changed dimension is evidence valid-time semantics, entered through
  the substrate database (materialized corrected reader:
  published_at -> eu_time_recovery.valid_start -> EXCLUDE; 254,524
  dated / 14,730 excluded), never through code changes.
- One shared catalog file snapshot (sha256 9c050a1e…) served both runs;
  corrected shadow sha256 fd696c78….
- Seal BEFORE historical read: `corrected/SEALED_OUTPUTS.json`
  sha256 63b4d8d18a9d0e734c005e1046f5bb8a01416562a234c380a87940f6bd98be72.
  Historical artifacts were opened only after sealing.

## Machinery equivalence (pre-repair shadow run)

The frozen harness on the pre-repair reader (verbatim catalog snapshot)
reproduces the of-record historical run SUBJECT-BY-SUBJECT: 0 flips on
every family over all 165 subjects; armA aggregate identical (36/42
recall, 75/124 negative rate, separation 0.2523). The frozen parity
gate passes exactly (pass_current_reference). Therefore the entire
corrected-vs-historical delta below is attributable to the
time-semantics dimension alone.

## Corrected-time decision (ORIGINAL mapping, verbatim)

**NO_NEW_MODEL_SUPPORTED.** No family passes the frozen bars in the
corrected run: D1 fails for all high-recall families (neg 0.379-0.484),
D2 fails for all selective families (POSTERIOR-EXCL 0.071, armD 0.357,
CHANNELNEW 0.405), and D3 (separation > the run's own armA) fails for
EVERY family because corrected-time armA separation ROSE to 0.4213.
Full mechanical bar table (D1-D5, D7) in `artifacts/SEALED_OUTPUTS.json`;
determinism identical; inject_future no-leak fixtures pass; cohorts
42 positives / 124 paired negatives.

## Causal classification (rule frozen before comparison)

**TIME_CORRECTION_NO_MATERIAL_EFFECT** — no family improves its
negative confirmed rate or separation by >= 0.10 absolute (max family
improvement: armB_EU1-W30/armC neg −0.024); qualified sets are empty in
both runs (no frontier reversal).

Reported observation (narrative, not a rule change): the REFERENCE arm
itself improves materially under corrected time (negative emerging rate
0.6048 -> 0.4597 = −0.145; separation 0.2523 -> 0.4213 = +0.169). The
time correction mostly cleans the v2 baseline's false emergences, which
RAISES the D3 bar every challenger must beat — the correction makes the
frozen frontier harder, not softer.

## Per-arm corrected-time metrics (vs historical)

armA: cand recall 41/42, emerging recall 37/42 (0.881), neg cand
85/124, neg emerging 57/124 (0.4597), separation 0.4213.

| Family | pos confirmed | neg confirmed | separation | median delay d |
|---|---|---|---|---|
| armB EU1-W30 | 0.7381 (31/42) | 0.4839 (60/124) | 0.2542 | 111 |
| armB EU2-W60 | 0.5714 (24/42) | 0.3790 (47/124) | 0.1924 | 56.5 |
| armB BUCKETS-W120 | 0.5238 (22/42) | 0.3952 (49/124) | 0.1286 | 105.5 |
| armB POSTERIOR-EXCL-W30 | 0.0714 (3/42) | 0.1613 (20/124) | −0.0899 | 1336 |
| armB CHANNELNEW-W30 | 0.4048 (17/42) | 0.3145 (39/124) | 0.0902 | 54 |
| armC | 0.7381 (31/42) | 0.4839 (60/124) | 0.2542 | 111 |
| armD two-signal | 0.3571 (15/42) | 0.1129 (14/124) | 0.2442 | 17 |

(= historical + shadow-prerepair: EU1 0.5081/63, EU2 0.3548/44,
BUCKETS 0.4113/51, POSTERIOR 0.1210/15, CHANNELNEW 0.3226/40,
armD 0.0968/12; armA 0.6048/75.)

Positive episodes (corrected): opened 42/42; EU1-confirmed 31; expired
unconfirmed 11. Perturbation20 confirmed-retention >= 0.80 on every
family. Boundary-exits sensitivity probe: armD unchanged (0.357/0.113).

## Subject-by-subject transitions (historical -> corrected)

Pairing complete: 165/165 sids, zero unpaired, no kind flips. Per
family (positives TP/FN, negatives FP/TN on the confirmed flag):
EU1-W30 and armC: FN->TP 2, TP->FN 2, FP->TN 8, TN->FP 5. EU2-W60:
2/3/5/8. BUCKETS: 2/4/10/8. POSTERIOR: 1/1/0/5. CHANNELNEW: 1/2/5/4.
armD: 2/2/1/3. Candidate-state: 20 subjects left candidate, 5 entered
(net −15); emerging-state: 23 left, 6 entered (net −17) — the time
correction removes far more false states than it creates, concentrated
on negatives. Full per-sid tables in
`artifacts/replay-comparison.json` (sha256
020326a01882230d…, see SEALED_OUTPUTS mirror + seal receipt).

## Checks

- No-lookahead: PASS (inject_future byte-identical on all counterfactual
  subjects; prefix-bounded perturbation; filter_le at every decision).
- As-of determinism: PASS (double run canonical sha256 identical in both
  runs: corrected cc9a29a7…, shadow 0675466c…).
- Frozen arms unchanged: YES. Frozen thresholds unchanged: YES.
- New labels created: NO. Interest holdout v1.1 accessed: NO.
- Historical detailed results read only after corrected outputs sealed:
  YES.
- Tests: 37/37 (test_bakeoff_temporal_emergence + test_temporal_time_policy).
- Production changed: NO. burst-policy-v1 untouched; no promotion; no
  temporal-migration rewrite.
- Review performed: NO — STOP at
  CORRECTED_TIME_REPLAY_READY_FOR_FRESH_REVIEW.

## Artifacts

- Runtime (gitignored, authoritative): `P:/.data/yt-is/ef/
  concept-discovery-calibration/temporal-emergence-modelgen-v1-replay-
  corrected-time/{substrate,corrected,shadow-prerepair,
  corrected-sensitivity-boundary-exits}` + replay-comparison.json.
- Durable mirrors (this directory): SEALED_OUTPUTS.json,
  corrected-aggregate.json, shadow-prerepair-aggregate.json,
  replay-comparison.json, substrate_manifest.json, REPLAY_MANIFEST.json,
  FREEZE_RECEIPT.md.
- Replay tooling: scripts/replay_substrate_builder.py,
  scripts/bakeoff_temporal_emergence.py (byte-frozen), scripts/replay_seal.py,
  scripts/replay_compare.py (written blind pre-run).
- Publication: branch `agent/sess_22f9a598-5a68-4270-bfc3-7ab947ed97b7`
  (no force push; no GitHub editing).

NEXT IMPLEMENTER DECISION: ARCHITECT PENDING
