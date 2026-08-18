# C8 (shard02) promotion preregistration — committed BEFORE consumption

I-gate rules 4-5. C8 = anchors + UNTOUCHED promotion_shard_02 + all
invariants under protocol v1.1. Thresholds are protocol product targets;
none derived from observed batteries. Terminal rule: full pass ->
promote via separate promoter, no further gate; any failure -> STOP,
shard02 preserved as consumed evidence.

## Shard02 construction (region [445:475], protocol v1 taxonomy)

Auto strata per v1.1: exact_df1_strong (structural shapes only) ≥25;
df bands strong ≥12 each; punct ≥12; weak_df1 ≥10 (mixed-case + ALLCAPS
ambiguous class, df=1); ambiguous_allcaps_df1 ≥8 (pure ALLCAPS, df=1);
zero_df ≥10; twins 12.

Hand strata (~50, contract-complete, blind-validity gated):
descriptive 40% / telegraphic-sufficient 15% / technical 15% /
comparison 15% / ambiguous-common 15% (including ≥2 conventional
ALLCAPS terms).

## Gates (hard)

- exact_df1_strong R@1 == 1.0 (n≥25)
- df2-10 strong, df11-100 strong, df101-1000, punct: literal_prefix == 1.0
- weak_df1 (incl. ambiguous ALLCAPS df=1): top3-literal == 1.0 AND
  literal_prefix ≥ 0.95 — the singleton pin guarantees R@1 for the
  df=1 subset
- ambiguous_allcaps conventional: literal discoverable in top-10 == 1.0
  AND judged any@3 ≥ 0.66
- zero_df empty == 1.0; twins false_pin == 0
- judged: descriptive ≥0.75, telegraphic ≥0.70, technical ≥0.75,
  comparison ≥0.75 (any@3)
- invariants: latency p95 ≤250ms; reopen ≥25 samples 100%; filter
  ≥15 100%; parity; namespace VALID; lag ≤50; restart/reconnect
- anchors: C1 strong df1 1.0; C2 strong literal 1.0; twins 0;
  C2 natural MRR ≥ 0.65

PASS -> receipt (suite c8_final_battery) -> ef.promote -> verify
generation 1 active + incremental healthy. FAIL -> STOP.

agent: zcode · 2026-08-17
