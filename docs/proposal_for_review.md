# yt-is Import Workflow — Proposed Fix (for critical review)

## Context
A YouTube transcript import workflow overwrote completed DB rows and lost provenance.
The root cause is a destructive upsert. Three critiques shaped this proposal.

## Verified defects (from code inspection)

1. `csf/batch_status.py:809` and `:1447` use `INSERT OR REPLACE` — replaces the WHOLE row.
   Only 5 fields (source, published_at, has_captions, last_stage, failure_reason) are
   null-guarded; title, description, channel_id, thumbnail, duration, privacy_status,
   upload_status, is_live_content, unavailable_reason are NOT preserved.
2. `scripts/import_video_ids.py:124-138` computes `new_entries` (the intended write set)
   but then writes `all_entries` — mismatch between displayed plan and actual mutation.
3. No "never downgrade complete" guard exists. `set_status(vid, "pending")` overwrites
   an existing `complete` row without check.
4. `scripts/fetch_custom_sources.py` calls `mark_failed(vid, source="notebooklm")` which
   overwrites the import-origin `source` field with the fetch backend name.
5. No test file exists for `batch_status.py`.
6. `concurrent.futures.Future.cancel()` cannot stop a running task; the `with` block of
   ThreadPoolExecutor waits for it. So the "timeout wrapper" in fetch_custom_sources.py
   marks items failed while the real NLM operation keeps running underneath.

## Proposed solution (corrected after critique)

### Tier 1 — Safe import API (highest priority, smallest)
- Add a NEW function `import_video_ids()` in batch_status.py (do NOT rename the existing
  `set_status_batch` — it has many callers and performs legitimate lifecycle transitions).
- Merge policy: never downgrade `complete`; never overwrite non-null existing with null
  incoming (for ALL 12 metadata fields, not just 5).
- Use a SINGLE bulk prefetch of all target IDs (one `SELECT ... WHERE id IN (...)`) before
  the loop, not N per-row reads.
- Return per-video decisions: `inserted | updated | skipped_complete | conflict`.
- Dry-run by default; `--execute` to actually write.

### Tier 2 — Immutable provenance manifest
- A separate append-only table (NOT reusing playlist_imports.py — it is playlist-specific
  and requires playlist_url/playlist_kind fields meaningless for history imports).
- Schema: import_run_id, video_id, origin_file, origin_row, imported_at, raw_title.
- Write provenance BEFORE touching analysis_status.

### Tier 3 — Manifest-scoped fetch
- `--manifest` flag on csf-source fetch (does NOT exist yet — must be implemented,
  internal functions accept list[str] but the CLI routing is unverified).
- Reads a JSON array of IDs, skips channel scan, feeds into the industrial batch queue.

## Questions for the reviewer
1. Is the single-bulk-prefetch approach correct for reading existing rows before a
   bounded merge? Any SQLite WAL / transaction concern at 5000-row scale?
2. Is the per-video decision return type (inserted|updated|skipped_complete|conflict)
   the right contract, or is there a cleaner shape?
3. For Tier 2 provenance: is a separate table the right call vs a source_json column
   on analysis_status? Trade-off is migration cost vs query simplicity.
4. Is "complete is terminal unless explicitly requeued" the right status policy, or
   should there be a requeue path that clears the transcript cache?
5. Any failure mode in the merge logic where a legitimate lifecycle transition
   (e.g. a video that was complete but its transcript was deleted) gets blocked
   by the "never downgrade" guard?
