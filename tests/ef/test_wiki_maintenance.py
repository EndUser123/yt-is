"""L-gate: /wiki maintenance-mode integration tests."""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from ef_wiki_maintenance import (  # noqa: E402
    mode_contradiction, mode_evidence, mode_staleness)


class _FakeR:
    def __init__(self, chunk_id, channel, title="t", score=0.5,
                 video_id=None):
        self.chunk_id = chunk_id
        self.channel_id = channel
        self.channel_title = channel
        self.title = title
        self.score = score
        self.video_id = video_id or chunk_id.split(":")[0]
        self.eu_id = chunk_id
        self.url = f"https://youtu.be/{self.video_id}"
        self.start_char, self.end_char = 0, 100
        self.snippet = "evidence snippet"
        self.retrieval_paths = ("fused",)


class _FakePQ:
    def __init__(self, results):
        self._r = results

    def relevant(self, q, limit=8, **kw):
        return self._r[:limit]


def test_evidence_candidates_are_unvalidated():
    pq = _FakePQ([_FakeR("v1:transcript", "chan1")])
    out = mode_evidence(pq, "some claim", 5)
    assert out["candidates"][0]["role"] == "retrieved_candidate"
    assert "UNVALIDATED" in out["note"]
    assert "rank is not truth" in out["note"]


def test_high_rank_not_auto_support():
    """L-gate: a rank-1 hit must NOT be labeled supporting evidence."""
    pq = _FakePQ([_FakeR("v1:transcript", "chan1", score=0.99)])
    out = mode_evidence(pq, "claim", 5)
    assert "support" not in out["candidates"][0]["role"]


def test_contradiction_source_diversity():
    same = _FakeR("v1:transcript", "claimSrc")
    diff = _FakeR("v2:transcript", "otherSrc")
    pq = _FakePQ([same, diff])
    out = mode_contradiction(pq, "claim", ["claimSrc"], 5)
    assert all(not c["same_source_as_claim"]
               for c in out["diverse_candidates"])
    roles = {c["role"] for c in out["all_candidates"]}
    assert roles == {"contradiction_candidate"}


def test_high_rank_not_auto_contradiction():
    pq = _FakePQ([_FakeR("v1:transcript", "x", score=0.99)])
    out = mode_contradiction(pq, "claim", [], 5)
    assert out["diverse_candidates"][0]["role"] == "contradiction_candidate"
    assert "contradiction judgment" in out["note"]


def test_staleness_no_timestamps_no_auto_stale():
    pq = _FakePQ([_FakeR("v1:transcript", "chan")])
    out = mode_staleness(pq, "claim", "2026-08-01T00:00:00Z", 5)
    assert out["signal"] == "newer_material_candidate_needs_review"
    assert "NOT staleness judgment" in out["note"]


def test_staleness_no_candidates():
    out = mode_staleness(_FakePQ([]), "claim", "2026-08-01T00:00:00Z", 5)
    assert out["signal"] == "no_new_evidence"


def test_staleness_unknown_last_verified():
    out = mode_staleness(_FakePQ([_FakeR("v1", "c")]), "claim", "", 5)
    assert out["signal"] == "unknown_last_verified"


def test_provenance_retained():
    pq = _FakePQ([_FakeR("vidX:transcript", "chan")])
    c = mode_evidence(pq, "claim", 5)["candidates"][0]
    for k in ("source_url", "video_id", "char_span", "eu_id",
              "evidence_text", "rank"):
        assert k in c
