---
agent: zcode
host: zcode
created: 2026-08-26
session: sess_8b2b8fbd-0e39-4fc6-a868-3bfc79512d6f
status: EVALUATOR_READY_WAITING_ON_INFERENCE_FREEZE
---

# ISEM v1 freeze — handoff (pending landing)

## ARCHITECT_AMENDMENT_1 applied (2026-08-27, pre-unseal)

Two orthogonal outputs now emitted by `evaluate()`:

1. GENERALIZATION EVIDENCE — per-type PASS/FAIL-gated
   SUFFICIENT/INSUFFICIENT evidence (MIN_N_PER_TYPE=5, unchanged).
2. FINITE_SET_CONFORMANCE — exact per-type PERFECT / IMPERFECT /
   NOT_EVALUABLE over the sealed operator-confirmed set using
   `provenance_valid_match`; item vector included; only a type's own
   negative class can make it IMPERFECT. Amendment text with frozen
   readings: see AMENDMENT 1 in METRIC_PLAN_PREREGISTRATION.md.
   Amendment basis: architect directive + PUBLIC counts only; sealed
   holdout still unread; matching/scorability/stability unchanged.

Tests: 23 passing (17 original + 6 amendment proofs incl. the five the
architect specified).

## What exists (working tree of packages/yt-is, verified 2026-08-27)

- `ef/eval_interest_semantic.py` — frozen evaluator core
- `scripts/eval_interest_holdout.py` — CLI (support/score/stability/
  stability-check/freeze-receipt)
- `tests/test_eval_interest_semantic.py` — 17 offline tests, all passing
  (`python -m pytest tests/test_eval_interest_semantic.py -q`
  → 17 passed)
- `METRIC_PLAN_PREREGISTRATION.md` — the ten preregistered elements +
  fail-closed bindings
- `FREEZE_RECEIPT.json` — sha256s of all four + judge prompts + model
  config + gate preconditions

Hashes at generation time (AMENDMENT_1 chain; verify before landing;
any drift invalidates):

- ef/eval_interest_semantic.py 3321d8aa8369012413e16439273e415915c737eaab6369de55f73ac7b31150dd
- scripts/eval_interest_holdout.py 623ea5b80435321b5a0b4b12de5c8402ebfe7b4bc481eeae328f9a7c932d91f8
- tests/test_eval_interest_semantic.py a7234474df8f8554c1953885bcd0333c8825986ef111133500bd493156c67ced
- METRIC_PLAN_PREREGISTRATION.md f3bcd0e72bbafbd461b6e868ff755990544ac0215bbec83898bee112381f46fb

Superseded pre-amendment chain (history only): d03755c1…, 623ea5b8…,
ea789ad9…, 604d17fd….

## Contamination record

This session never opened any file under `.data/yt-is/private/`. Reads:
public POLICY.md/RECEIPT.json (v1 + v1.1), project states,
`contract-compliance-bakeoff-preregistration.md`,
`ef/inference_contract.py`, `scripts/build_interest_graph.py` (main),
legacy `scripts/eval_interest_recovery.py`, bakeoff driver header. No
live-corpus inference outputs were read either (bakeoff artifact dirs
listed by name only).

## Why these artifacts are not yet committed

Direct commit in packages/yt-is is broker-gated. The diff exceeds the
150 changed-.py-line threshold, so the reviewed path is required:

1. `dispatch_review.py dispatch --worktree <yt-is lane worktree>
   --pathspec ef/eval_interest_semantic.py --pathspec
   scripts/eval_interest_holdout.py --pathspec
   tests/test_eval_interest_semantic.py --pathspec
   docs/handoffs/interest-intelligence/interest-semantic-evaluator-v1/`
   (yt-is lane: provision with `--repo P:/packages/yt-is
   --sessions-root P:/packages/yt-is/.data/sessions`)
2. An independent host-witnessed session reviews the packet and writes
   `verdict.json` with the receipt bindings (this cannot be satisfied
   from inside the authoring session — that is the control working).
3. `dispatch_review.py finalize --worktree <lane> --run <run>`
4. `commit_broker.py --worktree <lane> --run <run> --message ...`
5. `integration_broker.py --worktree <lane> --run <run>`

Note for the reviewing session: the sealing property to attest is that
the metric plan binds behaviors BEFORE holdout contact; test fixtures
are synthetic-fictional only.

## Post-freeze procedure (unchanged from FREEZE_RECEIPT.json)

Architect freezes the selected inference implementation SHA → bind →
re-verify manifest → then and only then open v1.1 → single scoring run
→ report exact per-item outcomes → stop.
