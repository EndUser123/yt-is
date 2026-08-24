# ytis — YouTube Intelligence System

Tracks YouTube channels, downloads transcripts, extracts code from videos, and builds a searchable knowledge base. One command.

## Quick start

```powershell
ytis status          # Is it working?
ytis run             # Scan channels for new videos, fetch transcripts
ytis search "React"  # Search everything semantically
ytis today           # What's new in the last 24 hours
ytis topics          # What topics are in my video corpus?
ytis channels        # What channels am I tracking?
ytis add <url>       # Track a new channel
```

> **First time?** Open a new PowerShell window, then type `ytis status`.
> If it says "not found," run `P:\packages\yt-is\bin\ytis status` directly.

## What it does

You give it YouTube channels to track. It:

1. **Scans** those channels for new videos (RSS + API, every time you `ytis run`)
2. **Fetches transcripts** for each new video (via Google NotebookLM — fast, handles most videos)
3. **Falls back to Whisper** for videos NotebookLM can't transcribe (downloads audio only, transcribes locally)
4. **Extracts code** from videos that show programming (downloads video, captures frames, runs OCR, uses Gemini to transcribe visible code)
5. **Clusters** all transcripts into topic areas (semantic similarity — "these 300 videos are about React")
6. **Makes everything searchable** — `ytis search "database design"` returns relevant chunks from any of your 200K+ videos

## Status output explained

```
ytis status

  ● Running (continuous ops loop active)    ← green dot = healthy
  ○ Stopped (loop not running)             ← open circle = needs restart

    Drain: running | Worker: active         ← background processes

  Transcripts: 205,568 complete            ← total videos transcribed
              126,530 pending              ← waiting to be processed
               14,753 failed               ← couldn't transcribe (normal ~5%)
  Channels: 2,865 tracked                  ← sources you're following
  Topics: 351 discovered                   ← semantic clusters found
  Code artifacts: 46 extracted             ← code pulled from videos
  Wiki pages: 10 staged                    ← knowledge summaries ready
  Last 24h: 65,045 new transcripts        ← yesterday's throughput
```

## Commands in detail

### `ytis status`
Shows health, counts, and recent activity. Run this first if anything seems wrong.

### `ytis run`
Full pipeline: scans all tracked channels for new videos, then fetches transcripts.
- Takes 30-60 minutes for the channel scan (~1,800 channels)
- Then processes new videos until done
- **Ctrl-C is safe** — already-scanned channels and fetched videos are saved
- Re-running after an interrupt is fast (already-checked channels skip through)

### `ytis search <query>`
Semantic search across all transcripts and extracted code.
- Results include the video title, relevant text snippet, and YouTube URL
- Uses vector similarity, not keyword matching — "database performance" finds videos about SQL optimization even if they never say "database performance"
- Needs the search service running: `python -m ef.warm_query_service` (or it falls back to slower CLI search). Since 2026-08-22 this one process serves BOTH faces: the `:6391` HTTP renderers and, when `MCP_HTTP_PORT` is set (the WinSW service sets 8324), the `search_ef` MCP — one BGE-M3 model shared between them

### `ytis today`
Shows what happened in the last 24 hours: new transcripts, new code extractions, new insight reports.

### `ytis topics`
Lists the topic clusters discovered in your video corpus, largest first. Each topic shows how many videos it covers and its top terms.

### `ytis channels`
Lists your tracked channels sorted by how many videos have been transcribed.

### `ytis add <url>`
Adds a new YouTube channel or playlist to track. Accepts:
- Channel URL: `https://youtube.com/@channelname`
- Channel ID: `UCxxxxxxxxxxxxxxxxxxxxxx`
- Playlist URL: `https://youtube.com/playlist?list=PLxxxxxxx`

After adding, run `ytis run` to scan it for videos.

## Beyond YouTube

The same knowledge base also ingests other sources. Everything lands in one
searchable index — `ytis search` returns YouTube, Reddit, HN, and Discord
results together.

### `ytis reddit`
Syncs recent posts + top comments from the tracked AI subreddits.

### `ytis hn`
Syncs top Hacker News stories + comments (free Algolia API, no key needed).

### `ytis discord`
Syncs messages from Discord channels you track. **Needs a one-time setup:**

1. https://discord.com/developers/applications → New Application → Bot
2. Copy the bot token → add to `P:/.env` as `DISCORD_BOT_TOKEN=...`
3. Invite the bot to your server with "Read Messages/View Channel" permission
   (OAuth2 URL Generator, scope `bot`)
4. Enable Developer Mode in Discord (Settings → Advanced), then right-click
   each channel → Copy Channel ID:
   ```
   python scripts/run_discord_sync.py --add 123456789
   ```
   Or auto-discover up to 5 text channels per server: `--all`

Then `ytis discord` (or `python scripts/run_discord_sync.py --list`) manages it.
Each sync stores the last 50 human messages per channel as a searchable batch.

### `ytis rss`
Syncs RSS/blog feeds (full-text extraction via trafilatura). Add feeds in the browser at /sources or via `python scripts/run_rss_sync.py --add <url>`.

### `ytis github`
Syncs READMEs + releases of tracked repos (authenticated gh CLI). `ytis github --add owner/repo`.

### `ytis sync`
Runs EVERY available content connector (Reddit, HN, RSS, GitHub, Discord-DHT) in parallel and indexes everything new.

### `ytis purge`
Removes channels from the entire knowledge base (all stores) — explicit, receipted, dry-run by default. `--and-blacklist` prevents re-adding.

### `ytis digest`
Generates the daily digest (what's new across all sources).

### Run everything at once
```
python scripts/run_all_syncs.py           # YouTube + Reddit + HN + Discord + digest
python scripts/run_all_syncs.py --quick   # skip the YouTube scan
```
New connector content is embedded into the search index automatically at the
end of the run (`ef.ingest_connectors`; manual: `python -m ef.ingest_connectors`).


## Understanding the numbers

| Metric | What it means | Notes |
|--------|---------------|-------|
| Transcripts complete | Videos fully transcribed | Higher is better |
| Pending | Videos waiting to be processed | Normal — drains over time |
| Failed | Videos that couldn't be transcribed | ~5-7% is normal (no-caption, deleted) |
| Code artifacts | Videos where visible code was extracted | Growing = visual pipeline working |
| Topics | Semantic clusters in your corpus | More = more diverse content |
| Last 24h | Recent throughput | 0 for >24h = something is stuck |

## Common issues

**"ytis status shows ○ Stopped"**
The background loop isn't running. Start it:
```powershell
cd P:\packages\yt-is
python scripts\run_continuous_ops.py --loop
```
Keep the terminal open (or use `pythonw` for no window).

**"ytis search returns nothing"**
The search service isn't running. Start it:
```powershell
cd P:\packages\yt-is
python -m ef.warm_query_service
```
Keep it running in a background terminal.

**"Pending count isn't going down"**
Either the drain is stuck (check `ytis status`), or remaining pending videos are on channels you've blocklisted. Check with `ytis channels`.

**"ytis run hangs at Phase 1 SYNC"**
Channel scanning takes 30-60 minutes. Watch the streaming output for progress. If stuck on one channel for >5 minutes, Ctrl-C and re-run.

## How it works (architecture)

```
Channels ──→ Scan (RSS/API) ──→ Pending queue
                                   │
                     ┌─────────────┤
                     ▼             ▼
              NLM drain       Whisper fallback
           (NotebookLM,       (audio-only download,
             fast, 3 accounts)  local transcription)
                     │             │
                     ▼             ▼
              Transcripts    Transcripts
              (205K+)        (same store)
                     │
                     ▼
            Evidence Fabric (embeddings + search)
                     │
             ┌───────┤
             ▼       ▼
        Topic     Code extraction
        clusters  (frame capture + OCR + Gemini)
             │       │
             ▼       ▼
        Wiki pages  artifacts.md
```

## Configuration

All settings have sensible defaults. Key overrides (environment variables):

| Variable | Default | Controls |
|----------|---------|---------|
| `YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR` | 30 | Rate limit for video downloads |
| `YTIS_WHISPER_MODEL` | large-v3-turbo | Whisper model (GPU) |
| `YTIS_OCR_GPU` | auto | Use GPU for OCR |
| `YTIS_VISUAL_ENQUEUE_MIN_SCORE` | 1.0 | Threshold for visual analysis |

Full validated list: see `csf/config.py`

## Data locations

| Path | Contains |
|------|----------|
| `P:/.data/yt-is/batch_status.sqlite` | Video metadata, channel info, processing status |
| `P:/.data/yt-is/transcripts.sqlite` | All transcript text |
| `P:/.data/yt-is/visual/` | Extracted code artifacts, frames, audio |
| `P:/.data/yt-is/ef/` | Evidence fabric (search index, topic clusters) |
| `P:/packages/yt-is/.logs/` | Run receipts, event logs |

## For developers and agents

- [AGENTS.md](AGENTS.md) — workspace rules for AI agents
- [HANDOFF.md](HANDOFF.md) — current session state and open work
- [docs/operations/](docs/operations/) — operational procedures
- [csf/config.py](csf/config.py) — all configuration variables
- [tests/](tests/) — 1,900+ tests
