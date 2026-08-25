# yt-is Personal Intelligence — Discovery / Concept Intelligence State
Updated: 2026-08-25 by fresh formal-v4 evaluator (FAIL postmortem diagnostics)

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

## Formal evaluation results

- 2026-08-25: [seen] FORMAL holdout-v2 -> INSUFFICIENT_EVIDENCE.
  Mechanically observed: 60 total / 0 scorable / 60
  UNSCORABLE_MISSING_EVIDENCE; artifacts (private) at
  P:/.data/yt-is/ef/concept-discovery-eval/eval-20260825T103445-FORMAL;
  ledger claim formal_20260825T103445_60193b6c COMPLETED — holdout-v2
  (sha256 60193b6c...) permanently consumed. NOT a Discovery recall
  failure: no PASS/PARTIAL/FAIL was emitted and no recall number exists.
- 2026-08-25: [seen] Raw KG diagnosis proves the cause is representation
  mismatch, exact-label scorer vs externally selected holdout names:
  0/60 v2 canonical names match any kg_nodes entity label (scorer
  universe = 388 entity labels, 321 with EU mentions); 4/60 collide
  case-insensitively with KG labels but all four are `channel` nodes
  with zero mentioned_in edges; 0/60 satisfy >=1 EU; 0/60 >=2 EUs.
- 2026-08-25: [seen] Holdout-v3 generated from preregistered
  raw-corpus evidence (policy holdout-v3-curation-v1 + disclosed
  amendment v1.1 implementing the policy's PREFERRED kg_nodes.meta_json
  NER-type branch: DOMAIN_TYPES = PRODUCT/TECH/ORG; receipt at
  P:/.data/yt-is/ef/concept-discovery-eval/holdout-v3-curation/).
  discovery_outputs_read_for_selection=false;
  v3_discovery_evaluation_run=false; formal ledger untouched for v3.
  Result: 4 targets of a 321-label universe; thresholds NOT loosened
  (binding floor: >=6 distinct EUs in [T,T+60d] eliminates 130/321).
  Preflight 4/4 on every axis incl. frozen-scorer T agreement; zero
  overlap with v2 or NON-BLIND fixtures. v3 sha256 45e14059...
  (private path P:/.data/yt-is/private/discovery-retrospective-holdout-v3.json).
- 2026-08-25: [seen] Architect decision: holdout-v3 is
  ABANDONED_UNCONSUMED_UNDERPOWERED (4 guaranteed-scorable targets <
  verdict-v2 minimum 20). Formal v3 was NEVER run; the formal holdout
  ledger was never touched for v3; the private v3 file remains for
  provenance and must never be submitted with --label FORMAL.
- 2026-08-25: [seen] Holdout-v4 curated under a NEW preregistered
  architect-specified raw-evidence policy (holdout-v4-curation-v1,
  policy sha256 c2cb4745...; receipt at
  P:/.data/yt-is/ef/concept-discovery-eval/holdout-v4-curation/).
  Domain broadened to PRODUCT|TECH|ORG|CONCEPT (PERSON/PLACE excluded)
  with a preregistered persistence ladder: Tier A(60d/5EU)=5 eligible,
  Tier B(90d/4EU)=18, Tier C(120d/3EU+late-after-T+30)=42 -> selected
  Tier C. 42 targets of a 321-label raw universe; deterministic
  stratified selection (T/mass/channel terciles x NER type;
  sha256(policy+label) within stratum). Preflight 42/42 on all axes
  incl. frozen-scorer T agreement, raw-control feasibility, zero
  overlap with v2/v3/fixtures. discovery_outputs_read_for_selection=
  false; v4 never scored; formal ledger untouched for v4. v4 sha256
  0cc6f1bc... (private path
  P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json).
  NER-type counts: CONCEPT 16, PRODUCT 12, TECH 8, ORG 6.
- 2026-08-25: [seen] FORMAL holdout-v4 consumed successfully by a fresh
  blinded evaluator (freeze receipt freeze-20260825T-FORMAL-V2 verified,
  evaluator sha 21a2704e..., sanity 25/25, ledger claim
  formal_20260825T114339_0cc6f1bc COMPLETED; private artifacts at
  P:/.data/yt-is/ef/concept-discovery-eval/eval-20260825T114338-FORMAL/).
  42 total / 42 scorable / 0 UNSCORABLE_MISSING_EVIDENCE. Candidate
  recall 0.714 [0.564,0.828]; emerging recall 0.000 [0.000,0.084];
  matched-negative emerging 0/126 [0.000,0.030]; perturbation10 0.405,
  perturbation20 0.333. Verdict FAIL.
- 2026-08-25: [seen] Exact mechanical FAIL predicate (verified against
  frozen apply_verdict code + recomputed baseline rows, which reproduce
  the artifact separations 0.0/0.379/0.0 exactly, n_rows=134):
  policy_beats_baselines == false is the ONLY true FAIL arm
  (nr 0.000 < 0.5 min and p20 0.333 > 0.3 max are both false).
  Baselines: policy target rate 0.0 / control 0.0 (sep 0.0);
  baseline A (recent>=6) target 0.625 / control 0.246 (sep 0.379);
  baseline B (recent>=4 and novel<=60d) target 0.0 / control 0.0
  (sep 0.0).
- 2026-08-25: [seen] Aggregate emerging-gate diagnostic (independent
  replays at T/T+30/T+60 using frozen code; 26/42 matched in this
  reduced replay set): gate A (recent>=4) passes 6/8/7, gate B
  (ratio>=2.0) 12/9/6, gate C (channels>=3) 7/7/6, gate D
  (source_types>=2) 0/0/0, A+B 1/2/2, A+B+C 1/0/0, A+B+C+D 0/0/0.
  Removing the D gate would emerge at most 1 target (at T); lowering
  the channels floor to 2 changes nothing (D still binds). Gate D is
  the binding constraint on the emerging path.
- 2026-08-25: [seen] Source-type semantics (mechanical, frozen code):
  SOURCE_LABELS maps notebooklm/ytdlp/selenium/whisper -> "youtube"
  and hackernews -> "hn"; reddit/discord/github/rss/dht-artifact stay
  distinct. At T+60, 25/26 matched targets have exactly 1 normalized
  source type (1 has 0), none have >=2; 17 targets have >=2
  independent channels but 1 source type. Hypothesis (inference, not
  yet promotion evidence): min_source_types=2 measures acquisition
  modality, not independent publisher identity, and structurally
  prevents single-modality (YouTube-only) concepts from reaching
  emerging even when corroborated across multiple channels.
- 2026-08-25: [seen] Candidate-miss diagnostic (12/42 never candidates
  at any of 6 formal checkpoints, aggregate): all 12 have >=2 lifetime
  mentions but <2 mentions in every recent-30d window at T/T+30/T+60 —
  below candidate_min_recent=2; 0 misses from missing evidence or name
  matching. Perturbation: retained 17/42 at 10% (all losses candidate
  disappearances; emerging loss 0 because nothing emerged; 4 losses had
  removed==0, i.e. already-absent targets), 14/42 at 20% (2 with
  removed==0).
- 2026-08-25: holdout-v4 is no longer promotion evidence after
  calibration begins and can never be reused for formal validation.

## Next action

Architect-approved Discovery calibration experiment on consumed
holdout-v4 as TRAINING/DIAGNOSTIC evidence, followed by a completely
new unseen holdout for promotion evidence. Leading mechanical finding
for the calibration packet: the source_types>=2 gate (acquisition
modality, not channel independence) is the sole blocker of the
emerging path on v4; "emerging classifier is too conservative overall"
remains a hypothesis pending the calibration experiment.
