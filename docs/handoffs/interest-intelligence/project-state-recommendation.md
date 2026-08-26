# yt-is Personal Intelligence — Recommendation State
Updated: 2026-08-26 by feedback-contract hardening session (agent: zcode)

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

- [seen] A feedback table exists (legacy `feedback`, frozen read-only
  history; nothing writes it since 2026-08-26).
- [seen] Current verdict vocabulary includes `useful`, `known_already`,
  `not_interested`, `wrong_inference`, `investigate`, `acted_on`, `save`,
  `more_like`, and `less_like`.
- [implemented, runtime-tested 2026-08-26] Impression + feedback-event
  contract in `ef/personal_graph.py`: `candidate_sets`,
  `impressions` (immutable; policy/version, rank, why-surfaced,
  provenance incl. render trigger `request|warm`; unknown fields NULL),
  `feedback_events` (immutable, unique ids, idempotency-keyed; key reuse
  with different payload rejected), `item_workflow_state` (mutable state,
  separate from event history; only `investigate`/`save`/`acted_on`/
  `not_interested` transition; evaluation verdicts never do).
- [implemented, runtime-tested 2026-08-26] `/feedback` is POST+JSON only
  on both servers (:6391, :6393) with Host allowlist + same-origin check;
  GET returns 405 and mutates nothing. No GET compat path (the only
  client, the Today page JS, migrated in the same change).
- [implemented] `/today` records a candidate set + impressions at render
  with stable item ids (`cluster:<id>`, `video:<id>` — note the namespace
  break vs legacy rows) and carries impression ids into feedback links.
  One batch == one 10-min TTL cache regeneration; whether an operator
  actually viewed a batch is explicitly unknown (not fabricated).
- [verified] No feedback path writes `interests` or any inference state.
- [implemented, runtime-tested 2026-08-26 closure] Additive event
  annotations (`feedback_event_annotations`): exclusion-from-evaluation
  marks without mutating the immutable event row. The synthetic probe
  from live contract verification (fe_3be9c657bd724962a5190e3bc226ef21,
  surface=probe, verdict=wrong_inference) is annotated test_probe /
  excluded; evaluation reads exclude it by default and audit/raw access
  preserves it. Raw immutable history remains fully inspectable.
- [seen] Current `/today` is not the final goal-aware utility scorer
  (policy `mechanical-clusters-recency` v1).
- [absent-unverified] Similarity+recency baseline for the regret experiment.
- [absent-unverified] Goal-aware ranking implementation suitable for fair
  comparison.
- [absent-unverified] Blinded evaluation harness and promotion threshold.
- [absent-unverified] Adaptive/contextual-bandit utility learner.

## Open questions

- How should feedback verdicts map, if at all, to utility rewards?
- How should `known_already` differ from `not_interested`?
- How should `save`, `investigate`, and `acted_on` be interpreted without
  conflating attention with utility?
- What candidate-set protocol makes the baseline comparison fair?
- What promotion threshold is practically meaningful?
- View-level attribution: impression batches record render trigger
  (request/warm) but not operator views — render != confirmed operator
  view stays an open future-ranking question until the ranking experiment
  design makes it material.

## Next action

Feedback semantics are hardened; new history accumulates under the
contract. Defer the main ranking falsifier until inference
recall/provenance passes its gate; then build the similarity+recency
baseline and blinded regret evaluation against the captured
impression/feedback history.
