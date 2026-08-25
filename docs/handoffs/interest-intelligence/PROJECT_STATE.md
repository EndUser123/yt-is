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
- [seen] formal_holdout_read is checked but not mechanically transitioned when
  a formal target set is consumed; one-use holdout authority is therefore not
  currently enforced by the evaluator.
- [seen] Verdict-v1 has no minimum scorable-target count, so statistical
  sufficiency is not mechanically part of PASS/PARTIAL/FAIL.
- [claimed] Full-coverage interest-inference bootstrap exists and its semantic
  recall gate remains outstanding.
- [claimed] Recommendation optimization, broad dashboard expansion, and broad
  external-intelligence expansion remain downstream of the
  inference/discovery evidence gates.

# Open questions

- What mechanism should make a formal holdout mechanically single-use across
  evaluator/policy generations: immutable consumption receipt, holdout hash
  registry, or equivalent fail-closed authority?
- What preregistered minimum number of scorable targets and uncertainty
  reporting are required before PASS/PARTIAL/FAIL is decision-grade?
- Should insufficient scorable sample produce a fourth verdict such as
  INSUFFICIENT_EVIDENCE rather than PARTIAL/FAIL?
- What actor generates/custodies the unseen holdout so the implementation and
  policy-tuning contexts cannot inspect it before freeze?
- If either formal-gate correction changes evaluator bytes, what new freeze
  receipt supersedes the current pre-holdout receipt?
- After discovery and inference gates resolve, which downstream workstream has
  the highest decision-value priority: dashboard drill-down, feedback/ranking
  hardening, or external-intelligence acquisition?

# Next action

- Harden the formal retrospective-evaluation boundary BEFORE consuming the
  unseen holdout.
- Specifically, the architect must resolve:
  1. mechanically single-use formal-holdout authority; and
  2. decision-grade sample sufficiency / insufficient-evidence semantics.
- If evaluator bytes or frozen policies change, create a NEW frozen receipt
  before any formal holdout is opened.
- Only after that may a fresh contamination-isolated evaluator lane consume
  one unseen formal holdout and return evidence for architect judgment.
