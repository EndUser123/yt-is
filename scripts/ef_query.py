#!/usr/bin/env python
"""ef_query — read-only CLI over the Evidence Fabric production index.

Single shared query surface for non-yt-is consumers (skills, subagents,
humans): hybrid dense (BGE-M3) + sparse (BM25) retrieval with the D-gate
intent routing (semantic / identifier / strict-exact), over the currently
claimed production generation of the yt-is transcript corpus.

Corpus data is opened read-only (sqlite mode=ro). First use may start the
package-owned Qdrant server (P:/.data/yt-is/ef/server/, ports 6390/6391);
pass --no-start-server to forbid that.

Ranking is under active Evidence Fabric gate refinement (E-gate/C4).
Treat output as evidence retrieval with citations, not ranked authority.

Usage:
  python scripts/ef_query.py "query text" [--limit 8] [--channel-id ID]
      [--exact] [--device cuda|cpu] [--json] [--no-start-server]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import buildspec, catalog, embedding, server
from ef.query_server import ProductionQuery


def _claimed_production_generation() -> int:
    gen = buildspec.active_generation()
    if gen > 0:
        return gen
    conn = sqlite3.connect(f"file:{catalog.CATALOG_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "select generation from build_claims where kind='production' "
            "order by claimed_at desc limit 1").fetchone()
    finally:
        conn.close()
    if row:
        return int(row[0])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Query the yt-is Evidence Fabric (read-only).")
    ap.add_argument("query", nargs="+", help="query text (words joined)")
    ap.add_argument("--limit", type=int, default=8,
                    help="max results (1-50, default 8)")
    ap.add_argument("--channel-id", default=None,
                    help="restrict to one channel_id")
    ap.add_argument("--exact", action="store_true",
                    help="force strict literal matching (no semantic fill)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                    help="encoder device (default cuda)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    ap.add_argument("--no-start-server", action="store_true",
                    help="fail instead of starting the Qdrant server")
    args = ap.parse_args()

    query = " ".join(args.query).strip()
    if not query:
        print("ef_query: empty query", file=sys.stderr)
        return 2
    if not 1 <= args.limit <= 50:
        print("ef_query: --limit must be 1-50", file=sys.stderr)
        return 2

    gen = _claimed_production_generation()
    if gen <= 0:
        print("ef_query: no production generation claimed "
              "(promotion file absent, build_claims empty)", file=sys.stderr)
        return 2

    st = server.status()
    if not st["running"]:
        if args.no_start_server:
            print("ef_query: Qdrant server not running and "
                  "--no-start-server given", file=sys.stderr)
            return 2
        server.start()

    try:
        enc = embedding.BGEM3Dual(device=args.device)
    except Exception as e:  # e.g. CUDA unavailable — degrade to exit 2 so
        # callers' [LOCAL-CORPUS-UNAVAILABLE] fallback fires, not a traceback
        print(f"ef_query: encoder load failed on device '{args.device}': {e}",
              file=sys.stderr)
        return 2
    q = ProductionQuery(enc, generation=gen)
    try:
        results = q.relevant(query, limit=args.limit,
                             channel_id=args.channel_id,
                             exact=True if args.exact else None)
    except Exception as e:
        print(f"ef_query: query failed: {e}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps({
            "query": query,
            "generation": gen,
            "count": len(results),
            "results": [r.to_json() for r in results],
        }, ensure_ascii=False, indent=1))
        return 0

    if not results:
        print(f"no results for: {query} (generation {gen})")
        return 0
    for i, r in enumerate(results, 1):
        paths = ",".join(r.retrieval_paths)
        snippet = " ".join(r.snippet.split())
        if len(snippet) > 300:
            snippet = snippet[:297] + "..."
        print(f"[{i}] {r.score:.4f}  {r.title} — {r.channel_title}")
        print(f"    {r.url}  ({paths})")
        print(f"    {snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
