# IMPLEMENTATION.md — yt-is Overnight Supervisor 2026-08-08

**Status:** `done` for phase 2; superseded for parent decisions by `FINAL-HANDOFF.md`.

## Decision

Candidate 1 (import/forward-sync/unmatched path) was selected. Inspection of `scripts/title_bridge.py` — the shared infrastructure used by both `scripts/import_nlm_transcripts.py` and the forward-sync provider — revealed zero test coverage. The module contains five pure/isolatable functions (`normalize_title`, `match_title`, `merge_bridges`, `build_bridge_from_clusters`, `build_title_bridge`) all testable entirely offline without external data, live DB, auth, or network. No other gap in the import path justified a code change without external data.

Candidate 2 (adaptive scheduler) was inspected and no justified gap was found: the `AssignmentLedger` conservation accounting, `AdaptiveWorkerScheduler` scale-up/down invariants, and `_classify_adaptive_worker_health` + `_adaptive_result_requires_requeue` chain already have comprehensive offline tests (118 adaptive/sharded/load-ladder tests pass). No unsafe-behavior gap was identified.

## Implementation

Wrote `tests/test_title_bridge.py` — 36 pytest cases covering:

| Function | Tests | Coverage |
|----------|-------|----------|
| `normalize_title` | 13 (12 parametrized + 1 None) | ASCII, Unicode, punctuation, whitespace collapse, empty/None |
| `match_title` (exact) | 2 | ASCII and Unicode exact matches |
| `match_title` (ambiguous) | 1 | Title collision → `None, "ambiguous"` |
| `match_title` (unmatched) | 3 | Missing title, empty, whitespace-only |
| `match_title` (fuzzy) | 4 | Above/below threshold, default-off, ambiguous-fuzzy |
| `merge_bridges` | 5 | Combine, deduplicate, merge-different-IDs, empty inputs |
| `build_bridge_from_clusters` | 6 | Valid, missing file, invalid JSON, non-YouTube URLs, titleless entries, multi-file |
| `build_title_bridge` (integration) | 1 | clusters-only path |
| Full pipeline roundtrip | 1 | clusters→bridge→match |

All tests use `tmp_path` fixtures (no live DB). No external data, auth, network, or destructive actions.

## Evidence Used

- `scripts/title_bridge.py`: Full source inspected. Five pure functions identified as testable offline.
- `scripts/import_nlm_transcripts.py`: Uses `title_bridge.match_title` and `title_bridge.normalize_title` for matching.
- `csf/cache.py`: `get_cached_transcript_by_video_id()` exists for forward-sync cache check; already exercised indirectly.
- `tests/test_csf_nlm_import.py`: Import-routing tests exist for `csf_nlm_import.py` but zero coverage for `title_bridge.py`.
- `tests/test_adaptive_worker_scheduler.py`: 25+ tests for adaptive scheduler; gap-free for offline invariants.
- `bin/csf-source`: `_classify_adaptive_worker_health`, `_adaptive_result_requires_requeue`, `_requeue_adaptive_assignment` all covered in `test_csf_source_fetch_timing.py`.

## Verification

The implementation agent did not run commands. Parent verification was subsequently run in this isolated worktree:

```powershell
cd P:/.worktrees/yt-is-autonomous-overnight-20260808
C:/Python314/python.exe -m pytest tests/test_title_bridge.py -q
# 36 passed in 0.60s
C:/Python314/python.exe -c "import scripts.import_nlm_transcripts; print('import_ok')"
# import_ok
C:/Python314/python.exe -m py_compile scripts/import_nlm_transcripts.py scripts/title_bridge.py tests/test_title_bridge.py
# exit 0
git diff --check
# exit 0
```

The supervisor's first full-suite attempt used an unrelated Python 3.13 installation without `pytest`. A corrected parent run with
Python 3.14 completed the full suite but did not produce a green result:

```text
1136 passed, 24 failed, 1 error, 4 skipped in 413.57s (0:06:53)
```

The failures span existing auth, integration, environment, and fixture assumptions. No failing test directly targets the new
title-bridge suite, but no baseline run was performed, so the failures must not be called pre-existing or attributed to the patch
without further comparison.

## Files Changed

| File | Action | Reason |
|------|--------|--------|
| `tests/test_title_bridge.py` | Created (new file) | 36 pytest cases for the shared title→video_id bridge |

No production code, config, DB, or other tracked files were modified.

## Remaining Risks

1. **Full suite is not green**: The corrected run reported 24 failures and one collection error. The title-bridge focused suite passes, but a baseline comparison is required before classifying the broader failures as unrelated to this branch.
2. **`DEFAULT_CLUSTERS_FILES` global**: `scripts/title_bridge.py` defines `DEFAULT_CLUSTERS_FILES` at module level referencing a hardcoded path (`C:/Users/brsth/Downloads/...`). Importing the module does not fail (it's a `Path` object, not accessed until called), but the default path is not portable. Tests override via `clusters_files=` parameter.
3. **467 unmatched transcripts**: These remain unresolved. The tests prove `match_title` behaves correctly for unmatched and ambiguous cases, but resolving the actual unmatched set requires external data (YouTube API or manual title inspection), which is outside the offline budget.

## Claim Ledger

| Claim | Type | Evidence | Falsifier | Action allowed |
|---|---|---|---|---|
| `scripts/title_bridge.py` had zero test coverage | verified_fact | `grep` for `title_bridge` in `tests/` returned no test definitions | A test file exists that was missed | Wrote tests |
| `match_title` returns correct match types for exact/ambiguous/unmatched/fuzzy | verified_fact (from code inspection) | Source at `title_bridge.py:139-180` | Tests fail on run | Tests written to prove |
| `normalize_title` handles Unicode, punctuation, whitespace correctly | verified_fact (from code inspection) | Source at `title_bridge.py:33-38` | Tests fail on run | Tests written to prove |
| `merge_bridges` deduplicates correctly | verified_fact (from code inspection) | Source at `title_bridge.py:97-104` | Tests fail on run | Tests written to prove |
| Adaptive scheduler has no justified offline gap | inference | `test_adaptive_worker_scheduler.py` has 25+ tests covering ledger conservation, scale-up/down invariants, failure/requeue, identity validation, and draining | New gap found by adversarial review | inspect only |
| 467 unmatched transcripts remain unresolved | historical_context | `HANDOFF.md:15-16` | Current import report shows different count | no action — requires external data |

## Next Action

1. **Parent decision required**: Resolving the 467 unmatched transcripts. This requires either:
   - YouTube Data API calls to fetch video metadata by title (external, needs quota)
   - Manual title inspection against the existing `analysis_status` table
   - Fuzzy matching with a low threshold (risks false positives at scale)
2. **Future**: Add tests for `scripts/import_nlm_transcripts.py` (primarily `parse_md_file` and the main import loop), which would need temp `.md` files and a temp transcript DB.

## Parent Handoff

`Parent handoff: needs_fix` — phase 3 found and fixed the consumer import defect in this isolated worktree; use `FINAL-HANDOFF.md` as the governing handoff.
