# Plan: yt-is CLI restructuring

## Tasks

- [x] TASK-1: Add `get_newest_published_for_source` to batch_status.py public API — ALREADY EXISTS (batch_status.py:375, 566)
- [x] TASK-2: Add `mark_complete` overload to batch_status.py public API — ALREADY EXISTS (batch_status.py:452)
- [x] TASK-3: Add `set_status_batch` bulk operation to batch_status.py
- [x] TASK-4: Fix bin/csf-source imports + replace N+1 loops with batch calls
- [x] TASK-5: Add InterProcessLock to cmd_sync for multi-terminal coordination

### Added 2026-09-12 — per-pipeline CLI handles (source: pipeline-gap analysis, session 01a09247; evidence: 6 raw python invocations carried by /ytis skill because CLI lacks handles; connectors already prove the pattern)

> Landing constraint: package checkout is on lane branch `agent/01a08852-visual-closers`; these land via a fresh worktree off main, not the shared tree. All subcommands are thin argparse delegations to existing stable script CLIs — no pipeline logic moves.

- [ ] TASK-6: Add `ytis scan` — discovery-only channel scan, unfused from fetch (currently only reachable inside `ytis run`)
- [ ] TASK-7: Add `ytis drain status|start|stop` — delegates to supervisor/relay machinery; must respect the fetch-run DB lock (single-flight by design)
- [ ] TASK-8: Add `ytis visual status|start|stop` — delegates to run_visual_worker.py; respects worker-alive check and download budget
- [ ] TASK-9: Add `ytis index status|start` — delegates to EF index incremental; respects pid guard
- [ ] TASK-10: Add `ytis dht status|ingest` — delegates to scripts/dht-capture + run_dht_ingest.py
- [ ] TASK-11: Add `ytis loop status|start|stop` — wraps run_continuous_ops.py --loop; kills the "continuous vs 1 run" start ambiguity (2026-09-11 operator confusion receipt)
- [ ] TASK-12: Update /ytis skill (P:/.agents/skills/ytis/SKILL.md) command table to mirror pipelines 1:1; remove raw python invocations from the skill
- [ ] TASK-13: Dispatcher delegation tests per new subcommand (pattern: tests/test_ytis_cli_*.py)

### Added 2026-09-12 — connector historical backfill (source: connector-scan asymmetry discussion, session 01a09247; connectors are new-only-by-watermark today)

- [ ] TASK-14: Backfill mode for connector sync — enumerate historical listing pages per source, dedupe against content cache, ingest via the existing EF path under a SEPARATE backfill watermark (never advance the live sync watermark; no double-ingest)
- [ ] TASK-15: Per-source depth feasibility matrix + config — HN: full history (Algolia, complete); GitHub: EXCLUDED from content backfill, 2026-09-12 (operator) — releases are announcement/now-signals, not discourse; no temporal-baseline value; READMEs are current-state; release cadence derivable on demand from gh API metadata without EF ingestion; Reddit: API-capped (~1k/listing — top-all-time + new window, no arbitrary depth); RSS + Twitter/X: structurally unavailable beyond feed window (document as EXCLUDED, not silently shallow); Discord/DHT: full ingest of existing G:/backups/dht archives. Remaining feasibility claims are platform knowledge [INFERENCE], verify caps against live APIs at implementation time
- [ ] TASK-16: `ytis <connector> backfill --since <date>` CLI handle — extends the TASK-6..13 uniform verb set; depth default per TASK-15 matrix
- [x] DECIDED 2026-09-12 (operator): backfill depth default = 2 years where feasible (temporal-emergence and interest-trajectory consumers want 1-2y baselines; shorter makes trend detection noise-dominated); per-source feasibility matrix (TASK-15) still caps sources that cannot reach 2y

### Added 2026-09-12 — arch-review remediation (source: P:/docs/handoffs/arch-review-on-wiki-skill-graph-20260912/yt-is-2026-09-12.md, 7/7 cite-verified, first yt-is package review)

- [ ] TASK-17 (F1+U2): Sweep ~34 stale session worktrees under `.data/sessions/*/worktree/` — owner/liveness check first (inspect branch tips for unmerged unique work), sanctioned sweeper `P:/.agents/scripts/git/sweep_worktrees.py`; after sweep, re-run one scanner unscoped to validate the ~30x contamination factor drops (falsifier test from wiki concept session-worktrees-nested-in-package-repos)
- [ ] TASK-18 (F1): Return the package primary checkout to main — currently parked on lane branch `agent/01a08852-visual-closers` (tip 83c36b16; inspect before any rebase/discard, may fold into TASK-17's check)
- [ ] TASK-19 (gap): Default `.data/sessions` exclusion in the workspace scanners (fmea_scan, safe_write_audit, coupling_audit) — receipt: same scanner returned 12,487 findings unscoped vs 35 scoped to csf/; NOTE: change lands in P:/.agents tooling, not this repo
- [ ] TASK-20 (F4): Atomic-write hardening of state-critical paths — nlm_keepalive._push_backup auth-backup delete-before-copy → tmp+os.replace (the artifact the 2026-09-11 auth repair depended on); csf_logging jsonl writers (:141, :235); transcript cookie-file handling (:1095-:1130)
- [ ] TASK-21 (F5): check/timeout on the 6 unchecked subprocess sites — connectors.py:44, nlm_batch.py:1353, worker_count_sweep.py:764, batch_size_series.py:214, breadth_series.py:427, visual/media_fetch.py:354
- [ ] TASK-22 (F2, block-tier trigger met): Decompose nlm_batch.py (8,086 lines / 2,593 callsites) along the persistence seam — staging/notebook persistence, subprocess orchestration, shared-dir globbing, cleanup receipts become separate modules; contradicts frozen outcome "industrial path not locked in god-files" until done; requires refactor or recorded concrete technical constraint
- [ ] TASK-23 (F3): Identity filters on the 49 rglob-without-filter sites (RPN 576) — priority order: nlm_command_failed_classifier (11), sharded_lane_stage_reducer (9), nlm_scraper (5), transcript (4); full site list in review report section (b)
- [ ] TASK-24 (F6): Single source of truth for batch-DB path resolution — dedupe the constant re-declared across ~20 scripts alongside the YTIS_BATCH_STATUS_DB_PATH contract
- [m] F7 (info): README verified current 2026-09-12 — no action. U1 resolved 2026-09-12 (.mka accretion identified and purged; disk 1.8→32.2GB) — no action.

### Added 2026-09-12 — review-run infrastructure findings (source: /review-code run 20260911-232546, verdict healthy, zero target findings; these three are the run's own process incidents — changes land in workspace tooling, not this repo)

- [x] TASK-25: review-code specialist contract needs a post-return deliverable-existence check — DONE 2026-09-12: deliverable check + one auto-resume + parent-copy-first ordering + markdown-fence extraction landed in P:/.agents/skills/review-code/SKILL.md (tp-reviewed 2-lens, REVISE resolved)
- [x] TASK-26: hard-429 failover classification — DONE 2026-09-12: three-class table (hard-quota failover / Retry-After retry-once / ambiguous conservative) in review-code SKILL.md; spawn-pool auto-quarantine as backstop
- [x] TASK-27: refresh critic-model-pool slugs — DONE 2026-09-12: `minimax` in skill + wiki pool contract; Excluded section rewritten (legacy slug, never dispatch)

## Optimization pass (2026-09-12) — execution waves

Sequencing logic: measurement integrity and backlog-unlockers first (they de-risk every later wave), CLI surface second (ergonomics for everything after), hardening third, structural refactor fourth (needs its own design packet), backfill program last (depends on the CLI verb set). TASK-18 folds into TASK-17's owner check.

- **Wave 1 — unlock (highest value, hours):** TASK-17 (worktree sweep + falsifier re-scan; includes TASK-18's return-to-main check) → TASK-7 implemented as the drain RESUME mechanism (the real blocker; a `ytis drain start` that merely wraps the skip behavior is not done) → TASK-19 (scanner `.data/sessions` exclusion default) → nightly-sync RCA (not yet a task — recommended addition, gates "all sources")
- **Wave 2 — CLI surface (half-day):** TASK-6, 8, 9, 10, 11, 13 (per-pipeline handles + delegation tests) → TASK-12 last (skill table mirrors what exists)
- **Wave 3 — hardening (day):** TASK-20 (atomic writes — auth-backup path first) → TASK-21 (subprocess checks) → TASK-23 (rglob filters, priority order as listed)
- **Wave 4 — structural (own design packet):** TASK-22 (nlm_batch decomposition along the persistence seam) → TASK-24 (DB-path dedup)
- **Wave 5 — backfill program:** TASK-14 → 15 → 16 (16 depends on Wave 2's verb set; depth decided: 2y)

**Recommended additions not yet accepted (operator call):** drain-resume design packet (= TASK-7's core), YtisContentSync exit-1 RCA, no-caption/deferred-audio lane disposition (47% of pending backlog).






