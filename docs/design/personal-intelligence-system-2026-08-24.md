# Design: Personal Intelligence System — yt-is end-state architecture

agent: zcode | host: both | status: spec-preserved-execution-started | created: 2026-08-24
supersedes: the "v2 interests" scope of interest-graph-2026-08-24.md
(v1.5 evidence clusters remain the substrate — unchanged)

## Operator directive (2026-08-24, condensed — full text in session log)

The system is not a content recommender. It is a personal intelligence
graph with recommendation as one downstream consumer.

corpus → evidence clusters → durable interest/goal model →
novelty/opportunity detection → research/recommendation →
decisions/actions → feedback → improved model

The dashboard answers: what am I trying to understand or accomplish?
what changed? what am I missing? what connects my domains? what is
worth acting on now? what have I already learned? which beliefs are
changing?

## Layered state model (three separations, not one scalar)

OBSERVATION: "many documents about ApoB, exercise, Alzheimer's"
INTEREST: "healthy aging / cognitive preservation"
INFORMATION NEED / GOAL: "identify interventions that reduce long-term
dementia risk without excessive cost/risk"

Temporal states — DO NOT collapse into interest_score:
durable_interest | active_interest | current_problem |
episodic_investigation | emerging_interest | dormant_interest

## Typed personal graph (relational tables, NO graph database yet)

USER  ├ INTERESTED_IN → Interest   ├ PURSING → Goal
      ├ BELIEVES → Claim           ├ INVESTIGATING → Question
      └ ACTED_ON → Action
Interest: SUBTOPIC_OF / SUPPORTS→Goal / RELATED_TO / CONTRADICTS
Evidence: SUPPORTS→Claim / CONTRADICTS→Claim / ABOUT→Interest /
ANSWERS→Question

This is personal epistemic state, not recommendation input.

## Tables (v2 schema, catalog.sqlite)

    interests(interest_id, name, kind, parent_id, temporal_state,
              stance, confidence, intensity, persistence, recency,
              trajectory, observed_vs_inferred, goal_id, updated_at)
    goals(goal_id, statement, status, created_at)
    questions(question_id, text, status, interest_id, opened_at)
    claims(claim_id, statement, confidence, last_challenged_at)
    evidence_links(link_id, src_kind, src_id, dst_kind, dst_id,
                   relation, strength, created_at)
    feedback(ts, surface, item_kind, item_id, verdict, note)

## Scoring (utility, not similarity)

utility = relevance_to_goal × evidence_quality × novelty_to_user ×
expected_information_gain × actionability × timeliness
penalties: already_known, redundancy, source_duplication,
low_evidence_quality, topic_saturation

Novelty classification per surfaced item:
NEW_TO_WORLD | NEW_TO_CORPUS | NEW_TO_USER |
NEW_EVIDENCE_FOR_EXISTING_BELIEF | CONTRADICTS_EXISTING_BELIEF

Source-quality by domain (medical: RCT > guideline > lecture >
influencer; software: repo+tests > source > docs > blog; market:
exchange data > paper > dataset > pundit).

## Dashboard views (priority order)

ACT NOW: /today (new/emerging/contradicted/actionable + recommended
attention), /interests (atlas hierarchy + trajectories), /interest/{id}
(drill-down). THEN: Regret Feed, Research Queue, Belief Update Board,
Emerging Radar, Bridges, Timeline, Semantic Map, Idea Graph,
Opportunity Board.

Navigation end-state: TODAY (Intelligence Home, Regret, Research Queue)
/ ME (Interests, Goals, Questions, Beliefs, Ideas) / DISCOVER (Atlas,
Emerging, Bridges, Map, Trends) / EVIDENCE (Search, Graph, Sources,
Claims) / ACT (Opportunities, Investigations, Watchlist, Learned).

UX principle: documents are evidence BEHIND the UI. Primary objects:
interest → goal → question → claim → opportunity → action.

## External evidence (proactive acquisition)

Interest graph → external search agents → papers/repos/trials/filings
→ candidate evidence → novelty classification → regret feed.
Domain sources: PubMed/clinicaltrials.gov (health), GitHub/papers
(agents), CBOE/OCC/academic finance (options), SEC/FRED (markets).

## Research donors (ideas, NOT adoptions)

- PURPLE: contextual-bandit selection of profile evidence for
  downstream utility — semantic relevance ≠ personalization utility.
  The eventual reward model.
- BERTopic: hierarchical topics, dynamic topics, multi-aspect
  representations, LLM labels — inspect, don't replace UMAP+HDBSCAN.
- AI-Paper-Trends: the Interest Atlas UX pattern (macro/fine topics,
  share, keywords, reps, drill-through).
- LightRAG: incremental graph maintenance, graph+vector fusion.
  NOT GraphRAG (maintenance-mode, expensive indexing).
- Cytoscape.js inside the existing service: typed-graph visualization.
- marimo: research workspace for scoring/experiment iteration.
- implicit/LightFM/RecBole: evaluation donors only — single-user
  collaborative filtering is the wrong formulation.

## Feedback (ground truth — defer = lose training history)

Every surfaced item carries: useful | known_already | not_interested |
wrong_inference | investigate | acted_on | save | more_like |
less_like. Written to feedback table immediately. This is the eventual
bandit reward signal.

## Implementation order (operator)

NOW 1: v1.5 evidence clusters — SHIPPED (e7c2b6c0)
NOW 2: v2 typed interest/goal inference (citations required)
NOW 3: three pages — /today, /interests upgrade, /interest/{id}
NOW 4: feedback endpoints + table — immediate
INVESTIGATE 5-8: BERTopic benchmark, atlas pattern, Cytoscape, marimo
LATER 9: utility learning (weighted ranking → contextual bandit)

## Discriminating test (the falsifier)

Build both: goal/claim-aware ranking AND similarity+recency baseline.
Blinded comparison on "would regret missing this" over 20-30 inferred
interests. If the graph does not beat the baseline, keep the simpler
architecture and use the graph for visualization only. This test gates
the whole program.
