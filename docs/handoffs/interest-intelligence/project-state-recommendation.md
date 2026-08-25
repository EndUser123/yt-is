# yt-is Personal Intelligence — Recommendation State
Updated: 2026-08-24 by architect handoff

## Goal & constraints

- [seen] Rank information by expected decision usefulness rather than semantic
  similarity alone.
- [seen] Candidate utility may include goal relevance, evidence quality,
  novelty-to-user, expected information gain, actionability, timeliness,
  redundancy, saturation, and feedback.
- [seen] Preserve impression/ranking context sufficient for later evaluation.
- [seen] Compare against a simple baseline rather than assuming graph-aware
  ranking is superior.

## Non-goals

- [seen] Do not optimize an advanced recommender before inference recall is
  adequate.
- [seen] Do not treat clicks alone as utility.
- [seen] Do not use collaborative filtering as the primary formulation for a
  predominantly single-user system.
- [seen] Do not claim superiority from an unblinded or incomparable test.

## Decisions

- 2026-08-24: [seen] Ultimate falsifier: goal/claim-aware ranking versus
  similarity+recency on blinded "would regret missing this" judgments.
- 2026-08-24: [seen] If the graph-aware system fails to beat the baseline, keep
  the simpler ranking architecture and use the graph principally for
  explanation/visualization.
- 2026-08-24: [seen] Explicit feedback begins before adaptive ranking.
- 2026-08-24: [seen] Contextual-bandit learning is later work, after simple
  deterministic/weighted baselines and sufficient feedback.

## Current state

- [seen] A feedback table exists.
- [seen] Current verdict vocabulary includes `useful`, `known_already`,
  `not_interested`, `wrong_inference`, `investigate`, `acted_on`, `save`,
  `more_like`, and `less_like`.
- [seen] `/feedback` currently performs a state mutation through HTTP GET.
- [seen] Current feedback records lack an immutable event ID, impression/run
  ID, rank position, candidate-set identity, and ranking-policy/version
  context.
- [seen] Current `/today` is not the final goal-aware utility scorer.
- [absent-unverified] Similarity+recency baseline for the regret experiment.
- [absent-unverified] Goal-aware ranking implementation suitable for fair
  comparison.
- [absent-unverified] Blinded evaluation harness and promotion threshold.
- [absent-unverified] Adaptive/contextual-bandit utility learner.

## Open questions

- What immutable impression/event schema is sufficient for trustworthy offline
  evaluation?
- How should feedback verdicts map, if at all, to utility rewards?
- How should `known_already` differ from `not_interested`?
- How should `save`, `investigate`, and `acted_on` be interpreted without
  conflating attention with utility?
- What candidate-set protocol makes the baseline comparison fair?
- What promotion threshold is practically meaningful?

## Next action

Harden feedback event semantics before substantial history accumulates, but
defer the main ranking falsifier until inference recall/provenance passes its
gate.
