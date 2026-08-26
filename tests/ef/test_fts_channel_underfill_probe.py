"""PROBE (not a regression pin yet): channel-filtered FTS-lane underfill.

codex's open hypothesis, verified structurally here: the exact_strict and
identifier routes pull BM25 candidates from the FTS lane with NO channel
restriction, truncate to the limit, and only then drop rows whose payload
channel mismatches (the post-loop check in relevant()). When the top BM25
hits are dominated by other channels, a channel-filtered query underfills
— returning fewer results than the requested channel actually contains at
deeper FTS ranks, or nothing at all.

These tests assert the CORRECT behavior (full results for a channel that
has matches) and are marked strict xfail: they fail while the defect
exists and will flip to XPASS the moment a fix lands, at which point the
marker gets removed and they become regression pins.

Fully hermetic: tmp FTS5 index, fake Qdrant client, stubbed reopen.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import query_server
from ef.query_server import ProductionQuery

PHRASE = "raft log compaction"


class _FakeEncoder:
    def encode(self, texts, batch_size=16, max_length=512):
        dense = [[0.1] * 4 for _ in texts]
        lex = [{0: 1.0} for _ in texts]
        return dense, lex


class _FakeClient:
    """query_points (semantic legs) -> empty; retrieve(id) -> payload map."""

    def __init__(self, payloads):
        from ef import projection_server as ps
        self.payloads = payloads
        self.point_map = {ps.point_id(cid): cid for cid in payloads}

    def query_points(self, *args, **kwargs):
        return SimpleNamespace(points=[])

    def retrieve(self, collection, ids=None, with_payload=False):
        out = []
        for pid in ids or []:
            cid = self.point_map.get(pid)
            if cid is not None:
                out.append(SimpleNamespace(
                    id=pid, score=1.0, payload=self.payloads[cid]))
        return out


def _payload(chunk_id, chan):
    return {
        "chunk_id": chunk_id,
        "eu_id": f"{chunk_id}:transcript",
        "video_id": chunk_id,
        "title": f"doc {chunk_id}",
        "channel_id": chan,
        "channel_title": chan,
        "start_char": 0,
        "end_char": 100,
    }


@pytest.fixture()
def mixed(monkeypatch, tmp_path):
    """FTS index where channel B owns the top BM25 ranks for the phrase;
    channel A has 10 matching docs ranked strictly below B's 20."""
    fts_path = tmp_path / "fts5.sqlite"
    conn = sqlite3.connect(fts_path)
    conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED)")
    payloads = {}
    # channel B: short docs -> highest bm25 for the phrase
    for i in range(20):
        cid = f"b{i:02d}"
        conn.execute("insert into chunks(text, chunk_id) values (?, ?)",
                     (PHRASE, cid))
        payloads[cid] = _payload(cid, "chanB")
    # channel A: same phrase buried in longer docs -> strictly lower bm25
    filler = " ".join(f"fillerword{i}" for i in range(60))
    for i in range(10):
        cid = f"a{i:02d}"
        conn.execute("insert into chunks(text, chunk_id) values (?, ?)",
                     (f"{filler} {PHRASE} {filler}", cid))
        payloads[cid] = _payload(cid, "chanA")
    conn.commit()
    conn.close()

    monkeypatch.setattr(query_server, "FTS_DB", fts_path)
    monkeypatch.setattr(ProductionQuery, "_reopen",
                        lambda self, eu_id, s, e: "snippet")
    return _FakeClient(payloads)


def _make(client):
    q = ProductionQuery(_FakeEncoder(), generation=1)
    q.client_override = client
    return q


@pytest.fixture()
def patched_client(monkeypatch, mixed):
    monkeypatch.setattr(query_server.server, "client", lambda *a, **k: mixed)
    return mixed


@pytest.mark.xfail(strict=True, reason="FTS lanes filter AFTER truncation: "
                                       "channel-restricted exact queries "
                                       "underfill; fix pending operator go")
def test_exact_route_returns_full_results_for_matching_channel(patched_client):
    q = _make(patched_client)
    res = q.relevant(PHRASE, limit=8, channel_id="chanA", exact=True)
    # chanA has 10 BM25-matching docs below chanB's 20; a correct filtered
    # lane returns 8 of them. The truncating lane returns 0 (top-8 are all
    # chanB, dropped post hoc).
    assert len(res) == 8, (
        f"underfilled: got {len(res)} for a channel holding 10 matches")
    assert all(r.channel_id == "chanA" for r in res)


@pytest.mark.xfail(strict=True, reason="same truncation-then-filter shape "
                                       "on the identifier literal lane")
def test_identifier_route_returns_full_results_for_matching_channel(patched_client):
    q = _make(patched_client)
    res = q.relevant("raft-log-compaction", limit=8, channel_id="chanA")
    assert len(res) == 8, (
        f"underfilled: got {len(res)} for a channel holding 10 matches")
    assert all(r.channel_id == "chanA" for r in res)


def test_unfiltered_control_returns_full_top(patched_client):
    """Control: without a channel filter the same corpus serves 8 results
    (proves the fixture ranks chanB on top and the pipeline works)."""
    q = _make(patched_client)
    res = q.relevant(PHRASE, limit=8, exact=True)
    assert len(res) == 8
    assert all(r.channel_id == "chanB" for r in res)
