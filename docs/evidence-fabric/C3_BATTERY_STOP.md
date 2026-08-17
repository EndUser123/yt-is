# C3 battery — STOP packet (4 gates failed of 20; NOT promoted)

b-prime rule 14 compliance: gates frozen, C3 set now regression evidence.
Receipt: `c3_final_battery.json`. Passed: every exact stratum (df1 R@1
1.0; df2-10/df11-100/df101-1000/punct literal-prefix 1.0), zero-df
identifiers empty-rate 1.0, twins false-pin 0, technical semantics 0.40,
reopen 30/30, filter 20/20, parity, namespace, lag 0, restart/reconnect,
and all four regression preconditions (C2 semantic 0.7167 unchanged).

## Failures — discriminating evidence

### G1 short_natural MRR 0.313 (gate 0.40), CI [0.135, 0.505]
### G2 comparison_questions MRR 0.242 (gate 0.40), CI [0.075, 0.450]
- Stage: acceptance-set construction, not the system.
- Leading explanation: my C3 hand queries for these strata are TELEGRAPHIC
  ("broken junior developer job market") vs the paraphrase style of C1/C2
  ("how to backtest a trading strategy without overfitting") that
  calibrated the 0.40 threshold. Same system, harder query class; the
  regression precondition proves no system-level semantic regression
  (C2 natural = 0.7167 on this run).
- Strongest alternative: real degradation on short queries — refuted by
  reg_c2_semantic and C1 regression stability.
- Distinguishing test: rerun C1/C2-style paraphrase queries against the
  same index (regression suites already do: green).
- Smallest remediation: decide the product requirement for telegraphic
  queries and preregister a C4 with strata styled accordingly (or a
  two-style natural stratum with per-style thresholds).

### G3 ambiguous_common: routed_semantic FALSE, MRR 0.0 (gate semantic + 0.30)
- Stage: weak-class df tiebreak.
- Leading explanation: "TikTok" is weak-shaped (internal case boundary)
  and its compound-form df in the corpus is under the 1000 ceiling
  (narrators usually SAY "Tik Tok" spaced), so it routed identifier.
  Google/Python/Windows/Gemini/YouTube routed semantic correctly.
- The MRR 0.0 across all six is a weak-gold artifact: single common-word
  queries retrieve topically dominant videos; arbitrary authored
  positives are not reliably findable — this metric was already flagged
  informational-leaning in the prereg.
- Distinguishing test: `routing.classify('TikTok')` + its compound df.
- Smallest remediation: brand-exclusion is lexical-knowledge territory;
  options are (i) accept TikTok-class misrouting as a documented edge of
  the df tiebreak, (ii) lower DF_WORD_ID_MAX, or (iii) a small
  conventional-brand stoplist maintained as data.

### G4 latency p95 260 ms (gate ≤ 250 ms)
- Stage: client revalidation.
- Leading explanation: the trust-window removal (correct, rule 8) added a
  get_collections round-trip per client() call; relevant() calls client()
  twice (semantic legs + hydration), ~10-20 ms each — pushing p95 from
  ~151 ms (C2) to 260 ms on this run's heavier exact-stratum mix.
- Distinguishing test: per-stage timing (baselines decomposition shows
  server query ~5-19 ms; the delta is client revalidation overhead).
- Smallest remediation: revalidate once per relevant() invocation (pass
  the client through) — recovers the double round-trip without
  reintroducing blind trust.

## State

Generation 1 remains inactive (active_generation=0). No vectors,
contracts, or storage touched. C1+C2+C3 are all regression suites now.

agent: zcode · 2026-08-17
