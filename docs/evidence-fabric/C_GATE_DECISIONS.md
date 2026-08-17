# Evidence Fabric — C-Gate Decision and Authorization

Recorded verbatim from operator, 2026-08-17 (source: session message;
preserved as the binding Phase-C authorization).

1. BGE-M3 accepted as the canonical Phase-C text encoder. Use dense +
   learned-sparse representations. Preserve the preregistered
   PROMOTION=BLOCKED result as historical truth; this is a subsequent
   operator decision based on the total evidence and recognition that
   exact-token safety belongs to the retrieval layer, not dense-model
   selection. Qwen3-4B remains rejected.

2. Qdrant server accepted as the production projection engine. Use the
   native Windows v1.19.x server on dedicated yt-is-owned ports 6390/6391,
   dedicated config/storage, and PID-owned lifecycle. Never touch another
   Qdrant instance or kill by image name.

3. Index 7,109 incomplete-metadata transcripts. Quarantine the single
   unreopenable fixture. Never fabricate missing metadata.

4. Phase C is unblocked, including full-current-corpus BGE-M3 dense +
   learned-sparse backfill. Keep indexing off the fetch critical path.

5. Change the planned normal query path: Qdrant BGE dense + BGE
   learned-sparse is the default hybrid candidate. Do not synchronously
   include FTS5 in every query merely for the ~0.0004 nDCG gain observed
   from C→D.

6. Retain FTS5 as an exact-token baseline/fallback until explicitly
   displaced. Build ≥50 identifier development cases plus ≥50 untouched
   identifier acceptance cases. Compare BGE learned sparse, Qdrant BM25,
   FTS5, and justified combinations. Solve exact-token performance in this
   layer.

7. Before active-generation promotion, replay the complete production
   candidate — BGE dense + learned sparse + Qdrant server + final exact
   lane + final fusion — against the benchmark and latency/reopenability/
   filter acceptance tests. If it passes, promote atomically without
   another operator gate. If it fails, STOP and return the discriminating
   failure evidence.

8. Do not add BGE-M3 multi-vector/ColBERT storage or a reranker during
   this phase. Establish the production dense+sparse baseline first; those
   remain subsequent measured improvement candidates.

agent: zcode · host: both
