# Intelligence Service — cross-workstream project state

Updated: 2026-08-26 by the explicit-negative/evaluator-v4 implementer with
the architect scope-reconciliation amendment (replaces the evaluator-v2-era
snapshot from 2026-08-24/25).

## Canonical architecture (architect clarification 2026-08-26)

Discovery is a SUBSYSTEM, not a single policy. burst-policy-v1/v2 and the
retrospective case-control evaluation own ONE slice of it:

    TEMPORAL CONCEPT EMERGENCE

Discovery independently contains:
A. open-world concept candidate discovery (entities/clusters surface
   without predeclared names — exists: entity_burst + semantic-cluster
   paths);
B. temporal emergence / trend episodes (burst-policy-v1 production
   default; burst-policy-v2 SHADOW, calibrated but not frozen);
C. semantic-step-outward adjacency discovery (machinery exists via
   horizon-scout adjacent share; generation is currently SHALLOW);
D. cross-domain bridge / relationship-emergence discovery
   (concept_relations provides the STORAGE SUBSTRATE; bridge discovery is
   NOT yet proven as a first-class detector);
E. external horizon discovery (horizon-scout known/adjacent/wildcard
   machinery exists; wildcard queries are currently software-centric);
F. downstream personal utility / regret ranking (recommendation
   workstream; gated behind inference recall/provenance).

Preserved distinctions:
- Concept != Interest.
- Durable memory != durable salience.
- World signal != personal relevance.
- Internal discovery != external discovery.

Higher-order relevance center: durable personal agency / leverage /
resilience / optionality. CONCRETE OPERATIONAL ANCHORS — not this broad
sentence — drive search/query generation.

Horizon-scout exploration policy: 70/20/10 known/adjacent/wildcard is
preserved as an INITIAL policy (not a tuned optimum).

## Scope of the current evaluation work (de-conflated)

The explicit-negative / evaluator-v4 question is ONLY:

    "Does temporal-emergence policy v2 discriminate genuinely persistent
     emerging episodes from comparable explicit non-emerging episodes?"

It is NOT "does the overall Discovery subsystem have an acceptable
false-positive rate". Overall-subsystem selectivity is not measured by
this evaluator; conflation of the two gates is removed.

## Current state (2026-08-26)

- burst-policy-v2: implemented SHADOW (published feba5ec9); production
  default remains burst-policy-v1; parameters frozen, no tuning.
- Evaluator lineage: v1 → v2 (formal v2 consumed: holdout-v4 FAIL) →
  v3 (entity-only symmetric comparators, aligned baselines; diagnostic
  exposed the outcome-unlabeled comparator problem) → v4 (explicit
  curator-supplied negative ground truth; comparators demoted to
  secondary diagnostic).
- holdout-v4: consumed, TRAINING_DIAGNOSTIC_ONLY forever; never
  promotion evidence.
- Single-use formal ledger: global, content-hash claimed before label
  parsing; crash-after-claim consumes permanently. Synthetic-hash
  disclosure: cd9733d9… FAILED_AFTER_CONSUMPTION from a pre-fix test
  bug (no real holdout affected; row preserved; tests now redirect the
  ledger via YTIS_FORMAL_LEDGER_PATH).
- Inference workstream: semantic recall gate EVALUATED 2026-08-26 and
  FAILED (see below); Recommendation, dashboard, and external expansion
  remain downstream of the inference/discovery evidence gates.
<<<<<<< HEAD
- Interest-recovery gate result (evaluator interest-recovery-v1,
  preregistered pre-scoring, aggregate only, no private names):
  legacy top-25 baseline recall 0.024 (all) / 0.036 (supported subset) /
  0.000 (narrow half), provenance valid 1.0, explicit-negative rate
  0.50 (post review-fix rescore; 0.083 pre-fix); full-coverage bootstrap completed ZERO runs in 3 consecutive
  attempts — fail-closed contract violations (dangling related_to at
  batches 4 and 3, invalid temporal_state enum at batch 1) — so
  full-coverage recall is unavailable. Verdict FAIL; recommendation
  optimization remains blocked. Perturbation/stability runs beyond
  deterministic-replay (verified identical) were not executable without
  a completed bootstrap run.
||||||| b4c2bef5
=======
- Recommendation prerequisite (2026-08-26, agent: zcode): feedback and
  recommendation-observation event semantics HARDENED ahead of history
  accumulation — immutable `impressions`/`feedback_events` +
  candidate-set capture with policy/version/rank on `/today`,
  workflow-state separated from event history, `/feedback` moved from
  mutating GET to POST+JSON (405 on GET) on :6391/:6393, idempotent
  retries with key-reuse rejection, legacy `feedback` table frozen
  read-only. No ranking/algorithm change; bandits prohibited and absent.
  See project-state-recommendation.md.
>>>>>>> cb22856acc4e
- Evaluator-v3's 0.344 matched-comparator emerging rate is an
  OUTCOME-UNLABELED comparator rate, NOT a measured false-positive rate
  (audit: 125 rows / 33 unique concepts, heavy reuse; 6 of 21 promoted
  unique comparators had positive-like raw future evidence).

## Open questions

- Will evaluator-v4's explicit-negative diagnostic clear the unchanged
  pass-like axes (running at time of writing)?
- Which Discovery slice (C/D/E) justifies the next architecture packet
  after temporal emergence resolves?
- Same as before: after discovery/inference gates, which downstream
  workstream has highest decision-value priority.

## Next action

- Finish the explicit-negative/evaluator-v4 packet under the narrowed
  TEMPORAL_EMERGENCE scope: diagnostic → decision rule → (freeze and
  stop) or (stop without freeze, return to architect).
- Do NOT implement adjacency, bridge discovery, external expansion, or
  dashboard work in that packet.

## Concept/KG extraction quality audit (2026-08-26, agent: zcode)

Cold-start blinded audit of the production concept/KG substrate (full report:
docs/handoffs/interest-intelligence/concept-quality-audit-20260826/AUDIT-REPORT.md;
raw private packets under .data/yt-is/ef/concept-quality-audit-20260826/, uncommitted).
Decision: CONCEPT_LAYER_PARTIAL. Headline: adjudicated concept good-rate 0.56 overall
(entity 0.69, cluster 0.33); relations 0.72 supported (no wrong-direction/duplicate/
type errors in sample); reviewer agreement 0.80/0.82; methodology review APPROVE_WITH_NOTES.
Key structural facts: the Concept Registry (concepts/aliases/observations/relations
tables) is deployed in CODE ONLY — no production DB contains registry tables; the
audited substrate is entities/kg_nodes/kg_edges/topic_clusters/trend_alerts in
catalog.sqlite. Corpus-wide: 388/7390 extracted entities survive FTS qualification
(5.3%); 67/388 graph entity nodes orphaned; 52% of EUs undated (discord bulk capture);
779 casefold-collision entity names (no alias layer); 5/15 sampled trend labels
byte-duplicate cluster labels. Root causes RC1-RC6 and discriminating experiments
E1-E5 (evidence floor + distinct-source count, label polysemy gate, cluster relabel,
discord date policy, registry deploy-or-descope) are in the report. No production,
extraction, schema, or data changes were made.


## E1 executed: evidence-backed entity admission + publisher accounting (2026-08-26, agent: zcode)

Decision E1_SUPPORTED; implemented and migrated via deterministic KG rebuild (full
report: concept-quality-audit-20260826/E1-REPORT.md). Root cause verified in code:
entity_corpus admission ran on LLM self-reported mention sums with no evidence floor,
while the builder materialized a node for every corpus row independently of the
per-EU FTS edge staging. All 67 orphans classified mechanically:
QUALIFICATION_DEFECT 53 / NO_SUPPORT_CURRENT 13 / STALE_GRAPH_HAS_SUPPORT_NOW 1.
Counterfactual (frozen snapshot): Arm A 388 nodes vs Arm B 313, edges identical;
75 zero-support nodes removed, zero supported edges lost by the floor. Frozen-sample
re-audit (same policy hash): 7 sampled entities removed, all EXTRACTION_ARTIFACT,
0 GOOD; good-rate 0.686 -> 0.795. Production after rebuild: entity nodes 388 -> 313,
orphans 67 -> 0, mentioned_in 91,670 -> 102,454 (delta is index drift shared by any
rebuild), double-rebuild receipt identical. Independent-publisher accounting stored on
every entity node as meta_json.evidence (discord=guild identity, hackernews/newsletter=
explicit UNKNOWN, YouTube modalities share UC id); AUDIT FEATURE ONLY, never a gate.
Downstream inputs: TE/interest/adjacency/shadow-anchor pools UNCHANGED (measured);
warm-query entity browse list shrinks by exactly the 75 artifacts. Concept Registry
NOT deployed; E2 (polysemy gate + alias fold), E3 (cluster relabel), E4 (Discord date
policy), E5 (registry deploy-or-descope) remain deferred to architect.
