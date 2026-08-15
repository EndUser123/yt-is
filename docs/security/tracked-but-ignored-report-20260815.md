# Tracked-but-ignored files — report (2026-08-15)

agent: zcode | host: both | basis: `git ls-files -i -c --exclude-standard` at `main` = 477e77a

**Count: 1,553 files are tracked by git while matching a .gitignore rule.**
Gitignore never applies to already-tracked files, so these keep entering
every commit until explicitly untracked.

## Distribution (second-level path prefix, top entries)

| Count | Prefix | Likely intent |
|---|---|---|
| 687 | `.logs/sharded_lane_series/` | **Curated on purpose** — gitignore negation patterns keep `sharded_lane_series_summary.json` and `term_*.jsonl`; these are benchmark evidence, not accidental |
| 32 | `.logs/batch_size_series_v3/` | Ignore rule predates tracking; probably should be untracked |
| 7 | `.logs/batch_size_series/` | same |
| 3 | `.claude-state/tdd/` | Runtime state; probably should be untracked |
| ~824 | misc `.logs/*` (term logs, test outputs, one-off jsonl) | Bulk log noise; probably should be untracked |

## Recommendation (NOT executed — report only)

1. Confirm which `.logs/sharded_lane_series/` files are *meant* to be tracked
   (the negation patterns suggest deliberate curation) before any bulk action.
2. The rest (~860 files: batch_size_series trees, .claude-state, misc term/test
   logs) are candidates for `git rm --cached` in one cleanup commit.
3. After untracking, the "checkout is perpetually dirty" symptom noted in the
   2026-08-15 review should disappear for these paths.

Full file list: regenerate with
`git ls-files -i -c --exclude-standard` (at main >= 477e77a).

Provenance: part of the 2026-08-15 security remediation (see
`docs/security/credential-rotation-runbook-20260815.md` and HANDOFF).
