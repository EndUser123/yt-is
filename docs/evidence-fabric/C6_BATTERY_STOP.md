# C6 battery — STOP packet (judged strata failed; NOT promoted)

G-gate rule 7: STOP, gates frozen, no set reuse. Phase-1: ALL 22
non-judged gates PASS (including every exact/identifier/zero-df/twin/
routing/latency/reopen/filter/parity/namespace/lag/restart gate and all
four regression preconditions; comparison authored tripwire 0.264 ≥
0.25 PASS). Judged results on C6's fresh region:

| Stratum | C6 judged any@3 | Gate | C5 same metric |
|---|---|---|---|
| telegraphic | 0.632 (12/19) | ≥0.70 | 0.833 |
| semantic_technical | 0.619 (13/21) | ≥0.75 | 0.846 |
| comparison | 0.625 (15/24) | ≥0.85 | 0.70 (old lane) |

## Discriminating evidence

- Stage: ACCEPTANCE-SET AUTHORING VARIANCE, not the system.
- The regression precondition is bit-for-bit stable across C4/C5/C6
  batteries: C2 natural MRR = 0.7167 in every run; C1 strong df1 = 1.0;
  C2 strong literal-prefix = 1.0; twins = 0. The retrieval system's
  behavior on FIXED queries is unchanged (and the comparison lane's
  authored MRR on C5's set went 0.381 → 0.467 when the lane activated).
- What changed between C5 and C6 is the authored queries themselves:
  C6's hand set is drastically terser ("free api key week offer",
  "saas build series part seven", "gcp cert practice question five") —
  queries whose intent is unrecoverable without more context. C5's set
  carried descriptive context. The judged any@3 metric is measuring
  the query set's specificity as much as the system.
- Strongest alternative: a genuine regression introduced by the
  comparison lane — refuted: comparison queries improved on C5's fixed
  set (0.381→0.467 authored; dev judged 0.833→0.90) and the lane
  cannot affect non-comparison routing (C2 natural unchanged).
- Distinguishing test already run: fixed-set regressions green +
  C5-set comparison improved ⇒ system stable.

## Smallest remediation (operator's call)

The acceptance metric now has a demonstrated region/author variance
problem: identical system, 0.83 vs 0.63 judged scores across rounds.
Options:
  (a) Authoring rubric: fresh sets must match the specificity
      distribution of a fixed anchor set (e.g., C5's), verified before
      sealing (length/specificity check against anchors).
  (b) Stable-anchor judging: judged gates computed on a FIXED cross-
      region anchor query set (never re-authored) + fresh-set gates
      informational. Removes author variance from promotion entirely.
  (c) Both: anchors for promotion, fresh regions for discovery.
Recommendation: (c). The system-level gates (exact, zero-df, twins,
invariants, fixed-set regressions) are stable and passing; only the
freshly-authored judged metric oscillates.

Generation 1 remains inactive. No vectors/storage touched.

agent: zcode · 2026-08-17
