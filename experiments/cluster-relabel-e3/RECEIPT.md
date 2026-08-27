# E3 RECEIPT — Cluster Label Representation Bakeoff (shadow)

Agent: zcode (fresh implementer session sess_bc0b8ab7). Shadow experiment:
NO production mutation anywhere. Cluster membership, cluster IDs, Evidence
Units, embeddings, clustering algorithm, and cluster count UNCHANGED
(freeze hash f5f57ea54ea06b6a9e2ce807a07529c7e4eb72593840b81fa2b6408a33190010).

## Membership changed: NO

Evaluation sample: n=45 clusters (NEW deterministic draw; policy v2/v3
amendments in PREREGISTRATION.md; selection manifest stays in the private
.data mirror, pattern per prior audits).

## Arms

| arm | mechanism | reuse vs adaptation |
|---|---|---|
| A0 | stored production `topic_clusters.label` (primary baseline) | existing artifact |
| A1 | verbatim port of clustering.py extract_top_terms/generate_cluster_label on frozen membership | reused code |
| B  | KeyBERTInspired-style: c-TF-IDF candidates + bge-m3 term-vs-doc cosine, top-4 join | donor adapted (~100 LOC local; no BERTopic dependency added) |
| C  | generative short label, Hy3 (`codex-opencode-zen-hy3-free`) via go-llm-proxy temp-0, strict contract prompt | existing infra (zen.py client) |
| R* | scripts/relabel_topics.py junk-filter + cross-cluster-df distinctiveness on frozen membership | VERBATIM repo mechanism; OUTSIDE decision enum per prereg v3 |

R participates in mechanical metrics only; a pre-unblinding verification
showed relabel_topics has no production caller and its filtering signature
is absent from live labels.

## Blinded reviewer agreement

Inter-reviewer agreement (within ±1 on 1-5 axes): **97.7%** of item·axis
scorings; harsh-resolved 2.3%; ambiguous OVERALL_PREFERRED **0%**
(45/45 decisive, both reviewers picked the same candidate in every item).
Reliability override NOT triggered.

Panel record: reviewer B = zcode default GLM tier (general-purpose,
completed n=45 valid). Reviewer A seat ladder (prereg v4-v8):
nemotron-ultra x2 empty-turn upstream, lightning x1 empty-turn, muse x3
HTTP-429 upstream (same free-tier throttle that hit Arm C), one
GLM-5.3-Max seat DISCARDED UNREAD after violating its directory
restriction (out-of-dir reads proven by raw-id keying; outputs
quarantined as tainted-panelA-results-discarded.json), final v8 seat =
fresh GLM-5.3-Max session under hardened isolation (n=45 valid).

## Quality metrics (adjudicated means, n=45)

| axis (1-5) | A0 stored | A1 recompute | B KeyBERT | C generative |
|---|---|---|---|---|
| REFERENT_FIDELITY | 2.87 | 3.60 | 3.40 | **4.50** |
| SPECIFICITY | 2.84 | 3.20 | 3.16 | **4.06** |
| CLARITY | 2.14 | 2.50 | 2.38 | **4.71** |
| GRANULARITY | 2.69 | 3.47 | 3.38 | **4.23** |
| ARTIFACT_FREE | 2.36 | 2.79 | 2.76 | **4.91** |

Adjudicated flag rates (flagged iff either reviewer flags):

| flag | A0 | A1 | B | C |
|---|---|---|---|---|
| ARTIFACT | **68.9%** | 60.0% | 57.8% | **2.2%** |
| AMBIGUOUS | 31.1% | 4.4% | 13.3% | 2.2% |
| TOO_NARROW | 20.0% | 4.4% | 6.7% | 4.4% |
| WRONG_TOPIC | 6.7% | 0% | 0% | 2.2% |
| TOO_GENERIC | 0% | 8.9% | 4.4% | 2.2% |

Preferred distribution (45 decisive): C=43, B=1, A0=1, A1=0.
Win-rates vs A0 (decisive head-to-head shares): C=0.977, B=0.50, A1=0.0.

MATERIAL_BAR checks (thresholds frozen pre-evaluation):
- A1 (diagnostic): delta_referent +0.73 ✓, delta_clarity +0.36 ✓,
  artifact-drop 8.9pp ✓, stability 0.9116 ✓ — fails ONLY the burden RSS
  clause (shared measurement, below).
- B: delta_referent +0.53 ✓ but delta_clarity +0.233 < +0.35 ✗.
- C: delta_referent +1.63 ✓, delta_clarity +2.57 ✓, artifact-drop
  66.7pp ✓ — fails TWO frozen clauses: (a) burden RSS (shared
  standalone-process measurement), and (b) the temperature-zero
  nondeterminism-documentation clause (stability mean itself cleared
  0.82 on its usable n=134 subset; completeness was reduced by the
  upstream outage, repeats unevaluated fail-closed). Outcome-invariant:
  either failing clause alone blocks MATERIAL_BAR.

## Stability (bge-m3 cosine label_t0 vs perturbed −20% docs)

| arm | n | coverage | mean cos | frac<0.82 | exact-same | gate (>=0.82) |
|---|---|---|---|---|---|---|
| A1 | 319 | 100% | 0.9116 | 23.5% | 43.6% | PASS |
| B  | 319 | 100% | 0.8962 | 27.0% | 43.0% | PASS |
| C  | 134 | 42%  | 0.8402 | 37.3% | 23.1% | mean passes numerically on partial n; the preregistered temperature-zero NONDETERMINISM-documentation clause FAILED CLOSED (0/45 groups usable, all 135 repeat calls lost to upstream exhaustion) |

Arm C stability therefore does not satisfy its full frozen gate even
though its measured mean cosine clears the threshold; both facts are
recorded wherever they matter (EVALUATION.json material_bar_checks).

## Downstream representation impact (SHADOW-IMPACT.json; t0-based sets)

t0 coverage: A/A0/A1/B complete on all 319 clusters; Arm C t0 usable on
308/319 (11 blanks from the upstream outage; every D-metric skips blank
labels, verified during review).

- D0 regeneration-only effect: recomputed mechanical labels equal stored
  labels for 0/319 clusters — stored labels are stale artifacts of an
  earlier index state; "regeneration alone" is already a full relabel.
- D1 casefold dup pairs: A0=0, A1=1, B=0, C=3, R=0.
- D2 trend-topic collisions (16 distinct topics): A0=16 rows duplicated,
  A1/B/C=0, R=14. Every challenger candidate set REMOVES the current
  cross-namespace duplication.
- D3 semantic near-duplicate pairs (cos>=0.95): A1=7, B=2, C=2,
  A0=0/R=0 (mechanical sets stay lexically spread).
- D4 holdout-doc searchability proxy (352 docs): hit@1 A0 .13 / A1 .18 /
  B .21 / C .22 / R .15; hit@3 similar ordering. Proxy metric, not
  end-user search measurement.
- D5 Interest-Inference packet text: under the evidence_clusters
  eligibility replication (top-40 breadth packets), every challenger
  changes 100% of eligible packet texts (mean ~5-6 tokens/packet); R
  changes 20%.
- D6 consumer enumeration (readers of topic_clusters.label):
  ef/warm_query_service.py (/topics,/trends,digest rendering),
  ef/shadow_discovery.py:221 (shadow query anchors top-25 by member_count),
  ef/evidence_clusters.py:169 (v2 inference packet field),
  scripts/compute_trend_alerts.py:165 (future trend topic identity),
  scripts/topic_inventory_step.py:24, scripts/wiki_from_cluster.py:51,
  scripts/mcp_server.py, scripts/relabel_topics.py (writer tool).
  Classification: all INPUT_CHANGED mechanically under any promotion
  (display text / packet fields / future-derived identities); membership
  substrates (chunk_clusters, centroids, eu) untouched everywhere.

## Operational burden (Arm C generative full-set labeling)

Standalone run measured: elapsed within preregistered 2h wall budget for
the main phases; peak RSS 4.52GB vs the <=4GB gate FAILS strictly.
Production-shape caveat: EF already keeps bge-m3 resident as a service;
the standalone process duplicated it — added deployment cost is lower
than measured. Strict-gate result stands in the decision math; caveat
recorded. Dependencies added: NONE.

## Infrastructure incidents (disclosed, timestamps UTC)

1. Membership freeze taken 2026-08-27T00:33Z before work began; catalog
   never consulted again by experiment code except one read-only
   trend-topics pull at shadow time and Qdrant vector reads by frozen ids.
2. Mid-run drift: shared zen.py (go-llm-proxy client) was modified by a
   concurrent session; my long-lived process kept the older import (3-tuple)
   until detected; adaptation recorded in arm_c.py comments.
3. Free-tier exhaustion window (response.failed "backend returned HTTP
   429" inside SSE): killed Arm C's pert tail, all repeats, several
   blinded-review subagent seats (see ladder above). Fail-closed rule per
   prereg v6 applied to stability gating.
4. Live Qdrant index dropped some frozen point_ids during the run
   (~14 clusters) -> preregistered fallback v6: bge-m3(title) embedding
   for vanished docs; membership untouched.
5. Concurrent-session edits caused duplicate rows in the private labels
   ledger mid-flight; canonical compaction to exactly 638 rows
   (319 x {t0,pert}) performed; file now first-class consistent.
6. Review-limitation disclosures: burden clause is one shared
   standalone-process measurement applied to all arms (cumulative wall
   across resumed repair runs not folded into elapsed_s); the
   preregistration was committed only at session end, so amendment
   ordering rests on mtimes (favorable ordering verified by the
   methodology reviewer); arm_c.clean_output trims over-long outputs to
   8 words — a defensive bound slightly looser than the <=6-word prompt
   contract; contract breaches flow through unmodified and were scored
   as-is by reviewers.

## Decision

**NO_MATERIAL_DIFFERENCE** (frozen decision-mapping branch 4: no
challenger passed the full MATERIAL_BAR because every challenger failed
the standalone-measured <=4GB peak-RSS clause; mechanical reason string:
"signal present but below MATERIAL_BAR").

Translation for the architect (no enum available for this outcome shape):
the blinded quality separation is large and unambiguous — generative
short-labels cleared BOTH primary deltas by 5x the required margin,
collapsed the artifact-flag rate from 69% to 2%, and took 43/45 forced
choices at 98% reviewer agreement — but E3's operational-burden gate as
measured blocks promotion end-to-end, and Arm C carries reduced
stability-phase coverage plus absent temperature-zero repeats from the
upstream outage (fail-closed). Recommendation implied by evidence rather
than by the enum: a scoped HYBRID pilot (C composing labels from B-style
scored terms, run inside EF's resident-model service shape so the 4GB
process metric reflects real deployment cost) is the natural follow-up;
regeneration alone (A1 vs A0 drift, D0=0/319 identical) is itself worth
an architect decision independent of representation choice.

Arm R observation (outside the enum): relabel_topics' mechanism is
semantically near-faithful to stored labels (mean cosine 0.982; differs
on only 38/319 labels) but is PERTURBATION-UNSTABLE (0.714 mean cosine)
and keeps 14/16 trend-topic collisions — it neither fixes the storage
drift problem nor clears the quality gap.

Production cluster labels changed: NO. Concept Registry deployed: NO.
E2/E4/E5 executed: NO.

Publication (this directory, committed): freeze hash, PREREGISTRATION.md
with amendment history v1-v8, all experiment code, aggregate artifacts
mirrored into docs/handoffs/interest-intelligence/cluster-relabel-e3/
(EVALUATION.json, STABILITY.json, SHADOW-IMPACT.json, this RECEIPT).
Private mirrors (.data): frozen snapshot, per-item reviews, ARM-KEY/
MASK-KEY, raw label sets, tainted-output quarantine.

NEXT IMPLEMENTER DECISION:
    ARCHITECT PENDING
