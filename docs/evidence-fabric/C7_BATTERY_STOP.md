# C7 battery — STOP packet (one gate failed; NOT promoted)

H-gate terminal rule: valid-stratum failure → STOP with discriminating
evidence; gates frozen; shard_01 is consumed.

## Results (final pre-revert execution, run 3)

All 27 other gates PASS, including every judged stratum under the new
protocol — the variance problem is SOLVED:

| Stratum | judged any@3 | gate |
|---|---|---|
| descriptive_semantic (n=21) | **0.905** | ≥0.75 ✓ |
| telegraphic_but_sufficient (n=8) | **0.875** | ≥0.70 ✓ |
| technical (n=6) | **1.000** | ≥0.75 ✓ |
| comparison (n=8) | **0.750** | ≥0.75 ✓ |
| weak_common (n=3) | **1.000** | ≥0.66 ✓ |

Plus: exact_df1_strong 1.0, all literal-prefix strata 1.0, weak_df1
1.0/1.0, zero-df 1.0, twins 0, latency 167ms, reopen, filter, parity,
namespace, lag 0, restart/reconnect, all four anchors.

## The failure: ambiguous_plain_routed — "VPN"

- Observed: VPN routes IDENTIFIER (ALLCAPS strong-shape), gate expects
  semantic-or-dual for conventional terms.
- Root cause: pure short ALLCAPS acronyms are syntactically
  indistinguishable from identifiers: VPN (conventional, df in
  thousands) vs BTRFS/LUKS (identifier-like, df small). Same
  TikTok-vs-hizoJc problem, now in the ALLCAPS class.
- Attempted fix (mid-battery): route ≤5-letter pure acronyms to the
  ambiguous dual lane. Result: exact_df1_strong dropped 1.0 → 0.9375
  and literal-prefix strata → 0.84-0.87, because the dual lane's
  bounded permeability (by design, F-gate) lets semantic hits rank
  between literal tail matches — violating the identifier contract for
  the ALLCAPS tokens in the sealed strong strata. REVERTED.
- The boundary is a genuine product-contract question, not a bug:
  pure ALLCAPS without digits/underscores needs either (i) the dual
  lane with its permeable semantics accepted for that whole class
  (adjusting the strong-stratum contract accordingly = a protocol
  change), (ii) a narrow deterministic signal separating VPN-class
  from BTRFS-class without df (none known — that was the original
  TikTok lesson), or (iii) accepting VPN-as-identifier (its dual-lane
  results judged 1.0 relevant anyway — weak_common TikTok/YouTube pass
  through the same lane; VPN would likely too, making the
  routed-intent gate's semantic-only expectation the wrong gate rather
  than wrong routing).

Smallest remediation candidates for operator decision:
  (i) protocol v1.1: pure ALLCAPS single tokens (no digits/separators)
      → ambiguous class everywhere, strong strata rebuilt under that
      rule in shard_02 (unconsumed);
  (ii) keep routing as-is; amend the ambiguous_plain gate to accept
      identifier routing when judged any@3 ≥ 0.66 anyway (dual lane
      verified good for conventional tokens) — gate change, needs your
      authorization since prereg'd;
  (iii) treat VPN-class by the existing ambiguous lane via a
      case-sensitive heuristic: ≤5 letters + no digit + no separator →
      dual lane (my attempted (i)); requires accepting permeable
      literal semantics for BTRFS-class df=1 tokens — measured cost:
      R@1 0.94 not 1.0 for the ALLCAPS subset.

Shard_01 is consumed (sealed evidence + judged results preserved).
Shards 02/03 remain unconsumed under protocol v1.

Generation 1 remains inactive. No promotion.

agent: zcode · 2026-08-17
