"""Mock-based semantic-path test for ProductionQuery.relevant().

Drives the semantic route (route -> filter -> fusion -> reopen -> to_json)
with a fake encoder and a fake Qdrant client — no GPU, no server, no real
corpus DBs (the authority reopen is stubbed). This closes the gap where
the encode->Qdrant->RRF control flow of the consumer query path had zero
automated coverage (found by 3-lens /tp critique f933c7a4128e).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import query_server
from ef.query_server import ProductionQuery


class FakeEncoder:
    """encode() -> (dense_vectors, sparse_weights) like BGEM3Dual."""

    def encode(self, texts, batch_size=16, max_length=512):
        dense = [[0.1, 0.2, 0.3, 0.4] for _ in texts]
        lex = [{0: 1.0, 1: 0.5} for _ in texts]
        return dense, lex


def _point(chunk_id, video_id, score):
    return SimpleNamespace(
        id=chunk_id,
        score=score,
        payload={
            "chunk_id": chunk_id,
            "eu_id": f"{video_id}:transcript",
            "video_id": video_id,
            "title": f"title {video_id}",
            "channel_id": "chanA",
            "channel_title": "Channel A",
            "start_char": 0,
            "end_char": 100,
        },
    )


class FakeClient:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(points=list(self.points))


@pytest.fixture
def patched(monkeypatch):
    def _patch(points):
        fake = FakeClient(points)
        monkeypatch.setattr(query_server.server, "client", lambda *a, **k: fake)
        monkeypatch.setattr(
            ProductionQuery, "_reopen",
            lambda self, eu_id, start, end: "fake snippet text")
        return fake

    return _patch


def _make_query():
    return ProductionQuery(FakeEncoder(), generation=1)


def test_semantic_route_end_to_end(patched):
    pts = [_point("v1:transcript#0", "v1", 0.9),
           _point("v2:transcript#0", "v2", 0.5)]
    fake = patched(pts)
    q = _make_query()
    res = q.relevant("some natural language question", limit=2)
    assert len(res) == 2
    # semantic route was taken (no exact_fts5 path)
    assert "exact_fts5" not in res[0].retrieval_paths
    assert "fused" in res[0].retrieval_paths
    # evidence fields populated from payload
    assert res[0].video_id == "v1"
    assert res[0].url == "https://youtu.be/v1"
    assert res[0].channel_title == "Channel A"
    assert res[0].snippet == "fake snippet text"
    # to_json is lossless for consumers
    j = res[0].to_json()
    assert j["chunk_id"] == "v1:transcript#0"
    assert j["retrieval_paths"] == list(res[0].retrieval_paths)
    # the fused query actually carried dense + sparse prefetch legs
    call = fake.calls[0]
    assert len(call["prefetch"]) == 2


def test_semantic_route_respects_channel_filter(patched):
    pts = [_point("v1:transcript#0", "v1", 0.9),
           _point("v2:transcript#0", "v2", 0.5)]
    patched(pts)
    q = _make_query()
    res = q.relevant("another question", limit=2, channel_id="chanOTHER")
    # all payloads carry chanA -> filter excludes everything
    assert res == []
