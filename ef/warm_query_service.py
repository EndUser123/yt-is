"""Persistent warm EF query service — eliminates model cold-start per query.

Review F-5 (2026-08-19): bin/ef-query constructs BGEM3Dual() per invocation,
paying a full BGE-M3 reload (~5-15s) every call. This service holds one warm
ProductionQuery instance behind a localhost HTTP endpoint. The ef-query CLI
becomes a thin client with graceful fallback to in-process mode.

Usage:
    python -m ef.warm_query_service          # start the service (background)
    curl http://127.0.0.1:6391/query?q=test  # query
    curl http://127.0.0.1:6391/health        # readiness check
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOST = "127.0.0.1"
PORT = int(os.environ.get("YTIS_EF_QUERY_PORT", "6391"))
PID_FILE = REPO / ".data" / "yt-is" / "ef" / "query-service.pid"

_query_instance = None
_query_lock = threading.Lock()


def get_query():
    """Lazy singleton for the warm ProductionQuery."""
    global _query_instance
    if _query_instance is None:
        with _query_lock:
            if _query_instance is None:
                from ef import embedding, buildspec
                from ef.query_server import ProductionQuery

                _query_instance = ProductionQuery(
                    embedding.BGEM3Dual(),
                    buildspec.read_buildspec(),
                )
    return _query_instance


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            try:
                q = get_query()
                self._json(200, {"status": "ready", "model": "warm"})
            except Exception as e:
                self._json(503, {"status": "warming", "error": str(e)[:100]})

        elif parsed.path == "/query":
            params = parse_qs(parsed.query)
            query_text = params.get("q", [""])[0]
            if not query_text:
                self._json(400, {"error": "missing q parameter"})
                return
            top_k = int(params.get("top_k", ["8"])[0])
            channel_id = params.get("channel_id", [None])[0]
            fmt = params.get("format", ["json"])[0]

            try:
                q = get_query()
                results = q.relevant(query_text, limit=top_k, channel_id=channel_id)
                if fmt == "text":
                    lines = []
                    for r in results:
                        lines.append(f"- {r.title}")
                        lines.append(f"  {r.snippet[:200]}")
                        lines.append(f"  https://youtu.be/{r.video_id}")
                        lines.append("")
                    self._text(200, "\n".join(lines))
                else:
                    self._json(200, {
                        "results": [
                            {
                                "chunk_id": r.chunk_id,
                                "video_id": r.video_id,
                                "title": r.title,
                                "snippet": r.snippet,
                                "score": r.score,
                                "retrieval_paths": r.retrieval_paths,
                                "url": f"https://youtu.be/{r.video_id}",
                            }
                            for r in results
                        ]
                    })
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})

        else:
            self._json(404, {"error": "not found"})

    def _json(self, code, data):
        body = json.dumps(data, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default request logging


def is_running() -> bool:
    """Check if the warm query service is already running."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if is_running():
        print(f"warm query service already running on {HOST}:{PORT}")
        return 0

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"warm EF query service starting on {HOST}:{PORT}")
    print(f"  model loading in background... queries will wait until warm")

    def shutdown(signum, frame):
        print("\nshutting down")
        server.shutdown()
        PID_FILE.unlink(missing_ok=True)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Warm the model in a background thread so the server accepts
    # connections immediately (health returns 503 until warm)
    def warm():
        try:
            get_query()
            print("  model warm — ready for queries")
        except Exception as e:
            print(f"  model load failed: {e}")

    threading.Thread(target=warm, daemon=True).start()

    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
