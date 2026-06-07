# Space Discipline

Use this policy to keep useful evidence while avoiding orphaned browser files and old benchmark roots.

## Keep

- Active benchmark browser roots:
  - `P:\.data\yt-is\browser\notebooklm-pro`
  - `P:\.data\yt-is\browser\notebooklm-free`
- The current live run root for the benchmark that is still running.
- The latest comparator root for an active hypothesis.
- Any run root whose conclusion has not yet been promoted into `test-registry.md` or the next-test plan.

## Prune

- Browser roots that are not active benchmark roots and are not referenced by a live run.
- Run roots whose conclusions have already been promoted into the docs and are no longer the current or comparator evidence for an active branch.
- Launcher scratch files and out-of-band logs once their failure mode has been captured in a doc or test.

## Procedure

1. Record the conclusion in `docs/operations/test-registry.md` or `docs/operations/hot-path-throughput-next-test-plan.md`.
2. Run the audit helper:
   - `python P:\packages\yt-is\bin\csf-space-audit`
3. Review any rows marked `candidate`.
4. Delete only the rows you still agree are obsolete.
5. Re-run the audit and confirm the size drop.

## Notes

- The audit helper is report-only by default.
- Do not delete live browser roots or current run roots.
- If a root is still the only source of a conclusion, keep it.
