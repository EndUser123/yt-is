# C6 final acceptance preregistration — committed BEFORE the set is built

G-gate items 6-7. Fresh untouched region [372:415]. Promotion ONLY via
the separate promoter validating this suite's authorized PASS receipt.

## Gates (hard)

Comparison stratum (fresh, ≥24 cases, all five comparison forms):
- judged any@3 ≥ 0.85 (dev S measured 0.90; 0.05 margin)
- judged nDCG@3 ≥ 0.75 (dev 0.853)
- entity coverage@5 ≥ 0.45 (dev 0.50; loose title-token approximation)
- authored MRR@10 ≥ 0.25 informational gate (dev-measured live 0.4667
  on C5's exposed set; kept as a weak tripwire only)

All other gates exactly as C5's (already-green production gates, run on
C6's fresh region where applicable): strong exact strata 1.0; weak_df1
1.0/1.0; zero-df empty 1.0; twins 0; weak_common routed+judged;
ambiguous_plain routed semantic; telegraphic judged ≥0.70; technical
judged ≥0.75; latency p95 ≤250ms; reopen 30/30; filter 20/20; parity;
namespace; lag ≤50; restart/reconnect; C1/C2 regression preconditions.

Judged strata protocol as C5 (anonymous listings, blind judge, finisher
computes metrics and writes the immutable verdict receipt).

PASS -> emit promotion-authorized receipt -> invoke ef.promote ->
verify active_generation==1 and incremental health. FAIL -> STOP,
gates frozen, discriminating evidence.

agent: zcode · 2026-08-17
