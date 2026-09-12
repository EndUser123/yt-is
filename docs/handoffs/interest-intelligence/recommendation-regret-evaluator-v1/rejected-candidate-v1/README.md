# Rejected v1 candidate — preserved historical evidence

agent: zcode
host: zcode
preserved: 2026-08-27
provenance: byte-exact snapshot taken by the freeze reviewer session
sess_453bc40e-9bbd-4d62-b7da-5cbc2c9f655a BEFORE applying
ARCHITECT_AMENDMENT_1_REVIEW_REPAIR. The v1 candidate files were never
git-committed (untracked working-tree files), so this directory is the
only durable copy of the rejected bytes.

## Contents

- `ef/eval_recommendation_regret.py` — v1 evaluator core
- `scripts/eval_recommendation_regret.py` — v1 CLI
- `tests/test_eval_recommendation_regret.py` — v1 test suite (39 tests)
- `METRIC_PLAN_PREREGISTRATION.v1.md` — v1 normative plan
- `FREEZE_RECEIPT.v1.json` — v1 freeze receipt (verbatim)

## Verification

All four artifact hashes in FREEZE_RECEIPT.v1.json reproduce against
the files in this directory (verified 2026-08-27):

- ef/eval_recommendation_regret.py
  95369544be76f418e9ae86ecbe0882af27ee051e5f9722b85f17444f85b05e85
- scripts/eval_recommendation_regret.py
  26f265b6f6ea559138717c1b0813407e7f6872584a233476e819fe0fa7712ce5
- tests/test_eval_recommendation_regret.py
  0c56aff6cfcae3cd700d17121937f012a4e8a8f803408af680178a3df79ab992
- METRIC_PLAN_PREREGISTRATION (v1)
  3308d67d7deb889fb64d543b808d714cf82d0ef9030cc8fb1d29dc1aa247b255

## Disposition

REJECTED by REVIEW-freeze-20260827.md (commit
732e2cfa5dbe2848421b48cf5b46d9bf4c68039); rejection accepted by the
architect; superseded by ARCHITECT_AMENDMENT_1_REVIEW_REPAIR.md. Do
not run, import, or resurrect these files: they contain the review
defects R1-R7 (false generalization semantics, absent-similarity
falsifier pass, incomplete/unenforced leak validation, judge-visible
similarity diagnostic, unbound sidecars, detached evidence classes,
counting integrity gaps).
