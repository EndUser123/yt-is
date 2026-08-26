"""Health-gate and input-validation tests for ef.warm_query_service.

The 2026-08-23 incident class: /health claimed "ready" while every /query
refused (encoder canary not passed). These tests pin the honest contract —
503 warming before the canary, 200 ready after — plus the double-start
guard's liveness semantics (any HTTP response counts as running) and the
/query top_k validation (400 on garbage, clamped to 1..100).

No production databases or models are touched: the handler is served on
an ephemeral loopback port with get_query stubbed and _warm_ok swapped
for a fresh Event per test.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import ef.warm_query_service as wqs


class _StubQuery:
    def relevant(self, query_text, limit=None, channel_id=None):
        return []


@pytest.fixture()
def server(monkeypatch):
    """Serve the real Handler on an ephemeral port with the model stubbed."""
    monkeypatch.setattr(wqs, "get_query", lambda: _StubQuery())
    monkeypatch.setattr(wqs, "_warm_ok", threading.Event())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), wqs.Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_health_is_503_warming_before_canary(server):
    code, body = _get(server, "/health")
    assert code == 503
    assert body["status"] == "warming"


def test_health_is_200_ready_after_canary(server):
    wqs._warm_ok.set()
    code, body = _get(server, "/health")
    assert code == 200
    assert body["status"] == "ready"


def test_query_refused_with_503_while_warming(server):
    code, body = _get(server, "/query?q=test")
    assert code == 503
    assert "warming" in body["error"]


def test_query_invalid_top_k_is_400_not_crash(server):
    # Before the canary too: validation happens before the warm gate,
    # so a malformed request never kills the handler thread uncaught.
    code, body = _get(server, "/query?q=test&top_k=abc")
    assert code == 400
    assert "top_k" in body["error"]
    # a huge but well-formed int parses fine and clamps — here it reaches
    # the warm gate and gets the standard 503, proving no uncaught crash
    code, _ = _get(server, "/query?q=test&top_k=999999999999999999999")
    assert code == 503


def test_query_clamps_top_k_when_ready(server, monkeypatch):
    calls = {}

    class _Recording(_StubQuery):
        def relevant(self, query_text, limit=None, channel_id=None):
            calls["limit"] = limit
            return []

    monkeypatch.setattr(wqs, "get_query", lambda: _Recording())
    wqs._warm_ok.set()
    code, body = _get(server, "/query?q=test&top_k=5000&federation=off")
    assert code == 200
    assert calls["limit"] == 100
    assert body == {"results": []}


def test_is_running_counts_warming_503_as_alive(server, monkeypatch):
    monkeypatch.setattr(wqs, "PORT", server.server_address[1])
    # warming (503) — a live server, just not ready: double-start guard holds
    assert wqs.is_running() is True
    wqs._warm_ok.set()
    assert wqs.is_running() is True


def test_is_running_false_when_nothing_listens(monkeypatch):
    # bind then immediately release an ephemeral port: nothing answers there
    s = ThreadingHTTPServer(("127.0.0.1", 0), wqs.Handler)
    port = s.server_address[1]
    s.server_close()
    monkeypatch.setattr(wqs, "PORT", port)
    assert wqs.is_running() is False
