# Final acceptance preregistration (C1) — committed BEFORE the set is built

A" sections 12-14. All thresholds derive from committed development
evidence (routing_dev_results.json v3, same_corpus_baselines.json).
The old sealed identifier set is reclassified regression/debug only
(A" section 2 ruling) and is NOT part of final acceptance.

## Sampling protocol (untouched region)

Fresh-region window: per-category videos[157:200] (dev used [0:120],
B.1 holdout [120:132], old acceptance [132:156]). Identifier tokens
sampled with recorded production-scale df; one per video. Hand-authored
queries written from printed excerpts before any scoring. Seal: sha256 of
the query file recorded before the replay.

## Strata, metrics, gates

| Stratum | n | Metric | Gate |
|---|---|---|---|
| exact_df1 | ≥30 | chunk R@1 | == 1.0 (deterministic) |
| exact_df2_100 | ≥30 | chunk R@10 | ≥ 1.0 (i.e. 1.0; dev 1.0) |
| exact_df101_1000 | ≥20 | literal containment@10 | ≥ 0.95 |
| common_lexical | 8 fixed | containment@5 | ≥ 0.95 |
| punct_heavy | ≥15 | containment@10 | ≥ 0.95 |
| near_twins | 12 | mutant false-pin @R@1 | == 0 |
| semantic_natural | ≥30 authored | video MRR@10 | ≥ 0.40 (dev hybrid 0.4945 on like queries; margin 0.09 for fresh-sample variance) |
| semantic_technical | ≥30 authored | video MRR@10 | ≥ 0.40 |
| comparison_questions | ≥10 authored | video MRR@10 | ≥ 0.40 |
| system-wide latency | all above | server-only p95 / full-path p95 | < 100 ms / < 250 ms (measured 18.7 / 54.2) |
| reopenability | 30 samples, 7 classes | exact-span equality | 100% |
| filter correctness | 20 | wrong-channel results | 0 |
| structural | — | catalog/projection parity + namespace | parity AND VALID |
| freshness | — | index lag at replay | ≤ 50, catch-up before promotion |

Bootstrap: 10k-resample 95% CI reported for every MRR gate (point
estimate gates; CI is evidence, not pass/fail).

## Promotion condition (A" 20)

ALL gates pass -> atomic promotion (buildspec.promote, generation 1),
retain rollback (promotion.json history), confirm continuous incremental
indexing. ANY gate fails -> STOP, discriminating evidence per A" 21,
no threshold edits, no set replacement.

agent: zcode · host: both · 2026-08-17
