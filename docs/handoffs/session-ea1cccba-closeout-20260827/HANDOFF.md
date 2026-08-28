---
thread_id: sess_ea1cccba-e4f1-439b-8610-0f1f670ca5f9-closeout-20260827
session: sess_ea1cccba-e4f1-439b-8610-0f1f670ca5f9
produced_at: 2026-08-27T19:30:00-06:00
status: open
handoff_type: session-closeout
agent: zcode
host: both
---

# Session closeout — yt-is pipeline rebuild + dispatcher migration (chain ea1cccba, 2026-08-25 → 08-27)

## Objective

Take yt-is from invisible-failure scheduled scripts to receipted,
queued, self-running pipelines: rebuild the visual intake on measured
signals, migrate scheduling to a dispatcher with job rows, repair
backup/search/ingest durability, and make every failure autopsiable.

## State per stream (verified, with receipts)

1. **Dispatcher migration — 90% done, gate pending.**
   - Landed: dispatcher.py (job rows, dependency gating, predicated
     claims, per-attempt receipts, recurring enrollment, single-instance
     lock); thin-tick form live (`YtisDispatcherTick`, 1-min `--once`,
     healthy flags); keepalive + resident loop DEPRECATED (keepalive
     task DISABLED; ps1 replaced by dispatcher_keepalive.py, pythonw).
   - Podcast worker budget 5400s (2400 failed first real cycle); feed
     rotation (oldest last_synced first); browser UA on feed fetch;
     last_synced writer into batch DB.
   - GATE PENDING: a dispatcher-managed podcast cycle must reach
     outcome=ok under the new budget; then disable YtisPodcastSync
     (`schtasks /change /tn YtisPodcastSync /disable`). A session-bg
     watcher was armed but DIES WITH THIS SESSION — check manually:
     `select id,outcome from pipeline_jobs where kind='podcast_sync'`
     in P:/.data/yt-is/dispatch.sqlite. Previous two cycles failed on
     PRE-fix code (2400s timeout; PyAV abort killed whole sync — now
     child-isolated). Windowless: tick task runs pythonw ✓.
2. **Visual intake — working at production scale.**
   - Legacy text gate measured dead (0/60, ρ=0.133) → replaced by MMX
     VLM intake (visual_vlm_score.py) + Path A prefilter (channel-prior
     CTE + keywords). Production: 250/250 scored, 129+84 dense queued,
     worker runs 202-for-202 then 45 more, zero failures.
   - Calibration vs Gemini frame-truth: 9 pairs, threshold-5 agreement
     100%, 0 FP/0 FN. Top up ~6 pairs/day via visual_frame_truth.py
     (--count 6 --seed-stratified); falsifier: agreement collapse.
   - Backlog decision PENDING (operator): Path A (recommended) vs full
     sweep vs newest-first for the 191K un-scored 10-day window.
3. **EF search freshness — fixed, verify tomorrow.**
   - Root cause of "new content invisible": freshness.py hardcoded
     source-exclusion (stale policy) — fixed spec-driven (36c68e92),
     deployed to primary, indexer restarted, 258 rows processed,
     watermark current, lag 0. VERIFY next morning: rounds report
     processed>0 for new content and watermark ≈ now.
4. **Backup cadence — repaired.** Restic trigger had PT15M interval
   inside PT10M duration (zero repetitions since creation); rebuilt
   P3650D; verified 6.41GB/139 chunks overnight + NextRun advancing.
5. **Windowless — complete for our tasks.** DispatcherTick +
   keepalive on pythonw; yt-dlp spawn CREATE_NO_WINDOW; restic wrapper
   was already clean. Remaining flashes: sibling powershell tasks
   (brief, WindowStyle Hidden) + their bakeoff bare-spawn ratchet
   violation (reported via ledger).
6. **Durability — findings ledger commit-promptly mitigation live**
   (restore-wipe hazard root-caused: git-tracked path + restore to
   HEAD; wiki: git-tracked-runtime-state-restore-wipe-hazard).
7. **Infra-lane handoffs landed** (sibling): hook-deployment race,
   alert lane-partitioning, exit-code family note →
   docs/handoffs/hook-race-and-findings-restore-20260826/HANDOFF.md.

## Pending / next actions (ranked)

1. Check podcast gate (query above); on ok disable YtisPodcastSync.
2. Run next vlm worker window for remaining ~84 dense videos.
3. Resume Path A sweeps daily (MMX quota resets ~overnight); operator
   decision still open on full-backlog scale (A/B/C recorded).
4. IndexIncremental: confirm today's long-run pattern resolves with the
   exclusion fix (watch one cycle; do not restart first).
5. ContentSync porting session (design:
   docs/handoffs/dispatcher-contentsync-port-20260827/DESIGN.md; answer
   Q1 concurrency on shared canonical storage first).
6. Findings-ledger fleet triage (347+ open rows; CL-001).
7. Sibling follow-ups: hook-race ordering fix; bakeoff file
   console-spawn ratchet violation (their file, flagged in ledger).

## Environment facts (cold-start)

- Dispatcher: `python P:/packages/yt-is/scripts/dispatcher.py --once`
  drains; tick task YtisDispatcherTick fires it every 1 min
  (pythonw, StartWhenAvailable, no battery gates, IgnoreNew, PT3H).
  Keepalive task DISABLED (superseded by tick). Heartbeat:
  P:/packages/yt-is/.logs/dispatch/heartbeat.json.
- Queue: P:/.data/yt-is/dispatch.sqlite `pipeline_jobs`.
- Windowless standard: pythonw + CREATE_NO_WINDOW on every spawn;
  ratchet test test_no_visible_console_spawns.py enforces (sibling's
  scripts/bakeoff_temporal_emergence.py currently violates — theirs).
- Known-native-crash: PyAV aborts on some episode audio — transcribe
  runs in a child by design; do not "simplify" back to in-process.
- Gemini: frame-quota large, video-URL ~3/day free; MiniMax: daily
  token plan. Paid tiers declined by operator (2026-08-27).

## Acceptance criteria for the open gate

podcast_sync pipeline_jobs row reaches outcome=ok (not just rc0):
episode(s) stored in transcript_cache source='podcast'; then
YtisPodcastSync disabled; two further automatic dispatcher cycles ok =
migration complete.
