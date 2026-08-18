# EF Benchmark Protocol v1.1 (amendment to v1; frozen before shard02)

All v1 rules (roles, query contract, validity gate, distribution,
sealing, judging) carry over unchanged EXCEPT:

## Taxonomy amendment: ambiguous_allcaps stratum

Pure ALLCAPS single tokens (no digits/underscores/separators) are an
intrinsically ambiguous syntactic class. They no longer appear in a
"strong identifier" stratum. This is a taxonomy correction, not
exclusion: ALLCAPS cases remain REQUIRED acceptance cases.

Gates on OBSERVABLE BEHAVIOR (never internal route labels):

```text
unique literal ALLCAPS (df==1):
    exact occurrence R@1 = 1.0        # guaranteed by singleton pin

conventional ALLCAPS (VPN/API/GPU/JSON/HTTP-class):
    judged semantic relevance >= threshold
    literal evidence discoverable in top-10
```

## Strong identifier stratum (redefined)

Structurally strong shapes only: digits, underscores, snake_case, CLI
flags, dotted/slash paths, letter+digit mixes, model/version syntax,
ALLCAPS WITH digit or underscore. Gates unchanged (R@1 == 1.0 for df=1,
literal_prefix == 1.0 for df bands).

## Routing contract (i-prime)

```text
explicit exact=true / quoted literal -> exact semantics
strong structural identifier         -> identifier semantics
weak/conventional single token
  (mixed-case boundary or pure ALLCAPS) -> ambiguous dual retrieval:
      df==1 -> unique literal pinned at rank 1, semantic fills after
      df>1  -> literal subgroup + semantic, bounded permeability
      df==0 -> semantic results, never labeled exact/literal
ordinary multiword language           -> semantic semantics
comparison-shaped                      -> sparse-heavy comparison lane
```

df influences RANKING BEHAVIOR after ambiguity is established — never
intent classification.

agent: zcode · 2026-08-17
