# Corrected-Time Replay — Freeze Receipt

agent: zcode
session: sess_22f9a598-5a68-4270-bfc3-7ab947ed97b7
date: 2026-08-27

## What was frozen

`REPLAY_MANIFEST.json` pins every dimension of the Temporal Emergence
model-generation replay before measured execution:

- exact temporal substrate commit `1f72ee16` (local main == origin/main),
  repair lineage `49e7ba72 -> 2b873f2c -> e2a95acf -> ad218261` verified
  ancestral;
- byte-identity of the harness, its tests, the frozen evaluator, and the
  burst-policy-v2 module against the ORIGINAL run commit `761b688a`
  (git blob equality — the code is unchanged; only the substrate
  dimension, evidence valid-time semantics, differs);
- consumed diagnostic identity (42 positives / 124 negatives,
  TRAINING_DIAGNOSTIC_ONLY), arms, thresholds, perturbations, decision
  mapping, as-of and no-lookahead rules;
- the mechanical causal-classification rule and the subject-transition
  rule, frozen BEFORE any historical result artifact was opened;
- the seal protocol: corrected outputs are hashed and sealed before the
  historical detailed results may be read.

## Replay design (one changed dimension)

Two runs share ONE catalog file snapshot and byte-frozen code:

1. `shadow-prerepair` — the harness's own pre-repair reader
   (published_at -> captured_at fallback) on the snapshot;
2. `corrected` — the same code pointed at a materialized corrected
   substrate implementing the production reader policy
   (published_at -> eu_time_recovery.valid_start -> EXCLUDE).

The historical-vs-shadow delta isolates catalog drift (A1 drift class);
the shadow-vs-corrected delta isolates exactly the corrected dimension.

## Contamination state at freeze time

Historical artifacts (`temporal-emergence-modelgen-v1/*.json`, the
results narrative, fork recommendations) are UNREAD by this session.
The broad fact `NO_NEW_MODEL_SUPPORTED` is known as required by the
handoff; no per-arm number, winner/loser interpretation, or
subject-level classification has been accessed.
