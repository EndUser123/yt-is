---
agent: zcode
host: both
created: 2026-08-22
verified: live (read-only endpoints)
---

# Warm Query Service HTTP API (:6391)

The yt-is Evidence Fabric (EF) warm query service is a Python
`ThreadingHTTPServer` (`ef/warm_query_service.py`) that keeps one warm
`ProductionQuery` instance (BGE-M3 dual embedding + Qdrant + SQLite catalog)
resident in memory, eliminating the ~5-15s model cold-start that per-invocation
`ef-query` paid. It listens on `http://127.0.0.1:6391` and serves both the HTTP
API documented here and the search UI at `/` from the same origin (required so
a browser page can call the API without hitting Chromium's Private Network
Access rules). **Do not confuse ports:** `6391` is this warm query service;
`6390` is the Qdrant vector server it queries as a backend.

Service facts:

| Property | Value |
|---|---|
| Implementation | `ef/warm_query_service.py` (`ThreadingHTTPServer`, `Handler`) |
| Bind address | `127.0.0.1:6391` (port via env `YTIS_EF_QUERY_PORT`) |
| PID file | `P:/.data/yt-is/ef/query-service.pid` |
| Warm-up | Model loads in a background thread; `/health` returns 503 until warm |
| CORS | Every response carries `Access-Control-Allow-Origin: *` and `Access-Control-Allow-Private-Network: true` |
| Request logging | Suppressed (`log_message` is a no-op) |

Endpoint classes used below: **READ-ONLY** (no state change, no external
calls), **LLM-COSTLY** (calls an external LLM provider chain), **MUTATING**
(changes files or database rows; do not call casually).

## Endpoint summary

25 paths total: 18 GET, 7 POST, plus an OPTIONS catch-all.

| Method | Path | Class | Response |
|---|---|---|---|
| GET | `/` | READ-ONLY | HTML (search UI) |
| GET | `/search` | READ-ONLY | HTML (alias of `/`) |
| GET | `/health` | READ-ONLY | JSON |
| GET | `/query` | READ-ONLY | JSON (default) or text |
| GET | `/library` | READ-ONLY | JSON |
| GET | `/reopen` | READ-ONLY | JSON |
| GET | `/topics` | READ-ONLY | JSON |
| GET | `/trends` | READ-ONLY | JSON |
| GET | `/digest` | READ-ONLY | HTML |
| GET | `/home` | READ-ONLY | HTML |
| GET | `/entities` | READ-ONLY | HTML |
| GET | `/sources` | READ-ONLY (page) | HTML |
| GET | `/status` | READ-ONLY | HTML |
| GET | `/review` | READ-ONLY | HTML (503 if not generated) |
| GET | `/reddit` | READ-ONLY | HTML |
| GET | `/discord` | READ-ONLY | HTML |
| GET | `/ask` (no `q`) | READ-ONLY | HTML |
| GET | `/ask?q=...` | LLM-COSTLY | JSON |
| GET | `/candidates/approve` | MUTATING | 302 redirect (currently broken, see notes) |
| POST | `/ingest-extension` | MUTATING | JSON status |
| POST | `/sources/rss/add` | MUTATING | JSON |
| POST | `/sources/rss/remove` | MUTATING | JSON |
| POST | `/sources/podcast/add` | MUTATING | JSON |
| POST | `/sources/podcast/remove` | MUTATING | JSON |
| POST | `/sources/reddit/add` | MUTATING | JSON |
| POST | `/sources/reddit/remove` | MUTATING | JSON |
| OPTIONS | any | — | 204 (CORS preflight) |

Unknown GET or POST paths return `404` JSON `{"error": "not found"}`.

## READ-ONLY JSON endpoints (live-verified 2026-08-22)

All examples below are real responses captured with `curl -s -m 10/90` this
session, truncated.

### GET /health

Readiness probe. Returns `{"status": "ready", "model": "warm"}` once the
BGE-M3 model is loaded; `503` with `{"status": "warming", "error": ...}` while
loading. No parameters. Implemented in `Handler.do_GET` (inline).

```json
{
 "status": "ready",
 "model": "warm"
}
```

### GET /query

Semantic search over the corpus. The dominant API endpoint. Note the limit
parameter is **`top_k`**, not `limit`.

Parameters:

| Param | Type | Default | Notes |
|---|---|---|---|
| `q` | string | required | query text; missing → `400 {"error": "missing q parameter"}` |
| `top_k` | int | 8 | result limit |
| `channel_id` | string | none | restrict to one YouTube channel |
| `format` | string | `json` | `text` returns a plain-text bullet list instead |
| `federation` | string | `on` | `off` disables CHS conversation-history federation |

Returns `{"results": [...]}`. Corpus rows carry `chunk_id`, `video_id`,
`title`, `snippet` (truncated to 8000 chars), `score`, `retrieval_paths`,
`url`, `source_type: "corpus"`, and — only when the span is reopen-safe
(valid char range, ≤ 64K) — reopen provenance: `eu_id`, `channel_id`,
`channel_title`, `start_char`, `end_char`. With federation on (default), up to
3 BM25 hits from the CHS chat-history DB (`P:/.data/chs/chat_history.db`,
read-only) are appended as `source_type: "conversation"` rows (score 0.05,
`retrieval_paths: ["conversation_fts"]`, no reopen provenance). Internal
errors → `500 {"error": ...}`. Implemented in `Handler.do_GET` (inline);
federation in module function `_chs_search`.

```json
{
 "results": [
  {
   "chunk_id": "9YkeTW6p3bo:transcript#00002",
   "video_id": "9YkeTW6p3bo",
   "title": "Integrating PowerShell with OpenAI's New AI Assistant Technology - Doug Finke",
   "snippet": "h takes a file ID perfect so let's give it the file ID ...",
   "score": 0.8333334,
   ...
```

### GET /library

Read-only library membership check for one video: does a transcript EU exist
in the fabric catalog (`eu_id = "<video_id>:transcript"`). Returns presence
and provenance only, never transcript content. Used by the browser extension
header state.

Parameters: `video_id` (string, required, ≤ 64 chars; missing/invalid →
`400 {"error": "missing or invalid video_id"}`). Response is
`{"video_id", "status": "in_library" | "not_found", "eu_id",
"transcript_chars", "transcript_source", "cached_at"}`. Implemented in
`Handler.do_GET` (inline) calling module function `library_lookup`.

```json
{
 "video_id": "9YkeTW6p3bo",
 "status": "in_library",
 "eu_id": "9YkeTW6p3bo:transcript",
 "transcript_chars": 45856,
 ...
```

### GET /reopen

Exact authoritative span reopen: fetches the literal transcript substring for
a span referenced in `/query` provenance. **Read-only** — despite the name it
mutates nothing; `reopen_exact` opens both the catalog and the transcripts DB
with `mode=ro` and only reads. Fails closed: the returned `text` length is
exactly `end_char - start_char` or the request 404s.

Parameters: `eu_id` (string, required, ≤ 256), `start_char` (int ≥ 0),
`end_char` (int > start, span ≤ 64K). Malformed → `400`; unknown EU/span →
`404 {"error": "evidence unit or authority span not found"}`. Implemented in
`Handler.do_GET` (inline) calling module function `reopen_exact`.

```json
{
 "eu_id": "9YkeTW6p3bo:transcript",
 "video_id": "9YkeTW6p3bo",
 "start_char": 36784,
 "end_char": 36799,
 "text": "test.png let's "
}
```

### GET /topics

Top 10 topic clusters by member count from the catalog
(`P:/.data/yt-is/ef/catalog.sqlite`, read-only). No parameters. Returns
`{"topics": [{"label", "videos"}, ...]}`. Implemented in `Handler.do_GET`
(inline sqlite query).

```json
{
 "topics": [
  {
   "label": "Cowork Skills Hours",
   "videos": 2498
  },
  ...
```

### GET /trends

Topic momentum over three windows (24h, 72h, 7d): topics with the most newly
assigned chunks and the biggest percent change vs the preceding equal-length
window (floored at 25 chunks minimum volume; `pct: 999.0` marks a topic with
no baseline). No parameters. Implemented in module function `_topic_trends`.

```json
{
 "24h": {
  "most_new": [],
  "biggest_change": []
 },
 "72h": {
  ...
```

### Error and fallback responses (live-verified)

`GET` an unknown path → `404`:

```json
{
 "error": "not found"
}
```

`GET /query` without `q` → `400`:

```json
{
 "error": "missing q parameter"
}
```

## READ-ONLY HTML pages (live-verified 2026-08-22, confirmed 200 + text/html)

All render HTML server-side from read-only sqlite connections. They are
browser pages, not APIs.

- **GET /** and **GET /search** — the search UI. Serves `docs/search.html`
  from the repo so the page shares the API origin (file:// pages are blocked
  by Chromium PNA rules). 17592 B. Implemented in `Handler.do_GET` (inline).
- **GET /digest** — daily brief + 7-day rolling view, computed live at request
  time (today/week channel tables, per-day counts, top Reddit posts, code
  artifacts). Slow: ~12s live. `_render_digest_page`.
- **GET /home** — unified glance dashboard: corpus numbers, 24h topic
  momentum, trend alerts, source cards, and the corpus-breadth candidate
  panel with approve links (which point at the mutating
  `/candidates/approve`). ~9s live. `_render_home_page`.
- **GET /entities** — named entities across the corpus with chunk counts,
  type breakdown, and per-topic key entities; clicking an entity searches it.
  `_render_entities_page`.
- **GET /sources** — source management page listing RSS feeds, podcasts, and
  subreddits. The page itself only reads (`mode=ro`); its Add/Remove buttons
  call the mutating POST endpoints below. `_render_sources_page`.
- **GET /status** — pipeline health: transcripts complete/pending/failed,
  Qdrant chunk count, index watermark age, indexer daemon liveness,
  connector doc counts. Auto-refreshes every 60s. `_render_status_page`.
- **GET /review** — the YouTube channel review page, served from
  `P:/.logs/channel_review/review.html` (1.6 MB live). Returns `503` with a
  "run: ytis review" hint when the file has not been generated.
- **GET /reddit** — tracked subreddits with stored post counts and latest
  sync time. `_render_source_page("reddit")`.
- **GET /discord** — tracked Discord channels (guild, batches, last sync) or
  an empty-state panel. `_render_source_page("discord")`.
- **GET /ask** (without `q`) — the ask page shell only: an input box whose
  JavaScript calls `/ask?q=...`. No LLM call unless `q` is present.
  `_render_ask_page`.

## LLM-COSTLY endpoint — documented from code, NOT called this session

### GET /ask?q=...

With a non-empty `q` parameter, runs retrieval-augmented answering: retrieves
context via `ef/qa.py:retrieve`, then walks an external LLM provider chain.
Default order `codex → agy → openrouter → gemini` (configurable via the
`YTIS_QA_PROVIDERS` env var; first successful provider wins; all-fail returns
an "All providers failed" answer with `provider: "none"`). Response JSON keys:
`answer`, `sources` (cited results with title/url/snippet), `provider`.
Implemented in `Handler.do_GET` (inline) calling `ef.qa.answer`. Every call
with `q` can spend external LLM quota — do not probe blindly.

## MUTATING endpoints — use with care (documented from code only, NOT called this session)

### GET /candidates/approve

A **GET that mutates**: rewrites `P:/.data/yt-is/ef/channel-candidates.json`,
setting `status: "approved"` on one candidate (`?name=<name>`) or all
(no/empty name), stamps `approved_by`, then redirects 302 to `/home`.
Linked directly from the /home candidate panel. Failures → `500` text.
Implemented in `Handler.do_GET` (inline).

Code observation (structurally verified by reading, not runtime-tested): the
handler calls `time.strftime(...)` but `time` is never imported in this
module, so the `NameError` is caught and the endpoint returns
`500 "approve failed: name 'time' is not defined"` before the file write —
as written, the approval never persists. Approving by editing the JSON
directly (the fallback the /home panel also documents) is currently the
reliable path.

### POST /ingest-extension

Idempotent single-video transcript ingestion from the browser extension.
Request: JSON body with `videoId` (required, `[A-Za-z0-9_-]{1,64}`),
`segments` (required, non-empty list ≤ 20000 of objects with `text`),
optional `provider` (default `"extension"`), `title`, `url`. Body size cap
8 MB. Writes a new row to `transcript_cache` in the transcripts authority
(`P:/.data/yt-is/transcripts.sqlite`); the incremental EF indexer projects it
into catalog/chunks/Qdrant on its next cycle. Responses: `200 {"status":
"saved", "transcriptChars": N}` for new, `200 {"status": "already_present",
...}` when any provider's transcript exists, `400 {"error": "malformed ingest
request"}`, `409 {"error": "existing_transcript_differs"}` when the same
cache_key has different content. Implemented in `Handler.do_POST` (inline)
calling module function `ingest_extension`.

### POST /sources/rss/add

Adds an RSS feed to track. Query param `url` (must start with `http`, else
`400 {"error": "url required"}`). `INSERT OR IGNORE` into the `rss_feeds`
table of `P:/.data/yt-is/batch_status.sqlite` (creates the table if absent).
Feeds sync daily at 06:00 or via `ytis rss`. Returns `200 {"ok": true}`.

### POST /sources/rss/remove

Removes an RSS feed. Query param `url`. `DELETE FROM rss_feeds`. Returns
`200 {"ok": true}`.

### POST /sources/podcast/add

Adds a podcast feed. Query params `url` (required, must start with `http`)
and `name` (defaults to the URL). `INSERT OR IGNORE` into `podcast_feeds`.
Episodes are transcribed locally (Whisper). Returns `200 {"ok": true}`.

### POST /sources/podcast/remove

Removes a podcast feed. Query param `url`. `DELETE FROM podcast_feeds`.
Returns `200 {"ok": true}`.

### POST /sources/reddit/add

Adds a subreddit to track. Query param `name` (`r/` prefix stripped; empty →
`400 {"error": "name required"}`). `INSERT OR IGNORE` into
`reddit_subreddits`. Returns `200 {"ok": true}`.

### POST /sources/reddit/remove

Removes a subreddit. Query param `name` (`r/` prefix stripped). `DELETE FROM
reddit_subreddits`. Returns `200 {"ok": true}`.

All six `/sources/*` POST endpoints are implemented inline in
`Handler.do_POST` and write to `P:/.data/yt-is/batch_status.sqlite`. The /home
and /status pages render what these endpoints control.

## OPTIONS (CORS preflight)

`do_OPTIONS` answers any path with `204`, `Access-Control-Allow-Methods: GET,
OPTIONS`, `Access-Control-Allow-Origin: *`, and
`Access-Control-Allow-Private-Network: true`. Note it does not advertise
POST, though POST endpoints accept requests anyway (no method filtering).

## Verification appendix

Live-verified 2026-08-22 (~16:32-16:35 UTC) against the running service with
curl: `/health`, `/query?q=test&top_k=2`, `/library`, `/reopen`, `/topics`,
`/trends`, the 404 fallback, the `/query` missing-`q` 400, and the HTML pages
`/`, `/search`, `/digest`, `/home`, `/entities`, `/sources`, `/status`,
`/review`, `/reddit`, `/discord` (all 200 text/html; /digest ~12s and /home
~9s because they compute live). Not called this session, documented from code
only: `/ask?q=...` (LLM cost), `/candidates/approve` and all seven POST
endpoints (mutating). JSON examples above come solely from actual responses
captured this session; mutating/LLM endpoints carry no examples.
