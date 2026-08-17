# Phase B preregistration — model-selection rules (committed before any results)

Committed 2026-08-16 on branch `evidence-fabric` BEFORE any model comparison
was run. Per amendment v1.1 §8: rules are executable and thresholds fixed in
advance, so results cannot be post-hoc rationalized.

## Candidates

| Tier | Model | Params | Dim | Role |
|---|---|---|---|---|
| baseline | all-MiniLM-L6-v2 | 22M | 384 | A-0 plumbing model |
| 0.6B-class | BAAI/bge-m3 | 568M | 1024 | mid tier |
| 4B-class | Qwen/Qwen3-Embedding-4B | 4B | 2560 | top tier |

All runs use the same corpus sample, chunker, BM25 sparse leg, RRF fusion;
ONLY the dense model differs. Queries use each model's documented query
prefix (Qwen3: "query: " prefix; others: none).

## Benchmark

- Corpus: 3,000-transcript stratified sample (by channel category, capped),
  deterministic `video_id asc` ordering → ~6K chunks.
- DECISION tier (240 queries, automated): title-as-query and
  description-as-query, stratified across categories (10/category).
  Positive: same video (rel 1.0); graded: same category (rel 0.3).
- SMOKE tier (30 queries): hand-authored natural questions written from
  sampled transcript chunks by the agent BEFORE seeing any ranking results.
  Positive: source video.
- Metrics: Recall@5, Recall@20, MRR@10, nDCG@10 (graded).

## Decision rules (executable, fixed)

1. **0.6B over baseline** iff: nDCG@10(decision) ≥ baseline + 0.05
   AND Recall@20(decision) ≥ baseline + 0.05.
2. **4B over 0.6B** iff: nDCG@10(decision) ≥ 0.6B + 0.03
   AND Recall@20(decision) ≥ 0.6B + 0.02
   AND measured p95 query latency ≤ 2.0 s (interactive budget, §9)
   AND projected full-corpus embed time ≤ 4 h (measured rate × 133K chunks).
3. **Reranker stage evaluated** iff: best model Recall@20(decision) < 0.85.
4. Ties (within 0.01 on both primary metrics) → smaller model wins.
5. At-scale checkpoint: full-corpus (gen0 throwaway) MiniLM hybrid query
   p95 ≤ 500 ms at ~133K points, else Qdrant local mode flagged for C
   redesign before any canonical build.
6. If the 4B model cannot load on GPU alongside the fetch pipeline,
   measure on CPU; the latency rule (≤2.0 s p95) still applies — no
   exemptions for environmental constraints.

## Known limits (declared up front)

- Title/description-as-query inflates lexical leg (BM25); this biases ALL
  models equally in the hybrid setting, and the smoke tier (natural
  questions) serves as the human-shaped counterweight.
- Category labels come from the LLM/manual classifier — weak graded truth,
  adequate for ranking models, not absolute ground truth.
- Sample scale (3K transcripts) favors no model in particular; absolute
  numbers will shift on the full corpus (regression tier re-runs on real
  builds in Phase C+).

agent: zcode · host: both
