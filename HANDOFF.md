# yt-is Handoff

**This is the package-local operational reference for `yt-is`.** For active work
streams, see the integration handoff chain at the bottom of this file.

Last updated: 2026-08-05 (cleaned up — removed completed trust-floor, worktree-lifecycle,
and throughput-benchmarking sections; kept operational reference).

## Active work stream

The active work is documented in the `docs/handoffs/` chain:

1. **`P:/docs/handoffs/yt-is-nlm-to-wiki-integration-20260730/HANDOFF.md`** — parent.
   Making yt-is the single canonical YouTube transcript store. 3,918 of 5,070
   transcripts imported. Forward path: resolve remaining 467 unmatched transcripts,
   implement forward-sync, sync 740 unchecked channels.

2. **`P:/docs/handoffs/yt-is-nlm-to-wiki-fixes-20260730/HANDOFF.md`** — child.
   F2 (cache-first + feed-forward) shipped. F1 (wiki-query Stop hook) and F3
   (orphan resolver) deferred.

3. **`P:/docs/handoffs/wiki-yt-architecture-decisions-20260730/HANDOFF.md`** — child.
   Five locked architecture decisions (NotebookLM primary, cache-first shipped,
   Stage 0 rejected, wiki-yt rename, non-lossy metadata pipeline).

## Databases

Two transcript databases exist — know which one you're working with:

| Database | Location | Rows | Purpose |
|----------|----------|------|---------|
| **Primary** | `P:/.data/yt-is/transcripts.sqlite` | ~10,072 | The active cache. Integration imports land here. |
| **Stale package-local** | `P:/packages/yt-is/.data/yt-is/transcripts.sqlite` | 369 | Old dev DB. Do not use for new work. |

Before any operation, verify which DB your code targets via `YTIS_TRANSCRIPT_CACHE_DB_PATH`.

## Key files

- `csf/nlm_config.py` — NotebookLM batch size, source cap, materialization timeout, auth policy defaults
- `csf/nlm_batch.py` — worker-owned notebook rotation and source-add subbatch sizing
- `bin/csf-source` — preflight routing split, worker-run orchestration, logging
- `csf/transcript.py` — oEmbed probe, direct_api classification, Whisper fallback, negative-cache persistence
- `csf/batch_status.py` — transcript cache / negative cache / status persistence
- `csf/cache.py` — `get_cached_transcript_by_video_id()` (added by F2 forward-sync)
- `scripts/title_bridge.py` — shared title→video_id bridge (extracted from importer)
- `scripts/import_nlm_transcripts.py` — one-time backfill importer (nlm-to-wiki → yt-is cache)
- `tests/test_csf_source_fetch_timing.py` — routing regression tests
- `tests/test_transcript.py` — direct_api and Whisper regression tests
- `tests/test_shared_modules.py` — 31 tests for csf/urls.py, csf/paths.py, csf/clusters.py

## Import workflow operationalization

The import-workflow operationalization is now on `main` in the reviewed
cherry-picked commits `e75af02` and `deb26ba`. The former review branch
`codex/yt-is-import-operationalization` (tip `ae4952f`) was verified clean and
tree-equivalent before it was retired; `main` is now the sole active worktree.

- `scripts/build_video_selection_manifest.py` builds deterministic manifests
  from local `analysis_status` rows only.
- `bin/csf-source fetch --video-manifest PATH` selects exact IDs. Add
  `--selection-receipt PATH` for an atomic selection snapshot, and
  `--verify-selection-receipt PATH` to fail closed if the manifest or relevant
  status rows changed; live manifest fetches still require an explicit
  `--limit`.
- `scripts/reconcile_video_imports.py` lists unfinished `video_import` runs or
  reconciles one run against `analysis_status` without writing either DB. Import
  provenance records the effective batch-status DB path; the CLI uses that path
  unless `--batch-db` is supplied explicitly, and fails closed on unavailable DBs.
- Design and acceptance evidence: `docs/operations/import-workflow-next-design.md`
  and `docs/proposal_for_review.md`.

No live fetch, external API call, NotebookLM action, or raw-artifact mutation
was performed as part of this implementation. The current main worktree also
preserves the pre-existing modification to
`.logs/term_5bd58f58.jsonl`; do not reset, stage, or delete it. The canonical
transcript integration remains in progress: resolve the unmatched transcripts,
implement forward-sync, and sync the unchecked channels listed above. The
throughput investigation remains dormant and does not establish an optimal VPH.

Post-merge verification on `main` (2026-08-05): the focused batch-status
selection tests passed `14` with `34` deselected; the import, manifest,
reconciliation, playlist, and `csf-source` test set passed `41`; compilation,
`git diff --check`, and the three CLI help checks all passed. No live or
write-producing workflow was run.

## Routing split (still active)

`no_captions` items go to NotebookLM (not the slow fallback lane). Live/streamed/premiere
items go to `transcript_fallback`. This split fixed a major throughput collapse.

The fallback tail reaches Whisper for `yt-dlp = ok` videos with no captions.
Audio download includes `--js-runtimes node` when `node` is available, which
solves the YouTube `n` challenge on the fallback path. Successful fallback
transcripts are cached in the primary DB.

## Backup commands (before risky operations)

Before any risky sweep or cleanup:
```bash
python P:/packages/yt-is/bin/csf-backup-transcripts    # snapshots transcripts.sqlite
python P:/packages/yt-is/bin/csf-backup-channel-state  # snapshots batch_status.sqlite
```

Staging DB pattern (for long runs before promotion):
```bash
# Set env to staging DB, run, then promote
YTIS_TRANSCRIPT_CACHE_DB_PATH=P:/.data/yt-is/transcripts-staging.sqlite
python P:/packages/yt-is/bin/csf-promote-transcripts   # blocking, fail-closed

YTIS_BATCH_STATUS_DB_PATH=P:/.data/yt-is/batch-status-staging.sqlite
python P:/packages/yt-is/bin/csf-promote-channel-state  # blocking, fail-closed
```

Legacy URL→channel_id backfill:
```bash
python P:/packages/yt-is/bin/csf-migrate-channel-ids
```

## Debugging / logging rules

- **Read [DEBUGGING_PLAYBOOK.md](P:/packages/yt-is/DEBUGGING_PLAYBOOK.md) first.**
- Do not trust the JSONL trace alone. Several important warnings surface only in live stderr/stdout.
- When threading a new field through a wrapper, verify the callee signature before assuming it works.
- Treat the worker result file as the source of truth for completed work. Stdout summaries can be stale.
- For throughput questions, prefer completed-worker totals and stage timings over scan-progress rates.
- `YTIS_SCAN_STATUS_INTERVAL_S` controls heartbeat cadence for `yt-is sync` and fetch scans.
- Most useful live signals: `fetch_worker_finished`, `worker_completed`, `worker_batch_metrics`,
  `worker_source_profile_totals`, `negative_cache_reason_counts`, `add_cmd_elapsed_s` vs
  `materialization_wait_elapsed_s`.
- `active_workers: 0` in transcript-fallback logs is expected; that lane is not the industrial
  NotebookLM worker pool.

## Throughput investigation (dormant — read before resuming)

The throughput benchmarking investigation is **dormant, not resolved**. Before
resuming any throughput work:

1. Do not launch another same-shape benchmark. A code/harness change + fresh
   decision packet is required first.
2. The current leader (`3788.53` combined hot-path VPH) is NOT proven optimal
   (smoke promotion gate failed). Do not cite it as a proven ceiling.
3. Read these before any throughput decision:
   - [Throughput Optimization LLM Contract](P:/packages/yt-is/docs/operations/throughput-optimization-llm-contract.md)
   - [Throughput Decision Packet Template](P:/packages/yt-is/docs/operations/templates/throughput-decision-packet.md)
4. Leading hypothesis: batch-1 old-window `nlm source content` latency, with
   retry-heavy rows and Free batch_01/batch_02 as the main hotspots.

## Session bootstrap checklist

Read these before starting any yt-is work:

- [HANDOFF.md](P:/packages/yt-is/HANDOFF.md) (this file)
- [CODEX_MEMORY.md](P:/packages/yt-is/CODEX_MEMORY.md)
- [DEBUGGING_PLAYBOOK.md](P:/packages/yt-is/DEBUGGING_PLAYBOOK.md)
- [NLM Auth Architecture](P:/packages/yt-is/docs/operations/nlm-auth-architecture.md)
- The active integration handoff (see "Active work stream" above)

Fast verification:
```bash
python -m py_compile P:/packages/yt-is/bin/csf-source P:/packages/yt-is/csf/transcript.py P:/packages/yt-is/csf/batch_status.py
PYTHONPATH=P:/packages/yt-is python -m pytest P:/packages/yt-is/tests/test_transcript.py P:/packages/yt-is/tests/test_csf_source_fetch_timing.py -q
```

## Cross-package data source: wiki transcripts

`P:/.data/wiki/sources/transcripts/` contains full verbatim YouTube transcripts
exported by the wiki-yt skill via `nlm source content`. Format: one `.md` file
per source, named `<source_id>.md` (NotebookLM UUID). Each file has frontmatter
(`source_id`, `title`, `notebook_id`, `url`, `type`, `exported`) followed by
the complete transcript text.

YouTube source `url` is `null` (NotebookLM doesn't expose it); title-based
matching via `title_bridge.py` closes the provenance gap.

Scale: ~5,070 YouTube transcripts. Integration with yt-is is in progress (see
active work stream above).

<!-- BEGIN worktree-status (auto-generated; do not edit) -->
All worktrees relative to `main`. Generated by `handoff_sync.sync`.

| Path | Branch | Behind main |
|------|--------|----------------:|
| `P:/packages/yt-is` | `main` | 0 |

<!-- END worktree-status -->
