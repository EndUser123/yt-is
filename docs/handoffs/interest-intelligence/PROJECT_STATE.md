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
