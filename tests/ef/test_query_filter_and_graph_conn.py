"""First-coverage tests for ef.query.HybridQuery and ef.graph_query.

Two defects pinned here:
1. Channel-filtered hybrid retrieval: the dense prefetch (and its
   retrieval-path tagging re-query) did not take the channel filter, so
   the fusion pool filled with other channels' points and
   channel-restricted results shrank below the requested limit.
2. graph_query used `with sqlite3.connect(...)` believing it closes the
   connection — it only wraps a transaction. These functions back the
   warm service's /graph endpoints and open a connection per request.

All collaborators are stubbed or tmp-pathed; production databases are
never touched.
"""

from __future__ import annotations

import gc
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# === HybridQuery channel-filter symmetry ==============================


class _StubClient:
    def __init__(self):
        self.calls = []
        payload = {
            "chunk_id": "c1", "eu_id": "v1:transcript", "video_id": "v1",
            "title": "T", "channel_id": "chan", "channel_title": "Chan",
            "start_char": 0, "end_char": 10,
        }
        self.points = [SimpleNamespace(id=1, score=0.9, payload=payload)]

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(points=self.points)


class _StubBM25:
    def encode_query(self, q):
        return [0], [1.0]


@pytest.fixture()
def hq(monkeypatch):
    import ef.query as qmod
    monkeypatch.setattr(qmod, "reopen_span",
                        lambda *a, **k: "snippet")
    client = _StubClient()
    return qmod.HybridQuery(client, _StubBM25(),
                            lambda texts: [[0.1] * 4 for _ in texts])


def _filters_present(call):
    fused = "prefetch" in call
    if fused:
        legs = [p.filter for p in call["prefetch"]]
        return all(f is not None for f in legs) and \
            call.get("query_filter") is not None
    return call.get("query_filter") is not None


def test_channel_filter_applies_to_every_leg(hq):
    out = hq.relevant("some query", channel_id="chan")
    assert len(hq.client.calls) == 3          # fused + dense tag + sparse tag
    for call in hq.client.calls:
        assert _filters_present(call), f"unfiltered request: {call}"
    assert out[0].retrieval_paths  # fused + tagged legs populated


def test_no_channel_filter_means_no_filters(hq):
    hq.relevant("some query")
    for call in hq.client.calls:
        assert call.get("query_filter") is None
        if "prefetch" in call:
            assert all(p.filter is None for p in call["prefetch"])


# === graph_query connection hygiene ====================================

KG_SCHEMA = """
CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY, kind TEXT, label TEXT,
                       weight INTEGER);
CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT, relation TEXT,
                       weight INTEGER);
CREATE TABLE eu (eu_id TEXT PRIMARY KEY, title TEXT, source TEXT,
                 channel_title TEXT);
"""


@pytest.fixture()
def graph_db(tmp_path, monkeypatch):
    import ef.graph_query as gq
    db = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(KG_SCHEMA)
    conn.executemany(
        "insert into kg_nodes values (?,?,?,?)",
        [("e1", "entity", "Entity One", 10),
         ("e2", "entity", "Entity Two", 3),
         ("chan:1", "channel", "Chan", 5)])
    conn.executemany(
        "insert into kg_edges values (?,?,?,?)",
        [("e1", "eu:docA", "mentioned_in", 2),
         ("e1", "eu:docB", "mentioned_in", 1),
         ("e2", "eu:docA", "mentioned_in", 1),
         ("eu:docA", "chan:1", "in_channel", 1),
         ("eu:docB", "chan:1", "in_channel", 1)])
    conn.executemany(
        "insert into eu values (?,?,?,?)",
        [("docA", "Title A", "notebooklm", "Chan Title"),
         ("docB", "Title B", "rss", "Chan Two")])
    conn.commit()
    conn.close()

    monkeypatch.setattr(gq, "CATALOG", db)
    opened = []
    real_connect = gq._connect

    def spy():
        c = real_connect()
        opened.append(c)
        return c

    monkeypatch.setattr(gq, "_connect", spy)
    return {"gq": gq, "opened": opened}


def _assert_all_closed(conns):
    gc.collect()
    for c in conns:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("select 1")           # closed connections refuse


def test_graph_views_close_their_connections(graph_db):
    gq, opened = graph_db["gq"], graph_db["opened"]
    view = gq.entity_view("e1")
    assert view["label"] == "Entity One"
    assert len(view["docs"]) == 2
    # notebooklm + rss merge under display labels
    assert {s["source"] for s in view["sources"]} == {"youtube", "rss"}
    ch = gq.channel_view("chan:1")
    assert ch["label"] == "Chan"
    assert gq.search_nodes("Ent")[0]["node_id"] == "e1"
    assert len(opened) == 3
    _assert_all_closed(opened)
