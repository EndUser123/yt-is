# Graph bake-off — natural-language retrieval arm (packet P2, reopened 2026-08-22)

The CTE baseline (../wiki_traversal.py) settled the structured-traversal axis
(backlinks/wiki-handoff/evidence-chain latency, all PASS with headroom). The
falsifier-scope correction reopened the UNTESTED axis: natural-language
retrieval quality. This directory runs that arm.

## Question

"For a natural-language question over the wiki vault, does LightRAG retrieve
better context than the FTS5/CTE substrates we already run?"

## Arms

| Arm | What it is | Consumer it represents |
|---|---|---|
| fts_raw | wiki_search.py BM25, question verbatim | naive consumer |
| fts_kw | BM25 with hand-crafted keyword queries | skilled keyword consumer |
| cte_expand | FTS keyword seeds -> 1-hop CTE expansion (wiki_traversal edges) | link-walking consumer |
| lightrag | LightRAG 1.5.6 mode=mix, only_need_context | graph+vector consumer |

## Files

- `ingest_lightrag.py` — vault -> LightRAG store (NIM meta/llama-3.1-8b-instruct
  for entity extraction, Cohere embed-v4.0 for vectors; both probed live, see
  bake-off results doc for the rejected candidates). Idempotent: same doc ids
  skip, LLM cache makes restarts cheap.
- `queries.json` — 20 questions, gold slugs + gold facts, category-tagged
  (vocab / hop / direct). Golds verified against page content 2026-08-22.
- `run_comparison.py` — runs all arms, writes comparison-results.jsonl
  (hits, gold_hit@5, latency, context).
- `judge_bakeoff.py` — MiniMax-M3 judge via headless pi, rubric 0/1/2
  context-support scoring, writes judged-results.jsonl.

Data lives in P:/.data/scout/graph-bakeoff/ (venv, lightrag-wd, results) —
not tracked; results summary goes into the handoff packet.

## Disclosed limitations

- LightRAG doc attribution is regex slug-matching over returned context
  (slug prefix lives in chunk 1 of each doc; body wikilinks can match other
  slugs). gold_hit@5 for lightrag carries noise in both directions; the
  judge score does not depend on attribution.
- Gold set was authored by the same agent that runs the comparison
  (mitigation: golds are verbatim-anchored to page content; 6-item sample
  self-verified against judge scores).
- fts_kw keyword queries were crafted with knowledge of the gold pages —
  this inflates the keyword baseline in LightRAG's disfavor; reported as
  the "skilled consumer ceiling", not a neutral arm.
