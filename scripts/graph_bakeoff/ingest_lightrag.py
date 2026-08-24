"""ingest_lightrag — LightRAG arm of the graph bake-off (packet P2, reopened arm).

Ingests all wiki concept pages into a LightRAG store for the natural-language
retrieval-quality comparison vs the CTE/FTS baselines (run_comparison.py).

Bindings (fleet-lane compliant, operator directive 2026-08-22: only fleet
picker lanes + ZCode spawn agents; no third-party keys):
  LLM:        minimax-m3 — the fleet Code-lane escalation member
              ([[model-lanes-vs-roles]]), via its dedicated fleet key
              (api.minimax.io OpenAI-compat, 16K req/mo ration). Realistic
              extraction: 14-23s, valid JSON after <think> strip. Code-lane
              primary ccr-ornith was NOT used: disabled by prior operator
              directive (40s+ latency, go/reference/model-routing.md:218).
              minimax-m2.5@opencode-go was used briefly and reverted on
              operator correction (not a fleet-picker member; ~500 files of
              extraction discarded for extractor homogeneity). Free-pool
              transports measured and rejected for bulk: NIM (rolling-window
              429 storms silently fail chunks at concurrency 3/8/12),
              Mistral chat (stormed harder), Groq (403), Cerebras (catalog
              404s), HF router (401), Cohere trial (embed 429 storm).
  Embedding:  local BGE-M3 via sentence-transformers on the local GPU —
              same model family as the EF stack, zero external calls.
              Rejected earlier: Cohere embed-v4.0 (trial 429 storm),
              mistral-embed (clean but third-party key).

Design decisions:
  - entity_extract_max_gleaning=0  — halves LLM calls (~1400 chunks -> ~1400
    calls) to fit NIM free ~40 RPM with llm_model_max_async=3 (~30/min).
  - Each document is prefixed with its slug so retrieved chunks can be
    attributed to source pages (gold-slug hit metric in run_comparison.py).
  - Isolated venv (P:/.data/scout/graph-bakeoff/lightrag-venv); no contact
    with the EF stack (version contract M1-M8 untouched).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import threading
from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import setup_logger, wrap_embedding_func_with_attrs

VAULT = Path("P:/.data/wiki/concepts")
WD = Path("P:/.data/scout/graph-bakeoff/lightrag-wd")
MANIFEST = Path("P:/.data/scout/graph-bakeoff/ingest-manifest.json")

MINIMAX_BASE = "https://api.minimax.io/v1"
LLM_MODEL = "MiniMax-M3"
EMBED_MODEL = "BAAI/bge-m3"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_st_model = None
_st_lock = threading.Lock()


def load_env() -> dict[str, str]:
    env = {}
    for line in open("P:/.env", encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()


async def llm_model_func(prompt, system_prompt=None, history_messages=[],
                         keyword_extraction=False, **kwargs) -> str:
    raw = await openai_complete_if_cache(
        LLM_MODEL, prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        api_key=ENV["MINIMAX_API_KEY"],
        base_url=MINIMAX_BASE,
        **kwargs,
    )
    # MiniMax-M3 emits <think> reasoning inline; LightRAG wants clean output.
    cleaned = _THINK_RE.sub("", raw).strip()
    if not cleaned:
        # entire budget consumed by unterminated thinking — treat as a miss
        # so LightRAG's retry re-runs the call
        return ""
    return cleaned


def _get_st():
    global _st_model
    if _st_model is None:
        with _st_lock:
            if _st_model is None:
                from sentence_transformers import SentenceTransformer
                # Device policy: batch-64 GPU flushes OOM'd against the
                # shared production card (356 docs, 2026-08-22). Encode batch
                # is now 8 (~1-2GB peak), so GPU is safe WITH a free-VRAM
                # guard: use cuda only when >=3GB is free at load time,
                # else CPU. CPU measured fine for window-bound extraction
                # but became the replay-wave bottleneck (0 LLM calls, docs
                # creeping through embedding).
                device = os.environ.get("EMBED_DEVICE")
                if device is None:
                    try:
                        import torch
                        free_b, _total = torch.cuda.mem_get_info()
                        device = "cuda" if free_b >= 3 * 1024**3 else "cpu"
                    except Exception:
                        device = "cpu"
                print(f"embedding device: {device}", flush=True)
                _st_model = SentenceTransformer(EMBED_MODEL, device=device)
    return _st_model


@wrap_embedding_func_with_attrs(embedding_dim=1024, max_token_size=8192,
                                model_name=EMBED_MODEL)
async def embedding_func(texts: list[str]) -> np.ndarray:
    def _encode():
        m = _get_st()
        vecs = m.encode(texts, batch_size=8, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)
    return await asyncio.to_thread(_encode)


def _doc_status_counts() -> dict:
    """Read the doc_status KV store directly — ainsert() does NOT raise on
    per-doc pipeline failures (1,393 docs failed silently 2026-08-22 via
    embedding-worker timeouts while the batch loop saw '0 failed batches')."""
    p = WD / "kv_store_doc_status.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for v in d.values():
        if isinstance(v, dict):
            st = v.get("status", "?")
            out[st] = out.get(st, 0) + 1
    return out


async def main() -> int:
    setup_logger("lightrag", level="INFO")
    WD.mkdir(parents=True, exist_ok=True)

    # Preload BGE-M3 BEFORE any insert: concurrent first-calls during the
    # ~40-60s model load raced and timed out every embedding worker (the
    # silent mass-failure above). Loading once here removes the race.
    t0 = time.perf_counter()
    _get_st()
    print(f"BGE-M3 loaded on GPU in {time.perf_counter()-t0:.0f}s", flush=True)

    rag = LightRAG(
        working_dir=str(WD),
        llm_model_func=llm_model_func,
        embedding_func=embedding_func,
        entity_extract_max_gleaning=0,
        llm_model_max_async=25,
        embedding_func_max_async=4,
        embedding_batch_num=64,
        chunk_token_size=1200,
        default_embedding_timeout=300,
    )
    await rag.initialize_storages()

    files = sorted(VAULT.glob("*.md"))
    if os.environ.get("SMOKE_LIMIT"):
        files = files[: int(os.environ["SMOKE_LIMIT"])]
    texts, ids = [], []
    for p in files:
        slug = p.stem
        content = p.read_text(encoding="utf-8", errors="replace")
        texts.append(f"{slug}\n\n{content}")
        ids.append(slug)

    t0 = time.perf_counter()
    failures = []
    # batch inserts so a transient NIM failure kills at most one batch
    BATCH = 50
    for i in range(0, len(texts), BATCH):
        batch, bid = texts[i:i + BATCH], ids[i:i + BATCH]
        for attempt in range(3):
            try:
                await rag.ainsert(batch, ids=bid)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    failures.append({"batch_start": i, "error": str(exc)[:500]})
                    print(f"BATCH {i} FAILED: {exc}", flush=True)
                else:
                    await asyncio.sleep(30 * (attempt + 1))
        done = min(i + BATCH, len(texts))
        el = time.perf_counter() - t0
        counts = _doc_status_counts()
        print(f"progress: {done}/{len(texts)} docs, {el:.0f}s elapsed, "
              f"doc_status={counts}", flush=True)
        if counts.get("failed", 0) > 2000:
            print("ABORT: silent mass-failure detected (failed>2000)", flush=True)
            await rag.finalize_storages()
            return 2

    el = time.perf_counter() - t0
    manifest = {
        "lightrag_version": "1.5.6",
        "llm": LLM_MODEL, "embed": EMBED_MODEL,
        "docs_total": len(texts), "failures": failures,
        "elapsed_s": round(el, 1),
        "gleaning": 0, "chunk_token_size": 1200,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"ingest done: {len(texts)} docs, {len(failures)} failed batches, "
          f"{el:.0f}s -> {MANIFEST}")
    await rag.finalize_storages()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
