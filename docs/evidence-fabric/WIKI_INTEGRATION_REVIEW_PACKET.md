# /wiki Evidence Fabric Integration — operator review packet

L-gate deliverable. Three EF-backed maintenance modes run alongside the
unchanged wiki-native lookup for one operator review cycle.

## What was built

1. **`bin/ef-query`** — the consumer CLI seam (wiki_search.py
   conventions: positional query, --top-k, --format json default,
   exit 0 empty, exit 2 usage). Honors readiness (bounded 15s wait for
   warming; degraded -> graceful `{"status":"unavailable"}` result,
   never breaks the caller).

2. **`scripts/ef_wiki_maintenance.py`** — the three modes:

   - `evidence <claim>` — retrieval candidates with full provenance
     (url, video_id, char_span, eu_id, snippet, rank). Role label is
     `retrieved_candidate`, explicitly NOT "support" — /wiki owns that
     judgment.
   - `contradiction <claim> --existing-sources ...` — contrast-framed
     retrieval (two frames) + source-diversity demotion: hits from the
     claim's existing sources are flagged `same_source_as_claim` and
     excluded from the diverse set.
   - `staleness <claim> --last-verified ISO` — review-eligibility signal
     (`no_new_evidence` / `newer_material_candidate_needs_review` /
     `unknown_last_verified`); /wiki reopens candidates and compares
     captured_at itself — staleness is never auto-asserted.

3. **8 focused tests** (test_wiki_maintenance.py) — including the
   L-gate-mandated case: a rank-1 score-0.99 hit is NOT auto-labeled
   support or contradiction. Plus provenance retention, diversity
   filter, staleness signals, empty results.

4. **Degraded fallback verified live**: simulated degraded readiness →
   CLI exits 0 with `status: unavailable` + empty results → readiness
   restored. Native /wiki lookup path untouched throughout.

## Representative outputs (live, gen1)

- evidence / "ripgrep prunes dot-directories…": candidates from
  Grep-vs-vector-search and tooling videos; unvalidated roles; spans
  reopenable.
- contradiction / "quantization makes large models runnable…" with
  existing-source filter: diverse candidates from The Stack, Locally
  Hosted (non-echo channels).
- staleness / "qdrant local mode brute-force…" (last_verified
  2026-08-01): `newer_material_candidate_needs_review`, 3 candidates.

## Known integration gap (recorded, not blocking)

`EvidenceResult` does not carry captured/published timestamps — the
staleness mode returns candidates with reopen provenance and /wiki
compares timestamps after reopening (honest signal); a future
consumer-driven contract extension could surface timestamps in results.

## A/B protocol

Ordinary /wiki lookup: unchanged (wiki_search.py native path is
default). EF modes run alongside. Compare usefulness, provenance
quality, latency (~200-300ms warm), failure behavior after one review
cycle. Shards 04/05 untouched. No EF architecture changed.

agent: zcode · 2026-08-18
