"""check_doc_status — post-ingest verification for the LightRAG arm.

Counts docs by pipeline status (processed/failed/pending) and lists failures.
Run after ingest_lightrag.py completes; re-running ingest_lightrag.py
re-enqueues any non-processed ids (idempotent).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from lightrag import LightRAG
from ingest_lightrag import llm_model_func, embedding_func, WD


async def main() -> int:
    rag = LightRAG(working_dir=str(WD), llm_model_func=llm_model_func,
                   embedding_func=embedding_func)
    await rag.initialize_storages()
    statuses: dict[str, int] = {}
    failed_ids = []
    try:
        keys = await rag.doc_status.filter_keys(lambda k: True)
    except Exception:
        keys = []
    for k in keys or []:
        try:
            rec = await rag.doc_status.get_by_ids([k])
            st = rec[0].get("status") if rec and rec[0] else "unknown"
        except Exception:
            st = "unreadable"
        statuses[st] = statuses.get(st, 0) + 1
        if st in ("failed", "pending", "processing"):
            failed_ids.append(k)
    print("doc status counts:", statuses)
    print("non-processed ids:", failed_ids[:50])
    await rag.finalize_storages()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
