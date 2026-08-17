# C5 final acceptance preregistration — committed BEFORE the set is built

F-gate item 7. Fresh untouched region [329:372]. Promotion ONLY via the
separate promoter validating this suite's authorized PASS receipt.

## Gates (hard)

| Stratum | n | Metric | Gate |
|---|---|---|---|
| exact_df1 (STRONG shapes only) | ≥25 | R@1 | == 1.0 |
| exact_df2_10 (strong) | ≥15 | literal_prefix@10 | == 1.0 |
| exact_df11_100 (strong) | ≥15 | literal_prefix@10 | == 1.0 |
| weak_df1 (opaque weak, e.g. hizoJc-class) | ≥12 | literal in top-3 + literal_prefix | top3-literal == 1.0 AND prefix ≥ 0.95 |
| weak_common (TikTok/YouTube-class) | ≥6 | routed ambiguous + judged any@3 ≥ 1 relevant | routed all-ambiguous; any@3 ≥ 0.83 |
| zero_df_identifiers | ≥12 | primary empty | == 1.0 |
| near_twins | 12 | false primary pin | == 0 |
| ambiguous_words (plain: Google/Python-class) | ≥4 | routed semantic | all semantic |
| telegraphic | ≥24 | judged any@3 | ≥ 0.70; authored MRR informational ≥ 0.25 |
| semantic_technical (wiki/www/review shapes) | ≥25 | judged any@3 | ≥ 0.75; authored MRR informational ≥ 0.25 |
| comparison_questions | ≥10 | judged any@3 | ≥ 0.75; authored MRR informational ≥ 0.25 |
| latency | all | full-path p95 | ≤ 250 ms |
| reopenability | 30 | exact-span | 100% |
| filter | 20 | wrong-channel | 0 |
| structural / namespace / freshness / restart | — | as C4 | pass |

Judged strata: battery emits config-anonymous listings; judge labels
blind; finisher computes metrics and writes the immutable PASS/FAIL
receipt (promotion_authorized iff all gates pass). Regression
precondition: C1 df1 strong-only R@1 == 1.0 (weak subset reported), C2
literal-prefix (strong-only) == 1.0, twins == 0, natural MRR ≥ 0.35.

If C5 passes: emit receipt -> invoke promoter -> verify generation 1
active + incremental current. If it fails: STOP, gates frozen.

agent: zcode · 2026-08-17
