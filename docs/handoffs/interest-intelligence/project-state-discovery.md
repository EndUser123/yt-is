# yt-is Personal Intelligence — Discovery / Concept Intelligence State
Updated: 2026-08-26 evaluator-v4 explicit-negative diagnostic: V2_SELECTIVITY_FAILURE_CONFIRMED; scope reconciliation applied; no freeze

## Discovery subsystem scope (architect reconciliation 2026-08-26)

burst-policy-v1/v2 and the retrospective case-control evaluation own
TEMPORAL CONCEPT EMERGENCE only. Discovery independently contains:
A. open-world concept candidate discovery; B. temporal emergence / trend
episodes (this workstream's current evaluation); C. semantic-step-outward
adjacency discovery (machinery exists, generation shallow); D. cross-
domain bridge / relationship-emergence discovery (concept_relations is
storage substrate only; bridge detection not yet a first-class
detector); E. external horizon discovery (70/20/10 known/adjacent/
wildcard INITIAL policy preserved; wildcard queries currently
software-centric); F. downstream personal utility / regret ranking
(recommendation workstream). Preserved distinctions: Concept != Interest;
durable memory != durable salience; world signal != personal relevance;
internal != external discovery. Higher-order relevance center (durable
personal agency / leverage / resilience / optionality) is recorded but
CONCRETE OPERATIONAL ANCHORS, not this sentence, drive query generation.
The explicit-negative/evaluator-v4 question is narrowly: does temporal-
emergence policy v2 discriminate persistent emerging episodes from
comparable explicit non-emerging episodes — NOT overall Discovery
false-positive rate.

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

- 2026-08-25: [seen] Calibration experiment v2-policy-family-calibration-v1
  (preregistered plan sha256 bb1c0299... frozen BEFORE any grid result;
  plan + all artifacts under
  P:/.data/yt-is/ef/concept-discovery-calibration/v2-policy-family/).
  Training data: consumed holdout-v4, TRAINING_DIAGNOSTIC_ONLY.
  Reproduction guard (consistency check emulating the formal registry
  row-staleness semantics, expected values hardcoded from the formal
  artifact; 13 synthetic tests + integration guard pass): baseline A
  rates 0.625/0.246 (separation 0.379) reproduced exactly.
- 2026-08-25: [seen] Grid result (6 candidate x 36 emerging = 216
  configs, frozen): NO configuration qualified on any pass-like axis.
  Max emerging recall 0.238 (count-only arms) carries control rate
  0.333 (negative separation); at control rate ~0.008 emerging recall
  caps at 0.143. 5-fold grouped CV: no fold selected a winner
  (0 qualified in all 5 training splits); OOF undefined by rule;
  Pareto frontier = {C4, C5} x recent>=4, ratio>=2.0, channels>=1.
  Conclusion class: NO_SIMPLE_POLICY_SUPPORTED.
- 2026-08-25: [seen] Discriminating evidence for the next architecture
  decision: (1) removing the source_types gate alone yields emerging
  recall 0.071 / control 0.008 (separation 0.064) — source_types was A
  problem, not THE problem; the whole conjunction is miscalibrated
  (verified ablation). (2) count-only (baseline A) separates NEGATIVELY
  under stateless recall semantics (-0.04); the formal 0.379 came from
  the 8-row registry denominator. (3) best count+ratio+channels arm
  (C0, recent>=4, ratio>=2, channels>=2): emR 0.095, ctl 0.008,
  separation 0.087. (4) C3-C5 reach candidate recall 1.0 but that is
  partially tautological vs the scorer's >=2-lifetime-EU definition and
  inflates candidate volume 67 -> 96-125 mean per checkpoint (~191
  entities total). (5) perturbation20 candidate retention: the plain 30d-window family
  (C0) 0.31 — above the 0.3 reject line, below the 0.5 pass-like axis;
  every wider- or lifetime-gated variant reaches 0.62-1.0 (C1 0.69,
  C2 0.74, C3 0.62, C4/C5 1.0).
  Candidate future families (NOT implemented): time-decayed evidence,
  Bayesian burst detection, channel-weighted corroboration, persistence
  episodes, domain-conditioned thresholds.
- 2026-08-25: no production policy changed; burst-policy-v1 preserved as
  control arm; evaluator-v2, formal ledger, holdout files untouched; no
  FORMAL run. Promotion requires a NEW unseen holdout after any future
  v2 implementation; holdout-v4 can never be promotion evidence again.

- 2026-08-26: [seen] ARCHITECT AMENDMENT (scope reconciliation)
  applied: burst-policy-v1/v2 + retrospective case-control evaluation own
  TEMPORAL CONCEPT EMERGENCE only; Discovery independently contains
  open-world candidate discovery, temporal emergence, semantic adjacency
  (shallow), cross-domain bridges (concept_relations substrate only, not
  a proven detector), external horizon (70/20/10 INITIAL policy;
  software-centric wildcards), and downstream utility/regret ranking.
  Concept != Interest; memory != salience; world signal != personal
  relevance; internal != external. Relevance center (agency/leverage/
  resilience/optionality) recorded but operational anchors drive
  queries. Stale cross-workstream PROJECT_STATE.md rewritten. The
  explicit-negative question is the NARROW temporal-emergence
  discrimination question, not overall-Discovery false positives.
- 2026-08-26: [seen] V3 comparator audit (aggregate only): 125 rows but
  only 33 UNIQUE comparator concepts (one reused 19x; multiplicity
  1-19); row-weighted emerging rate 0.344, unique-level 0.636
  (21/33), target-pair-weighted 0.349. 6 of the 21 promoted unique
  comparators (28.6%) satisfy the raw Tier-C persistence conjunction
  over [T,T+120] — unlabeled-positive contamination. Matching-axes
  audit at T-30: source_diversity is degenerate (mean 1.0 both cohorts
  — acquisition modality, not publisher independence); evidence/channel
  counts and age differ materially (comparators row-weighted mean
  76.8 EUs vs positives 37.5). Evaluator-v3's 0.344 was an
  OUTCOME-UNLABELED comparator rate, not a measured false-positive
  rate.
- 2026-08-26: [seen] EXPLICIT-NEGATIVE ground truth built under
  preregistered policy explicit-negative-v1 (sha256 1e454421...,
  frozen before any negative identity or v2 outcome was inspected):
  124 negatives paired to the 42 positives (2.95/positive; one target
  NEGATIVE_CONTROL_INSUFFICIENT with 1), matched ONLY on pre-T-30 EU
  mass, channels, age; negatives fail the Tier-C persistence
  conjunction over [T,T+120] and satisfy the hard-negative activity
  requirement; no cross-target reuse. Private artifact:
  P:/.data/yt-is/private/discovery-retrospective-case-control-v4-
  diagnostic.json (TRAINING_DIAGNOSTIC_ONLY, never formal, never
  promotion evidence).
- 2026-08-26: [seen] EVALUATOR-V4 implemented (retrospective-evaluator-
  v4): case-control formal schema (positive_targets + curator-supplied
  negative_targets, both parsed only after the formal claim);
  explicit_negative_emerging_rate is the selectivity AUTHORITY
  (unchanged 0.20/0.50 bars); evaluator-v3's automatic comparators
  retained as matched_comparator_emerging_rate (secondary diagnostic,
  never drives verdict); sufficiency >=40 explicit negatives and >=2.0
  per positive (comparators excluded); baselines aligned on the
  explicit labeled cohorts; single-use ledger semantics unchanged plus
  YTIS_FORMAL_LEDGER_PATH test override (regression test added; the
  cd9733d9 synthetic-claim incident class cannot recur).
- 2026-08-26: [seen] EVALUATOR-V4 NON_BLIND_DIAGNOSTIC on the paired v4
  case-control set (ledger untouched; no FORMAL): positives 42,
  candidate recall 1.000, emerging recall 0.833 [0.694,0.917];
  EXPLICIT-NEGATIVE emerging rate 0.581 [0.493,0.664] (72/124) —
  EXCEEDS the 0.20 pass-like axis decisively; comparators 125 rows at
  0.344 (secondary); perturbation10 0.976, perturbation20 0.952;
  policy separation 0.253 vs baseline A 0.053 / baseline B 0.0.
  DECISION: V2_SELECTIVITY_FAILURE_CONFIRMED. Per packet: STOP — no
  tuning, no freeze, return to architect. v2 parameters unchanged;
  production default remains burst-policy-v1.
- 2026-08-26: [seen] CONTROL-METRIC POSTMORTEM. (1) Of the 0.344
  comparator rate, 28.6% of promoted unique comparators are
  positive-like by raw future evidence (unlabeled-positive
  contamination). (2) v2 emerging rate among VALID explicit negatives
  is 0.581; among positive-like comparators 0.60 — v2 fires at similar
  rates regardless of post-anchor persistence. (3) The 0.344 is a
  mixture, but DOMINATED by genuine over-promotion relative to the
  persistence ground truth, not mainly contamination. Mechanism
  (identified, NOT tuned): the v2 60-day recent window at anchor
  checkpoints still contains the pre-anchor evidence on which the
  negative was matched, so two-consecutive-positive promotion can
  complete before post-anchor silence matters.
- 2026-08-26: [seen] BLINDED SEMANTIC AUDIT (diagnostic only, 2
  independent fresh-context reviewers, 16 items: 4 positives / 4
  promoted negatives / 4 non-promoted negatives / 4 promoted
  comparators; class and outcome hidden): inter-reviewer agreement
  75% (12/16). Blinded humans judged only 1/4 sampled POSITIVES
  GENUINE_EMERGING_OR_RESURGENT (3/4 STABLE_BACKGROUND), 0/4 promoted
  negatives genuine, 1/4 promoted comparators genuine, and flagged
  NOISE_OR_EXTRACTION_ARTIFACT concepts on both sides (generic labels
  like "rings"/"Apple", single-publisher marketing streams, discord
  bulk captures, a nonsense label "Rege"). Bounded interpretation: the
  semantic notion of "emerging" diverges from both the v2 posterior
  AND the raw persistence ground truth; concept-extraction quality is
  itself a load-bearing weakness. Does not alter the mechanical
  labels or the pass bar.

## Next action (calibration concluded)

ARCHITECT DECISION REQUIRED: V2_SELECTIVITY_FAILURE_CONFIRMED under
explicit-negative ground truth (narrow temporal-emergence scope). v2
parameters untouched; no freeze; no second policy search. Evidence in
hand for the next packet: pre-anchor window contamination mechanism,
comparator reuse artifact, semantic-audit concept-quality findings.


ARCHITECT DECISION REQUIRED: NO_SIMPLE_POLICY_SUPPORTED — the simple
count/ratio/channel family is insufficient. Next-stage options are in
the packet's future-classes list; do not expand the frozen grid.

- 2026-08-25: [seen] Stateful burst bakeoff EXECUTED (preregistered
  plan sha256 a04ee198... frozen before results; consumed v4 as
  TRAINING_DIAGNOSTIC_ONLY; artifacts under
  P:/.data/yt-is/ef/concept-discovery-calibration/stateful-burst-v1/).
  Donors assessed in discovery-burst-model-donors.md: pybursts Kleinberg
  (MIT, dormant 2014) ported locally with a disclosed intra-bin-fraction
  adaptation (pybursts rejects duplicate offsets — the plan's
  incompatibility clause was invoked BEFORE any result); BOCD rejected
  (sparse-stream unfit).
- 2026-08-25: [seen] Results. Decay candidate D30-1.5 (half-life 30d,
  support >= 1.5, lifetime >= 2): candidate recall 0.9048 at 79.1 mean
  candidates (v1: 0.714 @ 67; hard-window C3: 1.0 @ 95.7).
  Gamma-Poisson episodes (recent 60d vs prior 180d, Gamma(0.5,0.5)
  prior, multiplier 1.5, threshold 0.80, channel floor 1, persistence
  episodes): FULL-V4 emerging recall 0.5476, control emerging 0.0714,
  separation 0.4762, perturbation20 candidate retention 0.8333
  (emerging retention 0.381). 5-fold grouped OOF: emerging recall
  0.5295, control 0.0717, separation 0.4578, candidate recall 0.9062;
  the SAME configuration was selected in all 5 folds. 13/36 Bayesian
  variants qualified. Kleinberg: never qualified in any fold (max
  emerging recall 0.4048; Pareto only). Ablations: persistence is the
  decisive component (OFF -> control rate 0.246, fails the 0.20 axis);
  channel floor inert at floor 1; raw/distinct/capped counts nearly
  identical (no single-publisher inflation). Conclusion class:
  BAYESIAN_EPISODES_SUPPORTED.
- 2026-08-25: [seen] Proposed burst-policy-v2 spec recorded at
  stateful-burst-v1/proposed-burst-policy-v2.md (decay candidate +
  Gamma-Poisson episodes + persistence lifecycle; source_types audit
  only). NOT implemented. No production/evaluator/ledger/holdout
  changes; no FORMAL run. holdout-v4 remains training-only forever;
  promotion requires a NEW unseen holdout (fresh curator, fresh
  evaluator) after v2 implementation and freeze.

- 2026-08-26: [seen] burst-policy-v2 IMPLEMENTED as a SHADOW policy
  (ef/burst_policy_v2.py pure module; explicit policy_version dispatch in
  scan_internal; CLI --policy-version; default remains burst-policy-v1).
  Numerical method: closed form P = 1 - I_{c/(1+c)}(a_r,a_b) via
  scipy betainc, validated vs calibrated GL-256 at max err 6.6e-13 with
  ZERO decision differences (all v4 points, full sweep, 0.70/0.80/0.99
  boundaries) — adopted; no parameter changed. Episodes persist in the
  EXISTING trend_episodes table; ranking score preserved from v1 (v2
  ranking calibration OPEN). Production parity gate vs a drift-free
  live-catalog stateless reference: 0 candidate and 0 emerging
  disagreements (PASS; earlier diffs vs the calibration snapshot traced
  to catalog drift — matched entities' EU rows were re-ingested after
  calibration). Design doc: docs/design/discovery-burst-policy-v2.md.
- 2026-08-26: [seen] evaluator-v3 implemented (retrospective-evaluator-
  v3): explicit burst-policy-v2 pinning from the freeze receipt
  (parameter hash + numerical method + python/numpy/scipy versions),
  entity-only negative controls selected at T-30, symmetric stateful
  replay of controls through T+60 in the same registry, ALIGNED
  baseline comparison (same cohorts/units; v2 registry-row denominator
  semantics removed), stateful perturbation prefix T-30..T+30 with the
  legacy candidate-retention metric preserved plus episode/posterior
  diagnostics. Verdict thresholds, sufficiency gate, single-use ledger,
  Wilson intervals unchanged.
- 2026-08-26: [seen] v4 NON_BLIND_DIAGNOSTIC under evaluator-v3
  (artifacts eval-20260826T005921-NON_BLIND_DIAGNOSTIC; ledger untouched,
  no FORMAL): 42/42 scorable; candidate recall 1.000; emerging recall
  0.833 [0.694,0.917]; ENTITY controls 125; control emerging rate 0.344
  [0.266,0.431] — EXCEEDS the <=0.20 pass-like axis; perturbation10
  0.976; perturbation20 0.952; policy separation 0.489 vs baseline A
  -0.05 / baseline B 0.0 (policy_beats_baselines true). Per the packet:
  an axis FAILED -> STOPPED before freeze. NO freeze receipt created; no
  v2 tuning; no threshold changes. Evidence for the architect: under
  symmetric stateful replay, 34% of evidence-mass-matched entity
  controls also promote; the target-control margin (0.49) is strong but
  the absolute control rate fails the frozen bar. Candidate directions
  (NOT explored here): channel floor, signal threshold, control-
  selection matching axes, promotion persistence strictness.
- 2026-08-26: implementation published (SHADOW; production default
  remains burst-policy-v1). Incident disclosure: one synthetic-file
  hash (cd9733d9...) was claimed in the formal holdout ledger by a
  pre-fix test bug (label FORMAL from a unit test; status
  FAILED_AFTER_CONSUMPTION); it is a tmp synthetic targets file, can
  never match a real holdout, and the ledger was not edited.

## Next action (stateful bakeoff concluded)

ARCHITECT DECISION REQUIRED: v3 diagnostic control-rate axis FAILED
(0.344 > 0.20). Do not freeze v2. Choose: adjust control-selection
matching axes / revisit promotion strictness via a NEW calibration
packet, or accept and revisit architecture. holdout-v4 remains
training-only; the promotion path (fresh curator -> new unseen holdout
-> different fresh evaluator) is unchanged but blocked on this axis.

ARCHITECT DECISION REQUIRED: approve/reject implementation of the
proposed burst-policy-v2 specification. Independence boundary after
freeze: fresh curator -> new unseen holdout -> different fresh
evaluator.

## Next action

Architect-approved Discovery calibration experiment on consumed
holdout-v4 as TRAINING/DIAGNOSTIC evidence, followed by a completely
new unseen holdout for promotion evidence. Leading mechanical finding
for the calibration packet: the source_types>=2 gate (acquisition
modality, not channel independence) is the sole blocker of the
emerging path on v4; "emerging classifier is too conservative overall"
remains a hypothesis pending the calibration experiment.
