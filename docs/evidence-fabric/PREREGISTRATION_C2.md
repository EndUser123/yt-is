# C2 final acceptance preregistration — committed BEFORE the set is built

D-gate rule 7. Gates implement D-gate rule 5 evaluation semantics,
calibrated on C1 regression measurements (literal-prefix 1.0 across exact
strata; semantic MRR 0.42/0.58/0.43). C1 set = permanent regression
suite. C2 samples the fresh untouched region [200:243] per category.

## Metrics (D-gate semantics)

- literal_prefix@K: the first min(K, df) results are ALL literal matches
  (semantic fill is contract-legal only after that prefix).
- R@1 (df==1): the single literal ranks first — deterministic.
- MRR@10 (video-level, authored positives): judged relevance.

## Gates (hard, pass required for promotion)

| Stratum | n | Gate |
|---|---|---|
| exact_df1 | ≥30 | R@1 == 1.0 |
| exact_df2_10 | ≥25 | literal_prefix@10 == 1.0 |
| exact_df11_100 | ≥20 | literal_prefix@10 == 1.0 |
| exact_df101_1000 | ≥20 | literal_prefix@10 == 1.0 |
| punct_heavy | ≥15 | literal_prefix@10 == 1.0 |
| near_twins | 12 | mutant false-pin == 0 |
| semantic_natural | ≥30 | MRR@10 ≥ 0.40 |
| semantic_technical | ≥30 | MRR@10 ≥ 0.40 |
| comparison_questions | ≥10 | MRR@10 ≥ 0.40 |
| common_lexical | 8 | informational only (semantic route; judged relevance needs authored judgments — reported, not gated) |
| latency | all | full-path p95 ≤ 250 ms |
| reopenability | 30 samples | exact-span equality 100% |
| filter | 20 | wrong-channel results 0 |
| structural | — | catalog/projection parity |
| namespace | — | ef_validate_namespace VALID |
| freshness | — | incremental catch-up; lag ≤ 50 at gate time |
| qdrant restart | 1 | kill server → next query auto-reconnects, correct result (no per-query PowerShell) |

Regression suite (C1 set) runs alongside — informational plus its
still-valid gates (df1, twins, semantic MRR ≥ 0.40, latency).

Policy-invariant ANN legs: measured once per experiment (D-gate rule 6);
no policy comparison re-runs invariant paths.

Promotion: ALL hard gates pass -> atomic promotion gen1 (gen0 rollback
retained). Any failure -> STOP per A" 21.

agent: zcode · 2026-08-17
