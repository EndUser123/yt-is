#!/usr/bin/env python3
"""search_ef MCP server — Evidence Fabric corpus search.

Corpus: 228K+ YouTube transcripts plus reddit/hn/rss/dht connector content
(Evidence Fabric generation index). Thin adapter over
ef.query_server.ProductionQuery — the same warm query path the :6391
service uses — so there is exactly one query implementation.

Stdio by default (debugging); when MCP_HTTP_PORT is set, runs as a shared
streamable-HTTP daemon (NSSM service "search-ef-mcp", port registry
search_* 8321+). Mirrors core/chs/mcp_server.py's switch pattern.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("search_ef")

_query_instance = None
_query_lock = threading.Lock()


def get_query():
    """Shared warm ProductionQuery — ONE model instance per process.

    When this module runs inside ef.warm_query_service (the merged
    single-model host), this returns the warm service's own singleton so
    the MCP face and the :6391 renderers share one BGE-M3. Standalone
    (stdio debugging) falls back to creating its own instance."""
    global _query_instance
    if _query_instance is None:
        with _query_lock:
            if _query_instance is None:
                try:
                    from ef.warm_query_service import get_query as _warm_get_query
                    _query_instance = _warm_get_query()
                except Exception:
                    from ef import embedding, buildspec
                    from ef.query_server import ProductionQuery

                    _query_instance = ProductionQuery(
                        embedding.BGEM3Dual(),
                        buildspec.load_spec()["generation"],
                    )
    return _query_instance


@mcp.tool()
def search(query: str, limit: int = 8, channel_id: str = "") -> str:
    """Search the Evidence Fabric corpus: 228K+ YouTube transcripts plus
    reddit/hn/rss/dht content, semantic+FTS hybrid (BGE-M3 dense + sparse,
    fused). Use for "what do our ingested sources say about X" questions.

    Args:
        query: Natural-language query.
        limit: Max results (default 8).
        channel_id: Optional corpus filter (YouTube channel_id or r/<subreddit>).

    Returns:
        JSON with results (title, snippet, url, source, video_id) and corpus
        meta (generation, index_lag_count).
    """
    from ef import freshness

    q = get_query()
    rows = q.relevant(query, limit=limit, channel_id=(channel_id or None))
    out = [
        {
            "title": getattr(r, "title", "") or "",
            "snippet": (getattr(r, "snippet", "") or "")[:300],
            "url": getattr(r, "url", "") or "",
            "source": getattr(r, "source", "") or "",
            "video_id": getattr(r, "video_id", "") or "",
        }
        for r in rows
    ]
    lag = freshness.compute_lag(freshness.load_state().get("indexed_watermark", ""))
    return json.dumps(
        {
            "results": out,
            "meta": {
                "generation": q.generation,
                "index_lag_count": lag["index_lag_count"],
            },
        },
        ensure_ascii=False,
        indent=1,
    )


@mcp.tool()
def status() -> str:
    """EF index status: build generation, indexed watermark, lag, last error."""
    from ef import freshness

    st = freshness.load_state()
    lag = freshness.compute_lag(st.get("indexed_watermark", ""))
    return json.dumps(
        {
            "build_id": st.get("build_id", ""),
            "indexed_watermark": st.get("indexed_watermark", ""),
            "index_lag_count": lag["index_lag_count"],
            "last_index_success": st.get("last_index_success", ""),
            "last_indexing_error": st.get("last_indexing_error"),
        },
        ensure_ascii=False,
        indent=1,
    )


if __name__ == "__main__":
    # Shared-daemon mode (NSSM): MCP_HTTP_PORT switches to streamable HTTP on
    # localhost so all sessions share one process. Stdio default unchanged.
    _port = os.environ.get("MCP_HTTP_PORT")
    if _port:
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = int(_port)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
