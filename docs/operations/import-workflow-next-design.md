# Import Workflow Next Design

## Status

Implementation-ready design and implementation record, verified against the
current `yt-is` source and tests on 2026-08-05. Commit A and Commit B are now
implemented locally. No external YouTube API spend, transcript fetching,
NotebookLM work, staging, or commit was performed.

## Decision Summary

Use the existing import-log owner in `csf/playlist_imports.py` for durable
provenance. Do not add a second generic manifest or provenance database.

The design was delivered in three bounded slices:

1. Make the three channel-identity tests hermetic so the affected suite is
   green without YouTube API spend.
2. Connect `scripts/import_video_ids.py` to the existing append-only import log
   on execute, using a bulk provenance API and preserving the current dry-run
   contract.
3. Add an exact-video selection manifest to `csf-source fetch` through a new
   `--video-manifest PATH` option. The manifest controls selection only; the
   database remains authoritative for status and routing metadata.

The slices were implemented and reviewed independently. Slice 3 uses a shared
selection adapter, bounded SQL lookup, dry-run coverage, exact-order selection,
and an end-to-end routing test; it is not a parser-only flag.

## Implementation Result

- Commit A: `scripts/import_video_ids.py --execute` now records one append-only
  `video_import` run and item rows in the existing playlist-import database;
  failed status mutation marks the run failed while preserving item evidence.
- Commit B: `bin/csf-source fetch --video-manifest PATH` now validates exact
  11-character IDs, resolves status rows directly, preserves manifest order,
  reports missing/non-pending/limit-omitted IDs, and routes only selected
  pending rows. Live manifest fetches require `--limit`.
- The selection receipt includes manifest and selected-set fingerprints. The
  active batch database is rejected as a manifest path, and large ID lists are
  queried in bounded chunks.
- The three channel-identity tests use deterministic local fixtures; no API
  call is needed for the affected regression suite.

## Discovery Record

The narrow preflight audit was run at:

`P:/tmp/yt-is-import-design-discovery-20260805-narrow.json`

It initially reported a `manifest` role collision between:

- `csf/benchmark_manifest.py`: benchmark cases and expected routing outcomes.
- `csf/browser_ownership_manifest.py`: browser profile ownership and health
  gating.

These are different owners and contracts. Neither owns a runtime video
selection manifest. The proposed role is therefore named
`video_selection_manifest`, not a generic `manifest`, and the apparent
collision is resolved as a semantic false positive.

The audit also found the current worktree changes:

- `csf/batch_status.py`
- `scripts/import_video_ids.py`
- `tests/test_batch_status.py`
- `tests/test_import_video_ids.py`
- `docs/proposal_for_review.md`

No active package plan claimed ownership of these files. The audit timed out
when run against the broad `P:/docs` and package roots together; the narrow
scoped run completed with no walk errors and is the authoritative discovery
record for this design.

## Current Source Of Truth

| Concern | Current owner | Verified behavior | Design consequence |
|---|---|---|---|
| Current video status | `csf/batch_status.py` `analysis_status` | One row per video; complete rows are terminal for the new importer | Do not add status columns to the provenance log |
| Safe ID import | `csf/batch_status.py` `import_video_ids` and `scripts/import_video_ids.py` | Dry-run by default; execute requires a plan and receipt; fingerprints target rows | Add provenance around this boundary, not another status writer |
| Playlist/history provenance | `csf/playlist_imports.py` | `playlist_import_run` and `playlist_import_item` are append-only in a separate SQLite DB | Reuse these tables; improve bulk recording and semantics |
| Channel discovery import | `bin/csf-source` `_cmd_playlist_import` | `history` and `watchlater` already record runs/items and update channel state | Do not redesign these commands as part of ID-import work |
| Exact transcript selection | `bin/csf-source` `cmd_fetch` and `csf/video_selection_manifest.py` | Supports validated `--video-manifest` selection plus `--limit`; database status remains authoritative | Keep selection as an adapter, not a second fetch pipeline |
| Benchmark manifests | `csf/benchmark_manifest.py` | Benchmark-only cases with expected outcomes | Keep separate from production fetch selection |
| Browser manifests | `csf/browser_ownership_manifest.py` | Browser-root ownership records | Keep separate from video selection |

Important current-state correction: the earlier statement that immutable
playlist provenance is "not implemented" is stale. The append-only tables and
writers exist. The new safe importer now covers its status mutation and bulk
provenance contract; live execution remains an operational decision outside
this design.

## Slice 1: Hermetic Channel Tests

### Objective

Make these tests deterministic without authorizing API spend:

- `test_batch_status_env_override_uses_live_data_root`
- `test_batch_status_normalizes_malformed_handle_urls`
- `test_backup_batch_status_db_snapshots_channel_state`

### Design

Patch the imported `csf.batch_status.resolve_channel_identity` symbol in the
tests with a deterministic resolver for `@example` and `@blocked`. Return
`ChannelIdentity` objects with stable UC IDs and canonical URLs. Do not patch
the production API gate and do not set spend authorization.

The fixture should:

- record calls and assert only the intended identity refs were resolved;
- return the same canonical identity for normal and malformed handle forms;
- raise an explicit assertion for an unexpected external lookup rather than
  silently returning a plausible value.

### Acceptance tests

- The three tests pass with the spend gate still disabled.
- The fake resolver is scoped to the tests and does not change production
  behavior.
- `python -m pytest tests/test_batch_status.py tests/test_import_video_ids.py -q`
  has no failures caused by API quota.

## Slice 2: Provenance Integration For Safe ID Import

### Existing constraints

`csf/playlist_imports.py` currently provides:

- `record_playlist_import_run`
- `record_playlist_import_item`
- `finish_playlist_import_run`
- `record_import_run`
- `complete_import_run`

The current generic helper writes one item at a time and commits each item.
The safe importer now writes `analysis_status`, its JSON report, and one
append-only provenance run on execute. Provenance fields remain separate from
lifecycle fields.

### New API contract

Add one bulk helper in `csf/playlist_imports.py`, named
`record_video_import_run`, with this conceptual signature:

```python
record_video_import_run(
    entries: Sequence[BatchEntry],
    *,
    origin: str,
    item_context: Sequence[dict[str, object]] | None,
    planned_decisions: Mapping[str, tuple[str, str | None]],
    notes: dict[str, object],
    db_path: Path | None = None,
) -> str
```

The helper must:

- create one `playlist_import_run` row with `playlist_kind='video_import'`;
- write all item rows in one SQLite transaction;
- require `item_context` to be absent or the same length as `entries`; map it
  by input order;
- accept the already-reviewed decision for each logical ID through
  `planned_decisions`; store the decision and reason as the item
  `classification`/`raw_json` audit fields;
- use deterministic item IDs within the run, such as
  `video_import:<ordinal>:<video_id>`;
- preserve source file, source-row, and parser information from
  `item_context` in `raw_json`;
- preserve the parsed metadata fields already accepted by the safe importer;
- never write `last_stage`, `failure_reason`, or `unavailable_reason` from an
  external import record;
- return the run ID only after the provenance transaction commits;
- leave the existing low-level playlist/history API compatible.

Do not add columns for plan metadata in this slice. Store the following in the
run `notes_json` and report instead:

```json
{
  "workflow": "scripts/import_video_ids.py",
  "input_fingerprint": "sha256:...",
  "database_fingerprint": "sha256:...",
  "playlist_path": "...",
  "history_path": "...",
  "decision_report": "...",
  "parse_stats": {"playlist": {}, "history": {}}
}
```

This avoids a schema migration while retaining enough information to audit or
replay the run.

### Execute ordering and failure semantics

The CLI remains dry-run by default and keeps its current plan binding:

1. Parse inputs and build the dry-run plan.
2. On `--execute`, reparse and revalidate input/database fingerprints.
3. Create the append-only provenance run and item rows; status is `running`.
4. Execute `import_video_ids` with the expected database fingerprint.
5. If the status transaction commits, finish the provenance run as
   `completed`; include `run_id` in the execution receipt.
6. If status mutation fails, best-effort finish the provenance run as
   `failed`, preserve the raw item rows, and return a nonzero exit.

The two SQLite databases are not one atomic transaction. This is intentional:
the provenance record is the audit trail for failures. A `running` or `failed`
run is valid evidence, not a reason to delete the record. A future recovery
command can find non-terminal runs and reconcile them against the status DB.

Dry-run must not write either database. Its JSON report remains the plan and
is the only durable dry-run artifact unless the caller explicitly chooses a
report path.

### Provenance item mapping

For the current importer:

| Log field | Value |
|---|---|
| `playlist_kind` | `video_import` |
| `playlist_url` | combined source description or first source path |
| `command` | `scripts/import_video_ids.py` plus normalized arguments |
| `cookie_source` | null; this importer does not fetch |
| `video_id` | parsed YouTube ID |
| `video_url` | canonical watch URL |
| `video_title` | parsed title, if present |
| `published_at` | parsed history date, if present |
| `duration_seconds` | parsed playlist duration, if present |
| `classification` | importer decision (`inserted`, `updated`, `skipped_complete`, `unchanged`, `conflict`, `blocked`) |
| `raw_json` | source file/row, input metadata, and decision reason |

The status API remains the only writer of `analysis_status`; the provenance
classification is descriptive and must not be passed back as a lifecycle
status.

### Slice 2 acceptance tests

1. Execute creates one completed provenance run and one item per logical input.
2. Dry-run creates no provenance DB and no status rows.
3. A target-row change detected during plan preflight aborts before a
   provenance row is created. A target-row change or status failure after the
   provenance run is created leaves that run marked `failed` with its item rows
   preserved. Both states are tested separately.
4. A status transaction failure leaves item rows and a failed run.
5. Complete rows remain unchanged and their provenance item is classified as
   `skipped_complete`.
6. Lifecycle fields in a crafted external `BatchEntry` remain excluded from
   the import write path and provenance metadata.
7. A 5,000-item import uses bounded SQL parameters and records all items.
8. Existing `tests/test_playlist_imports.py` and `TestImportVideoBatch` remain
   green; the new bulk helper does not change their legacy semantics.

## Slice 3: Exact Video Selection Manifest

### Objective

Allow `csf-source fetch` to process an explicit, reviewed set of video IDs
without scanning or selecting unrelated channels. This is a production fetch
selection feature, not a benchmark manifest and not a new transcript backend.

### CLI contract

Add:

```text
csf-source fetch --video-manifest PATH [--dry-run] [--limit N] [--workers N]
```

Rules:

- `--video-manifest` and `--source` are mutually exclusive in the first
  implementation. This prevents ambiguous intersection semantics.
- A live manifest fetch requires `--limit`, preserving the existing scope
  guard. Dry-run may omit it.
- The manifest path must be readable and must not be the active batch DB.
- Invalid IDs, duplicate IDs, unknown schema versions, or malformed rows fail
  closed before any fetch or status mutation.
- Manifest order is preserved. `--limit` selects the first eligible pending
  rows in manifest order.
- Missing IDs and non-pending IDs are reported as skipped, never implicitly
  requeued or mutated.
- Database `analysis_status.source`, status, and metadata remain authoritative;
  manifest metadata cannot override routing or lifecycle state.
- No channel enumeration or YouTube API call is needed to build the selection.

### Manifest schema v1

Use a dedicated loader `csf/video_selection_manifest.py`:

```json
{
  "manifest_version": 1,
  "generated_at": "2026-08-05T00:00:00+00:00",
  "selection_name": "reviewed-import-2026-08-05",
  "videos": [
    {"video_id": "dQw4w9WgXcQ", "source_note": "history.csv:42"}
  ]
}
```

Only `video_id` is used for selection. `source_note` is optional audit context
and is never trusted as a channel or provider route. The loader should return
an immutable `VideoSelectionManifest` with:

- version and generation metadata;
- ordered unique IDs;
- optional source notes;
- a SHA-256 fingerprint of the canonical JSON or raw file bytes.

Do not reuse `BenchmarkManifest`; its `expected` routing fields have different
semantics. Do not reuse `BrowserOwnershipManifest`; it has no video identity.

### Selection adapter

Refactor the selection portion of `cmd_fetch` into a pure/testable function:

```python
select_fetch_items(
    *,
    source_filter: str | None,
    video_manifest: VideoSelectionManifest | None,
    max_items: int | None,
    db_path: Path | None = None,
) -> FetchSelection
```

`FetchSelection` must contain:

- ordered eligible `(video_id, source_url)` pairs;
- `selected_count`;
- `missing_count`;
- `non_pending_count` split by `complete`, `failed`, and other status;
- `limit_omitted_count`;
- a stable selection fingerprint;
- a bounded sample of skipped IDs and reasons.

The existing channel-scan adapter and new manifest adapter must feed the same
downstream industrial/surgical queue. Do not duplicate the fetch worker,
retry, cache, cleanup, or NotebookLM routing logic.

### Report and logging

Add the manifest path, manifest fingerprint, selection fingerprint, and
selection counts to the existing `fetch_invoked`, `fetch_scan_completed`, and
`fetch_completed` payloads. In dry-run, print the counts and write no status or
transcript data. In live mode, preserve the existing worker result files and
add the selection receipt beside the run output only if the caller requests a
report path.

### Slice 3 acceptance tests

1. A manifest with three pending IDs selects exactly those IDs in order.
2. An ID from another channel is still selected when explicitly listed; no
   channel scan occurs.
3. Complete, failed, and missing IDs are reported and not requeued.
4. Duplicate and malformed IDs fail before worker startup.
5. `--limit 2` selects the first two eligible manifest IDs and reports the
   remainder as limit-omitted.
6. `--source` plus `--video-manifest` is rejected with a clear parser error.
7. Dry-run performs no transcript, NotebookLM, API, or status writes.
8. Industrial and surgical downstream routes receive the same selected IDs as
   the existing channel path and preserve worker/source metadata.
9. A changed manifest or database state is detected before a live run when a
   selection receipt/plan is used.
10. Existing `fetch --source`, unbounded-fetch guard, cache-hit, negative-cache,
    and terminal-item behavior remain unchanged.

## Implementation Order

### Commit A: test and provenance foundation

Files:

- `tests/test_batch_status.py`
- `csf/playlist_imports.py`
- `scripts/import_video_ids.py`
- `tests/test_playlist_imports.py`
- `tests/test_import_video_ids.py`
- `docs/proposal_for_review.md`

Verification:

```text
python -m pytest tests/test_batch_status.py tests/test_import_video_ids.py tests/test_playlist_imports.py -q
python -m py_compile csf/playlist_imports.py csf/batch_status.py scripts/import_video_ids.py
git diff --check
```

Do not authorize API spend to make tests green. The three identity tests must
use fixtures/mocks.

### Operational dry-run validation

The real CLI path was exercised locally on 2026-08-05 using a three-item
manifest generated from the active read-only status database:

```text
P:/tmp/yt-is-manifest-validation-20260805.json
```

Command:

```text
python bin/csf-source fetch --video-manifest P:/tmp/yt-is-manifest-validation-20260805.json --dry-run --limit 2 --workers 1
```

The run selected two pending IDs in manifest order, reported one
`limit_omitted` ID, emitted manifest and selection fingerprints, reported zero
channel-scan counts, and ended with `fetch_completed.status="dry_run"`.
The raw receipt is preserved at:

```text
P:/packages/yt-is/.logs/term_a683bd1c.jsonl
```

The three selected database rows remained `pending` afterward. No external
network call, transcript fetch, NotebookLM action, or status mutation was
performed.

### Commit B: video-selection manifest

Files:

- `csf/video_selection_manifest.py`
- `bin/csf-source`
- `tests/test_video_selection_manifest.py`
- `tests/test_csf_source_fetch_timing.py` or a dedicated fetch-selection test
- `docs/operations/import-workflow-next-design.md`

Verification must include parser, pure selection, dry-run, and mocked
downstream route tests before any live fetch is considered.

### Optional Commit C: recovery and observability

Only after A and B:

- add a `list/reconcile` command for non-terminal provenance runs;
- add a durable selection receipt if operators need resumable manifest runs;
- add a provenance-to-status audit report.

These are not prerequisites for the first safe implementation.

## Non-goals And Stop Conditions

- Do not create a new generic `manifest` module or table.
- Do not change NotebookLM behavior, throughput settings, auth, or worker
  topology.
- Do not requeue complete/failed videos implicitly from a manifest.
- Do not use external metadata or API spend for unit tests.
- Do not launch a live fetch while implementing or validating the selection
  adapter; mocked routing is sufficient for this design gate.
- Stop if an existing caller depends on the legacy `playlist_imports` commit
  behavior, if provenance/status ordering cannot be made observable, or if
  manifest selection would require bypassing the existing scope guard.

## Claim Ledger

| Claim | Type | Evidence | Verification | Confidence | Falsifier | Action allowed |
|---|---|---|---|---|---|---|
| An append-only playlist import log already exists | verified_fact | `csf/playlist_imports.py` schema and writers | existing playlist-import tests | high | schema absent on a fresh DB | reuse it |
| Safe ID import is connected to that log | verified_fact | `scripts/import_video_ids.py` calls `record_video_import_run` on execute; integration test reads completed run | execute/provenance test | high | status mutation can complete without a run/item | needs_fix |
| Exact-video fetch selection is implemented | verified_fact | parser, selection adapter, route test, and operational dry-run receipt | CLI/source search plus dry-run | high | non-manifest IDs enter the selected queue | needs_fix |
| A dedicated selection manifest avoids unrelated channel enumeration | verified_fact | manifest branch reads `analysis_status` by ID; dry-run receipt reports zero channels | operational receipt and route test | high | channel enumeration occurs in manifest mode | needs_fix |
| Existing provenance tables are sufficient for first integration | inference | fields plus `notes_json`/`raw_json` cover current audit data | schema/readers and failure tests | medium | required query needs normalized new columns | add migration deliberately |
| Bulk provenance transaction is preferable to per-item commits | inference | current item writer commits per row | failure/throughput test | high | caller requires each item durable immediately | retain per-item mode explicitly |

## Adversarial Design Review

The design was challenged against the main failure modes:

- **Duplicate owner:** resolved the discovery false positive and explicitly
  kept benchmark/browser manifests separate.
- **Stale documentation:** corrected the assumption that playlist provenance
  is absent; the new design treats it as partial existing infrastructure.
- **Cross-database atomicity:** explicitly does not claim atomicity; failed or
  running provenance runs are retained and observable.
- **Lifecycle injection:** provenance metadata and status lifecycle fields are
  separate; external import data cannot set pipeline-stage fields.
- **Selection bypass:** the manifest path reuses the existing queue and scope
  guard; it does not create a second fetch worker path.
- **Identity and retry ambiguity:** manifest IDs are ordered and unique;
  complete/failed rows are reported rather than silently requeued.
- **Tainted/external data:** all design verification is local and mocked; no
  API or NotebookLM run is authorized.

The exact `cmd_fetch` scan loop was preserved for channel mode; manifest mode
selects directly before entering the existing routing and counter paths. The
selection adapter and downstream-route tests verify that separation.

## Parent Handoff

`Parent handoff: ready_for_parent_review`

Commit A and Commit B are implemented and locally verified. Do not reopen the
provenance schema question or add another generic manifest owner. A future
operational goal may review a concrete manifest and authorize a bounded live
run, but this implementation did not perform one.
