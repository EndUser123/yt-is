# C7 final acceptance preregistration — committed BEFORE battery execution

H-gate items 8-10. C7 = stable regression anchors + UNTOUCHED
promotion_shard_01 (sealed 1ca23c33/e4e51d11, validity 0 rejections)
+ all structural/invariant gates. Thresholds fixed by
BENCHMARK_PROTOCOL_V1 as product targets (NOT derived from any observed
battery). Terminal rule: pass -> promote via separate promoter, no C8;
fail -> STOP with discriminating evidence, gates frozen.

## Gates (all hard)

Judged (shard01, blind judge, any@3):
- descriptive_semantic (n=21) ≥ 0.75
- telegraphic_but_sufficient (n=9) ≥ 0.70
- technical (n=6) ≥ 0.75
- comparison (n=8) ≥ 0.75
- ambiguous_common (n=6): routed per contract + judged any@3 ≥ 0.66

Auto (shard01): exact_df1_strong R@1 == 1.0; df2-10 strong prefix == 1.0;
df11-100 strong prefix == 1.0; df101-1000 prefix == 1.0; punct == 1.0;
weak_df1 top3-literal == 1.0 AND prefix ≥ 0.95; zero_df empty == 1.0;
twins false_pin == 0.

Anchors: C1 strong df1 R@1 == 1.0; C2 strong literal-prefix == 1.0;
C2 twins == 0; C2 natural MRR ≥ 0.65.

Invariants: latency p95 ≤ 250ms; reopen 30/30; filter 20/20; parity;
namespace VALID + build_id; freshness lag ≤ 50; qdrant
restart/reconnect.

Promotion: PASS receipt (suite c7_final_battery, promotion-authorized)
-> ef.promote -> verify active_generation == 1 + incremental healthy.

agent: zcode · 2026-08-17
