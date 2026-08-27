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

- 2026-08-26: [seen] Interest-recovery gate EVALUATED (fresh evaluator,
  preregistration interest-recovery-v1 frozen pre-scoring) and FAILED.
  Aggregate: legacy top-25 baseline recovered 1/42 known interests
  (narrow half 0/14, provenance valid 1.0, explicit-negative rate 0.50
  after the review-fixed rescore, 0.083 pre-fix); the full-coverage bootstrap completed 0/3 attempts — every
  attempt failed closed on provider contract violations (dangling
  related_to references twice, invalid temporal_state enum once) before
  reconciliation. Full-coverage recall is therefore unavailable, not
  merely low. Deterministic-replay stability of scoring verified
  identical; the four preregistered perturbation schemes were not
  executable without a completed bootstrap run.
- Structural finding: the bootstrap's fail-closed validation is working
  as designed (nothing invalid persisted), but per-batch provider
  noncompliance with the strict fragment contract makes a 13-batch run
  plus reconciliation unable to complete. The bottleneck is provider
  contract compliance at the batch seam, not coverage planning.
- Recommendation optimization remains blocked. Next architecture fork:
  either raise batch-level provider contract compliance (prompt-side
  compliance engineering, NOT semantic tuning — e.g. structural
  few-shot, repair-and-retry of invalid batches, or per-field
  validation relaxation decisions owned by the architect) or accept the
  legacy arm's output as the only completable inference and re-scope
  the gate. Both are architect decisions; the evaluator did not tune.

---

# 2026-08-27 UPDATE — contract architecture v2 (sess_ab0a0135)

Contract architecture generation v2 executed per architect packet. Prior
verdict CONTRACT_RELIABILITY_NOT_SUPPORTED stands for the monolithic
contract; a decomposed architecture was built, preregistered frozen
(contract-architecture-v2-preregistration.md), measured live on the same
unlabeled corpus + plan_01b09359b3f05784, and completed ALL six success
requirements including 3/3 clean-root full-coverage shadow bootstraps.

DECISION: DECOMPOSED_CONTRACT_SUPPORTED (shadow-only wiring live;
`scripts/contract_v2_bakeoff.py --shadow [N]`; persistence unreachable
by construction — canonical promotion still requires the operator's
Interest ground-truth curation lane + valid semantic-recall gate).

Evidence: contract-architecture-v2-results.md. Freeze+amendments:
959cd1ad, 831944e5, 4008a701, 02f56240. Reviews run-60dee6f5bb3d /
2104809e6a0f / e383c0c54d89 / 3b7dad037e99 / da0916a65d1d.
Key facts: decomposed phase-1 = 39/39 defect-free across arms;
monolithic reconciliation >=4 live failures across bakeoff-1+v2
(definitive); decomposed reconciliation zero silent loss across arm and
all 3 shadows; endpoint rejects uniqueItems and nested $defs.
