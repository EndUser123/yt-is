# AGENTS.md

## Overview

`yt-is` (YouTube Intelligence System) is a high-throughput transcript ingestion pipeline. It has recently transitioned to an **Industrial Architecture** to handle a 140,000-video backlog.

## Fresh Agent Throughput Gate

Before proposing or launching any NotebookLM throughput benchmark, read:

- `docs/operations/throughput-optimization-llm-contract.md`
- `docs/operations/templates/throughput-decision-packet.md`
- `docs/operations/hot-path-throughput-next-test-plan.md`
- `docs/operations/test-registry.md`
- `docs/operations/observability-contract-checklist.md`

Do not launch a live throughput run from chat memory alone. Complete the decision packet first. If the packet cannot name the raw artifacts, current control, falsifier, early-abort gate, and promotion rule, do offline attribution, a harness fix, or a code fix instead.

### The Industrial Transition (April 2026)
As of commit `bea672f`, the pipeline has been optimized for scale:
- **Persistent Staging:** Uses `NLMIndustrialScraper` with a module-level singleton. A staging notebook is reused for up to 300 videos, reducing setup overhead by 99.7%.
- **Automated Triage:** `bin/csf-source fetch` now automatically chooses between **Industrial (Batch)** and **Surgical (Sequential)** paths based on the `BACKLOG_THRESHOLD = 50`.
- **Deep Discovery:** `source_enumerator.py` now includes a Full Playlist Enumeration fallback to bypass the 15-video RSS limit and catch "Deep Gaps".
- **Self-Healing:** `BatchScheduler` now implements a **24-hour Retry Window** for transient failures (429s, timeouts).

## Architecture

```
User Input → Skill Invocation → CLI Script / Python → Transcript Sources → SQLite Cache
```

## Skills

### `/yt-is` — YouTube Channel Management

Check all tracked YouTube channels for new videos and manage your channel list.

**Entry point**: `bin/yt-is` (wraps `bin/csf-source`)

**Commands:**
- `sync` — Check all tracked channels for new videos (RSS + gap detection + API)
- `list` — List all tracked channels with metadata
- `add <url>` — Add a new channel or playlist to track
- `fetch` — Download pending transcripts via the full fallback chain (oEmbed → yt-dlp → yt-dlp+cookies → direct API → NotebookLM → Selenium → Whisper)

**Escalation Chain:**
1. oEmbed reachability probe — cheap early skip for removed/private videos
2. yt-dlp (WEB client) — fastest, works for most public videos
3. yt-dlp with cookies — for age-restricted videos
4. direct API — cheap terminal/no-transcript discriminator
5. NotebookLM Industrial — best for backlog and clean transcripts
6. Selenium Firefox — fallback for bot-check failures
7. Whisper — audio fallback

**Key files:**
- `bin/yt-is` — CLI entry point
- `bin/csf-source` — Backend implementation
- `csf/source_enumerator.py` — RSS + API enumeration
- `csf/batch_status.py` — SQLite storage (`channel_metadata`, `analysis_status` tables)

**Dependencies:**
- `yt-dlp>=2024.0.0`
- Firefox (Selenium fallback)
- `YOUTUBE_API_KEY` (for gap resolution)

### `/yt-nlm` — NotebookLM Transcript Extraction

Extract YouTube transcripts using NotebookLM's batch notebook workflow.

**Entry point**: `csf/transcript.py` via the NotebookLM batch path inside `bin/csf-source fetch`

**Why batch over ephemeral:**
- **Ephemeral (deprecated)**: 1 notebook per video — wastes NotebookLM slots, slow
- **Batch**: Up to 300 YouTube sources per notebook — reuses a single notebook

**Workflow:**
1. Create batch notebook: `nlm notebook create "batch_transcript_{id}"`
2. Add sources: `nlm source add <nb-id> --youtube <url1> --youtube <url2> ... --wait`
3. Get content: `nlm source content <source-id>` (returns raw JSON with `{"value": {"content": "..."}}`)
4. Delete notebook: `nlm notebook delete <nb-id> --confirm`

**Auth auto-recovery:**
- yt-is bootstraps the NotebookLM CLI with `uv tool install --upgrade notebooklm-mcp-cli` on first use unless `YTIS_NLM_AUTO_UPDATE=0`, then probes `nlm login --check` and falls back to the known-good pinned git spec via `YTIS_NLM_FALLBACK_SPEC` if the latest build breaks login on this machine.
- Before commands: `nlm login --check`
- If expired: `nlm login --force` (no user prompt)
- Before any benchmark trial: clear stale worker notebooks through the existing worker-notebook cleanup path, then let the worker process prewarm its notebook before timed batches start.
- Browser/process cleanup must be scoped to yt-is-owned runtime state only. Do not kill Chrome, Edge, or other browser processes by executable name. Only stop processes that can be tied to the active yt-is run by an explicit PID recorded by the harness or by a command line rooted under a configured yt-is browser profile such as `P:\.data\yt-is\browser\notebooklm-pro`, `P:\.data\yt-is\browser\notebooklm-free`, or another lane `browser_profile_root` from the active lane config. If ownership is ambiguous, leave the browser running and inspect the run logs/profile roots first.

**Key files:**
- `csf/transcript.py` — `_fetch_via_notebooklm_batch()` with auth recovery
- `csf/cache.py` — `set_cached_transcript()` for database caching

**Dependencies:**
- `nlm` CLI (NotebookLM command-line interface)
- NotebookLM Pro/Plus account (300 source limit per notebook)

## CLI Tools

### `yt-is`

Channel management CLI. Delegates to `csf-source` backend.

```powershell
yt-is sync                  # Check all tracked channels
yt-is list                  # List all tracked channels
yt-is add <url>             # Add a channel
yt-is fetch                 # Download pending transcripts
```

### `csf-source`

Backend implementation for all channel and transcript operations.

```powershell
csf-source list              # List tracked sources
csf-source add <url>         # Add a source
csf-source check <source>    # Check one source for new videos
csf-source check-all        # Check all sources
csf-source sync <source>    # Process pending videos for a source
csf-source fetch            # Download pending transcripts
```

When launching from a shell inside the repo, prefer `python bin/csf-source ...` so the command does not depend on PATH.

## Data Flow

```
/yt-is sync
    │
    ├─► RSS fetch (15 most recent per channel)
    ├─► Gap detection (new videos not in local DB)
    └─► API resolution (YouTube Data API with publishedAfter cursor)
            │
            ▼
    batch_status.sqlite: analysis_status (pending)
            │
            ├─► /yt-is fetch ──► python bin/csf-source fetch ──► transcripts.sqlite
            │                              └─► full fallback chain
            │
            └─► /yt-nlm ──► NotebookLM batch ──► transcripts.sqlite
                        │
                        ▼
            Combined markdown batches → CKS / Obsidian / analysis tools
```

## Storage

- **batch_status.sqlite** — Video tracking
  - `channel_metadata`: tracked channels, playlist IDs, last_checked
  - `analysis_status`: video_id, status (pending/complete/failed), last_stage, failure_reason
- **transcripts.sqlite** — Cached transcripts (video_id, lang, source, content)

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `YOUTUBE_API_KEY` | — | YouTube Data API v3 key (for gap resolution) |
| `YTIS_NLM_MAX_SOURCES_PER_NOTEBOOK` | 300 | Max YouTube sources per NotebookLM notebook |

### External Transcript Provider

Register a custom transcript provider:

```python
from yt_is.csf.transcript import register_external_transcript_provider

def my_provider(video_id: str, prefer_lang: str | None):
    # Return (success: bool, transcript: str | None, error: str | None)
    return True, "transcript content", None

register_external_transcript_provider(my_provider)
```

Called after all built-in methods fail, before returning final failure.

## Troubleshooting

### "No new videos found" after sync

The RSS feed only returns 15 most recent videos. If your tracked videos are older than that, the sync reports no new videos — even if there are unprocessed pending videos from prior syncs.

### NotebookLM auth expired

The batch workflow has auth auto-recovery: `nlm login --check` runs before commands, and `nlm login --force` runs automatically if expired. No manual intervention required.

### Transcript fetch fails for all methods

Check:
1. Video has captions (YouTube Studio → Subtitles)
2. Video is not age-restricted or region-blocked
3. `YOUTUBE_API_KEY` is set for gap resolution
4. Firefox is installed (for Selenium fallback)
