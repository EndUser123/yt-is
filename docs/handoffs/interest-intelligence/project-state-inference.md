# yt-is Personal Intelligence — Inference State
Updated: 2026-08-24 by architect handoff; v2 contract-fidelity implementer pass

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
- [seen] Current inference sends at most 25 clusters from a breadth-ranked
  candidate set.
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

Implement and falsify a bounded high-recall candidate-selection strategy,
then run the private blinded interest-recovery and perturbation/stability
gate. Do not begin recommendation-ranking optimization until that gate
passes.
