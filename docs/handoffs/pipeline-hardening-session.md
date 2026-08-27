# Delegation packet — landing-pipeline hardenings (one focused session)

Created: 2026-08-26 by sess_34a222b2 (agent: zcode). Origin: /todo NOW
item; owner/done-when fields pre-written from /tp improve (findings log).

## Objective

Make the yt-is landing pipeline self-enforcing so the three defect
classes shipped this week cannot recur silently:
tree-binding (review validates what actually runs), suite gating (red
trees cannot reach main), tree-provenance (live services identifiable
to their reviewed commit).

## Scope — three bounded changes, in this order

1. **dispatch_review.py pre-dispatch check** (`P:/.agents/scripts/dispatch_review.py`):
   before accepting a dispatch, for each --pathspec assert staged blob ==
   worktree blob; additionally refuse when untracked-or-modified .py files
   exist inside any pathspec'd DIRECTORY not covered by the pathspec set.
   Failure mode it kills: runs 9caef895f992 / ae25df6a7f7f / bb844988c8c2
   (three full reviewer rounds on stale/mismatched trees).
   Acceptance: unit tests cover each refusal branch; live re-dispatch of a
   knowingly-stale set exits 2 with the offending path named.
2. **Suite-gate at integration** (`P:/.agents/hooks/core/review_gate.py` or
   integration_broker.py call path): integration refuses a candidate tree
   whose `python -m pytest tests -q` exits nonzero in the lane worktree,
   unless `YTIS_INTEGRATION_BYPASS=<reason>` is set (reason recorded in
   receipt). Acceptance: red-tree integrate attempt denied with test
   tail; bypass path writes reason to receipt; disabled for non-yt-is
   repos via config key.
3. **Service tree-fingerprint** (`ef/warm_query_service.py` `/health` +
   `freshness.emit_status()`): add `git -C <repo> describe --always
   --dirty` subprocess (CREATE_NO_WINDOW) sampled once at startup;
   emit as `code_commit`, `code_dirty` fields. Acceptance: live service
   health shows the integrated commit sha; dirty status visible when
   running from a dirty primary (the 2026-08-26 silent-divergence class).

## Constraints

- Brokers are the only commit path; expect >=150-line review on change 1.
- Hooks/tests live in P:/.agents/hooks (tests mandatory per repo rule).
- Never write P:/.data/yt-is except the documented runtime surfaces.
- Fresh-context reviewer required per run; recompute pathspec sets after
  EVERY edit (three rejections this week came from stale sets).

## State anchor

main 3314172ca lineage at packet time; findings_log rows for all three
classes carry receipts. Related wiki: watermark-guards-*,
harness-run-comparison-protocol.
