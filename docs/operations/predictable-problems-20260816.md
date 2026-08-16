# Predictable Problems Matrix — yt-is Pipeline

> Generated: 2026-08-16 · agent: zcode · status: 8 auto-mitigated, 4 manual procedures, 3 accepted risks

## Auto-mitigated (pipeline/skill handles it)

| # | Problem | Likelihood | Impact | Mitigation | Where |
|---|---|---|---|---|---|
| 1 | Supervisor dies silently | High | Pipeline stops, no alert | Health watcher + Phase 0 cleanup | pipeline_health_watch.py |
| 2 | Transcripts claimed saved but aren't | Medium | Data loss (past occurrence) | verify_transcript_storage.py + Phase 4 | Pipeline Phase 4 |
| 3 | Stale worker notebooks after crash | High | Google quota waste | Phase 0 cleanup (skips if supervisor running) | run_intake_pipeline.py |
| 4 | Notebook cleanup deletes active notebooks | Medium | Workers fail mid-run | Recursive glob + supervisor-running check | nlm_batch.py + pipeline |
| 5 | Directory collisions between campaigns | High (before fix) | FileExistsError | Dated campaign roots | run_intake_pipeline.py |
| 6 | Auth expires mid-run | Medium | Workers fail | Keepalive probe + fail-closed | nlm_keepalive.py |
| 7 | Disk space exhaustion | Low (140GB free) | Pipeline stops, DB corruption | Pre-flight check (blocker at <10GB) | preflight_safety.py |
| 8 | SQLite corruption | Low | Data loss | Pre-flight integrity check (blocker) | preflight_safety.py |

## Auto-warned (health watcher or morning briefing catches it)

| # | Problem | Likelihood | Impact | Detection | Action |
|---|---|---|---|---|---|
| 9 | Memory pressure | Medium | Workers OOM | Pre-flight (warn at >90%) | Reduce workers or restart |
| 10 | Success rate degrading | Medium | More failures per chunk | Health watcher (<70% over 5 chunks) | Investigate failure class |
| 11 | No new transcripts (stalled) | Medium | Silent hang | Health watcher (>30 min) | Check supervisor, restart |
| 12 | LLM providers unreachable | Low | Classification fails | Pre-flight (DNS/TCP check) | Wait or switch provider |
| 13 | Backup stale (>48h) | Low | Can't recover recent work | Pre-flight (warn) | Check YtisStateBackup task |
| 14 | Backup not restorable | Low | False sense of security | Pre-flight (read test) | Re-create backup |
| 15 | Concurrent pipeline invocation | Medium | Lock contention | Concurrent guard (exit 2) | Wait or kill existing |

## Manual procedures (documented, not automated)

| # | Problem | Likelihood | Impact | Procedure |
|---|---|---|---|---|
| 16 | P: drive failure | Low | Total data loss | Restore from C:/Users/brsth/.ytis-state-backup |
| 17 | Google account suspension | Low | All fetching stops | Cannot be automated; operator must resolve with Google |
| 18 | yt-dlp breaks (YouTube UI change) | Medium | Discovery + yt-dlp fetch stops | Update yt-dlp: `pip install -U yt-dlp`, verify with single-video test |
| 19 | NotebookLM API changes | Low | Batch path breaks | Update notebooklm-py: `pip install -U notebooklm-py[headless]` |

## Accepted risks (documented, no current mitigation)

| # | Problem | Likelihood | Impact | Why accepted |
|---|---|---|---|---|
| 20 | Transcript DB grows to 10-50GB | High (300K pending) | Disk pressure | 140GB free; pre-flight catches; VACUUM can reclaim |
| 21 | Provider lottery (classification) | Medium | Inconsistent categories | Round-robin gives diversity; provider recording is a P1 backlog item |
| 22 | No alerting during off-hours | Medium | Problems discovered in morning | Morning briefing catches everything; real-time alerting is nice-to-have |
| 23 | Windows update forces restart | Medium | Supervisor killed mid-run | Pipeline handles recovery on next launch; state is durable |

## Recovery procedures

### After machine restart / power loss
```bash
# 1. Check state (supervisor will say "running" but is dead)
python scripts/preflight_safety.py

# 2. Clean stale state (pipeline does this automatically)
python scripts/run_intake_pipeline.py --skip-sync
```

### After P: drive failure
```bash
# 1. Restore latest backups from C: drive
cp C:/Users/brsth/.ytis-state-backup/batch-status-*.sqlite P:/.data/yt-is/batch_status.sqlite
cp C:/Users/brsth/.ytis-state-backup/transcripts-*.sqlite P:/.data/yt-is/transcripts.sqlite

# 2. Verify integrity
python -c "import sqlite3; print(sqlite3.connect('P:/.data/yt-is/batch_status.sqlite').execute('PRAGMA integrity_check').fetchone())"

# 3. Resume pipeline
python scripts/run_intake_pipeline.py --skip-sync
```

### After yt-dlp breaks
```bash
# 1. Update
pip install -U yt-dlp

# 2. Verify single-video fetch works
yt-dlp --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 3. Resume discovery
python bin/csf-source check-all
```

### After NotebookLM API changes
```bash
# 1. Update the client
pip install -U "notebooklm-py[headless]"

# 2. Verify auth still works
python -m csf.nlm_keepalive

# 3. Test single-source add
python bin/csf-source fetch --limit 1 --plan-only
```
