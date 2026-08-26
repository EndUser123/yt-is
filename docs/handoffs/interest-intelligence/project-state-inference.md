# yt-is Personal Intelligence — Inference State
Updated: 2026-08-24 by architect handoff; v2 contract-fidelity and
full-coverage bootstrap implementer passes

## Goal & constraints

- [seen] Infer durable and active interests, goals, information needs,
  questions, and adjacent/regret candidates from multi-view corpus evidence.
- [seen] Preserve provenance sufficient to audit every material inference back
  to evidence clusters and ultimately source evidence.
- [seen] LLMs perform semantic interpretation; mechanical layers perform
  candidate generation, counting, breadth, temporal statistics, and coverage.
- [seen] Narrow but important interests must not be excluded merely because
  broad clusters have greater channel breadth.
- [seen] Inference quality is a separate gate from recommendation quality.

## Non-goals

- [seen] Do not infer interests directly from raw entity counts.
- [seen] Do not treat syntactically parseable JSON as validated typed state.
- [seen] Do not let prose summaries substitute for relational provenance.
- [seen] Do not run the regret-ranking falsifier until inference recall and
  provenance are adequate.

## Decisions

- 2026-08-24: [seen] Multi-view evidence clusters are the inference unit.
- 2026-08-24: [seen] Observation, Interest, Goal, and InformationNeed remain
  separate concepts.
- 2026-08-24: [seen] Every inferred interest must reference supplied evidence.
- 2026-08-24: [seen] Counterevidence must remain representable.
- 2026-08-24: [seen] Inferred-adjacent/regret candidates must remain distinct
  from observed interests.
- 2026-08-24: [seen] A blinded interest-recovery plus perturbation/stability
  gate precedes downstream recommendation evaluation.
- 2026-08-24: [seen] V2 inference output must validate fail-closed before
  persistence; accepted semantic objects use deterministic identity and one
  transactional typed-graph write with inference-run provenance.
- 2026-08-24: [seen] Bootstrap inference must not truncate the mechanically
  eligible cluster universe to a global top-N; it uses deterministic bounded
  cluster batches plus bounded auditable reconciliation, while the prior
  top-25 breadth policy remains an explicit baseline.

## Current state

- [seen] v1.5 evidence clusters shipped at `e7c2b6c0`.
- [seen] `scripts/build_interest_graph.py` sends evidence-cluster packets to
  an LLM provider.
- [seen] Codex JSONL extraction was added at `7446d526`.
- [seen] The prompt requests interests, goals, questions, cluster IDs,
  counterevidence, relationships, and regret candidates.
- [seen] V2 provider output is mechanically validated before persistence for
  required structure, enums, confidence bounds, evidence-cluster references,
  and internal interest/question relationships.
- [seen] Invalid provider output fails closed before semantic database
  mutation.
- [seen] V2 persistence transactionally materializes deterministic interests,
  goals, information needs, parent hierarchy, questions, regret candidates,
  typed relationships, evidence-cluster provenance, and inference-run
  metadata.
- [seen] Replaying identical validated semantic output is idempotent for
  semantic objects and relationship edges.
- [seen] Focused offline regression tests cover contract rejection,
  fail-closed behavior, typed persistence, idempotence, rollback, and
  provider-output parsing.
- [seen] The former top-25 breadth policy remains available only as an
  explicit evaluation baseline.
- [seen] Bootstrap candidate planning enumerates the complete mechanically
  eligible cluster universe and covers each eligible cluster exactly once
  across bounded batches of at most 25.
- [seen] Batch inference outputs are validated intermediates and are not
  persisted directly as canonical graph state.
- [seen] Global reconciliation is bounded recursively and requires an
  auditable disposition for every semantic fragment before final V2
  validation/persistence.
- [seen] Read-only current-corpus planning confirmed bootstrap candidate
  coverage of 319/319 eligible clusters (100.0%) across 13 batches; this is
  candidate coverage, not yet personal-interest recall.
- [seen] Focused offline tests cover inventory completeness, batch coverage,
  top-N baseline contrast, bounded reconciliation, fragment disposition
  integrity, and fail-closed behavior.
- [claimed] One live inference produced coherent software, trading, options,
  macro, media-production, and knowledge-automation interests/goals.
- [claimed] That reported result did not visibly recover several deliberately
  relevant validation domains including longevity, ADHD mitigation, and
  cognitive enhancement.

## Open questions

- What bounded candidate-selection policy gives adequate recall of narrow but
  important interests?
- Should candidate selection combine breadth, specificity, recency,
  acceleration, source diversity, semantic coverage, and stratification?
- Should inference become hierarchical/batched followed by reconciliation?
- What known-interest and negative-control validation set should be frozen?
- What recall and stability thresholds gate recommendation evaluation?
- How should generated relationships be referentially validated?

## Next action

- 2026-08-26 (architect correction, same day): the interest-recovery
  evaluation recorded below used the WRONG ground truth. Evaluator
  interest-recovery-v1 treated Discovery temporal-emergence artifacts
  (holdout-v4 targets curated for retrospective concept-emergence from
  raw-corpus PRODUCT|TECH|ORG|CONCEPT; case-control paired negatives)
  as a known-Interest/Goal/InformationNeed/Question holdout. They are
  not: they establish no operator-confirmed Interests, Goals,
  Information Needs, or Questions and no explicit negatives for
  Interest inference.
- Classification: INVALID_EVALUATION_GROUND_TRUTH. All label-dependent
  metrics from that run (1/42 recall_all, 0.036 supported recall, narrow
  0/14, explicit-negative rate 0.50) are WRONG_GROUND_TRUTH_DIAGNOSTIC_ONLY;
  its FAIL verdict is INVALID_FOR_INTEREST_RECOVERY. The Interest
  semantic-recall gate remains UNRESOLVED / NOT YET VALIDLY RUN.
- Valid retained finding (label-free):
  FULL_COVERAGE_INFERENCE_COMPLETION_FAILURE — 3 attempted full-coverage
  bootstrap runs, 0 completed; every failure occurred before
  reconciliation/persistence (two dangling related_to violations, one
  invalid temporal_state enum); fail-closed validation correctly
  prevented invalid persistence. Inference-completion reliability can be
  repaired independently, without any private Interest label access.
- Recommendation optimization remains blocked because valid Interest
  recall is still unknown.
- Required next lane: dedicated Interest ground-truth curation
  (operator-confirmed Interests, Goals, Information Needs, Questions),
  contamination-separated from this evaluator session, which has seen
  private Discovery labels and is retired from all inference tuning and
  Interest holdout curation/evaluation.
- Historical record of the invalidated run: [seen] interest-recovery-v1
  preregistered pre-scoring; legacy top-25 baseline produced one valid
  payload (12 interests) while the bootstrap failed completion 3 times;
  perturbation schemes unexecutable without a completed bootstrap run;
  deterministic-replay stability verified. Those numbers carry no
  Interest-recovery meaning.
