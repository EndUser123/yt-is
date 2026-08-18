# C8 battery — STOP packet (one gate failed; NOT promoted)

I-gate terminal rule: any failure -> STOP, shard02 preserved as consumed
evidence, gates frozen.

## Results

All gates pass except one, including the two NEW v1.1 behavioral gates:

- **ambiguous_allcaps_df1 R@1 = 1.0 (n=10)** — the i-prime singleton
  pin works: BTRFS-class unique literals rank first through the
  ambiguous lane.
- **ambiguous_allcaps conventional discoverability = 1.0** — VPN/API/GPU
  literals discoverable in top-10 alongside semantic results.
- All exact/literal strata 1.0; weak_df1 1.0/1.0; zero-df 1.0; twins 0;
  anchors green (C2 natural 0.7167); latency 160ms; reopen 18/18;
  filter 8/8; parity; namespace; lag 0; restart/reconnect.

## The failure: ambiguous_plain_routed — "Node.js"

- Observed: Node.js routes IDENTIFIER (dotted-token strong shape per
  `[A-Za-z0-9]+(?:[-._/:][A-Za-z0-9]+)+`). Gate expects
  semantic-or-ambiguous for conventional terms. All six other terms pass
  (Blazor/Replit semantic; MongoDB/GraphQL/MySQL/HTML ambiguous).
- Root cause: the i-prime boundary was drawn at "pure ALLCAPS single
  token" but dotted product names (Node.js) are the SAME
  syntax-vs-convention tension in the joined-token class. Node.js is
  structurally dotted (ClassName.method-shaped) yet conventionally a
  household product name.
- Behavioral impact: identifier intent gives Node.js literal-prefix
  ranking — its literal df is enormous, so top results are literal
  Node.js chunks ranked semantically-within-literals; functionally close
  to the dual lane for high-df tokens. The residual difference from
  ambiguous-class treatment is bounded permeability (semantic-only
  results may interleave), which for a household term is arguably the
  DESIRED behavior anyway.
- Judged strata are PENDING (listing emitted, not yet judged) — recorded
  as-is; promotion is already blocked.

## Smallest remediation (operator's call)

  (i) protocol v1.2: dotted/joined single tokens whose final segment is
      a common runtime/TLD suffix (.js/.ai/.io/.dev/.py/.rs) join the
      ambiguous class — same i-prime principle, extended to the
      joined-token family; shard03 tests it;
  (ii) amend the ambiguous_common gate to accept identifier routing for
      structurally-strong shapes when literal discoverability and judged
      relevance pass (pure behavioral gate);
  (iii) accept Node.js-as-identifier (dotted names ARE more
      identifier-like; Node.js's literal-prefix behavior is defensible)
      and re-gate ambiguous_common as semantic-or-ambiguous-or-
      dotted-product — a gate clarification, not a lowering.

No vectors/storage/architecture touched. Generation 1 inactive.
Shard03 remains unconsumed.

agent: zcode · 2026-08-17
