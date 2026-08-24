"""Standalone server for the Discord-capture selection page (:6393).

The same routes live inside the warm query service (:6391 /dht) but a
service restart needs elevation (WinSW ACL); this runner serves the
page immediately from the user session. Reads/writes the same
selection JSON the nightly capture consumes, so both surfaces stay in
sync. Retire it when :6391 restarts — nothing depends on this port.
(6392 is taken by qdrant.)

    pythonw scripts/dht-capture/dht_page_server.py     (detached)
"""

from __future__ import annotations

import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef.warm_query_service import (  # noqa: E402
    DHT_CATALOG, _dht_selection, _dht_save_selection, _render_dht_page,
    _render_graph_page, _render_interests_page)

import json  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/interests":
            body = _render_interests_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/graph":
            q = (parse_qs(parsed.query).get("q") or [""])[0]
            body = _render_graph_page(q).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path in ("/", "/dht"):
            body = _render_dht_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/dht/toggle":
            params = parse_qs(parsed.query)
            gid = (params.get("g") or [""])[0]
            cid = (params.get("c") or [""])[0]
            mode = (params.get("all") or [""])[0]
            try:
                cat = json.loads(DHT_CATALOG.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cat = {"guilds": []}
            guild = next((g for g in cat.get("guilds", [])
                          if g.get("id") == gid), None)
            sel = _dht_selection()
            if guild and mode in ("0", "1"):
                for c in guild.get("channels", []):
                    if c.get("capturable"):
                        sel[f"{gid}/{c['id']}"] = mode == "1"
            elif guild and cid:
                sel[f"{gid}/{cid}"] = not sel.get(f"{gid}/{cid}")
            _dht_save_selection(sel)
            self.send_response(302)
            self.send_header("Location", "/dht")
            self.end_headers()
        elif parsed.path == "/dht/webhook":
            params = parse_qs(parsed.query)
            gid = (params.get("g") or [""])[0]
            try:
                cat = json.loads(DHT_CATALOG.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cat = {"guilds": []}
            guild = next((g for g in cat.get("guilds", [])
                          if g.get("id") == gid), None)
            if not guild:
                self._t(400, "unknown guild"); return
            target = next(
                (c for c in guild["channels"] if c.get("type") == 0 and
                 any(k in (c.get("name") or "").lower()
                     for k in ("general", "digest", "announce"))), None) \
                or next((c for c in guild["channels"]
                         if c.get("type") == 0), None)
            if not target:
                self._t(400, "guild has no text channel"); return
            r = subprocess.run(
                [sys.executable,
                 str(REPO / "scripts/dht-capture/enumerate_dht.py"),
                 "--create-webhook", gid, target["id"]],
                capture_output=True, text=True, timeout=300, cwd=str(REPO))
            if r.returncode == 0:
                self.send_response(302)
                self.send_header("Location", "/dht")
                self.end_headers()
            else:
                self._t(500, "webhook create failed — "
                        f"{(r.stdout + r.stderr)[-400:]}")
        else:
            self._t(404, "not found (try /dht)")

    def _t(self, code, msg):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 6393), Handler).serve_forever()
