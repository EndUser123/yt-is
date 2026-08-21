# yt-is Feature Roadmap — Personal Intelligence Platform

**Last updated:** 2026-08-20
**Current state:** 208K+ transcripts, all sources searchable (YouTube + Reddit + HN + RSS-ready) | 2,865 channels | titles 99.998% complete | 351 topics, 10 promoted to wiki | web: search, daily brief, 7-day brief, sources, status (all at http://127.0.0.1:6391)

**Vision:** One searchable knowledge base spanning everything you consume — YouTube, Reddit, TikTok, podcasts, articles, newsletters, GitHub, Discord, and more. Ask a question, get answers from all sources with citations.

Legend: ✅ shipped · 🔨 in progress · ❌ not built · 💤 deferred

---

## 1. Content Sources

### Video Platforms

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| YouTube | ✅ | NLM (3 accounts) + Whisper fallback | Transcripts, code artifacts | 206K transcripts, deepest integration |
| YouTube Shorts | ✅ | Same as YouTube | Transcripts | Processed with regular videos |
| TikTok | ❌ | Whisper transcription of video audio | Transcripts, trends | No official read API; needs scraping or third-party |
| Vimeo | ❌ | Whisper transcription | Transcripts | Lower priority, smaller audience |
| Twitch VODs | ❌ | Whisper transcription | Transcripts | Gaming/tech streams |
| Bilibili | ❌ | API or scraping | Transcripts, subtitles | Chinese tech content |

### Social Media

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| Reddit | ✅ | OAuth API | Posts + comments | 400+ posts, 10 subreddits, lock-resilient |
| Reddit saved posts | ❌ | User OAuth | Personally saved content | "Things I marked as important" |
| Twitter/X | ✅ | RSSHub twitter routes + operator auth token | Posts | 11 AI/programming accounts live; paced 75s/route |
| Mastodon | ❌ | Public API | Posts | Open protocol, free |
| Bluesky | ❌ | Public API | Posts | Growing tech community |
| Hacker News | ✅ | Algolia API (free) | Posts + comments | `ytis hn`; 30 batches ingested |
| Lobsters | ❌ | RSS | Posts | Tech link aggregation |
| Instagram | ❌ | Graph API (limited) | Captions, comments | Visual content, less text |

### Messaging & Communities

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| Discord | ✅ | DHT archive ingest (bot API parked) | Full message history | 3 archives: 620K msgs → 6,230 docs → 58K chunks searchable; nightly capture task 03:00 |
| Slack | ❌ | Web API | Messages, threads | Work-related discussions |
| Telegram | ❌ | Bot API or scraping | Channel posts | Tech channels, news |
| Matrix | ❌ | Client API | Messages | Open protocol |

### Text & Documents

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| RSS/Atom feeds | ✅ | feedparser, conditional GET | Posts/articles | `ytis rss` / `--add <url>`; empty until feeds added |
| Newsletters | ❌ | Gmail API or IMAP | Email content | Substack, Beehiiv, ConvertKit |
| Web pages | 🔨 | research+spec handoff written (browser-extension-research-20260821); build gated on spec approval |
| PDF documents | ❌ | Upload + text extraction | Papers, manuals, reports | PyPDF2/pdfplumber |
| Markdown files | ❌ | Watch directory | Notes, documentation | Obsidian vault sync |
| GitHub repos | ✅ | gh CLI (authed) | READMEs + releases | `ytis github --add owner/repo`; 6 repos live |
| Stack Overflow | ❌ | API (free) | Q&A pairs | Programming solutions |
| Wikipedia | ❌ | API (free) | Articles | Reference knowledge |

### Audio

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| Podcasts | ❌ | RSS → audio download → Whisper | Transcripts | Tech podcasts |
| Audiobooks | ❌ | File upload → Whisper | Transcripts | Long-form learning |
| Voice notes | ❌ | File upload → Whisper | Transcripts | Personal notes |

### Browser Activity

| Source | Status | Ingestion Method | Content Type | Notes |
|--------|--------|-----------------|--------------|-------|
| Browser history | ❌ | Browser extension or SQLite | URLs + page titles | "What did I read?" |
| Bookmarks | ❌ | Browser export or extension | URLs + titles | Organized interests |
| Reading list | ❌ | Browser extension | Saved articles | Pocket/Instapaper alternative |

---

## 2. Content Processing

| Feature | Status | Notes |
|---------|--------|-------|
| Transcript extraction (video) | ✅ | NLM primary + Whisper fallback |
| Audio transcription | ✅ | Whisper large-v3-turbo on GPU |
| Visual code extraction | ✅ | Frame capture → OCR → Gemini |
| Topic clustering | ✅ | 351 topics + daily incremental assignment (nearest-centroid) |
| Content scoring | ✅ | Deixis + keywords + thumbnail |
| Artifact dedup | ✅ | Consecutive block collapse |
| OCR (images) | ✅ | EasyOCR, GPU-enabled |
| CLIP tagging | ✅ | Visual classification |
| Entity extraction | ✅ | LLM over cluster reps + FTS counts |
| Content summarization | 🔨 | Templates exist, not automated |
| Cross-video synthesis | ❌ | "These 5 videos teach X" |
| Trend detection | ✅ | /trends + search-page momentum cards; series clusters excluded; alerts = handoff ytis-trend-alerts-20260821 |
| Contradiction detection | ❌ | Where sources disagree |
| Translation | ❌ | Non-English content |
| Speaker identification | ❌ | Who said what in multi-speaker |
| Sentiment analysis | ❌ | Community reaction |
| Content quality scoring | ❌ | Signal vs noise per source |
| Auto-tagging | ❌ | ML-assisted content categorization |
| Link extraction | ❌ | URLs mentioned in content → fetch and index |

---

## 3. Search & Retrieval

| Feature | Status | Notes |
|---------|--------|-------|
| Evidence fabric (Qdrant + FTS5) | ✅ | 285K chunks, hybrid retrieval |
| Intent-routed queries | ✅ | Exact/semantic/ambiguous lanes |
| CLI search (`ytis search`) | ✅ | Works across all indexed content |
| MCP server | ✅ | 4 tools for AI agents |
| Warm query service | ✅ | <100ms persistent search |
| **Web UI search** | ✅ | http://127.0.0.1:6391/ — served same-origin; suggestions, topic chips, trends |
| Search filters | ❌ | By source, channel, date, topic |
| Saved searches + alerts | ❌ | "Notify me when new content about X" |
| Natural language Q&A | ✅ | /ask page + service; provider chain codex→agy→openrouter→gemini (env-configurable) |
| Citation with timestamps | ❌ | Click claim → jump to source moment |
| Multi-modal search | ❌ | "Find the video showing this code" |
| Federated search | ❌ | One query across all sources |
| Search suggestions | 🔨 | 6 dynamic suggested questions live; as-you-type autocomplete ❌ |
| Search history | ❌ | What have I searched for? |

---

## 4. Knowledge Organization

| Feature | Status | Notes |
|---------|--------|-------|
| Evidence units with provenance | ✅ | Char-addressable |
| Topic clusters | ✅ | 351 discovered |
| Topic inventory (automatic) | ✅ | In continuous-ops loop |
| Wiki concept pages | ✅ | 10 promoted to vault; generator excerpts fixed; incremental topic assignment live |
| /dream consolidation | 🔨 | Corpus added, not running |
| Knowledge graph | ❌ | Entity relationships |
| Cross-source connections | ❌ | "This Reddit post discusses that video" |
| Learning paths | ❌ | "Watch these in order to learn X" |
| Content calendar | ❌ | "New content this week by topic" |
| Bookmarks/favorites | ❌ | Mark chunks as important |
| Collections | ❌ | Curated groups of content |
| Auto-categorization | ❌ | ML-assisted topic assignment |
| Duplicate detection | ❌ | Same content across sources |

---

## 5. Analysis & Insights

| Feature | Status | Notes |
|---------|--------|-------|
| Content mining | ✅ | Template for cross-video patterns |
| Failure pattern analysis | ✅ | Drain failure taxonomy |
| Recovery yield audit | ✅ | Daily, automated |
| Pipeline health | ✅ | Monitor + alerts |
| Daily digest | ✅ | Markdown + live web brief + 7-day rolling at /digest; automated 06:00 |
| Weekly report | 🔨 | 7-day rolling view covers it; standalone weekly doc ❌ |
| Notification system | ❌ | Email/Slack/Discord/desktop |
| Trend alerts | ❌ | "Topic X is trending in your corpus" |
| Influence mapping | ❌ | Which sources drive discussions |
| Content gap detection | ❌ | "Your corpus doesn't cover X" |
| Expert identification | ❌ | Who consistently provides high-signal content |
| Source reliability scoring | ❌ | Track which sources are consistently useful |

---

## 6. Output & Integration

| Feature | Status | Notes |
|---------|--------|-------|
| `ytis` CLI | ✅ | 18 commands (added rss, github, discord, sync, purge) |
| MCP server | ✅ | Any MCP-compatible agent |
| Channel review page | ✅ | Browser-based categorization |
| **Web dashboard** | ✅ | search + brief + trends + sources + status pages at 127.0.0.1:6391 |
| **Browser extension** | ❌ | Save pages, search from any tab |
| REST API | ❌ | Programmatic access |
| Export (JSON/Markdown) | ❌ | Bulk knowledge export |
| Slack integration | ❌ | Digests, search |
| Discord integration | 🟡 | Ingestion built (`ytis discord`, needs bot token); digests/search ❌ |
| Notion sync | ❌ | Knowledge pages to Notion |
| Obsidian sync | ❌ | Wiki pages to Obsidian vault |
| Email digests | ❌ | Automated daily/weekly summaries |
| Webhooks | ❌ | Notify other systems |
| Embeddable widgets | ❌ | Search box for other apps |

---

## 7. Operations

| Feature | Status | Notes |
|---------|--------|-------|
| Continuous ops loop | ✅ | Self-healing, 15-min ticks |
| Multi-account NLM | ✅ | 3 Google accounts |
| Rate limiting | ✅ | PO-token, download budget, cooldown |
| Self-healing | ✅ | Ghost detection, bounded recovery |
| GPU acceleration | ✅ | Whisper, OCR, CLIP |
| Connector abstraction | ✅ | csf/connectors.py registry + `ytis sync`; 6 AM run executes YouTube in parallel with all light connectors |
| Scheduled automation | ✅ | 05:00 index keeper + 06:00 full content sync (verified end-to-end) |
| Backup & recovery | 🔨 | Daily 03:30 state backup task exists; full DB strategy ❌ |
| Multi-user | ❌ | Currently single-operator |
| Cost tracking | ❌ | API spend monitoring |
| Health dashboard | ✅ | /status — corpus, index lag, daemon liveness, connectors (60s refresh) |
| Usage analytics | ❌ | Query patterns, popular topics |

---

## Priority Recommendations

### Phase 1: Make it usable (days) — ✅ COMPLETE 2026-08-20
1. **Web UI search page** — single page with search box and results
2. **Daily digest** — automated "what your sources taught you"
3. **Wiki page promotion** — move staged pages to wiki vault
4. **Scheduled automation** — `ytis run` on a timer
5. **Hacker News ingestion** — free API, high-signal content

### Phase 2: Multi-source expansion (weeks)
6. **Web dashboard** — topics, health, recent content
7. **Connector abstraction** — clean source interface
8. **RSS/blog ingestion** — tech blogs and news sites
9. **Browser extension** — save any page
10. **Podcast ingestion** — RSS → Whisper
11. **TikTok ingestion** — scraping + Whisper
12. **GitHub ingestion** — repos, docs, discussions

### Phase 3: Intelligence (weeks-months)
13. **Natural language Q&A** — "answer from my corpus"
14. **Entity extraction** — named entities from all text
15. **Cross-source synthesis** — "video + Reddit post + article = insight"
16. **Trend detection** — topic evolution over time
17. **Contradiction detection** — where sources disagree
18. **Notification system** — alerts for new content, trends

### Phase 4: Social & Integration (ongoing)
19. **Slack/Discord integration** — digests, search
20. **Notion/Obsidian sync** — knowledge export
21. **REST API** — programmatic access
22. **Email/newsletter ingestion** — index important emails
23. **Twitter/X ingestion** — if API cost is acceptable
24. **Knowledge graph** — entity relationships
