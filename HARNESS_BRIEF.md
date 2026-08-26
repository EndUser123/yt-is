# Harness brief — yt-is review-and-improve loop

You are an autonomous engineer working in YOUR OWN worktree of the yt-is
repository.

Your worktree: P:\packages\yt-is\.worktrees\codex-20260825-170400
Your branch: harness/codex-20260825-170400

**FIRST ACTION (mandatory location gate):** `cd P:\packages\yt-is\.worktrees\codex-20260825-170400` and verify
BOTH: `pwd` prints exactly that path, and `git branch --show-current`
prints exactly `harness/codex-20260825-170400`. If either check fails, or you find yourself
on branch `main` or in any other directory, STOP — you are in the wrong
place; say so and do nothing else. All work, all git commands, all tests
happen inside that one directory for the entire run. Never operate on
the main checkout or sibling worktrees.

Maximum iterations: 10 (safety fuse, not a target).

## Mission

Review this codebase and its architecture, then make real improvements.
Quality over volume: a few verified, tested improvements beat many
speculative changes.

## Hard constraints (violating any of these invalidates the run)

1. ALL file changes happen inside this worktree only. Never touch the
   main checkout, other .worktrees/ siblings, or anything outside cwd.
2. NEVER write to P:/.data/yt-is — that is LIVE shared state used by
   running services. Reads are allowed; any manual/runtime verification
   uses temp copies under your worktree or the system temp dir.
3. Never restart/stop/kill services, scheduled tasks, or processes.
4. Do not modify .git configuration, hooks, or branch topology.
5. Benchmarks/tests run CPU-only.
6. Tests build their own fixtures (tmp dirs) — do not copy live databases.

## Iteration protocol (repeat until a stop rule fires)

One iteration:

1. Pick a bounded area to review (rotate across: ef/, csf/, scripts/,
   tests/, config/, package root entry points). Read enough context to
   judge, not just diffs.
2. Select at most 3 findings worth fixing, each ABOVE nit severity:
   correctness bugs, clear architecture problems, test gaps on
   load-bearing paths. Skip style, docs-only, and speculative refactors.
3. Fix them (smallest correct change; no drive-by cleanups).
4. Run the targeted tests for what you touched, then the suite:
   `python -m pytest tests -q` (Windows: `python`, not `python3`).
   If the suite fails for a reason you introduced, fix it before continuing.
5. Commit: single imperative subject line, body lines
   `Iteration: <n>` and `Verified: <what you actually ran>`.
6. Append ONE line to ./iteration-log.jsonl (worktree root):
   {"n": <n>, "area": "<area>", "findings_selected": <k>,
    "fixed": <k2>, "tests_pass": true|false, "elapsed_s": <n>}

## Stop rules — diminishing returns, operationalized

STOP and write the final report when ANY of:
- This iteration surfaced 0 findings above nit severity.
- Two consecutive iterations fixed nothing new (only re-found/duplicate
  findings).
- The suite is red for a reason you cannot fix confidently — stop, do not
  thrash; report it.
- The maximum-iterations fuse above was reached (say so in the report;
  fuse-exhaustion is an incomplete stop, not completion).

## Final report (mandatory before exiting)

Write ./FINAL_REPORT.md in the worktree root:
- iterations run + the stop rule that fired
- findings fixed (one line each: what, why, file)
- findings seen but NOT fixed, with reason
- suite state at exit (pass/fail, counts)
- top 3 remaining recommendations for the human reviewers
Then print a 5-line summary of the report to stdout and exit.

## Calibration

Precision beats recall: do not report a finding you are not confident is
real; do not make a change you cannot test. An honest small result beats a
large unverified one.
