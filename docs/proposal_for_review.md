# yt-is Import Workflow - Review and Implementation Status

## Status

Verified 2026-08-05 against the current source, tests, and CLI routing. This
document supersedes the earlier proposal text below. The original findings are
kept as historical context; they are not current claims about the code.

## Original concern

The import workflow needed a bounded way to add playlist/history video IDs
without overwriting completed analysis rows, silently discarding metadata, or
claiming that the displayed plan matched the actual write set. Provenance was
also not modeled as an independent many-to-many relationship.

## Current verified state

1. The current `set_status` and `set_status_batch` implementations use guarded
   UPSERTs for `analysis_status` and have regression coverage in
   `tests/test_batch_status.py`. The earlier claim that they still use a
   destructive whole-row replacement for this table is stale. Other tables in
   `batch_status.py` still use `INSERT OR REPLACE`; that is a separate review
   scope and is not evidence that `analysis_status` is destructive.
2. `scripts/import_video_ids.py` now calls the explicit
   `csf.batch_status.import_video_ids` boundary. It is dry-run by default and
   requires `--execute` for status-row writes.
3. The importer merges duplicate IDs through the API, reports per-video
   decisions, preserves complete rows, fills only missing existing metadata,
   and can write an optional JSON decision report with `--report PATH`.
   Reports cannot replace the database or source inputs, and existing reports
   require explicit `--overwrite-report`.
4. `scripts/fetch_custom_sources.py` is a sequential reusable-batch workflow;
   the current file contains no `Future.cancel()` timeout wrapper. It still
   passes backend labels such as `notebooklm` to lifecycle helpers. Whether
   those labels should be separate from import provenance is an open data-model
   decision, not a verified destructive-write defect in the current importer.
5. `bin/csf-source fetch --video-manifest PATH` now provides an exact-video
   route. It validates the manifest, resolves status rows directly, preserves
   manifest order, and reports missing/non-pending/limit-omitted IDs.

## Implemented Tier 1 - Safe import API

Implemented in `csf/batch_status.py`:

- `import_video_ids(entries, execute=False)` is a separate API; the existing
  lifecycle API was not renamed.
- A bounded bulk prefetch reads all target rows before decisions are made,
  chunked below SQLite's common host-parameter limit.
- Duplicate IDs with incompatible statuses become `conflict` and are not
  written.
- Existing `complete` rows become `skipped_complete` and cannot be downgraded.
- Existing non-null fields remain authoritative; incoming non-null values fill
  only missing fields.
- Lifecycle fields (`last_stage`, `failure_reason`, and `unavailable_reason`)
  are not accepted as import metadata.
- Decisions are `inserted`, `updated`, `skipped_complete`, `unchanged`,
  `conflict`, or `blocked` for an incompatible read-only schema.
- The write path uses one transaction and a guarded UPSERT. The default plan
  path performs no status-row writes.

The importer script exposes `--execute`, explicit `--dry-run`, optional
`--db-path PATH`, optional `--report PATH`, and `--overwrite-report`.
Execution requires both `--plan PATH` from a prior dry-run report and
`--report PATH` for a durable execution receipt. The plan records
resolved inputs, input and target-row database fingerprints, parse rejection
counts, and decisions. Execution rechecks those fingerprints and decisions
inside the write path before committing, so a changed imported row aborts.
Reports are written to a same-directory temporary file and moved into place
atomically without replacing protected inputs or the database.

The parsers retain their list-returning API by default and expose parse stats to
the CLI. The report and stdout now show malformed/invalid/duplicate records and
history rows omitted by the 5,000-row limit instead of silently presenting the
accepted subset as the complete input.

## Implemented Tier 2 - Import Provenance

The append-only provenance owner already exists in
`csf/playlist_imports.py`, using `playlist_import_run` and
`playlist_import_item` in the separate `playlists.sqlite` database. The
`history` and `watchlater` channel-discovery paths already write these rows.

`scripts/import_video_ids.py --execute` now records one bulk append-only
`video_import` run and item rows through `record_video_import_run`, while the
status mutation remains owned by `csf.batch_status.import_video_ids`. A failed
mutation marks the provenance run failed. Dry-run remains write-free.

## Implemented Tier 3 - Exact-Video Fetch Selection

`bin/csf-source fetch --video-manifest PATH` is implemented with a validated
selection adapter and mocked dry-run routing coverage. The manifest controls
selection only; the status database remains authoritative. Live manifest
fetches require an explicit `--limit`, and `--video-manifest` is mutually
exclusive with `--source`.

## Compact claim ledger

| Claim | Type | Evidence | Falsifier | Action allowed |
|---|---|---|---|---|
| Import API preserves complete rows | verified_fact | API code and `TestImportVideoIds` | regression test shows status/metadata changed | use dry-run or reviewed execute |
| Current importer is dry-run by default | verified_fact | CLI parser and call site | no-flag invocation writes a status row | safe plan review |
| Execute is bound to reviewed target state | verified_fact | plan fingerprint and targeted-row mutation test | changed target row does not abort | execute only with a valid plan |
| Import origins need independent provenance | inference | playlist/history are multiple origins per ID | existing log cannot represent the needed audit | integrate with the existing log; add schema only if a tested query requires it |
| Manifest fetch avoids broad channel selection | verified_fact | manifest selection test and `fetch_manifest_selection` receipt | selected IDs differ from downstream IDs | use only with reviewed manifest and limit |

## Verification

Required checks for this branch:

```text
python -m pytest tests/test_batch_status.py -q -k "ImportVideoIds or SetStatusBatch"
python -m pytest tests/test_import_video_ids.py -q
python -m py_compile csf/batch_status.py scripts/import_video_ids.py tests/test_batch_status.py tests/test_import_video_ids.py
git diff --check
```

Current results: the combined affected suite passed 97 tests, including the
status/import/provenance and fetch timing tests. The focused manifest suite
passed 38 tests. Compilation, CLI help, and diff checks passed. No external
spend or live fetch was authorized.
The FMEA scanner reports the temporary-file boundary as a heuristic risk; code
inspection and the report test confirm that the final report uses atomic
replacement and does not leave a temporary file behind.

The full `tests/test_batch_status.py` suite also contains environment-dependent
channel identity/quota tests. If those fail, report their exact failures and do
not authorize external spend merely to make the suite green.

## Review boundary

This branch hardens import planning, status-row mutation, provenance logging,
and exact-video selection. It does not fetch YouTube metadata, launch
NotebookLM, run a transcript batch, stage, commit, or push changes. Live
manifest execution remains a separate operational decision requiring a
reviewed manifest, explicit limit, and normal runtime/auth preflight.
