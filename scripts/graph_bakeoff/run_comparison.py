"""run_comparison — graph bake-off, natural-language retrieval-quality arm.

Packet P2 (reopened 2026-08-22): "LightRAG might be better than the CTE" —
this script measures that. Four arms, same 20-question set (queries.json):

  fts_raw    production FTS5 BM25 (wiki_search.py), question verbatim.
             AND-semantics — this is what a naive consumer gets.
  fts_kw     FTS5 with hand-crafted keyword queries (queries.json keywords_fts)
             — the skilled-consumer ceiling for keyword search.
  cte_expand FTS keyword seeds (top-3) -> 1-hop CTE expansion over wiki_edges
             (wiki_traversal.py) -> seeds + top-degree neighbors.
  lightrag   LightRAG 1.5.6, mode=mix, only_need_context=True, chunk/entity/
             relation top_k=5. Requires ingest_lightrag.py to have completed.

Metrics:
  gold_hit@5   did the arm surface any gold slug in its top-5? (doc-level)
  latency_ms   per-query wall time
  context      the retrieved passages (truncated), consumed by judge_bakeoff.py

Attribution note (disclosed limitation): LightRAG slugs are recovered by
regex-matching the known 1,360-slug vocabulary inside its returned context;
slug prefixes live in chunk 1 of each doc, and body wikilinks can match other
slugs, so lightrag gold_hit carries mild noise in BOTH directions.

Run inside the bake-off venv:
  P:/.data/scout/graph-bakeoff/lightrag-venv/Scripts/python.exe run_comparison.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "P:/.agents/scripts")
sys.path.insert(0, "P:/packages/yt-is/scripts")

from wiki_search import WikiSearch  # noqa: E402
import wiki_traversal as wt  # noqa: E402

VAULT = Path("P:/.data/wiki/concepts")
OUT = Path("P:/.data/scout/graph-bakeoff/comparison-results.jsonl")
ALL_SLUGS = [p.stem for p in VAULT.glob("*.md")]
SLUG_SET = set(ALL_SLUGS)
TOP_K = 5
CTX_LIMIT = 8000


def _norm_doc_id(doc_id: str) -> str:
    """FTS doc ids are paths; reduce to a vault slug when possible."""
    s = str(doc_id).replace("\\", "/")
    if "/" in s:
        last = s.rsplit("/", 1)[-1]
        if last.endswith(".md"):
            last = last[:-3]
        if last in SLUG_SET:
            return last
    return s if s in SLUG_SET else str(doc_id)


def arm_fts(query: str):
    ws = WikiSearch(collection="wiki")
    res = ws.search(query, top_k=TOP_K)
    ws.close()
    hits = [_norm_doc_id(r["doc_id"]) for r in res]
    ctx = "\n--\n".join(f"{r['doc_id']}\n{r.get('text','')}" for r in res)
    return hits, ctx


def arm_cte(keywords: str):
    """FTS seeds -> 1-hop expansion over the CTE edge table."""
    seeds_raw, _ = arm_fts(keywords)
    seeds = [s for s in seeds_raw if s in SLUG_SET][:3]
    conn = sqlite3.connect(f"file:{wt.DB}?mode=ro", uri=True)
    ranked: dict[str, int] = {}
    for s in seeds:
        for (src, tgt) in conn.execute(
                "SELECT source, target FROM wiki_edges WHERE source=? OR target=?",
                (s, s)):
            other = tgt if src == s else src
            if other in SLUG_SET and other not in seeds:
                ranked[other] = ranked.get(other, 0) + 1
    conn.close()
    neighbors = sorted(ranked, key=lambda k: -ranked[k])
    hits = seeds + neighbors[: TOP_K - len(seeds)]
    ctx = "\n--\n".join(
        f"{h}\n{(VAULT / (h + '.md')).read_text(encoding='utf-8', errors='replace')[:4000]}"
        for h in hits if h in SLUG_SET)
    return hits, ctx


async def arm_lightrag(question: str, rag):
    from lightrag import QueryParam
    ctx = await rag.aquery(question, param=QueryParam(
        mode="mix", only_need_context=True,
        top_k=TOP_K, chunk_top_k=TOP_K))
    ctx = str(ctx)
    found = [s for s in ALL_SLUGS if re.search(
        r"(?<![a-z0-9-])" + re.escape(s) + r"(?![a-z0-9-])", ctx)]
    hits = list(dict.fromkeys(found))[:TOP_K]
    return hits, ctx


async def main():
    queries = json.loads(
        Path("P:/packages/yt-is/scripts/graph_bakeoff/queries.json").read_text()
    )["queries"]

    from lightrag import LightRAG
    from ingest_lightrag import llm_model_func, embedding_func, WD

    rag = LightRAG(working_dir=str(WD), llm_model_func=llm_model_func,
                   embedding_func=embedding_func)
    await rag.initialize_storages()

    rows = []
    for q in queries:
        for arm, fn in [
            ("fts_raw", lambda qq=q: arm_fts(qq["question"])),
            ("fts_kw", lambda qq=q: arm_fts(qq["keywords_fts"])),
            ("cte_expand", lambda qq=q: arm_cte(qq["keywords_fts"])),
        ]:
            t0 = time.perf_counter()
            try:
                hits, ctx = fn()
                err = None
            except Exception as exc:  # noqa: BLE001
                hits, ctx, err = [], str(exc)[:300]
            lat = round((time.perf_counter() - t0) * 1000)
            rows.append({"qid": q["id"], "category": q["category"], "arm": arm,
                         "hits": hits, "latency_ms": lat,
                         "gold_hit": any(h in q["gold_slugs"] for h in hits),
                         "context": ctx[:CTX_LIMIT], "error": err})

        t0 = time.perf_counter()
        try:
            hits, ctx = await arm_lightrag(q["question"], rag)
            err = None
        except Exception as exc:  # noqa: BLE001
            hits, ctx, err = [], str(exc)[:300]
        lat = round((time.perf_counter() - t0) * 1000)
        rows.append({"qid": q["id"], "category": q["category"], "arm": "lightrag",
                     "hits": hits, "latency_ms": lat,
                     "gold_hit": any(h in q["gold_slugs"] for h in hits),
                     "context": ctx[:CTX_LIMIT], "error": err})
        got = [r["gold_hit"] for r in rows[-4:]]
        print(f"{q['id']} done: gold_hit fts_raw={got[0]} fts_kw={got[1]} "
              f"cte={got[2]} lightrag={got[3]}", flush=True)

    await rag.finalize_storages()
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nsummary -> {OUT}")
    for arm in ("fts_raw", "fts_kw", "cte_expand", "lightrag"):
        arm_rows = [r for r in rows if r["arm"] == arm]
        n = len(arm_rows)
        hit = sum(r["gold_hit"] for r in arm_rows)
        lats = sorted(r["latency_ms"] for r in arm_rows)
        p50 = lats[n // 2] if n else 0
        p95 = lats[min(n - 1, int(n * 0.95))] if n else 0
        print(f"  {arm:11s} gold_hit@5 {hit}/{n} | p50 {p50}ms p95 {p95}ms")


if __name__ == "__main__":
    asyncio.run(main())
