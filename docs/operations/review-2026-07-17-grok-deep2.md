---
title: yt-is Grok deep package review (run 20260717-020558)
source_run: P:/tmp/grok-review/yt-is/20260717-020558
head: f1ffca9
verify: independent
prior:
  - docs/operations/critical-review-2026-07-17.md
  - docs/operations/review-2026-07-17-grok.md
---

# yt-is package review (Grok /review)

**bugs: 16, risks: 4, suggestions: 0, nits: 0 (capped)**

| Field | Value |
|---|---|
| Target | package:P:/packages/yt-is |
| HEAD | f1ffca9 |
| Depth | deep |
| Lenses | integrity, concurrency, correctness |
| Specialists | 3 explore (integrity, concurrency, correctness) |
| Verify | independent critic 019f6f20-5487-7ca1-b88a-5fb597328f9c |
| Run dir | `P:/tmp/grok-review/yt-is/20260717-020558` |
| Policy | P:/.grok/REVIEW.md + package AGENTS/HANDOFF |
| Prior | critical-review-2026-07-17.md, review-2026-07-17-grok.md |
| Finished | 2026-07-17T08:11:41.181762+00:00 |

## Summary

Multiple verified data-integrity and multi-worker bugs remain on main (HEAD f1ffca9): channel metadata wipe, promote REPLACE, positional source mapping, shared-retry drain permanent-fail of deferred work, synthetic NLM cache keys, and auth contract no-ops. Do not treat industrial cache or channel metadata as trustworthy until P0 fixes land.

**Verdict:** `critical` — 16 verified bugs remain on main (all pre-existing). Industrial NLM cache identity, channel metadata, and multi-worker shared-retry are not trustworthy until P0 fixes land.

**Prior merge:** 12 new / 9 confirmed / 0 closed / 8 residual

## Bugs (blocking)

### COR-001 — validate_auth() is a permanent no-op success — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/youtube_auth.py:44`
- **Detail:** Public API claims to validate YouTube auth availability but always returns True with no probe of cookies, yt-dlp, browser, or credentials. Callers that gate on validate_auth() will proceed as authenticated when they are not.
- **Evidence:** def validate_auth() -> bool: ... return True  (youtube_auth.py:44-50). No other references call it from production paths; the function still exports a false contract.
- **Fix:** Implement a real probe (e.g. yt-dlp cookie/auth check against a known public URL, or verify cookie file presence + non-empty SID) and return False with a logged reason on failure. Delete or rename the function if intentionally unused so the contract cannot be trusted.
- **Confidence:** 1.00
- **Verify note:** validate_auth return True only.

### INT-001 — set_channel_metadata packs None kwargs and wipes existing channel fields — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/batch_status.py:1011`
- **Detail:** Docstring claims partial-update preservation via upsert_channel, but set_channel_metadata always builds a kwargs dict including every optional field at default None (playlist_id, video_count_estimate, subscriber_count, keywords, custom_url, etc.). upsert_channel treats key presence as 'provided', so every partial set_channel_metadata call overwrites unspecified columns with NULL. Call sites such as playlist_imports.replay_playlist_import_run_into_batch_status and bin/csf-source history import invoke this path and can silently destroy richer live metadata.
- **Evidence:** batch_status.py:1008-1032 packs all fields including keywords/custom_url defaults; upsert only preserves keys not present in kwargs (1035-1038, 1153-1154). playlist_imports.py:340-349 calls set_channel_metadata with playlist_id=None, video_count_estimate=None, description=None.
- **Fix:** Only put kwargs keys that the caller explicitly set (sentinel/omit pattern). Mirror next_page_token/quota_exhausted_at gating for all optional fields. Add a regression test: write full metadata, partial set_channel_metadata(last_checked=...), assert subscriber_count/keywords/playlist_id unchanged.
- **Confidence:** 0.99
- **Verify note:** set_channel_metadata always packs optional fields at default None into kwargs (batch_status.py:1011-1027) and only gates next_page_token/quota_exhausted_at; upsert updates every key present in kwargs (1153-1154). playlist_imports.py:340-349 passes playlist_id=None, video_count_estimate=None, description=None.

### INT-005 — Importer stores transcripts under MD5 synthetic video_ids in shared cache — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/csf_nlm_import.py:171`
- **Detail:** source_id_to_video_id hashes NotebookLM source IDs to 11-char hex strings that pass cache validation but are not YouTube IDs. import_notebook_transcripts then set_cached_transcript under that key and has_cached_transcript skips on subsequent runs. Real pipeline keys are YouTube video_ids, so imported text is unjoinable to inventory, can collide with rare hex-only real IDs, and pollutes the shared transcripts.sqlite identity space. Metadata source_video_id is also the synthetic value, not a real video id.
- **Evidence:** hashlib.md5 → hex[:11] uppercased (csf_nlm_import.py:171-195); video_id = source_id_to_video_id(source_id) then set_cached_transcript(video_id, 'en', 'notebooklm', ...) (223-263). cache._validate_video_id accepts any [a-zA-Z0-9_-]{11}.
- **Fix:** Resolve real YouTube video_id from source title/url (same extractors as nlm_batch._extract_video_id_from_source_entry). Refuse to cache when no real video_id is found. Do not write synthetic keys into the shared cache.
- **Confidence:** 0.99
- **Verify note:** source_id_to_video_id MD5 hex[:11]; set_cached_transcript under synthetic key (csf_nlm_import.py:171-263).

### INT-002 — upsert_channel SELECT omits keywords/custom_url; UPDATE always NULLs them — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/batch_status.py:1049`
- **Detail:** Even pure upsert_channel partial updates (used heavily by bin/csf-source) load existing rows without keywords or custom_url, then UPDATE writes existing.get('keywords')/existing.get('custom_url') which are always None unless the same call re-supplies them. Any upsert that updates last_checked or video_count_estimate after a keywords/custom_url backfill permanently erases those columns.
- **Evidence:** SELECT list at 1049-1054 has no keywords/custom_url; existing dict 1115-1133 omits them; UPDATE binds existing.get('keywords') and existing.get('custom_url') at 1182-1183. backfill_channel_metadata.py writes keywords/custom_url via upsert; later upsert_channel(url, last_checked=now) paths in bin/csf-source:954,1368-1370 wipe them.
- **Fix:** Include keywords and custom_url in the SELECT and existing map. Prefer COALESCE(excluded, existing) SQL for non-null merge. Test: upsert keywords, then upsert only last_checked, assert keywords remain.
- **Confidence:** 0.98
- **Verify note:** SELECT at 1049-1053 omits keywords/custom_url; existing map 1115-1133 omits them; UPDATE binds existing.get('keywords') and existing.get('custom_url') at 1182-1183.

### INT-004 — Sticky complete freezes status but still mutates failure_reason/last_stage — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/batch_status.py:824`
- **Detail:** UPSERT keeps status='complete' on conflict, so is_complete() continues to skip the video forever, but last_stage and failure_reason always take the incoming values. mark_failed after mark_complete yields status=complete with a failure_reason set—contradictory durable state. set_status_batch has the same pattern. Concurrent or retry writers can also stamp failure diagnostics onto completed work while identity fields (source) stay first-write-wins via COALESCE.
- **Evidence:** status = CASE WHEN analysis_status.status = 'complete' THEN 'complete' ELSE excluded.status END; last_stage = excluded.last_stage; failure_reason = excluded.failure_reason (batch_status.py:824-829). Mirrored in set_status_batch at 1509-1526. mark_failed → set_status(_STATUS_FAILED, failure_reason=...) at 1708-1715.
- **Fix:** When existing status is complete: no-op the update (or only bump updated_at under an explicit force flag). Never write failure_reason onto complete rows. Apply the same CASE guard to last_stage/failure_reason as to status.
- **Confidence:** 0.97
- **Verify note:** status CASE sticky-complete (824) but last_stage/failure_reason always excluded.* (828-829).

### CON-001 — Shared-retry drain permanently fails deferred items (including re-enqueued ones) — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/dev/worker_pool/worker_main.py:462`
- **Detail:** After claim_ready + process_industrial_batch_reusable, the drain loop treats every non-success result as terminal: mark_shared_retry_permanent_failure. Metrics correctly subtract shared_retry_deferred_count from final_failed, but the pool mutation does not: deferred videos are still permanent-failed. process_industrial_batch_reusable (with shared pool enabled) re-calls enqueue_shared_retry for still-not-ready items (nlm_batch.py:4421-4427), setting status='pending'; the next statements then overwrite that row to permanent_failure. reschedule() exists in shared_retry_pool.py but has zero production callers. Net effect under multi-worker shared-retry: retryable content is killed after one drain attempt instead of remaining claimable.
- **Evidence:** worker_main.py:455-468 computes deferred for metrics then: for video_id, (success, transcript, _error) in shared_results.items(): if success and transcript: mark_shared_retry_complete else: mark_shared_retry_permanent_failure. nlm_batch.py:4408-4427 re-enqueues deferred when shared pool enabled. shared_retry_pool.reschedule is only used in tests/test_shared_retry_pool.py.
- **Fix:** On drain completion: success → mark_complete; still-deferrable → leave pending / call reschedule(retry_count+1, delay_s=...) and do not permanent-fail; only true terminal outcomes → mark_permanent_failure. Gate permanent_failure with WHERE status='claimed' AND claimed_by=?. Never permanent-fail rows whose process metrics counted them as shared_retry_deferred.
- **Confidence:** 0.96
- **Verify note:** drain permanent-fails every non-success (worker_main.py:462-468) despite deferred metrics.

### CON-002 — Shared-retry deferred videos filled as false 'Source not found' failures — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:4798`
- **Detail:** When queued_for_retry is true, _run_fetch_round appends to round_retry_queue and continues without writing round_results (4356-4364). Shared-pool path enqueues those IDs (4421-4427) but never puts a success/fail tuple into results. The extract epilogue then does: for vid in batch_ids: if vid not in results: results[vid] = (False, None, 'Source not found'). That falsifies the serial→shared handoff: deferred/retryable work is returned to worker_main as hard failures. Primary batch path counts them as total_failed (worker_main.py:811-813). Drain path (CON-001) then permanent-fails them with error 'Source not found'. Serial retry-queue path avoids this by writing real results after the in-process drain window.
- **Evidence:** nlm_batch.py:4356-4364 skip results for queued_for_retry; 4408-4427 enqueue-only when shared pool on; 4798-4800 unconditional 'Source not found' fill-in. Contrast serial branch 4441+ which re-runs fetch and updates results[vid].
- **Fix:** After shared-pool enqueue, set results[vid] = (False, None, None) with a distinct deferred marker (or omit from failure fill-in and track deferred_ids). Change fill-in to only default unmapped missing source IDs, not retry-queued IDs. Propagate deferred set to worker so primary loop does not count them as batch_failed and drain does not permanent-fail them.
- **Confidence:** 0.96
- **Verify note:** shared enqueue only; fill-in Source not found at 4798-4800.

### INT-003 — promote_batch_status_db INSERT OR REPLACE overwrites live rows with sparse staging — **verified** [pre-existing]

- **Severity / priority:** bug / P0
- **Location:** `P:/packages/yt-is/csf/batch_status.py:2014`
- **Detail:** Channel-state promote is not field-wise merge: every staging row fully replaces the destination row by primary key. Sparse staging (nulls for subscriber_count, description, keywords, etc.) clobbers richer live data. channel_metadata PK is channel_url with UNIQUE(channel_id); a staging row with a different URL form for the same channel_id triggers SQLite REPLACE against the unique index and deletes the live URL row, so promote can both wipe fields and change canonical identity. Transcript promote is append-only INSERT OR IGNORE; channel promote is destructive without saying so.
- **Evidence:** INSERT OR REPLACE INTO {table} (batch_status.py:2014-2017). channel_metadata PK channel_url (335-336) plus UNIQUE idx on channel_id (413-415). promote CLI documents merge but implementation replaces whole rows. Test test_promote_batch_status_db_merges_channel_state only checks presence of distinct channels, not field preservation on PK collision.
- **Fix:** Promote with per-field COALESCE/non-null merge (or upsert_channel semantics). Match rows by channel_id first, then channel_url. Refuse promote when staging null would erase a non-null live column unless --force-overwrite. Add a test that staging sparse row cannot null out live subscriber_count.
- **Confidence:** 0.96
- **Verify note:** _copy_table_rows uses INSERT OR REPLACE whole-row (2014-2017). channel_metadata PK channel_url with UNIQUE channel_id.

### INT-006 — Source↔video mapping replaces exact matches with positional zip — **verified** [pre-existing]

- **Severity / priority:** bug / P1
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:3042`
- **Detail:** extract_transcripts first builds source_id_by_video_id from title/url identity, then if _last_added_source_ids length equals batch size it replaces the entire map with dict(zip(batch_ids, canonical_source_ids)). That assumes add-stdout Source ID order equals URL order. Any reordering silently attributes source content to the wrong video_id and downstream cache/status writes lock that wrong pairing. A second positional zip remains for unmapped IDs when list counts align—the failure mode HANDOFF.md already called out for source list order.
- **Evidence:** Comment at 2989-2991: list order not stable; exact map at 3024-3028; unconditional replace at 3038-3046; fallback zip at 3061-3063. Tests encode order fallback as desired (test_nlm_batch.py:5801-5843) without cross-check against title matches.
- **Fix:** Never discard exact title/url matches. Use canonical IDs only to fill missing entries, cross-checking each source_id against the source list entry for that video. Fail closed on residual ambiguity; do not positional-zip.
- **Confidence:** 0.95
- **Verify note:** dict(zip(batch_ids, canonical_source_ids)) replaces exact map at 3038-3046.

### CON-003 — enqueue ON CONFLICT unconditionally resets claims and terminal states — **verified** [pre-existing]

- **Severity / priority:** bug / P1
- **Location:** `P:/packages/yt-is/csf/shared_retry_pool.py:139`
- **Detail:** INSERT ... ON CONFLICT(video_id) DO UPDATE always sets status=excluded.status, claimed_by=NULL, claimed_at=NULL with no predicate on the existing row. Concurrent effects: (1) if worker A holds status='claimed' and worker B enqueues the same video_id, the claim is stolen mid-flight and another worker can claim_ready the same item (duplicate NotebookLM work); (2) if status is 'completed' or 'permanent_failure', a late enqueue resurrects the row to pending; (3) combined with CON-001, drain re-enqueue then permanent_failure is a forced last-writer race on the same key. claim_ready uses CAS on status, but enqueue bypasses that contract.
- **Evidence:** shared_retry_pool.py:139-146 ON CONFLICT updates status/claimed_by/claimed_at without AND status NOT IN ('claimed','completed','permanent_failure') or claimant checks. claim_ready CAS is at 195-216 only for claim transitions.
- **Fix:** ON CONFLICT DO UPDATE only when existing status IN ('pending') OR (status='claimed' AND claimed_at stale), or use separate insert-if-absent vs reschedule APIs. Never clear a live non-stale claim. Never downgrade completed without an explicit reset API.
- **Confidence:** 0.93
- **Verify note:** enqueue ON CONFLICT clears claimed_by/claimed_at (139-146).

### CON-004 — mark_complete / mark_permanent_failure ignore claimant and status — **verified** [pre-existing]

- **Severity / priority:** bug / P1
- **Location:** `P:/packages/yt-is/csf/shared_retry_pool.py:283`
- **Detail:** Both terminalizers UPDATE ... WHERE video_id=? only. Any process can complete or permanent-fail a row another worker just re-enqueued or reclaimed after stale timeout. Multi-worker race: Worker A processes claim; item re-deferred via enqueue (pending); Worker B claims; Worker A still runs the drain else-branch and permanent-fails B's in-flight claim. Same hole for mark_complete after a second claim. claim_ready is careful; terminal updates are not.
- **Evidence:** shared_retry_pool.py:283-292 and 307-317: WHERE video_id=? with no claimed_by/status predicate. worker_main.py:462-468 calls these without passing claimant into the SQL guard.
- **Fix:** Require claimant_id (or expected status='claimed') on mark_complete/mark_permanent_failure: WHERE video_id=? AND status='claimed' AND (claimed_by=? OR ?=''). Return false on mismatch so callers can log lost races instead of silently clobbering.
- **Confidence:** 0.92
- **Verify note:** mark_complete/mark_permanent_failure WHERE video_id=? only.

### COR-006 — Auth check cache fail-opens for full TTL without session revalidation — **verified** [pre-existing]

- **Severity / priority:** bug / P1
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:1347`
- **Detail:** _ensure_nlm_auth returns True on in-process cache hit without re-running login --check. Cross-process session death, cookie wipe, or account switch remains invisible until TTL expires (default 30s via auth_check_cache_ttl_seconds). Combined with empty expected_email, the cache key is (profile, '') and never binds identity.
- **Evidence:** cache_hit branch returns True (nlm_batch.py:1347-1363). auth_check_cache_key uses (profile, expected_email) (nlm_auth_guard.py:579-580). TTL default 30.0 (nlm_auth_guard.py:568-576).
- **Fix:** On cache hit, still fail closed on auth-bearing command errors (already partial). Optionally store a session fingerprint and invalidate on wrong_account/auth_error. When expected_email is required for the profile family, refuse to cache or accept success without a verified account string.
- **Confidence:** 0.92
- **Verify note:** auth cache hit returns True without re-check (1347-1363).

### COR-007 — Empty expected_email treats any successful login --check as authorized — **verified** [pre-existing]

- **Severity / priority:** bug / P1
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:1518`
- **Detail:** check_matches_expected = returncode==0 and (not expected_email or check_account == expected_email). With expected_email empty, any account (or no parsed Account: line) is accepted and cached. _session_matches_expected_account returns True when expected is empty. Worker family mapping can be skipped when expected_email is falsy (family = ... if expected_email else None), so CDP family refresh is bypassed.
- **Evidence:** nlm_batch.py:1518-1519, 1631; _session_matches_expected_account at 311-314; family selection gated on expected_email at 1385-1386.
- **Fix:** For known worker profiles, require expected_email_for_profile(profile) and fail closed if unset in noninteractive mode. Treat missing Account: output as check failure when an expected email is configured.
- **Confidence:** 0.92
- **Verify note:** empty expected_email accepts any account (1518).

### COR-009 — Family auth refresh disables live session verification — **verified** [pre-existing]

- **Severity / priority:** bug / P2
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:401`
- **Detail:** After refresh_source_profile, sync_worker_profiles is called with source_session_checker=lambda _profile: True. _source_session_ok short-circuits to True and skips profile_session_matches_expected. Credentials are then copied to siblings and the auth cache is stored as established without a live --check of the worker profile being used.
- **Evidence:** source_session_checker=lambda _profile: True (nlm_batch.py:398-401). _source_session_ok uses checker when provided (nlm_worker_auth.py:722-728).
- **Fix:** Pass None (default live checker) or a real checker that runs login --check against family.expected_email for the source profile after refresh and before sibling copy.
- **Confidence:** 0.93
- **Verify note:** source_session_checker=lambda True skips live check.

### COR-008 — refresh_reason UnboundLocalError when check succeeds without Account line — **verified** [pre-existing]

- **Severity / priority:** bug / P2
- **Location:** `P:/packages/yt-is/csf/nlm_batch.py:1709`
- **Detail:** If login --check returns 0, expected_email is set, and _extract_account returns '', check_matches_expected is False, the wrong_account branch requires truthy check_account so it is skipped, force_scheduled branch is false, and returncode!=0 branch is false. Control falls through to nlm_login_started using refresh_reason, which was never assigned → UnboundLocalError aborts auth recovery. Same hole exists inside the InterProcessLock re-check path (no elif returncode!=0 after the locked re-check; outer binding may be absent).
- **Evidence:** Branches that set refresh_reason: 1541-1549, 1565-1573, 1587-1595 (and locked 1654-1686). Usage at 1709 without default initialization. Empty account: check_account == expected_email is False; wrong_account needs check_account truthy.
- **Fix:** Initialize refresh_reason = _describe_nlm_auth_refresh_reason(...) before the branch ladder, and treat returncode==0 with missing account as check_failed/wrong_account and fail closed when expected_email is set.
- **Confidence:** 0.90
- **Verify note:** refresh_reason UnboundLocalError path when account empty.

### CON-005 — Drain exit uses pending_count only; in-flight claimed work abandoned within budget — **verified** [pre-existing]

- **Severity / priority:** bug / P2
- **Location:** `P:/packages/yt-is/dev/worker_pool/worker_main.py:431`
- **Detail:** When claim_ready returns empty, drain breaks if shared_retry_pending_count() <= 0. pending_count() only counts status='pending' (shared_retry_pool.py:330-331), not status='claimed'. If peer workers hold claims, this worker exits (OK). If a peer dies after claim, items stay claimed until stale_claim_s=900s default, while drain_budget_s defaults to source_content_retry_queue_budget_s=30s (nlm_config.py:52). No worker in the same run will wait long enough to reclaim; recovery depends on a later process invoking claim_ready after 15 minutes. Combined with CON-001, the common path is permanent-fail rather than long-lived claim, but crash-between-claim-and-finish still loses work for 900s with no same-run reclaim.
- **Evidence:** worker_main.py:431-447; shared_retry_pool.py:325-341 pending only; claim_ready stale_claim_s default 900.0 at line 168; nlm_config source_content_retry_queue_budget_s default 30.0.
- **Fix:** pending_count should include reclaimable claimed rows (claimed_at <= now-stale) or drain should call claim_ready which already selects stale claims. Align drain_budget with stale reclaim or run a final claim_ready pass with explicit stale reclamation logging. On worker crash, SessionEnd/parent should reschedule claimed_by=that worker.
- **Confidence:** 0.85
- **Verify note:** pending_count only pending status; stale 900s vs budget 30s.

## Risks

### INT-008 — block_channel hard-deletes all analysis_status rows for the channel — **verified** [pre-existing]

- **Severity / priority:** risk / P2
- **Location:** `P:/packages/yt-is/csf/batch_status.py:1282`
- **Detail:** Blocking is not blocklist-only: it DELETE FROM analysis_status WHERE source = ? OR channel_id = ?, discarding complete/failed history for every video attributed to that channel. playlist import replay calls block_channel for blocked* classifications, so a bulk reclassify can wipe durable status without a soft-block option. Recovery requires backup restore.
- **Evidence:** DELETE FROM analysis_status WHERE source = ? OR channel_id = ? (batch_status.py:1282-1285). playlist_imports.py:351-352 calls block_channel on blocked* rows.
- **Fix:** Default block_channel to insert-only blocklist (and optional metadata flag). Keep destructive purge behind an explicit delete_channel/purge API with backup prompt.
- **Confidence:** 0.98
- **Verify note:** block_channel DELETE analysis_status (1282-1285); playlist_imports blocked* calls it.

### INT-007 — Transcript cache first-write-wins permanently; any-source has_cached skips re-fetch — **verified** [pre-existing]

- **Severity / priority:** risk / P2
- **Location:** `P:/packages/yt-is/csf/cache.py:113`
- **Detail:** INSERT OR IGNORE freezes the first (video_id, lang, source) payload forever—no upgrade path for longer/higher-quality text after a bad write. has_cached_transcript only checks video_id existence across sources, so orchestrator/batch/selenium treat any source hit as terminal skip. Wrong mapping (INT-006) or a weak first source therefore permanently blocks re-ingestion for that video.
- **Evidence:** INSERT OR IGNORE (cache.py:113-116); promote also INSERT OR IGNORE (385); has_cached_transcript SELECT 1 WHERE video_id=? (511-514). Callers: batch.py:135, orchestrator.py:234, csf_selenium.py:142.
- **Fix:** Allow quality-aware UPSERT (e.g. longer usable text, preferred source rank) or explicit overwrite API after mapping fixes. has_cached_transcript should optionally require preferred source/lang or minimum content length.
- **Confidence:** 0.92
- **Verify note:** INSERT OR IGNORE (cache.py:113-116); has_cached any-source (511-514).

### CON-006 — Per-write WAL TRUNCATE checkpoint under multi-process transcript writers — **verified** [pre-existing]

- **Severity / priority:** risk / P2
- **Location:** `P:/packages/yt-is/csf/cache.py:130`
- **Detail:** _db_access_lock is process-local only. Multi-worker set_cached_transcript each opens a connection and runs PRAGMA wal_checkpoint(TRUNCATE) after every INSERT. Under parallel workers this serializes writers at the SQLite checkpoint layer, amplifies SQLITE_BUSY, and can stall readers. timeout=30s on connect mitigates hang-forever but not throughput collapse. retry_queue.py:161 has the same TRUNCATE-per-write pattern (multi writer-thread processes if used).
- **Evidence:** cache.py:30 _db_access_lock; 106-131 write + wal_checkpoint(TRUNCATE) per entry; docstring lines 3-4 claim multi-terminal shared DB. worker_main.py:464/754/807 call set_cached_transcript from each worker process.
- **Fix:** Checkpoint periodically (N writes or time-based PASSIVE/RESTART), not TRUNCATE after every row. Keep WAL for multi-process; avoid TRUNCATE under concurrent writers.
- **Confidence:** 0.82
- **Verify note:** process-local RLock + wal_checkpoint(TRUNCATE) per write.

### CON-008 — retry_queue has no claim/lease; multi-process writers + get_pending race — **verified** [pre-existing]

- **Severity / priority:** risk / P3
- **Location:** `P:/packages/yt-is/csf/retry_queue.py:145`
- **Detail:** Architecture claims multi-terminal isolation via per-terminal writer threads, but all threads write the same shared DB path (get_shared_db_path). get_pending is a plain SELECT with no claim transition; two consumers can dequeue the same ready rows. mark_permanent_failure uses INSERT OR REPLACE which resets created_at and races with concurrent enqueue_retry (last writer wins). Currently enqueue_retry/get_pending_retries appear test-only (production transcript_phase2 only reads get_retry_entry), so this is latent for the NLM multi-worker path but broken if the queue is wired into multi-worker recovery.
- **Evidence:** retry_queue.py:145-161 INSERT OR REPLACE + TRUNCATE; 215-231 get_pending without UPDATE claim; grep shows enqueue_retry only in tests/test_retry_queue.py outside definition.
- **Fix:** If multi-worker use is intended: claim rows with BEGIN IMMEDIATE + status='claimed' like shared_retry_pool. If single-consumer only: document and assert single writer process. Stop INSERT OR REPLACE for permanent_failure (UPDATE status only).
- **Confidence:** 0.78
- **Verify note:** retry_queue get_pending no claim; latent multi-consumer race.

## Dedupe collapsed (same defect, other specialist IDs)

- `CON-007` → `INT-004` (verified)
- `COR-002` → `INT-008` (verified)
- `COR-003` → `INT-006` (verified)
- `COR-004` → `INT-004` (verified)
- `COR-005` → `CON-003` (verified)
- `COR-010` → `INT-007` (verified)

## Prior ledger merge

### Confirmed still open on main

- #1 positional zip → **INT-006**
- #2 cache first-write-wins → **INT-007**
- #3 promote REPLACE → **INT-003**
- #5 enqueue clears claims → **CON-003**
- #6 retry_queue no lease → **CON-008**
- #7 parallel failure handoff → **CON-001+CON-002**
- #8 validate_auth no-op → **COR-001**
- #11 auth-check cache → **COR-006**
- #18 block_channel DELETE → **INT-008**

### Closed this run

- None fully closed on main. A1 sticky-complete status non-downgrade remains by design; INT-004 shows residual field-mutation hole.

### New this run (not in prior primary IDs / expanded)

- INT-001 set_channel_metadata None-wipe
- INT-002 upsert omits keywords/custom_url SELECT
- INT-004 sticky complete + failure_reason mutation
- INT-005 MD5 synthetic video_ids in shared cache
- CON-001 drain permanent-fails deferred shared-retry
- CON-002 deferred filled as Source not found
- CON-004 terminalizers ignore claimant
- CON-005 drain pending_count ignores claimed
- CON-006 WAL TRUNCATE per multi-process write
- COR-007 empty expected_email accepts any account
- COR-008 refresh_reason UnboundLocalError path
- COR-009 family refresh disables live session check

### Residual (prior, not re-hunted as primary this pass)

- prior #4 provider_score dual identity (not re-hunted this run)
- prior #9 auth-on-every _run_cmd (architecture residual)
- prior #10 cookie/profile sync races + hardcoded emails
- prior #12 library promote weaker than CLI
- prior #13 staging column SQL interpolation
- prior #14-17 god modules / multi control planes / SQLite planes / doc dual-authority
- prior #19 tautological reconciliation
- prior #20 marketplace skills lag

## Claim ledger

| Claim | Status | Evidence |
|---|---|---|
| Channel metadata partial updates wipe fields | verified | INT-001/002 + parent read batch_status.py:1011-1183 |
| Promote REPLACE clobbers live channel rows | verified | INT-003 |
| Sticky complete mutates failure_reason | verified | INT-004 |
| NLM importer uses MD5 synthetic video_ids | verified | INT-005 csf_nlm_import.py:171-263 |
| Positional source_id zip still on main | verified | INT-006 nlm_batch.py:3038-3046 |
| Shared-retry drain permanent-fails deferred | verified | CON-001 worker_main.py:462-468 |
| Deferred filled as Source not found | verified | CON-002 nlm_batch.py:4798-4800 |
| Enqueue clears in-flight claims | verified | CON-003 |
| validate_auth always True | verified | COR-001 |
| block_channel deletes analysis history | verified | INT-008 |
| A2 fail-closed mapping on main | still open | prior + INT-006 |

## Recommended next actions

**Implementation backlog is the root-cause program (not this ID list alone):**  
`docs/operations/root-cause-program.md`

Ship **contracts**; use finding IDs as **acceptance cases**. Close findings under C# when falsifiers pass.

| Phase | Contract | Acceptance IDs (primary) |
|-------|----------|---------------------------|
| 1 | **C1** Work outcomes + shared-retry lease | CON-001, CON-002, CON-003, CON-004, CON-005 |
| 1 | **C2** Identity + cache write gate (incl. A2) | INT-005, INT-006, INT-007 |
| 2 | **C3** Durable row-merge policy | INT-001, INT-002, INT-003, INT-004, INT-008 |
| 3 | **C4** Fail-closed auth | COR-001, COR-006, COR-007, COR-008, COR-009 |
| 4 | **C5** Control-plane collapse | after C1–C4; dual outcome paths |

### Patch-level reminders (from findings)

1. **C1:** CON-001 + CON-002 (no permanent-fail deferred; no `"Source not found"` fill-in) + CON-003 enqueue claim guard + CON-004 claimant-aware terminals.
2. **C2:** Merge A2 fail-closed mapping (INT-006); refuse MD5 synthetic cache keys (INT-005); quality-aware cache after bind (INT-007).
3. **C3:** INT-001/002 omit-None + SELECT keywords/custom_url; INT-003 promote field-merge; INT-004 freeze failure_reason on complete; INT-008 soft block.
4. **C4:** COR-001 implement or delete `validate_auth`; COR-006/007/008/009 fail-closed paths.

```text
/go implement yt-is trust floor Phase 1: C1 + C2 per docs/operations/root-cause-program.md
```

## Verdict

- overall_correctness: **package critical**
- overall_confidence: **0.92**
- package verdict: **critical**
