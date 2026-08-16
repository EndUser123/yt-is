"""Synthetic adaptive coordinator tests; no subprocess or NotebookLM calls."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from csf.adaptive_worker_scheduler import (
    AdaptiveWorkerScheduler,
    AssignmentLedger,
    HealthSample,
    SchedulerConfig,
    SchedulerSnapshot,
    WorkerIdentity,
)


REQUIRED_TELEMETRY_FIELDS = {
    "run_id",
    "lane",
    "worker_id",
    "target_workers",
    "active_workers",
    "queued_batches",
    "reason",
    "policy_version",
    "timestamp_monotonic_s",
}


def _identities():
    return tuple(
        WorkerIdentity(i, f"profile-{i}", f"notebook-{i}", f"state-{i}", f"browser-{i}")
        for i in range(1, 5)
    )


@dataclass
class FakeAdaptiveCoordinator:
    """Minimal fake launcher exercising the same policy/ledger boundaries."""

    scheduler: AdaptiveWorkerScheduler
    ledger: AssignmentLedger

    def __post_init__(self):
        self.events: list[dict[str, object]] = []
        self.assignment_seq = 0

    def claim(self, worker_id: int, batch_id: str, outcome: str) -> None:
        self.assignment_seq += 1
        assignment_id = f"assignment-{self.assignment_seq}"
        self.scheduler.mark_started(worker_id, now_s=float(self.assignment_seq), assignment_id=assignment_id)
        self.ledger.claim(assignment_id, (batch_id,))
        self.events.append(
            {
                "run_id": "fake-run",
                "lane": "fake-lane",
                "worker_id": worker_id,
                "target_workers": self.scheduler.target_workers,
                "active_workers": 1,
                "queued_batches": 0,
                "reason": "claimed",
                "policy_version": "adaptive-worker-scheduler-v1",
                "timestamp_monotonic_s": float(self.assignment_seq),
                "assignment_id": assignment_id,
                "batch_ids": [batch_id],
            }
        )
        if outcome == "completed":
            self.ledger.complete(assignment_id)
            self.scheduler.mark_completed(worker_id, now_s=float(self.assignment_seq) + 0.1, assignment_id=assignment_id)
        elif outcome == "requeue":
            self.ledger.fail(assignment_id, requeue=True)
            self.scheduler.mark_failed(worker_id, now_s=float(self.assignment_seq) + 0.1, assignment_id=assignment_id, failure_kind="timeout")
        elif outcome == "terminal_failed":
            self.ledger.fail(assignment_id, requeue=False)
            self.scheduler.mark_failed(worker_id, now_s=float(self.assignment_seq) + 0.1, assignment_id=assignment_id, failure_kind="result_parse")
        else:
            raise ValueError(outcome)


def _coordinator():
    return FakeAdaptiveCoordinator(
        AdaptiveWorkerScheduler(_identities(), SchedulerConfig(1, 4, cooldown_s=0, health_window=2)),
        AssignmentLedger(),
    )


def test_fake_coordinator_covers_growth_cooldown_draining_and_conservation():
    coordinator = _coordinator()
    scheduler = coordinator.scheduler
    decision = scheduler.choose(SchedulerSnapshot(0, 4, active_worker_ids=frozenset({1})))
    assert decision.reason == "health_missing_or_failure"
    scheduler.apply(decision, now_s=0)
    decision = scheduler.choose(
        SchedulerSnapshot(1, 4, active_worker_ids=frozenset({1}), health_samples=(HealthSample(1, True), HealthSample(1, True)))
    )
    assert decision.target_workers == 2
    scheduler.apply(decision, now_s=1)

    coordinator.claim(1, "batch-a", "completed")
    coordinator.claim(2, "batch-b", "completed")
    decision = scheduler.choose(SchedulerSnapshot(2, 0))
    assert decision.target_workers == 1
    scheduler.mark_started(2, now_s=2, assignment_id="active-drain")
    scheduler.apply(decision, now_s=2)
    assert scheduler.status(2) == "draining"
    scheduler.mark_completed(2, now_s=3, assignment_id="active-drain")
    assert coordinator.ledger.accounting().balanced


def test_fake_coordinator_distinguishes_requeue_from_terminal_failure_and_rejects_duplicates():
    coordinator = FakeAdaptiveCoordinator(
        AdaptiveWorkerScheduler(_identities(), SchedulerConfig(2, 4, cooldown_s=0, health_window=2)),
        AssignmentLedger(),
    )
    coordinator.claim(1, "batch-a", "requeue")
    coordinator.claim(2, "batch-a", "completed")
    coordinator.claim(2, "batch-b", "terminal_failed")
    with pytest.raises(ValueError, match="already owned or terminal"):
        coordinator.ledger.claim("duplicate", ("batch-b",))
    accounting = coordinator.ledger.accounting()
    assert accounting.balanced
    assert accounting.completed == 1
    assert accounting.terminal_failed == 1
    assert accounting.requeued == 0


def test_fake_coordinator_assignment_events_have_required_fields():
    coordinator = _coordinator()
    coordinator.claim(1, "batch-a", "completed")
    assert coordinator.events
    assert REQUIRED_TELEMETRY_FIELDS <= coordinator.events[0].keys()
    assert coordinator.events[0]["assignment_id"]
    assert coordinator.events[0]["batch_ids"] == ["batch-a"]
