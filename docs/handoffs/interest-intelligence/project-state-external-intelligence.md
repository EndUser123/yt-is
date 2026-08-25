# yt-is Personal Intelligence — External Intelligence State
Updated: 2026-08-24 by architect handoff

## Goal & constraints

- [seen] Use accepted interests/goals to search outside the historical corpus
  for important new evidence, contradictions, opportunities, and under-covered
  adjacent topics.
- [seen] Acquisition should be goal-driven and evidence-quality-aware rather
  than broad news collection.
- [seen] Health, software, and markets require domain-specific evidence
  authority models.
- [seen] External findings should enter the same provenance, novelty,
  claim/question/opportunity, and recommendation substrate.
- [seen] The Daily Priority Brief should eventually consume this substrate.

## Non-goals

- [seen] Do not build a generic broad-news firehose.
- [seen] Do not let external search volume overwhelm higher-quality evidence.
- [seen] Do not treat all web sources as equally authoritative.
- [seen] Do not create a separate preference ontology solely for the Daily
  Brief.

## Decisions

- 2026-08-24: [seen] Health acquisition should prioritize guidelines,
  systematic reviews/meta-analyses, randomized trials, major prospective
  evidence, regulatory decisions, and authoritative trial registries.
- 2026-08-24: [seen] Software acquisition should prioritize working
  repositories, tests, source, maintainer documentation, release notes, and
  primary research over commentary.
- 2026-08-24: [seen] Market acquisition should prioritize filings,
  exchange/regulatory sources, central-bank/official data, and robust empirical
  research over punditry.
- 2026-08-24: [seen] External evidence should be classified by novelty relative
  to world, corpus, user knowledge, and existing belief state rather than
  merely "new document."

## Current state

- [seen] yt-is already contains multi-source evidence beyond YouTube.
- [seen] The design describes interest-driven acquisition from papers, GitHub,
  clinical/trial sources, filings, and official market data.
- [seen] Current inference/recommendation contracts are not mature enough to
  drive broad external acquisition safely.
- [absent-unverified] Production goal-driven external research orchestration.
- [absent-unverified] End-to-end novelty classification for external evidence.
- [absent-unverified] Daily Brief consumption of authoritative typed
  interest/goal state.

## Open questions

- Which external domain should be piloted first after inference/ranking gates?
- What shared source registry and evidence-authority model is required?
- How should acquisition budget be divided among active questions, durable
  interests, regret candidates, and contradictions?
- When should a finding create a Question, Claim, Opportunity, Watch item, or
  only Evidence?
- How should external-search feedback feed the common utility model?

## Next action

Hold broad implementation until inference and recommendation contracts
stabilize. Prepare concrete source/evidence requirements after authoritative
goals and information needs pass the inference gate.
