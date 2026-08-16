---
name: channel-intake
description: Full channel intake workflow — discover channels from Watch Later/History, backfill descriptions/titles, classify, review in the local web page, apply decisions, exclude/blacklist, and sync
version: 1.1.0
enforcement: strict
triggers:
  - User asks to import channels from Watch Later or YouTube history
  - User asks to classify or categorize YouTube channels
  - User asks to exclude channel categories from sync, or to blacklist/delete channels
  - User asks for the channel review page
  - User asks to run the discovery cycle or channel intake
workflow_steps:
  - Verify prerequisites (browser YouTube session; keys auto-load from P:/.env)
  - Run the discovery cycle (one command) or individual steps
  - Backfill descriptions and titles (free yt-dlp tier first, API fallback)
  - Detect dead channels; classify the rest (provider chain; the session agent can classify residues directly)
  - Build and open the review page for the operator's manual pass
  - Apply the exported decisions (assignments, blocks, stars, exclusions, reclassification) — apply archives+deletes the export
  - Sync channels
allowed_first_tools:
  - Bash
required_first_command_patterns:
  - '^python (P:/packages/yt-is/)?scripts/run_discovery_cycle\\.py'
required_first_command_hint: Use `python scripts/run_discovery_cycle.py --skip-sync --allow-spend` from P:/packages/yt-is for a full intake pass without the long sync.
aliases:
  - channel intake
  - discovery cycle
  - channel review
  - classify channels
depends_on_skills: []
---

# /channel-intake — discover → describe → classify → review → apply → sync

One repeatable workflow that turns Watch Later + watch history into a
classified, exclusion-filtered channel set ready for sync and bulk fetch.

## The one command

```
python P:/packages/yt-is/scripts/run_discovery_cycle.py --allow-spend               # everything incl. sync
python P:/packages/yt-is/scripts/run_discovery_cycle.py --skip-sync --allow-spend   # fast iteration
python P:/packages/yt-is/scripts/run_discovery_cycle.py --no-open --allow-spend     # scheduled runs
```

Steps executed, each with receipts in `.logs/discovery_cycle/<timestamp>/`:
refresh cookies → watchlater/history dry-run+import (spend-gated enrichment)
→ categorize (provider chain; exit 1 if zero classify) → promote excluded
categories → sync (skippable) → build + open the review page.

## Individual steps (all idempotent, safe to re-run)

```
# Evidence backfills (free yt-dlp first, API fallback; never pay twice)
python scripts/backfill_channel_descriptions.py --allow-spend   # titles + descriptions
python scripts/backfill_channel_stats.py                         # shorts/playlists counts
python scripts/detect_dead_channels.py                           # mark terminated/deleted (auto-blocks)

# Classification tiers — all with full evidence (title+description+video titles)
python bin/csf-source categorize --workers 3                     # NULL rows only
python bin/csf-source categorize --retry-other --workers 3       # stored "Other" (never manual)
python bin/csf-source categorize --category "News" --workers 3   # whole category (OVERRIDES manual)
python bin/csf-source categorize --channels "URL1,UCid|@file"    # exact list (OVERRIDES manual)
python bin/csf-source categorize --all --workers 3               # EVERYTHING (OVERRIDES manual)
# flags: --video-titles (cached, 14-day TTL) · --refresh-video-titles

# Review page
python scripts/build_channel_review_page.py --open               # build (and show)
python -m http.server 8765 --bind 127.0.0.1                      # run from .logs/channel_review/ —
                                                                 # the operator's LIVE TAB is
                                                                # http://127.0.0.1:8765/review.html —
                                                                # keep it running during review; a
                                                                # stopped server FREEZES that tab
# Decisions
python scripts/apply_channel_review.py                           # auto-finds NEWEST export
python scripts/apply_channel_review.py <file> [--apply-promotion]
python scripts/promote_excluded_categories.py --exclude "News,Entertainment" [--apply]
python scripts/blacklist_channels.py --channels <list>@file|--dead-all [--apply]
```

### Current exclusion state (2026-08-16)

11 categories excluded: Physics, News, Politics, Music, Sports, Lifestyle,
Gaming, Science, History, True Crime, Entertainment.
1,033 category blocks + 342 operator blocks = 1,096 of 2,865 blocked (38%).
Active fetch set: 1,769 channels across AI/ML (597), Health (302), Software
Engineering (203), Markets (156), AI News (121), Technology (112), Finance
(105), Business (105), Education (43), Storytelling (37), Robotics (21),
Mathematics (11).

### The export lifecycle (contract — never re-ingest)

1. Operator marks on the page; **Download** writes `review_decisions*.json`
   to the browser's download folder (browsers rename repeats
   `review_decisions (1).json` — discovery GLOBS the pattern, newest wins,
   across ~/Downloads, P:/tmp, cwd).
2. `apply_channel_review.py` validates and applies: assignments,
   blocks/unblocks, ★ exceptions, ⟳ reclassification (runs the classifier
   with full evidence), locks/unlocks, category clears, exclusions +
   promotion receipt.
3. **On success apply ARCHIVES the export** to
   `.logs/channel_review/applied/<ts>.json` (the operator-decision audit
   trail — this is the provenance record for manual classifications) and
   **DELETES the original**. Failed applies keep the file for retry.
4. Apply **regenerates review.html**; the operator clicks **↻ Refresh
   data** (built-at stamp in the header shows freshness). localStorage
   marks survive the refresh; already-applied marks drop out of the deltas.

## The three decision tiers (keep / dead / gone)

| Tier | Tool | DB effect | Reversible |
|---|---|---|---|
| **Exclude** (✕ / chips) | page + promotion | kept, blocklisted, fetch/sync skip | unblock / re-apply |
| **Dead** (auto-detected) | detect_dead_channels | kept, `channel_status` + blocked, struck-through on page | unblock |
| **Blacklist** | blacklist_channels | DELETED (rows+cache) + tombstone (`channel_blacklist_reason`) — importer skips tombstoned channels forever | via daily backups only |

Blacklist is dry-run by default; `--apply` performs the deletion with a
receipt. Use it for spam/reupload junk and confirmed-dead channels, not
routine pruning (exclude covers that).

## Classification model

- **Vocabulary**: `csf/categorize.py::CATEGORIES` (17 incl. Lifestyle) — the
  single source; chips/columns/prompts/exclusion-validation all derive from
  it. Adding a category is one edit, but membership only migrates on the
  next evidence pass (targeted ⟳ or `--all`) — budget that sweep.
- **Provenance**: `category_source` — `manual` (operator/agent, sticky) vs
  `llm` (auto). ⚠️ The 2026-08-15 retro-mark stamped legacy auto rows as
  `manual`, so the Auto/Manual filter overstates human decisions; true
  operator/agent decisions are recoverable from
  `.logs/channel_review/applied/` + `agent_pass_20260815.json`.
- **Selection semantics**: `--all`/`--category`/`--channels`/⟳ override
  manual; plain and `--retry-other` never do.
- Mixed channels classify by DOMINANT mode. Known gaps: politics/true-crime
  fold into News; history folds into Education — revisit before excluding
  those categories.

## The review page contract (also the reusable template)

`.logs/channel_review/review.html` — self-contained (data embedded,
file:// works), built by `build_channel_review_page.py`:

- **View** (what you see): header click includes a category in the filter
  (set semantics; no clicks = all); sortable Channel/Subs/Videos/Shorts/
  Lists headers; Auto/Manual provenance filters; search; Clear-filters.
- **Policy** (what will happen): red chips exclude a category; ✕ blocks a
  channel (solid = per-channel, dashed = inherited from category; clicking
  an inherited ✕ grants the ★ exception); ★ keeps a channel despite its
  category; ⟳ marks for full-evidence reclassification; clicking the set
  cell un-assigns (pending) or clears to unclassified (stored, confirmed).
- **Bulbs**: "Exclude all shown" / "🔒 Lock all shown" / "⟳ Auto-categorize
  all shown" act on the filtered view (single-category view toggles the
  chip). "Revert category edits (N)" one-click-undoes accidental
  reassignments. Marking ⟳ shows a banner: categories change AT APPLY.
- **Durability**: localStorage autosave (theme, width, marks; Reset clears;
  beforeunload warns on unexported changes); dead channels struck through
  with reason; built-at stamp + ↻ Refresh data.
- Verify changes with `node bin/verify_review_page.mjs` (Playwright driving
  installed Chrome; 17 interaction checks) and the JS-syntax gate in pytest.

### Template for other review workflows

Generator (query state → embed JSON in static HTML) → page (view/policy
separation, autosave, export deltas) → applier (validates, writes through
tested APIs, receipts; dangerous halves behind flags) → consume-and-delete
the export. Reference: the two scripts above, pinned by
`tests/test_channel_intake_workflow.py`.

## One-fetch cache inventory (never pay for the same data twice)

| Data | Stored in | Freshness | Refresh |
|---|---|---|---|
| Channel titles, descriptions | channel_metadata | until empty | backfill scripts |
| Subscriber/video/shorts/playlist counts | channel_metadata | fetched once | backfill_channel_stats |
| Video titles (RSS evidence) | channel_metadata.recent_video_titles | 14-day TTL | --refresh-video-titles |
| Video enumeration, titles, durations, captions flags | analysis_status | permanent | sync |
| Transcripts | transcripts.sqlite (canonical cache) | permanent | n/a |
| Dead-video negatives | negative_video_cache | permanent | n/a |
| Dead-channel state | channel_metadata.channel_status | permanent | detect_dead_channels |
| Classification + provenance | category/category_source | until reclassified | --all/--category/--channels/⟳ |
| Operator decisions | .logs/channel_review/applied/ | permanent (audit trail) | n/a |

Durability: the caches concentrate months of fetches into two SQLite files —
the `YtisStateBackup` scheduled task (scripts/install_state_backup_task.ps1)
backs up both daily at 03:30 to P:/.data/yt-is/backups/.

## Configuration

`config/discovery-settings.json` (machine-local, gitignored; example
committed): `cookies_browser`, `auto_import`, `min_*_videos`,
`categorize_workers`, `excluded_categories`, `run_sync`,
`build_review_page`, `open_review_page`.

## Worker notebook lifecycle

Worker notebooks are **disposable**: created on demand by the industrial
batch path, reused within the run, deleted during worker shutdown.

| Phase | What happens | Where |
|---|---|---|
| During fetch | Notebook created per worker, reused across batches | csf/nlm_batch.py |
| Normal shutdown | Notebook deleted + cleanup receipt logged | csf/nlm_batch.py |
| Supervisor killed / crash | Orphaned notebooks remain (NOT auto-cleaned) | — |
| Recovery | `csf-source cleanup-worker-notebooks --delete` | bin/csf-source |
| Pipeline pre-flight | Phase 0 cleans stale state + legacy output root | run_intake_pipeline.py |
| Campaign isolation | Each pipeline invocation gets its own dated output root (unattended-<ts>/) | run_intake_pipeline.py |
| Health watcher | Detects stale notebooks as an alert | pipeline_health_watch.py |

If a run dies abnormally, the next pipeline invocation cleans stale
notebooks automatically (Phase 0). The health watcher also flags them.
Manual recovery: `python bin/csf-source cleanup-worker-notebooks --delete`
(run with the account-scoped worker environment for multi-account).

## Known gaps (2026-08-15 red-team — fix before trusting at scale)

- Blocklist rows carry no REASON: exclusion-caused vs operator vs dead
  blocks are indistinguishable, so category exits never auto-unblock
  (exclusion drift). Cleared rows still render their old ✓ until apply.
- Provider round-robin is a classification lottery: same channel, different
  answer depending on which provider answers; `llm` source doesn't record
  the provider.
- No measured precision: Sports was 79% mis-classified pre-evidence-tier;
  current error rate unknown — run a stratified human sample before big
  exclusions.

## Constraints

- The spend gate is deliberate: `--allow-spend` is per-run and dies with
  the process; free tiers (yt-dlp, RSS) always run first;
  `--spend-budget N` caps units per run.
- Promotion never blocks uncategorized channels and rejects unknown
  category names (exit 2).
- Never hard-delete without the blacklist path (tombstone + receipt);
  never re-import a tombstoned channel.
