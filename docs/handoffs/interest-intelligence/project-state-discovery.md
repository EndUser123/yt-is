# yt-is Personal Intelligence — Discovery / Concept Intelligence State
Updated: 2026-08-24 by open-world discovery implementer pass

## Goal & constraints

- [seen] Discover concepts the system has never been told about: internal
  corpus novelty (new entities, new/accelerating semantic clusters) and
  external horizon scanning, without requiring a concept name in any
  prompt, query, list, or configuration.
- [seen] Durable memory ≠ durable attention: a supported concept is
  remembered forever (dormant at minimum); its lifecycle and the user's
  relationship to it change independently.
- [seen] World signal (global trend strength) and personal relevance are
  separate axes; popularity alone never promotes a user interest.
- [seen] Discovery must preserve provenance and temporal evidence so a
  later contamination-separated retrospective evaluation can falsify
  detection quality against hidden historical concepts.
- [seen] Reuse existing infrastructure: Evidence Fabric KG/EU data,
  evidence-cluster inventory, personal graph, and the P search fleet.
  No new crawler, search service, vector DB, graph DB, entity extractor,
  or topic model.

## Non-goals

- [seen] No dashboard surface in this workstream yet (/today, /interests,
  warm_query_service untouched); the Discovery Radar is a stable read
  model only.
- [seen] No domain-specific external scouts (papers, trials, markets) in
  v1 — the External Intelligence workstream owns those; the Concept
  Registry is the sink.
- [seen] No automatic user-relationship promotion beyond `adjacent`:
  active_project/durable_interest/rejected require operator action or
  strong accepted user-state evidence.
- [seen] No tuning to any known real-world example; the motivating
  repository is not named, aliased, searched, or fixtured anywhere.

## Decisions

- 2026-08-24: [seen] Concepts are distinct from Interests.
- 2026-08-24: [seen] Concept identity is durable; attention/lifecycle
  state is mutable.
- 2026-08-24: [seen] Open-world discovery must not require a concept
  name to be known beforehand.
- 2026-08-24: [seen] Internal corpus novelty and external horizon
  scanning are independent discovery paths.
- 2026-08-24: [seen] Global trend strength and personal relevance are
  separate axes.
- 2026-08-24: [seen] Existing P search-fleet infrastructure is reused
  for external horizon discovery rather than introducing another
  crawler/search service.
- 2026-08-24: [seen] Concept discovery must preserve provenance and
  temporal evidence.
- 2026-08-24: [seen] Popularity alone is insufficient for
  durable-interest promotion.
- 2026-08-24: [seen] Historical/as-of replay is required so discovery
  quality can later be falsified against withheld real-world events.
- 2026-08-25: [seen] Discovery policy evaluation is contamination-separated
  from implementation: production discovery is frozen before holdout labels
  are loaded; target labels are post-hoc scoring inputs only and remain
  outside the public repository. The six technology names exposed to the
  implementing context on 2026-08-24 are contaminated: usable only as
  NON-BLIND_DIAGNOSTIC plumbing cases, never as promotion evidence; the
  formal gate uses a different unseen holdout.

## Current state

- [seen] Concept Registry is implemented as durable identity
  (deterministic sha256 `concept_<type|normalized-name>` ids) plus
  mutable lifecycle (candidate/emerging/active/durable/cooling/dormant/
  obsolete) and user_relationship (unknown/adjacent/monitoring/learning/
  active_project/durable_interest/rejected) state, with append-only
  `concept_state_events` receipts; concepts are never deleted when
  attention decays.
- [seen] Aliases normalize conservatively (case/whitespace/punctuation
  variants merge; token overlap never merges); ambiguous names remain
  separate candidates until explicit evidence-backed merge.
- [seen] Observations are idempotent (deterministic observation ids);
  trend episodes preserve rise/cooling/re-emergence history instead of
  overwriting one trend value.
- [seen] Internal discovery detects previously unseen entities and
  semantic cluster emergence from existing Evidence Fabric kg/eu data
  without requiring predeclared topic names; cluster identity is the
  cluster LABEL (evidence carries the cluster_id), never the cluster_id
  itself.
- [seen] Versioned burst policy (burst-policy-v1) separates absolute
  support, smoothed acceleration, source diversity, persistence, and
  novelty; emerging requires absolute floors plus independent
  channel/source evidence — 1→2 bumps and single-source floods stay
  candidates. Weights/windows are initial policy values, not optimal.
- [seen] `--as-of` replay excludes post-cutoff evidence from all
  observations, windows, first_seen, and lifecycle calculation.
- [seen] Horizon scouting reuses the existing search_web/search_all MCP
  fleet (:8323) via the proven initialize/tools-call pattern and issues
  category/goal queries derived from interests/goals/needs — results
  supply the unknown names; query volume is bounded and deduplicated,
  with a non-zero adjacent+wildcard exploration budget
  (initial 70/20/10) so the system cannot become a filter bubble.
- [seen] scout-run is the only network command and requires
  --allow-search; tiers are fast/medium/deep only (pro/quota never sent
  silently); transport failure fails closed without corrupting the
  registry and without fabricating concepts.
- [seen] Software/GitHub results normalize to deterministic
  owner/repo repository Concepts; repeated independent appearances join
  the same concept as distinct observations; novelty distinguishes
  new_to_registry / new_to_corpus / previously_known / unknown (unknown
  on unavailable evidence, never guessed).
- [seen] Personal relevance is mechanical and provenance-labeled
  (shared_cluster via evidence_links supports chains; scout query-origin
  links via method 'semantic'); personal_relevance_score stays NULL
  rather than being fabricated; NULL relevance never hides a globally
  important candidate.
- [seen] Discovery Radar has a stable read contract (discovery_radar())
  ranked by attention only, with no state mutation; no UI is built yet.
- [seen] A generic retrospective evaluator exists
  (scripts/evaluate_concept_discovery.py, evaluator-v1) with frozen metric
  plan, matching/scorability/negative-control/perturbation/baseline/verdict
  policies, a freeze-receipt gate that fails closed on production or
  evaluator drift and refuses targets before freezing, and artifacts
  outside git under P:/.data/yt-is/ef/concept-discovery-eval/.
- [seen] The frozen-evaluator receipt of record is
  P:/.data/yt-is/ef/concept-discovery-eval/freeze-20260825T-FORMAL/
  frozen-code-hashes.json (production commit d21270a9, burst-policy-v1,
  formal_holdout_read=false at freeze).
- [seen] NON-BLIND_DIAGNOSTIC plumbing runs against the real corpus
  validated every machinery stage (freeze gate, scorability both
  directions, six-checkpoint as-of replays, matched negative controls,
  10%/20% perturbation on catalog snapshots, baseline comparison,
  aggregate + verdict + labeled report); these results are labeled
  NON-BLIND / NOT PROMOTION EVIDENCE and involved only the contaminated
  exposed names.
- [seen] Current read-only calibration scan reproduced 321 entities
  scanned, 106 candidates, 99 emerging (93% of candidates promoted) with
  median source diversity 1 — emerging classification is extremely broad
  under uncalibrated burst-policy-v1; recorded as calibration evidence,
  no tuning applied.
- [seen] The formal holdout file has NOT been read by the implementing
  context; the formal retrospective gate runs in a separate
  contamination-isolated evaluator lane against an unseen holdout using
  the frozen receipt.
- [absent-unverified] Historical real-world discovery quality against a
  hidden holdout set.
- [absent-unverified] Domain-specific external scouts for medical,
  market, regulatory, and research-primary sources.
- [absent-unverified] Production Discovery Radar dashboard surface.

## Open questions

- What absolute floors/ratios best calibrate emerging detection against
  false positives on the real corpus (initial burst-policy-v1 values
  are uncalibrated)?
- Should external-candidate promotion use a unified trend policy engine
  shared with internal discovery rather than the v1 recurrence rule?
- How should cluster split/merge over time rebind label-identity
  concepts to new cluster_ids as evidence?
- What wildcard exploration queries maximize horizon breadth without
  wasting the bounded query budget?
- Which novelty corroboration sources (EF vs wiki vs chat) matter most
  for new_to_corpus confidence?

## Next action

Execute the frozen evaluator-v2 (receipt freeze-20260825T-FORMAL-V2) in a
FRESH IMPLEMENTER/EVALUATOR context against one new unseen private
holdout; the current implementer must be retired only at that switching
boundary (its successor cold-start handoff exists — see the FRESH
DISCOVERY EVALUATOR HANDOFF returned with the v2 freeze). The
implementing context that saw the exposed names must not run or score
that holdout. The formal boundary is now mechanically hardened: FORMAL
holdouts are single-use by content hash (a crash after claim consumes
permanently), verdict-v2 returns INSUFFICIENT_EVIDENCE below the
preregistered sufficiency minimums (20 scorable / 40 controls / 2.0 per
target), and formal proportion metrics carry 95% Wilson intervals. If
the gate passes, integrate Discovery Radar into the dashboard and add
domain-specific
external source adapters; if PARTIAL/FAIL, run an architect-approved
policy-calibration experiment first (the extremely broad emerging
classification is the leading known suspect).
