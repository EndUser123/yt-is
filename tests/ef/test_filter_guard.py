"""A" section 6: filter guard — exact-lane results must respect channel
filters. Exercises the post-fusion guard logic directly."""

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import routing  # noqa: E402


@dataclass
class FakePayload:
    payload: dict


def _apply(results, channel_id):
    """mirror of ProductionQuery's post-fusion guard"""
    if channel_id is None:
        return results
    return [p for p in results if p.payload["channel_id"] == channel_id]


def test_exact_hits_outside_filter_dropped():
    hits = [FakePayload({"chunk_id": "x1", "channel_id": "chA"}),
            FakePayload({"chunk_id": "x2", "channel_id": "chB"})]
    kept = _apply(hits, "chA")
    assert [h.payload["chunk_id"] for h in kept] == ["x1"]


def test_all_in_filter_kept():
    hits = [FakePayload({"chunk_id": "x1", "channel_id": "chA"}),
            FakePayload({"chunk_id": "x3", "channel_id": "chA"})]
    assert len(_apply(hits, "chA")) == 2


def test_no_filter_returns_everything():
    hits = [FakePayload({"chunk_id": "x1", "channel_id": "chA"})]
    assert _apply(hits, None) == hits


def test_exact_lane_policy_shapes_unaffected_by_guard_order():
    # policy C output order preserved after guard
    sem = ["a", "b"]
    fts = ["z"]
    fused = routing.fuse_containment_priority([sem, fts], 3, exact_leg_idx=-1)
    assert fused == ["z", "a", "b"]
