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

Run a fresh, contamination-separated evaluator comparing the preserved top-25
baseline against the full-coverage bootstrap on the private known-interest
set, including perturbation/stability tests. Recommendation-ranking
optimization remains blocked until that semantic recall gate passes.
