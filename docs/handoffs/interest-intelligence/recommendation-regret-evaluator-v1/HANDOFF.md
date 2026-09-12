---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_3396ff71-5f79-48fb-973a-9672dd3ca3c8
status: RECOMMENDATION_EVALUATOR_READY_WAITING_ON_ACCEPTED_INTEREST_STATE
---

# RRE v1 freeze — handoff (pending landing)

## What exists (working tree of packages/yt-is, verified 2026-08-27)

- `ef/eval_recommendation_regret.py` — frozen evaluator core (packet
  schema, deterministic arm blinding + sealed sidecar, three frozen
  judge prompts + listwise, provenance validation fail-closed, feedback
  role map with annotation exclusion, exact pairwise/listwise metrics,
  quadrant profiles, dual small-n output, primary falsifier gate).
  No utility-weight arithmetic and no training path exist anywhere;
  the offline-reward seam raises OffPolicyError by construction.
- `scripts/eval_recommendation_regret.py` — CLI (validate/blind/
  judge/evaluate/demo/verify-freeze/freeze-receipt); `judge` refuses to
  run without explicit --transport; no live ranking run is possible
  through it.
- `tests/test_eval_recommendation_regret.py` — 39 offline tests, all
  passing (`python -m pytest tests/test_eval_recommendation_regret.py -q`
  → 39 passed).
- `METRIC_PLAN_PREREGISTRATION.md` — twelve preregistered sections +
  fail-closed bindings.
- `FREEZE_RECEIPT.json` — sha256s of all four artifacts + judge prompt
  hashes + model config + gate activation preconditions +
  contamination record. `verify-freeze` → FROZEN MANIFEST OK.

Offline demo evidence class is synthetic-only:
`python scripts/eval_recommendation_regret.py demo`.

## Contamination record

This session never opened any file under `.data/yt-is/private/`. No
live DB was read — the impression/feedback schema came from the public
contract source `ef/personal_graph.py`. No ranking outputs were
inspected (none exist). Interest GT v1.1 labels were never used as
recommendation labels. No private holdout was created (later-curator
rule). All fixtures are synthetic-marked.

## Landing

Direct commit in packages/yt-is is broker-gated; diff exceeds the
150 changed-.py-line threshold, so use the reviewed path:

1. `dispatch_review.py dispatch --worktree <yt-is lane worktree>
   --pathspec ef/eval_recommendation_regret.py --pathspec
   scripts/eval_recommendation_regret.py --pathspec
   tests/test_eval_recommendation_regret.py --pathspec
   docs/handoffs/interest-intelligence/recommendation-regret-evaluator-v1/`
   (yt-is lane: provision with `--repo P:/packages/yt-is
   --sessions-root P:/packages/yt-is/.data/sessions`)
2. Independent host-witnessed review writes `verdict.json`.
   Sealing property to attest: metric plan froze behavior BEFORE any
   candidate existed; fixtures synthetic-fictional only.
3. `dispatch_review.py finalize` → `commit_broker.py` →
   `integration_broker.py`.

## Post-freeze procedure

Activation requires ALL of: accepted Interest state (recall/
provenance gate passed); candidates A/B implemented and frozen
elsewhere; then independent curator builds ONE private packet set per
§12 of the preregistration; single judging pass; report BOTH finite-set
outcome and generalization status; stop. Arm C joins only by amendment
adding pair schedules.

## Hashes at generation time (verify before reuse; drift invalidates)

See FREEZE_RECEIPT.json `frozen_artifacts` — authoritative.
