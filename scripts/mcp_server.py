"""yt-is MCP server — expose the knowledge base as tools for AI agents.

Any MCP-compatible agent (Grok, Claude Code, Cursor, etc.) can query the
yt-is evidence fabric through this server. Tools:

    ytis_search     Semantic search across all transcripts and artifacts
    ytis_status     System health and statistics
    ytis_topics     List discovered topic areas
    ytis_today      What's new in the last 24 hours

Usage:
    # Add to your MCP client config:
    {
        "mcpServers": {
            "ytis": {
                "command": "python",
                "args": ["P:/packages/yt-is/scripts/mcp_server.py"]
            }
        }
    }

    # Or run standalone for testing:
    python scripts/mcp_server.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
EF_CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")


# ── Data helpers ─────────────────────────────────────────────────────────────

def _ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _get_stats():
    conn = _ro(DB)
    pending = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='pending'").fetchone()[0]
    complete = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='complete'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='failed'").fetchone()[0]
    channels = conn.execute("SELECT COUNT(*) FROM channel_metadata").fetchone()[0]
    conn.close()

    tdb = _ro(TDB)
    transcripts = tdb.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0]
    tdb.close()

    visual_root = Path("P:/.data/yt-is/visual")
    artifacts = len(list(visual_root.glob("*/artifacts.md"))) if visual_root.is_dir() else 0

    topics = 0
    try:
        ef = _ro(EF_CATALOG)
        topics = ef.execute("SELECT COUNT(*) FROM topic_clusters WHERE member_count > 0").fetchone()[0]
        ef.close()
    except sqlite3.OperationalError:
        pass

    return {
        "transcripts_complete": complete,
        "transcripts_pending": pending,
        "transcripts_failed": failed,
        "channels_tracked": channels,
        "total_transcripts": transcripts,
        "code_artifacts": artifacts,
        "topics_discovered": topics,
    }


def _get_topics(limit=20):
    try:
        conn = _ro(EF_CATALOG)
        rows = conn.execute("""
            SELECT label, member_count, video_count, top_terms
            FROM topic_clusters WHERE member_count > 0
            ORDER BY member_count DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [
            {"label": r[0], "chunks": r[1], "videos": r[2],
             "terms": json.loads(r[3]) if r[3] else []}
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def _search(query, top_k=8):
    """Search using the warm service, falling back to ef-query CLI."""
    # Try warm query service
    try:
        import urllib.request
        import urllib.parse
        params = urllib.parse.urlencode({"q": query, "top_k": top_k, "format": "json"})
        with urllib.request.urlopen(f"http://127.0.0.1:6391/query?{params}", timeout=30) as r:
            if r.status == 200:
                return json.loads(r.read().decode("utf-8")).get("results", [])
    except Exception:
        pass

    # Fall back to ef-query CLI
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, str(REPO / "bin" / "ef-query"), query,
             "--top-k", str(top_k), "--format", "json"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return data.get("results", [])
    except Exception:
        pass
    return []


def _get_today():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn = _ro(DB)
    rows = conn.execute("""
        SELECT video_id, title, updated_at FROM analysis_status
        WHERE status = 'complete' AND updated_at >= ?
        ORDER BY updated_at DESC LIMIT 10
    """, (cutoff,)).fetchall()
    conn.close()
    return [
        {"video_id": r[0], "title": r[1] or r[0], "url": f"https://youtube.com/watch?v={r[0]}"}
        for r in rows
    ]


# ── MCP Protocol (JSON-RPC over stdio) ─────────────────────────────────────

TOOLS = [
    {
        "name": "ytis_search",
        "description": "Semantic search across all YouTube transcripts and extracted code artifacts. Returns relevant text chunks with video titles and URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "top_k": {"type": "integer", "description": "Number of results (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ytis_status",
        "description": "Get system health and statistics: transcript counts, channels, topics, artifacts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ytis_topics",
        "description": "List discovered topic areas from the video corpus, largest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max topics to return (default 20)"},
            },
        },
    },
    {
        "name": "ytis_today",
        "description": "What's new in the last 24 hours: recent transcripts and code extractions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle_tool_call(name, arguments):
    if name == "ytis_search":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 8)
        results = _search(query, top_k)
        if not results:
            return {"content": [{"type": "text", "text": f"No results for '{query}'"}]}
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', r.get('video_id', '?'))}**")
            lines.append(f"  {r.get('snippet', '')[:200]}")
            lines.append(f"  {r.get('url', f'https://youtube.com/watch?v={r.get('video_id', '')}')}")
            lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    elif name == "ytis_status":
        stats = _get_stats()
        text = "\n".join(f"{k}: {v:,}" for k, v in stats.items())
        return {"content": [{"type": "text", "text": text}]}

    elif name == "ytis_topics":
        limit = arguments.get("limit", 20)
        topics = _get_topics(limit)
        if not topics:
            return {"content": [{"type": "text", "text": "No topics discovered yet."}]}
        lines = [f"{'Topic':40s} Videos  Terms"]
        for t in topics:
            terms = ", ".join(t["terms"][:4])
            lines.append(f"{t['label']:40s} {t['videos']:6,}  {terms}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    elif name == "ytis_today":
        items = _get_today()
        if not items:
            return {"content": [{"type": "text", "text": "No new transcripts in the last 24 hours."}]}
        lines = [f"• {i['title']}" for i in items]
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


def main():
    """Run the MCP server over stdio (JSON-RPC)."""
    # Load workspace env for database paths
    try:
        from csf.paths import load_workspace_env
        load_workspace_env()
    except ImportError:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")
        result = None

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ytis", "version": "1.0.0"},
            }

        elif method == "tools/list":
            result = {"tools": TOOLS}

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = _handle_tool_call(tool_name, arguments)

        elif method == "notifications/initialized":
            # No response needed for notifications
            continue

        if result is not None and req_id is not None:
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
