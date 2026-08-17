# Evidence Fabric — Phase A discovery findings

Phase: A (amendment v1.1 §15) · Date: 2026-08-16 · Agent: zcode · Branch: `evidence-fabric`
A-0 evidence: `a0_smoke_receipt.json` (9/9 checks, this directory)

---

## 1. Actual source/data/control-flow map (measured, not assumed)

Authority layer (verified by direct inspection 2026-08-16):

| Store | Role | Cardinality (live) |
|---|---|---|
| `P:/.data/yt-is/transcripts.sqlite` → `transcript_cache` | transcript TEXT authority | 75,706 rows = 75,706 distinct videos (grows ~20K/day while pipeline runs) |
| `P:/.data/yt-is/batch_status.sqlite` → `analysis_status` | video metadata | 346,644 (75,032 complete) |
| `batch_status.sqlite` → `channel_metadata` | channel provenance | 2,865 |
| `batch_status.sqlite` → `video_catalog` | discovery catalog | 343,119 |
| `transcript_cache` sources | notebooklm 75,071 · ytdlp 436 · selenium 112 · whisper 83 · test 1 | |

Control flow today: pure ingestion. `run_intake_pipeline.py` (Phase 0 cleanup → 0.5 safety → 1 sync → 2 verify → 3 fetch supervisor → 4 storage verify). No retrieval path exists anywhere (see §6).

Decoys verified: `P:/.data/yt-is/yt-is.db`, `transcript_cache.sqlite`, `state.sqlite` are empty; repo-root `transcripts.sqlite`/`batch_status.sqlite` are 0-byte copies. Older session summaries that placed `transcript_cache` inside batch_status.sqlite were wrong; operator's "assume nothing about the DB" directive was correct.

## 2. Component classification (full inventory by subagent, 2026-08-16)

Retrieval-relevant:

| Component | Classification | Rationale |
|---|---|---|
| `ef/*` (this branch) | KEEP | the fabric |
| `csf/cache.py` | KEEP | stays the sole WRITER of transcript_cache; fabric is RO consumer |
| `bin/csf-transcripts` | EXTEND | point-read CLI today; `ef-query` joins as sibling command surface |
| `csf/title_bridge.py` | KEEP | orthogonal (ID resolution, not content search) |
| `test_nlm_query.py` (repo root) | ADAPT→reference | only existing "semantic query" (remote NLM); move to dev/ eventually |
| `check_cache.py` (repo root) | DELETE AFTER PARITY | stale DB path; superseded by verify_transcript_storage.py |
| repo-root empty `*.sqlite` | DELETE | 0-byte decoys |
| everything else (fetch chain, briefing, verify, categorize…) | KEEP | no retrieval overlap |

## 3. Measured corpus cardinality + projections

- Sample: 2,000 transcripts → 3,878 chunks → **1.94 chunks/transcript**, avg chunk 1,080 chars (median), tail chunks up to 6K.
- **Projection: 68,596 eligible transcripts → ~133,000 chunks, ~0.81 GB text.**
- **7,110 transcripts (9.3%) lack title/channel provenance** (`--` video IDs et al.) — excluded by EU contract. Options for Phase C: backfill via existing `register_orphan_transcripts.py` mechanics, or skip. Never fabricate provenance. Decide at C.
- Future corpus: video_catalog holds 343K videos; if fully fetched → ~665K chunks. Capacity plan must size for this (§4).

## 4. Measured hardware/model feasibility (RTX 5070 12GB, pipeline running concurrently)

| Operation | Measured rate | Full-corpus (133K chunks) | Future (665K) |
|---|---|---|---|
| MiniLM dense (384d, batch 32-128) | ~830 chunks/s | ~2.7 min | ~13 min |
| client BM25 fit+encode | ~2,040 chunks/s | ~1.1 min | ~5.4 min |
| Qdrant local upsert | 281 pts/0.1s | minutes | <1h |
| hybrid RRF query latency | 60-70 ms @ 281 pts | re-measure at scale (Phase B) | — |

VRAM: 3.9/12.2 GB in use during measurement (browser + pipeline) → ~8 GB free. 4B-class fp16 embedding (~8GB) is marginal while the fetch pipeline runs; 0.6B-class fits easily. Phase B measures both for real; scheduling 4B runs when the pipeline is idle is an option, not a blocker.

Vector memory projections: MiniLM 384d ≈ 0.2 GB · 0.6B-class 1024d ≈ 0.5 GB · 4B 2560d ≈ 1.3 GB (float32, 133K pts; ×5 for future corpus — all fit in RAM; on-disk quantization available if needed).

## 5. Integration seams (from /wiki and /www skill inspection)

**A canonical retrieval CLI already exists to copy:** `P:/.agents/scripts/wiki_search.py` — `search|info|add` subcommands, `--format text|json` with **json default**, exit 0 on empty results / exit 2 on usage error, sqlite `mode=ro`. The /www skill's subagent rule (subagents get no MCP) makes a CLI — not MCP — the mandatory first surface. Spec (build in B/C):

```
python P:/packages/yt-is/bin/ef-query "<query>" --top-k N [--channel-id X] [--format json|text]
# json: array of EvidenceResult.to_json() + qmd-compat keys (file, chunk_ref)
# text: "- [[title]] / snippet / url" mirroring search_wiki MCP rendering
# exit 0 (empty ok) / 2 usage
```

- **/wiki seam:** evidence→`RELEVANT`, contradiction→`CONTRADICTORY`, staleness→freshness compare (`captured_at` vs wiki `last_verified`). /wiki already has contradiction-scan + half-life mechanics to plug into.
- **/www seam:** three injection points — Phase 1b "what do we already know" (corpus before web), Round 3 disconfirmation (corpus as counter-evidence), Phase 2b practitioner signal (local corpus instead of live yt-dlp). Citation format needs `url` + `score` (both already in EvidenceResult).
- **Visual pipeline seam:** `visual_jobs` = 5,652 queued, `visual_status`/`visual_artifacts` empty (extraction not yet running). EU `media_kind` is the only extension point needed — no conflict, complementary per amendment §13.

## 6. Conflicts between live implementation and design

1. **"Do not replace FTS5" targets a vacuum.** No FTS5/LIKE text search exists anywhere in yt-is (verified: no `CREATE VIRTUAL TABLE`, no search subcommand). The fabric replaces nothing; it fills absent capability. Zero parity burden, zero migration risk.
2. **Timestamp reopen → char-offset reopen.** Cached transcripts carry no timestamps (verified by sampling). EU provenance = `[start_char, end_char)` (D005). Round-trip exactness proven in A-0.
3. **Qdrant server → Qdrant local mode.** No Docker/WSL on host. Local mode verified to support sparse vectors + hybrid RRF (probe receipt in DECISIONS.md D002). Same client API; server promotion later = config change.
4. **150K-corpus assumption → 75.7K today, 343K ceiling.** Original design's "150K documents" was neither today's nor the ceiling's number. Capacity gates must use 665K-chunk future sizing.
5. **`channel_metadata.channel_id` is not indexed** — full-corpus EU build join needs an index (authority is RO; index must go on the WRITER side or a build-time copy). Phase C blocker to resolve, noted here.

## 7. Proposed BuildSpec + generation/promotion contract

```
docs/evidence-fabric/buildspec.json  (git-tracked, immutable per generation)
{
  "generation": 1,
  "authority": {"transcripts_db": "...sha256 of db identity + row count at build start",
                 "status_db": "..."},
  "eligibility": {"min_chars": 100, "require_provenance": true},
  "chunker": {"target_chars": 1100, "overlap_chars": 150, "min_chars": 200},
  "dense_model": {"id": "<phase-b winner>", "dim": N, "normalize": true},
  "sparse": {"kind": "bm25-lucene", "k1": 1.2, "b": 0.75, "vocab_size": N},
  "projection": {"engine": "qdrant-local", "collection": "evidence_chunks__gen1",
                  "dense_on_disk": false}
}
```

Rules:
- **Immutable:** a generation's spec never changes after build starts; edits bump `generation`.
- **Single writer:** Qdrant local holds an exclusive lock (D008) — exactly one builder process per generation; shards (EU-id ranges) are its internal work units.
- **Promotion:** `ef-promote --generation N` atomically rewrites `P:/.data/yt-is/ef/promotion.json` (single promotion authority); queries resolve the active generation from that pointer. Previous generation retained until parity check passes, then deletable.
- **Rebuildability:** any generation reproducible from authority + spec (deterministic `video_id asc` ordering). Deleting `ef/qdrant_local/*` + rebuilding loses nothing.

## 8. A-0 evidence

`a0_smoke_receipt.json`: 200 EUs → 281 chunks → 281 points; authority-join, catalog, dims, counts, queries, **exact-span round-trip**, URL format — 9/9 PASS. Dense 0.6s on cuda; total 8.8s. Sample queries return in 60-70 ms. (Retrieval quality deliberately not evaluated at A-0.)

## 9. Evidence that should change the target architecture

**None found.** All five conflicts above are accommodated without architecture change (they change deployment mode, provenance representation, and sizing inputs — all already absorbed). Two Phase-B checkpoints carry the remaining risk:
1. Qdrant local-mode query latency at 100K-665K points (measure on real index before C).
2. 4B-class model VRAM contention with the fetch pipeline (measure; schedule if marginal).

---

Provenance: agent: zcode · host: both · measurements reproducible via `scripts/ef_smoke_a0.py` and `P:/tmp/ef_meas_throughput.py` (scratch) on branch `evidence-fabric`.
