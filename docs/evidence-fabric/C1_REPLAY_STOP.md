# C1 final replay — STOP packet (4 gates failed, generation 1 NOT promoted)

A" section 21 compliance: thresholds untouched, acceptance set untouched,
no promotion. Receipt: `c1_final_replay.json`. Gates that passed:
df1 (R@1 = 1.0, the deterministic property), near-twins (0 false-pins),
all three semantic strata (MRR 0.42/0.58/0.43, CI reported), latency
(p95 84 ms), reopenability (30/30 exact-span), filter (20/20), structural
(166,714 parity), freshness (lag 0, incremental live).

## Failing gates — discriminating evidence

### G1 exact_df2_100: observed R@10 0.686, gate ==1.0
- Stage: fusion tie-structure + gate derivation.
- Leading explanation: gate derived from the v3 dev df2-10 sub-stratum
  (R@10 = 1.0 there) but applied to df 2-100. Dev's own lowdf exact
  measurement (R@10 0.833, routing_dev_results lowdf_exact_info) already
  showed df 11-100 cannot guarantee top-10 containment of ONE arbitrary
  sampled positive among up-to-100 literal ties.
- Strongest alternative: FTS lane regression at prod scale (weakened by:
  df1 passes perfectly and containment of literal hits is intact — the
  lane works; misses are ranking-among-ties).
- Distinguishing test: recompute R@10 restricted to df 2-10 of this set
  (expect 1.0) vs df 11-100 (expect ~0.6-0.8).
- Smallest remediation: rescope the gate to df 2-10 for R@10==1.0 and
  make df 11-100 containment-based (as dev measured it).

### G2 exact_df101_1000 containment: observed 0.628, gate >=0.95
### G3 common_lexical containment@5: observed 0.825, gate >=0.95
### G4 punct_heavy containment: observed 0.133, gate >=0.95
- Stage: ROUTING CLASSIFICATION (by design, not accident).
- Leading explanation: `routing.classify` sends identifier-shaped tokens
  with df>100 to SEMANTIC intent (no FTS lane, per the df<=100 cut and
  A" 7.3 "common lexical term routes semantic"). The acceptance metric
  then demands literal containment of top-k — which semantic results do
  not provide. My dev containment numbers (1.0) measured the FTS LANE
  DIRECTLY, bypassing routing — dev measured the lane, acceptance
  measured the routed system. The prereg gates and the routing design
  were mutually inconsistent; the replay exposed it.
- Strongest alternative: punct tokens additionally fail
  `identifier_shaped` (regex misses hyphen/dot mixes), so they never even
  reach the df test — consistent with G4's extreme 0.133.
- Distinguishing test: rerun the three strata with the explicit
  `exact=True` escape hatch (A" 10) — if containment jumps to ~1.0, the
  lane is healthy and only routing/derivation disagree.
- Smallest remediation (operator's call):
  (i) gate-side: common/moderate/punct gates become judged-relevance or
      explicit-exact-mode containment (the §10 hatch exists precisely
      for callers demanding literal semantics), OR
  (ii) routing-side: extend containment-priority to identifier-shaped
      tokens at ANY df (literal pins first, semantic fills) — keeps
      semantic evidence per §7.3 while satisfying literal containment.

## What must NOT change
Vectors, EU/chunk contracts, Qdrant storage (A" 16 — nothing here
implicates them; df1 and reopen prove the pipeline end-to-end).

agent: zcode · 2026-08-17
