"""Offline tests for ef.interest_candidates; synthetic fictional topics only."""

from __future__ import annotations

import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ef.interest_candidates import (
    BASELINE_POLICY,
    BOOTSTRAP_POLICY,
    DEFAULT_MAX_CLUSTERS_PER_CALL,
    CandidateBatch,
    CandidatePlan,
    PlanCoverageError,
    build_baseline_plan,
    build_bootstrap_plan,
    plan_coverage,
    priority_score,
    validate_plan_coverage,
)

_TOPICS = [
    "distributed databases",
    "compiler optimization",
    "gardening",
    "astronomy",
    "urban beekeeping",
    "sensor networks",
    "paper marbling",
    "tide pooling",
    "histology",
    "letterpress",
]


def entry(cid: int, **overrides) -> dict:
    """Synthetic inventory entry with fictional topic label."""
    base = dict(
        cluster_id=cid,
        label=f"{_TOPICS[cid % len(_TOPICS)]} #{cid}",
        member_count=5 + cid,
        video_count=10 + cid,
        channels=max(1, cid % 40),
        documents=20 + cid * 3,
        active_months=4 + cid % 12,
        first_month="2024-01",
        last_month="2025-06",
        phase=None,
        sources=[["youtube", 10 + cid], ["discord", cid % 5]],
        terms=[f"term{cid}"],
        evidence_signature=f"sig{cid:04d}",
    )
    base.update(overrides)
    return base


def many(n: int, **overrides) -> list[dict]:
    return [entry(i, **overrides) for i in range(1, n + 1)]


def test_empty_entries_bootstrap():
    plan = build_bootstrap_plan([])
    assert plan.batches == ()
    assert plan.metrics.coverage_pct == 100.0
    assert plan.metrics.dropped_count == 0
    validate_plan_coverage(plan)


def test_empty_entries_baseline():
    plan = build_baseline_plan([])
    assert plan.batches == ()
    assert plan.metrics.coverage_pct == 100.0
    validate_plan_coverage(plan)


def test_single_cluster_one_batch():
    plan = build_bootstrap_plan([entry(1)])
    assert len(plan.batches) == 1
    assert plan.batches[0].cluster_ids == (1,)
    assert plan.batches[0].batch_id == "b001"
    validate_plan_coverage(plan)


def test_exactly_25_one_batch():
    plan = build_bootstrap_plan(many(25))
    assert len(plan.batches) == 1
    assert len(plan.batches[0].cluster_ids) == 25


def test_26_two_batches():
    plan = build_bootstrap_plan(many(26))
    assert [len(b.cluster_ids) for b in plan.batches] == [25, 1]
    validate_plan_coverage(plan)


def test_137_batches_sizes_and_exact_coverage():
    entries = many(137)
    plan = build_bootstrap_plan(entries)
    sizes = sorted((len(b.cluster_ids) for b in plan.batches), reverse=True)
    assert sizes == [25, 25, 25, 25, 25, 12]
    seen = [cid for b in plan.batches for cid in b.cluster_ids]
    assert len(seen) == 137
    assert set(seen) == {e["cluster_id"] for e in entries}
    assert len(set(seen)) == 137
    assert plan.batch_size == DEFAULT_MAX_CLUSTERS_PER_CALL
    validate_plan_coverage(plan)


def test_custom_max_per_call():
    entries = many(30)
    assert all(
        len(b.cluster_ids) <= 25 for b in build_bootstrap_plan(entries).batches
    )
    plan = build_bootstrap_plan(entries, max_per_call=7)
    assert len(plan.batches) == 5
    assert [len(b.cluster_ids) for b in plan.batches] == [7, 7, 7, 7, 2]
    validate_plan_coverage(plan, max_per_call=7)


def test_determinism_shuffle_and_now():
    entries = many(50)
    shuffled = list(entries)
    random.Random(42).shuffle(shuffled)
    p1 = build_bootstrap_plan(entries, now="2026-01-01T00:00:00")
    p2 = build_bootstrap_plan(shuffled, now="2027-12-31T23:59:59")
    assert p1.plan_id == p2.plan_id
    assert [list(b.cluster_ids) for b in p1.batches] == [
        list(b.cluster_ids) for b in p2.batches
    ]


def test_priority_orders_but_never_drops():
    low = entry(1, channels=1, documents=1, active_months=1, sources=[["youtube", 1]])
    high = entry(2, channels=50, documents=900, active_months=30, sources=[["youtube", 9]])
    plan = build_bootstrap_plan([low, high])
    covered = {cid for b in plan.batches for cid in b.cluster_ids}
    assert covered == {1, 2}
    assert plan.batches[0].cluster_ids[0] == 2


def test_emerging_bonus_orders_first():
    steady = entry(10, channels=20, documents=100, active_months=10, phase=None)
    emerging = entry(20, channels=20, documents=100, active_months=10, phase="emerging")
    plan = build_bootstrap_plan([steady, emerging])
    assert plan.batches[0].cluster_ids[0] == 20
    assert plan.priority_scores[20] > plan.priority_scores[10]


def test_no_single_count_dominates():
    # huge documents, low breadth vs moderate mass, high channels
    narrow = entry(1, channels=3, documents=1000, active_months=6, sources=[["youtube", 5]])
    broad = entry(2, channels=40, documents=300, active_months=24, sources=[["youtube", 6], ["discord", 3]])
    maxima = {
        "channels": 40.0,
        "sources": 2.0,
        "active_months": 24.0,
        "documents": 1000.0,
    }
    s_narrow = priority_score(narrow, maxima)
    s_broad = priority_score(broad, maxima)
    assert s_broad > s_narrow
    plan = build_bootstrap_plan([narrow, broad])
    assert plan.batches[0].cluster_ids[0] == 2


def test_tiebreak_cluster_id_ascending():
    e1 = entry(7, channels=10, documents=50, active_months=8)
    e2 = entry(3, channels=10, documents=50, active_months=8)
    plan = build_bootstrap_plan([e1, e2])
    assert plan.batches[0].cluster_ids == (3, 7)


def test_baseline_top25_of_60():
    entries = many(60)
    # narrow low-breadth cluster guaranteed outside top-25
    entries.append(entry(999, channels=1, documents=2, active_months=1))
    plan = build_baseline_plan(entries)
    assert plan.policy == BASELINE_POLICY
    planned = plan.batches[0].cluster_ids
    assert len(planned) == 25
    expected = sorted(
        (e for e in entries),
        key=lambda e: (-e["channels"], -e["documents"], e["cluster_id"]),
    )[:25]
    assert list(planned) == [e["cluster_id"] for e in expected]
    assert 999 not in planned
    assert plan.metrics.dropped_count == 61 - 25
    assert plan.metrics.eligible_count == 61
    assert plan.metrics.planned_count == 25
    validate_plan_coverage(plan)


def test_to_dict_from_dict_round_trip():
    plan = build_bootstrap_plan(many(30), exclusions={"too_small": 3})
    rebuilt = CandidatePlan.from_dict(plan.to_dict())
    assert rebuilt.to_dict() == plan.to_dict()
    baseline = build_baseline_plan(many(40))
    assert CandidatePlan.from_dict(baseline.to_dict()).to_dict() == baseline.to_dict()


def _mutate(plan: CandidateBatch = None, **kwargs) -> CandidatePlan:
    return replace(plan, **kwargs)


def test_validate_raises_on_duplicate():
    plan = build_bootstrap_plan(many(3))
    batches = (CandidateBatch("b001", (1, 2)), CandidateBatch("b002", (2, 3)))
    broken = _mutate(plan, batches=batches)
    with pytest.raises(PlanCoverageError):
        validate_plan_coverage(broken)


def test_validate_raises_on_missing():
    plan = build_bootstrap_plan(many(4))
    batches = (CandidateBatch("b001", (1, 2)), CandidateBatch("b002", (3,)))
    broken = _mutate(plan, batches=batches)
    with pytest.raises(PlanCoverageError):
        validate_plan_coverage(broken)


def test_validate_raises_on_oversized_batch():
    plan = build_bootstrap_plan(many(30))
    batches = (CandidateBatch("b001", tuple(range(1, 31))),)
    broken = _mutate(plan, batches=batches)
    with pytest.raises(PlanCoverageError):
        validate_plan_coverage(broken)


def test_plan_coverage_reports_duplicates_and_missing():
    plan = build_bootstrap_plan(many(5))
    broken = _mutate(
        plan,
        batches=(CandidateBatch("b001", (1, 2, 2)), CandidateBatch("b002", (4,))),
    )
    cov = plan_coverage(broken)
    assert cov["covered"] == 3
    assert cov["eligible"] == 5
    assert cov["duplicate_cluster_ids"] == [2]
    assert cov["missing_cluster_ids"] == [3, 5]


def test_signatures_carried_through():
    entries = many(12)
    plan = build_bootstrap_plan(entries)
    assert plan.signatures == {e["cluster_id"]: e["evidence_signature"] for e in entries}
    baseline = build_baseline_plan(entries)
    assert baseline.signatures == plan.signatures
