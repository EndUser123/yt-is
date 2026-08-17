# Phase B.1 preregistration — confirmatory model selection (committed before any B.1 run)

Per B_GATE_DECISIONS.md Decision 1. Committed 2026-08-16 BEFORE any B.1
evaluation ran. The B experiment stands as run: MiniLM retained per original
preregistered rule; this document defines the NEW confirmatory experiment.

## Holdout

- Fresh videos: sampled AFTER the frozen benchmark cap (videos 121-132 per
  category, video_id asc) — never present in any Phase B index.
- Corpus: frozen 2,579-video corpus ∪ fresh supplement (positives come
  exclusively from fresh videos; frozen corpus provides distractors).
- Queries: `benchmark/holdout_hand_queries.json` (89 agent-authored from
  printed excerpts, before any scoring) + `holdout_auto_queries.json`
  (25: 10 exact identifiers, 15 title lookups). Total 114.
- Relevance: binary, positive = source video (chunk-level for identifier
  stratum). No category-graded credit in B.1 (removes classifier noise).
- Retrieval: exact dense search (brute-force cosine, NO ANN) + FTS5 lexical
  + RRF fusion (k=60 standard RRF). Engine-neutral: model comparison must
  not depend on ANN recall.

## Strata weights — derived from expected production consumer mix (fixed before evaluation)

| Stratum | n | Weight | Rationale (production expectation, not performance) |
|---|---|---|---|
| ytis_natural | 30 | 0.30 | day-1 interactive search surface, heaviest expected use |
| wiki_evidence | 20 | 0.20 | amendment §6: evidence retrieval mandatory for /wiki |
| www_prior | 15 | 0.15 | /www phase-1b prior-evidence + phase-2b practitioner lookup |
| wiki_contradiction | 10 | 0.10 | amendment §6 contradiction/staleness mandatory |
| review_arch | 14 | 0.10 | implementation/precedent questions during reviews |
| title_entity | 15 | 0.10 | known-item lookup, real but secondary |
| exact_identifiers | 10 | 0.05 | small share BUT critical acceptance stratum |

Weights sum to 1.00. Primary weighted metric: **W-MRR@10**. Reported
alongside: W-nDCG@10, W-Recall@10.

## Configurations under test

- **A** MiniLM dense + FTS5 (lexical leg identical across all configs)
- **B** BGE-M3 dense + FTS5
- **C** BGE-M3 dense + BGE-M3 learned sparse (no FTS5)
- **D** BGE-M3 dense + BGE-M3 learned sparse + FTS5

Dense: MiniLM via sentence-transformers (512 max_seq); BGE-M3 via
FlagEmbedding BGEM3FlagModel (512 max_length, dense+sparse one pass).
Learned sparse leg: brute-force sparse dot product; fusion = RRF over
top-100 of each contributing leg.

## Decision rules (fixed)

R-B1.1 **Promote BGE-M3** iff ALL of:
  (a) W-MRR@10(B) − W-MRR@10(A) ≥ +0.03;
  (b) stratified paired bootstrap (10,000 resamples, resample queries within
      strata) 95% CI of per-query ΔMRR@10 excludes 0 on the positive side;
  (c) no critical-stratum regression: exact_identifiers Recall@10 drop
      ≤ 0.02 AND title_entity Recall@10 drop ≤ 0.05;
  (d) W-Recall@10 does not decrease by more than 0.01.
  Otherwise **retain MiniLM**.

R-B1.2 **Learned sparse adoption (advisory to operator, rule still fixed):**
  D over B iff W-nDCG@10(D) ≥ W-nDCG@10(B) + 0.02 with no
  exact_identifiers Recall@10 regression (≤0.02 drop). C is reported
  against B for the sparse-only question; no adoption of C alone.

R-B1.3 Throughput/VRAM recorded (already known feasible per Phase A); they
  inform but do not override R-B1.1 — cost difference is within budget
  either way (41 min vs 3 min full-corpus embed; 2.3 GB vs 0.2 GB VRAM).

R-B1.4 No re-running with modified queries/weights. If a defect is found in
  the harness, the fix and the re-run are both recorded in the receipt.

agent: zcode · host: both
