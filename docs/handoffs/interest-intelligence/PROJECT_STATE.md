# Goal & constraints

- [claimed] Turn the yt-is corpus into ongoing decision intelligence built on
  typed interests, goals, questions, evidence, concepts, opportunities, and
  actions rather than document browsing alone.
- [claimed] Preserve provenance and uncertainty through inference,
  recommendation, discovery, dashboard, and external-intelligence layers.
- [claimed] Evaluation gates precede downstream optimization: discovery quality
  must be falsifiable; inference recall/provenance precede recommendation
  optimization; broad dashboard/external expansion follows trustworthy
  upstream state.
- [seen] The project currently maintains five workstream-specific state files:
  discovery, inference, recommendation, dashboard, and external intelligence.
- [seen] The active workstream is contamination-separated retrospective
  evaluation of open-world concept discovery.

# Decisions

- 2026-08-24: [claimed] Multi-view evidence/provenance is the substrate for
  semantic interest inference.
- 2026-08-24: [claimed] Inference quality is a separate gate from
  recommendation quality.
- 2026-08-24: [claimed] Goal/claim-aware recommendation must beat a simpler
  baseline rather than being assumed superior.
- 2026-08-24: [claimed] Dashboard expansion waits for trustworthy typed state.
- 2026-08-24: [claimed] Broad external-intelligence acquisition waits for
  trustworthy inference/recommendation contracts.
- 2026-08-24: [claimed] Open-world concept discovery cannot require the target
  concept name in advance.
- 2026-08-25: [seen] The committed discovery evaluation architecture freezes
  production/evaluator/policy machinery before target labels enter the
  evaluator.
- 2026-08-25: [claimed] Six previously exposed technology names are
  contaminated and may be used only for NON-BLIND_DIAGNOSTIC plumbing, never
  as formal promotion evidence.

# Current state

- [seen] Current yt-is main includes discovery-evaluator commit
  b4a9dafc521f86871e9b0f9d8329206bfb05e3df.
- [seen] That commit adds scripts/evaluate_concept_discovery.py,
  tests/test_evaluate_concept_discovery.py, and updates only the discovery
  workstream state document.
- [seen] The evaluator freezes hashes for production discovery files,
  discovery policy, evaluator code, metric plan, matching, scorability,
  negative controls, perturbation, baselines, and verdict rules.
- [seen] Target loading occurs only after frozen-receipt verification.
- [seen] Synthetic tests exercise production/evaluator drift rejection,
  holdout-state rejection, blind discovery inputs, scorability,
  perturbation isolation, negative controls, baselines, verdict math, and
  artifact locality.
- [claimed] The focused evaluator suite passed 12/12 and the combined discovery
  regressions passed 67 tests.
- [claimed] A private frozen-evaluator receipt exists for production SHA
  d21270a9 with formal_holdout_read=false.
- [claimed] Real-corpus NON-BLIND_DIAGNOSTIC plumbing exercised both scorable
  and unscorable paths without using the contaminated names as promotion
  evidence.
- [claimed] Target-free calibration produced 321 scanned / 106 candidates /
  99 emerging, with median source diversity 1; this indicates an extremely
  broad emerging policy but has not been used for tuning.
- [claimed] The unseen formal holdout has not been read by the implementing
  context.
- [seen] FORMAL holdouts are atomically claimed by content hash
  (sha256 of exact file bytes, private sqlite ledger with
  UNIQUE(holdout_sha256)) before label parsing and are globally single-use
  across evaluator generations (evaluator-v2, commit 6a50de36; 25 focused
  tests prove first-claim/duplicate/cross-generation rejection).
- [seen] A crash after formal claim permanently consumes that holdout
  (ledger records FAILED_AFTER_CONSUMPTION; retry with the same holdout
  fails closed).
- [seen] Verdict-v2 returns INSUFFICIENT_EVIDENCE below 20 scorable
  targets, below 40 matched negative controls, or below 2.0
  controls/target, and only then evaluates the frozen substantive
  PASS/PARTIAL/FAIL thresholds.
- [seen] Formal proportion metrics include 95% Wilson intervals
  (candidate/emerging recall, matched-negative emerging rate, 10%/20%
  perturbation retention).
- [seen] A new frozen evaluator-v2 receipt
  (freeze-20260825T-FORMAL-V2, evaluator sha256 21a2704e…, includes
  single-use and uncertainty policy hashes; production discovery hashes
  unchanged from the v1 receipt) supersedes evaluator-v1 before any
  unseen holdout is opened; the v1 receipt now fails closed against the
  published tree.
- [claimed] Full-coverage interest-inference bootstrap exists and its semantic
  recall gate remains outstanding.
- [claimed] Recommendation optimization, broad dashboard expansion, and broad
  external-intelligence expansion remain downstream of the
  inference/discovery evidence gates.

# Open questions

- What actor generates/custodies the unseen holdout so the implementation and
  policy-tuning contexts cannot inspect it before freeze?
- If a future formal-gate correction changes evaluator bytes again, what new
  freeze receipt supersedes evaluator-v2? (Mechanism proven: a new receipt
  from published bytes; the supersession itself is procedural.)
- After discovery and inference gates resolve, which downstream workstream has
  the highest decision-value priority: dashboard drill-down, feedback/ranking
  hardening, or external-intelligence acquisition?

# Next action

- Execute evaluator-v2 against ONE unseen formal holdout using a
  FRESH IMPLEMENTER/EVALUATOR (single-use ledger enforces first-and-only
  consumption; INSUFFICIENT_EVIDENCE applies below the preregistered
  minimums; a replacement holdout generation is required if the sample is
  inadequate).
