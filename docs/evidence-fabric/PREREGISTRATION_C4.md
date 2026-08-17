# C4 final acceptance preregistration — committed BEFORE the set is built

E-gate item 5. Fresh untouched region [286:329]. Promotion ONLY via the
separate promote command validating this suite's PASS receipt.

## Gates (hard)

| Stratum | n | Metric | Gate |
|---|---|---|---|
| exact_df1 | ≥30 | R@1 | == 1.0 |
| exact_df2_10 | ≥20 | literal_prefix@10 | == 1.0 |
| exact_df11_100 | ≥20 | literal_prefix@10 | == 1.0 |
| exact_df101_1000 | ≥20 | literal_prefix@10 | == 1.0 |
| punct_heavy | ≥15 | literal_prefix@10 | == 1.0 |
| zero_df_identifiers | ≥12 | primary evidence empty | == 1.0 |
| near_twins | 12 | false primary pin | == 0 |
| ambiguous_words (TikTok-class) | ≥6 | routed intent | all semantic |
| telegraphic_semantic | ≥24 | judged any@3 (blind judge, config-anonymous listing) | ≥ 0.70 |
| telegraphic_semantic | same | judged P@3 | ≥ 0.40 |
| telegraphic authored MRR | same | informational | report-only |
| semantic_technical (incl. /wiki //www /review shapes) | ≥25 | MRR@10 | ≥ 0.40 |
| comparison_questions | ≥10 | MRR@10 | ≥ 0.40 |
| latency | all | full-path p95 | ≤ 250 ms (preferred < 100 server-only) |
| reopenability | 30 | exact-span | 100% |
| filter | 20 | wrong-channel | 0 |
| structural | — | parity | equal |
| namespace | — | validator VALID + build_id matches BuildSpec | pass |
| freshness | — | lag after catch-up | ≤ 50 |
| qdrant restart/reconnect | 1 | stale client recovers | pass |
| regression precondition | — | C1 df1 R@1==1.0; C2 literal==1.0 + twins==0 + natural MRR ≥ 0.35 | pass |

Judging protocol (telegraphic): the battery emits a config-anonymous
(query, video, title) listing for its top-3; the judge labels relevance
blind to config identity; metrics computed per config from judgments.
Telegraphic gate applies to the production config; other configs
reported for component visibility.

## Sequence

Build+seal AFTER this commit; battery emits immutable PASS/FAIL receipt
(promotion_authorized=true iff all gates pass); promotion executes ONLY
via `python -m ef.promote --receipt <path>` verifying per ef/receipt.py;
then verify active_generation==1 and incremental indexing current.
Any gate fails: STOP, gates frozen, evidence returned.

agent: zcode · 2026-08-17
