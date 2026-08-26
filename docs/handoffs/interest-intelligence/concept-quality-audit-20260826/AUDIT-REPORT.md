# Concept / KG Extraction Quality Audit — 2026-08-26

Agent: zcode (burst, cold-start implementer session). Diagnostic only; no production,
extraction, schema, or data changes. Audit base: origin/main b4c2bef5 in isolated worktree
P:/tmp/ytis-audit-b4c2bef5.

## Representation audited (Task 1 finding that reframes the audit)

The Concept Registry layer (concepts / concept_aliases / concept_observations /
concept_relations / trend_episodes; ef/concept_registry.py) has NO production data:
no sqlite file under P:/.data/yt-is contains any registry table. The registry exists
in code only. The production Concept/KG substrate is, in catalog.sqlite
(P:/.data/yt-is/ef/catalog.sqlite, generation 1):

- entities (7390 LLM-extracted names; types CONCEPT/PRODUCT/ORG/TECH/PERSON/PLACE)
- entity_corpus (388 names that passed FTS phrase qualification) -> kg_nodes kind='entity' (388)
- topic_clusters (351; 319 non-series) with mechanical top-4-title-term labels
- kg_edges: mentioned_in 91,670 / in_channel 36,365 / of_source 3,945
- trend_alerts (56 rows, 16 distinct topics)
- eu (36,583 evidence units with channel/source/title/published_at provenance)

Extraction is source-agnostic at the KG layer; source differences exist only in EU
metadata shaping. Discord DHT bulk capture is 47% of EU mass (17,191 EUs, 436 channels,
top channel 14% share — broad, not single-channel).

## Sampling (Task 2)

Frozen deterministic policy sample.py (policy hash 290a3fbdd9b6a031), manifest
sample-manifest.json written before review. Strata: entity nodes by mentioned_in
degree tertile x publisher-diversity tertile; clusters by member_count bucket
(<100 / 100-999 / >=1000); edges by FTS weight bucket (2 / 3-5 / >5); trend topics
by stride. Sample: A=51 entities, B=30 clusters, R=60 edges, T=15 trend topics.
v1 sample and both v1 reviews discarded (sampler defect: publisher/channel fields
blank because kg eu meta_json is empty and titles live in the eu table); v2 re-frozen
and re-reviewed by two fresh-context reviewers. Hidden: emergence labels, holdout
status, v1/v2 posteriors, lifecycle, recommendation outcomes.

## Reviewer agreement (Task 4)

Concepts exact agreement 65/81 = 0.80. Relations 49/60 = 0.82. Trends 14/15.
Adjudication rule (preregistered in aggregation script): exact match kept; one-GOOD
disagreements resolve to the non-GOOD verdict (conservative); other disagreements
resolve by fixed severity class order.

## Concept quality (Tasks 3, 5) — adjudicated, n=81

| class | n | rate |
|---|---|---|
The n=81 concept total is a two-layer mix (entity + cluster) with known semantic
nesting between layers (e.g. an instance entity belonging to a sampled cluster);
the layer-decomposed rates below are the citable numbers.

| GOOD_CONCEPT | 45 | 0.56 |
| TOO_GENERIC | 17 | 0.21 |
| EXTRACTION_ARTIFACT | 16 | 0.20 |
| TOO_SPECIFIC_OR_EPHEMERAL | 1 | 0.01 |
| ALIAS_FRAGMENT | 1 | 0.01 |
| TYPE_ERROR | 1 | 0.01 |
| AMBIGUOUS | 0 | 0 |

Breakdowns:
- entity_concept (n=51): good 0.69, too_generic 0.08, artifact 0.18.
- cluster_concept (n=30): good 0.33, too_generic 0.43, artifact 0.23. HYPOTHESIS
  (pending a member-title export): cluster failure is label quality, not referent —
  reviewers judged most clusters decode to coherent topics behind artifact labels,
  but member titles were not inspectable (see Provenance).
- Evidence mass: zero-evidence concepts (7/51 entities) good-rate 0.00; <10 ev 0.77;
  10-99 0.62; >=100 0.54 (high-evidence entities still fail via word-collision labels).
- Publisher diversity (entities): single-publisher 6/6 good in sample (small n);
  failure is not concentrated in single-publisher — it concentrates in label quality
  and evidence join.
- Mean scores (1-5, two reviewers averaged): evidence_traceability 3.44, label_clarity
  3.09, granularity 3.09, cross_source_identity 2.91.

Structural (corpus-wide, not sample):
- 388 / 7390 extracted entities survive FTS qualification (5.3% survival; 94.7%
  attrition between extraction and graph).
- 67 / 388 graph entity nodes have zero mentioned_in edges (orphan rate 17%).
- 37 / 321 connected entity nodes single-publisher (11.5%); 275 multi-publisher.
- entities table: 779 casefold-collision names (e.g. claude x21, microsoft x30) —
  no alias/canonicalization in production path.
- published_at missing for 18,944 / 36,267 eus (52%; dominated by discord bulk
  capture, which carries no timestamps) — direct temporal-evidence gap.
- Trend topics vs cluster labels: 5 of 15 sampled trend topics are byte-identical
  to cluster labels (flagged by reviewer 1, one additionally by reviewer 2) — no
  cross-namespace dedup key.
- Cluster->member-title evidence is not exported anywhere reviewers can reach:
  cluster evidence traceability is structurally capped (top_terms only).

## Relationship quality (Task 6) — n=60 mentioned_in edges

| class | n |
|---|---|
| SUPPORTED_RELATION | 43 |
| WEAK_OR_UNSUPPORTED | 17 |

- No WRONG_DIRECTION, WRONG_RELATION_TYPE, or DUPLICATE_EDGE in the sample
  (direction is structurally fixed by the builder).
- Weakness concentrates in (a) bulk discord chat-window documents, (b) placeholder/
  UI-string documents ("Now playing", blank titles) admitted as EUs, (c) polysemous
  short tokens ("AI", "Slides") where FTS phrase match does not establish the
  intended sense.
- Evidence reconstructability: partial. The edge exists only as an FTS count
  (weight); distinct-source corroboration is not stored, so within-document
  repetition and cross-source agreement are indistinguishable. 3/60 edges point at
  placeholder documents and are unreconstructable.

## Root-cause taxonomy (Task 7) — earliest mechanical cause per failure

RC1 Entity qualification (entity_corpus FTS phrase >= 2): causes both the 94.7%
attrition and the promotion of common-noun word-collision labels (Keen, Finder,
Slides). Earliest cause: label normalization has no polysemy/word-level gate and
FTS existence is treated as semantic support. -> TOO_GENERIC + artifacts.
RC2 No evidence floor at node creation: entities enter kg_nodes with 0 edges
(67 orphans; 7/51 in sample, 0% good). -> EXTRACTION_ARTIFACT.
RC3 Cluster label generation (top-4 title terms, ef/clustering.py:223-254):
mechanical concatenation yields generic/artifact labels while membership is often
coherent. -> cluster TOO_GENERIC 0.43 / artifact 0.23.
RC4 Discord DHT bulk capture without timestamps (empty published_at,
ingest_connectors.py:124-128) + chat-window EUs with placeholder titles.
 -> temporal evidence gaps (52% of EUs undated) + weak/unreconstructable edges.
RC5 No canonicalization/alias layer in the production path (registry undeployed;
entities table has 779 casefold collisions; trend labels duplicate cluster labels).
 -> DUPLICATE_OR_FRAGMENT / ALIAS_FRAGMENT class + double counting risk.
RC6 KG builder admits placeholder/UI-string documents as EUs (build_knowledge_graph
ingestion has no title sanity filter). -> unreconstructable edges.

## Impact analysis (Task 8)

- Temporal Emergence: EVIDENCE-BACKED RISK. 52% of EUs undated degrades windowing
  wherever discord mass dominates; cluster-label genericity (0.43) feeds trend topic
  identity (trend labels derive from the same top-term mechanism and duplicate
  cluster labels). The prior formal eval's v2 selectivity failure is consistent with,
  but not proven caused by, these defects (no counterfactual measured).
- Interest Inference: EVIDENCE-BACKED RISK. cross_source_identity 2.91/5 and absent
  alias layer; entity 5% survival starves the candidate pool.
- Semantic adjacency / bridge discovery: EVIDENCE-BACKED RISK. Adjacency rides the
  same FTS/cluster substrate; no measured adjacency metric exists (not measured here).
- Recommendation / External Intelligence: HYPOTHESIS (downstream of the above; no
  direct measurement).
- Verified impact (measured here): concept good-rate 0.56 overall / 0.33 clusters;
  relation support 0.72; evidence reconstructability partial by construction.

## Decision

CONCEPT_LAYER_PARTIAL.

Entity path is salvageable (0.69 good with targeted label/evidence gates); cluster
labeling and namespace canonicalization are the dominant defects; the registry
(promises aliases, provenance, relations, lifecycle) is undeployed, so the
architectural requirements list is currently satisfied only in code, not in data.

## Prioritized discriminating experiments (no fixes implemented)

E1 Evidence floor + distinct-source count at entity node creation (kills RC2,
tests how much of the 0.18 artifact rate and 17% orphan rate are zero-evidence
noise). Measure: re-audit frozen strata good-rate delta.
E2 Polysemy/word-level gate on entity labels + alias fold of the 779 collision
families (tests RC1's contribution to TOO_GENERIC 0.08 + weak edges). Measure:
re-audit; FTS-qualification survival delta.
E3 LLM relabel of cluster labels only, membership frozen (tests RC3; reviewers
predict most clusters are coherent behind artifact labels). Measure: cluster
good-rate 0.33 -> ? on frozen sample.
E4 Discord date policy: impute window dates or exclude undated EUs from temporal
features (tests Temporal Emergence impact). Measure: dated-EU coverage under
burst-policy-v1 windowing; trend topic identity stability.
E5 Registry deployment decision: run one discovery pass against the registry schema
or formally descope it (the architectural requirements currently have no data
substrate). Measure: registry table population + observation provenance completeness.

## Fresh-context methodology review

general-5-3-max reviewer, APPROVE_WITH_NOTES: sampling independence, rubric,
hidden-outcome discipline, reviewer-agreement arithmetic, and root-cause support
all PASS (numbers independently recomputed from raw artifacts; v1->v2 re-freeze
verified as moving the headline against the implementer's favor). CONCERNS:
aggregation-script archival (fixed: aggregate.py archived), two overstatements
(fixed above), and the n=81 layer-mix qualification (fixed above).

## Provenance and limits

- Full sample packets, both v2 reviews, aggregate.json, and the sampler live under
  P:/.data/yt-is/ef/concept-quality-audit-20260826/ (uncommitted; contain private
  sampled concept names). This committed report carries rates and redacted examples only.
- Sample sizes are modest (81/60/15); rates carry roughly +/-8-10pp binomial noise
  at these n; the corpus-wide structural rates (attrition, orphans, undated share)
  are exact counts, not samples.
- Reviewers could not inspect cluster member titles (not exported) — cluster
  evidence traceability is a measured absence of the export, possibly of the substrate.
