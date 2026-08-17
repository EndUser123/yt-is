# C3 final acceptance preregistration — committed BEFORE the set is built

b-prime rules 5-6. C1 and C2 are permanent regression suites (their
history, including C2's 19/20, is preserved verbatim). C3 validates the
COMPOSED PRODUCTION SYSTEM under the amended zero-literal contract on a
fresh untouched region [243:286] per category.

## Gates (hard; promotion requires ALL)

| Stratum | n | Metric | Gate |
|---|---|---|---|
| exact_df1 | ≥30 | R@1 | == 1.0 |
| exact_df2_10 | ≥20 | literal_prefix@10 | == 1.0 |
| exact_df11_100 | ≥20 | literal_prefix@10 | == 1.0 |
| exact_df101_1000 | ≥20 | literal_prefix@10 | == 1.0 |
| punct_heavy | ≥15 | literal_prefix@10 | == 1.0 |
| zero_df_identifiers | ≥12 | primary evidence count | == 0 (empty) |
| near_twins (mutant df=0) | 12 | false primary pin | == 0 |
| ambiguous_common_terms | 6+ | routed intent + judged MRR | semantic intent; MRR@10 ≥ 0.35 (any-positive video from same query's authored context; informational-leaning given weak gold — see note) |
| short_natural | ≥20 | MRR@10 | ≥ 0.40 |
| semantic_technical (incl. /wiki //www /review-arch shapes) | ≥30 | MRR@10 | ≥ 0.40 |
| comparison_questions | ≥10 | MRR@10 | ≥ 0.40 |
| latency | all | full-path p95 | ≤ 250 ms |
| reopenability | 30 | exact-span equality | 100% |
| filter | 20 | wrong-channel | 0 |
| structural | — | parity | equal |
| namespace | — | validator | VALID |
| freshness | — | catch-up + lag | lag ≤ 50 |
| qdrant restart/reconnect | 1 | stale client recovers | PASS |

Note on ambiguous_common_terms: authoring strong single-video positives
for "Google"-class queries is weak gold by construction; the gate
primarily asserts SEMANTIC routing (never hard-empty) with MRR
informational-leaning at a low bar.

## Metric definitions

- literal_prefix@K: first min(K, df) results are all literal matches.
- zero-primary: relevant() returns [] for identifier-intent queries with
  df == 0 (no suggestions channel exists; b-prime allows empty-only now).

## Regression precondition (b-prime rule 12)

C1 + C2 suites show no material regression (C2's previously-green gates
stay green; its near_twins gate is expected to pass under the amended
contract — recorded as regression evidence, NOT as promotion evidence).

Promotion: ALL C3 gates + regression precondition + freshness threshold
-> atomic promotion gen1, retain gen0, verify active generation and
continuous incremental indexing. Any failure -> STOP, gates frozen.

agent: zcode · 2026-08-17
