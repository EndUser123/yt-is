# yt-is Feature Roadmap — Prioritized by Value

> Generated: 2026-08-16 · agent: zcode · scope: all discussed features from session + research + existing handoffs

## Phase 0: Running now (no action needed)

| # | Feature | Status | ETA |
|---|---|---|---|
| 0.1 | Transcript fetch pipeline (157K active pending) | Running at tested optimum (2,214 VPH) | ~3 days |
| 0.2 | Morning briefing + health watcher | Automated, daily | Ongoing |
| 0.3 | Pre-flight safety checks | 8 checks, all passing | Ongoing |
| 0.4 | State backup (daily + off-site) | Automated at 03:30 | Ongoing |

---

## Phase 1: Unlock the data (after transcripts are fetched)

### 1A. Semantic search over transcripts
**Value:** You have 60K+ transcripts in SQLite with no way to search by meaning. This is the single highest-value feature — it turns a data dump into a usable knowledge base.

**What it does:** Type "how to implement a rate limiter" → get the 5 most relevant transcript chunks across your entire corpus, with video title, channel, and timestamp.

**How:** ChromaDB + local embeddings (sentence-transformers or Ollama). Zero API cost. Reads from existing `transcripts.sqlite`.

**Effort:** ~2 hours (script + CLI command)
**Depends on:** Nothing (transcripts already exist)

### 1B. Chat with your videos (RAG)
**Value:** "Ask any question, get answers grounded in your transcript corpus with video citations." Replaces NotebookLM for Q&A. Private, local, no Google dependency.

**How:** Same embeddings as 1A + a retrieval + generation loop. Can use local LLM (Ollama) or API (ZAI/Gemini).

**Effort:** ~4 hours
**Depends on:** 1A (embeddings)

### 1C. Transcript quality scoring
**Value:** Know which transcripts are actually good vs garbage. Currently only length-based. With embeddings: information density, topic coherence, and "is this actually about what the title claims" detection.

**How:** Embed transcript, compare against channel's other content and the channel's classified category. Low similarity = quality flag.

**Effort:** ~2 hours
**Depends on:** 1A (embeddings)

---

## Phase 2: Extract visual artifacts (code, diagrams, charts)

### 2A. Visual pipeline connection (U-05 + U-07)
**Value:** Connect the transcript pipeline to the visual pipeline. When a transcript is cached, automatically queue visual analysis. Without this, 5,652 visual jobs sit queued forever.

**What it does:** Transcript completes → cheap filter (keywords + category) → if code/diagram signal → queue visual job → OCR frame extraction → if code detected → promote to Gemini full-video.

**How:** Implement U-05 (worker pool split) + U-07 (cache-hit enqueue). The providers (`ocr_clip_provider`, `profiles.py`) already exist.

**Effort:** ~6 hours
**Depends on:** Phase 1 (at least 1A for the cheap filter)

### 2B. OCR + CLIP standard profile (zero API cost)
**Value:** First-pass visual analysis at zero cost. Extracts frames, runs EasyOCR (or PaddleOCR) for code detection, CLIP for visual tagging. Decides which videos warrant expensive Gemini analysis.

**How:** Already designed (U-03 shipped). Just needs workers (U-05) and enqueue (U-07).

**Effort:** ~2 hours (mostly wiring)
**Depends on:** 2A

### 2C. Gemini full-video artifact extraction
**Value:** "Ask an AI to create all the artifacts shown" — extract actual code files, diagram descriptions, chart data from videos that show them. This is the operator's stated goal.

**What it does:** For videos where OCR found code, send the full video (or key frames) to Gemini's [video understanding API](https://ai.google.dev/gemini-api/docs/video-understanding) with prompts like "extract all code shown as clean, runnable files" and "describe every diagram as structured data."

**How:** Gemini API (needs working key — current keys invalid). Uses the `visual` profile already designed.

**Effort:** ~4 hours
**Depends on:** 2B (OCR promotion), Gemini API key

### 2D. Artifact storage and retrieval
**Value:** Extracted artifacts (code files, diagrams) stored alongside transcripts, searchable and exportable. `visual_artifacts` table already exists (empty).

**How:** Implement U-06 (idempotent `publish_artifact`). Artifacts keyed by `(video_id, artifact_type, content_hash)`.

**Effort:** ~2 hours
**Depends on:** 2C (or 2B for OCR-level artifacts)

---

## Phase 3: Classification and intake improvements

### 3A. Embedding-based classification validation
**Value:** Catch misclassified channels by comparing what the channel TALKS ABOUT (embedded transcripts) vs what its metadata CLAIMS (title/description). Would have caught Jasmin Laine (Politics ≠ News) automatically.

**How:** Embed sample transcripts from each channel → compare embedding distance to the channel's classified category → flag high-distance channels as suspects.

**Effort:** ~3 hours
**Depends on:** 1A (embeddings)

### 3B. Content deduplication
**Value:** Many channels cover the same topics. Identify videos that explain the same concept → prioritize the most comprehensive → skip redundant fetches. Save 10-20% of quota.

**How:** Embedding cosine similarity between transcript chunks → cluster → identify near-duplicates.

**Effort:** ~2 hours
**Depends on:** 1A (embeddings)

### 3C. Discovery relevance scoring
**Value:** When new channels arrive from Watch Later / History, score their relevance to your existing corpus. "This channel is 87% similar to your AI/ML content" or "this is unlike anything you have."

**How:** Embed 3-5 sample transcripts from the new channel → compare against existing corpus centroid.

**Effort:** ~2 hours
**Depends on:** 1A (embeddings)

### 3D. Provider recording (red-team B2)
**Value:** Know which LLM provider classified each channel. Eliminates the "provider lottery" — same channel, different answer depending on which provider responded.

**How:** Record provider name in `category_source` or a sidecar column.

**Effort:** ~1 hour
**Depends on:** Nothing

### 3E. Provenance recovery (red-team B1)
**Value:** Fix the retro-marked `manual` classifications that lie about who decided. Introduce `legacy` as a third source value.

**How:** Script to relabel retro-marked rows, recovering true-manual from agent-pass JSON + apply receipts.

**Effort:** ~1 hour
**Depends on:** Nothing

---

## Phase 4: Operational enhancements

### 4A. Real-time alerting (not just morning briefing)
**Value:** Know immediately when the pipeline fails, auth expires, or success rate drops. Currently problems are caught at the morning briefing — up to 8 hours of silent failure.

**How:** Schedule `pipeline_health_watch.py` every 5 minutes via Task Scheduler. On alert: write to a file the operator checks, send a Windows notification, or write to a log a monitor watches.

**Effort:** ~1 hour (Task Scheduler + notification)
**Depends on:** Nothing

### 4B. NotebookLM circuit breaker
**Value:** When source-adds fail repeatedly (RPC9), pause the worker instead of continuing to hammer the API. Prevents wasted quota and cascading failures.

**How:** Track consecutive source-add failures per account. Above threshold → pause that account's workers for N minutes → alert.

**Effort:** ~3 hours
**Depends on:** Nothing

### 4C. yt-dlp breakage detection
**Value:** yt-dlp breaks when YouTube changes their UI. Detect it before processing thousands of videos into failures.

**How:** Pre-flight check: fetch one video's metadata. If it fails with a yt-dlp error, block the pipeline.

**Effort:** ~1 hour
**Depends on:** Nothing (add to preflight_safety.py)

### 4D. Backup restore testing
**Value:** Know backups actually work before you need them. Currently backups exist but restores are never tested.

**How:** Weekly scheduled test: read the latest backup, verify row counts match expectations.

**Effort:** ~1 hour
**Depends on:** Nothing

---

## Phase 5: Wiki-yt and knowledge integration

### 5A. Semantic linking between videos and wiki concepts
**Value:** The wiki-yt pipeline creates concept pages from transcripts. Embedding similarity could link related concepts across videos automatically.

**How:** Embed wiki concept pages + transcript chunks → find cross-references.

**Effort:** ~4 hours
**Depends on:** 1A (embeddings), wiki-yt pipeline

### 5B. Knowledge graph construction
**Value:** Build a graph of "video A mentions concept X, which is also in video B" from embedding similarity. Enables "show me everything about topic X" queries.

**How:** Cluster embeddings → identify concept nodes → link videos to concepts.

**Effort:** ~6 hours
**Depends on:** 1A, 5A

---

## Summary: dependency graph and recommended order

```
Phase 0 (running)
  └─→ Phase 1A: Semantic search (2 hrs) ← START HERE
        ├─→ 1B: Chat with videos (4 hrs)
        ├─→ 1C: Quality scoring (2 hrs)
        ├─→ 2A: Visual pipeline connection (6 hrs)
        │     └─→ 2B: OCR standard profile (2 hrs)
        │           └─→ 2C: Gemini artifacts (4 hrs)
        │                 └─→ 2D: Artifact storage (2 hrs)
        ├─→ 3A: Classification validation (3 hrs)
        ├─→ 3B: Content dedup (2 hrs)
        └─→ 3C: Discovery scoring (2 hrs)

Independent (can do anytime):
  3D: Provider recording (1 hr)
  3E: Provenance recovery (1 hr)
  4A: Real-time alerting (1 hr)
  4B: Circuit breaker (3 hrs)
  4C: yt-dlp detection (1 hr)
  4D: Backup testing (1 hr)
```

**Total estimated effort:** ~48 hours for everything
**Minimum viable enhancement:** 1A (2 hrs) gives you searchable transcripts
**Next big unlock:** 2A + 2B + 2C (~12 hrs) gives you extracted code/diagrams
