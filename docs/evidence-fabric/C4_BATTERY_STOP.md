# C4 battery — STOP packet (multiple gates failed; NOT promoted)

E-gate rule: STOP, gates frozen, no set reuse. Receipt:
`c4_battery_partial.json` (telegraphic judging not reached — moot given
hard failures).

## Discriminating evidence

### Root cause: the unresolved weak-shape contradiction, now measured

The E-gate dev-case list places `hizoJc` under IDENTIFIER; the E-gate
rule text routes "ambiguous human-language token" SEMANTIC; `TikTok` and
`hizoJc` are syntactically identical (single word, one internal case
boundary). Syntax cannot satisfy both. I implemented rule text (weak ->
semantic, flagged in commits/tests at the time); C4 measures the cost:

| Gate | Observed | Prior (C3, weak->identifier-df-tiebreak era) |
|---|---|---|
| exact_df1 R@1 | 0.886 (≈4/35 weak tokens routed semantic) | 1.0 |
| df2_10 literal-prefix | 0.867 | 1.0 |
| df11_100 | 0.900 | 1.0 |
| df101_1000 | 0.840 | 1.0 |
| reg_c1_df1 | 0.800 | 1.0 |
| reg_c2_literal | 0.886 | 1.0 |

NOT a code regression: reg_c2_semantic (0.7167) and reg_c2_twins (0)
are unchanged; system health gates all pass (reopen 30/30, filter 20/20,
parity, namespace+build_id, lag 0, restart/reconnect, latency p95 135ms,
zero-df empty 1.0, punct 1.0, ambiguous-words all-semantic per the new
contract).

### Secondary failures

- semantic_technical 0.3814 vs 0.40 (CI [0.23, 0.54] straddles); comparison
  0.339 vs 0.40. Fresh-region authored positives; CI overlap suggests
  threshold-marginal rather than clear regression. Telegraphic authored
  MRR improved to 0.611 (C4 queries).

## The decision only you can make

The three-way contract {TikTok semantic, hizoJc identifier, no lexical
knowledge} is unsatisfiable by syntax. Options:
  (a) weak -> SEMANTIC everywhere (current implementation): accept that
      exact strata containing weak-shaped tokens score ~0.85-0.9, or
      rebuild exact strata sampling STRONG shapes only (strata follow
      the contract rather than vice versa).
  (b) weak -> IDENTIFIER: TikTok-class misrouting returns — rejected in
      your E-gate text.
  (c) Keep weak -> semantic AND exclude weak-shaped tokens from exact
      acceptance strata (they are, by contract, semantic queries whose
      literal expectations users express via quotes/exact=true).

(c) == (a) with strata alignment. It is my recommendation: the contract
is coherent; the strata were built under the old contract.

No vectors, contracts, or storage touched. All four acceptance sets
remain sealed history (regression evidence).

agent: zcode · 2026-08-17
