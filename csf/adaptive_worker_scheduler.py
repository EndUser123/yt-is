"""Pure bounded policy for opt-in industrial worker scaling.

The scheduler owns target capacity and worker lifecycle state, but it does not
launch, cancel, or requeue work.  Callers supply observations and perform the
returned assignment/transition actions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


WorkerStatus = Literal["idle", "active", "draining", "quarantined"]
BatchStatus = Literal[
    "queued",
    "in_flight",
    "completed",
    "terminal_failed",
    "requeued",
]


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    worker_id: int
    profile: str
    notebook_title: str
    state_path: str
    client_namespace: str
    account_profile: str = ""

    def __post_init__(self) -> None:
        if self.worker_id < 1:
            raise ValueError("worker_id must be >= 1")
        for name in ("profile", "notebook_title", "state_path", "client_namespace"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    initial_workers: int
    max_workers: int
    min_workers: int = 1
    scale_up_backlog: int = 2
    scale_down_backlog: int = 0
    cooldown_s: float = 60.0
    health_window: int = 2
    max_failure_rate: float = 0.25
    max_recovery_attempts: int = 2

    def __post_init__(self) -> None:
        if self.min_workers < 1:
            raise ValueError("min_workers must be >= 1")
        if self.initial_workers < self.min_workers:
            raise ValueError("initial_workers must be >= min_workers")
        if self.max_workers < self.initial_workers:
            raise ValueError("max_workers must be >= initial_workers")
        if self.scale_up_backlog < 0 or self.scale_down_backlog < 0:
            raise ValueError("backlog thresholds must be >= 0")
        if self.cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")
        if self.health_window < 1:
            raise ValueError("health_window must be >= 1")
        if not 0 <= self.max_failure_rate <= 1:
            raise ValueError("max_failure_rate must be between 0 and 1")
        if self.max_recovery_attempts < 0:
            raise ValueError("max_recovery_attempts must be >= 0")


@dataclass(frozen=True, slots=True)
class HealthSample:
    worker_id: int
    success: bool
    failure_kind: str = ""


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    now_s: float
    backlog_batches: int
    active_worker_ids: frozenset[int] = frozenset()
    health_samples: tuple[HealthSample, ...] = ()
    input_closed: bool = True

    def __post_init__(self) -> None:
        if self.backlog_batches < 0:
            raise ValueError("backlog_batches must be >= 0")
        if not isinstance(self.input_closed, bool):
            raise ValueError("input_closed must be boolean")


@dataclass(frozen=True, slots=True)
class ScaleDecision:
    target_workers: int
    reason: str
    changed: bool


@dataclass(frozen=True, slots=True)
class Transition:
    now_s: float
    worker_id: int
    event: str
    reason: str
    target_workers: int


@dataclass(frozen=True, slots=True)
class AssignmentAccounting:
    input_batches: int
    completed: int
    terminal_failed: int
    requeued: int
    still_in_flight: int

    @property
    def balanced(self) -> bool:
        return self.input_batches == (
            self.completed + self.terminal_failed + self.requeued + self.still_in_flight
        )


class AssignmentLedger:
    """Batch-level ownership ledger used by adaptive dispatch.

    Active callers register batches when they enter the queue. Requeued batches
    retain their identity and may be claimed again without becoming duplicates.
    The ledger still permits direct claim of an unregistered batch for small
    compatibility/test callers.
    """

    def __init__(self) -> None:
        self._batches: dict[str, BatchStatus] = {}
        self._assignments: dict[str, tuple[str, ...]] = {}

    def checkpoint(self) -> dict[str, object]:
        """Return a JSON-safe ownership checkpoint for restart recovery.

        ``in_flight`` is deliberately retained.  A restarted coordinator must
        reconcile that ownership with a terminal receipt before it can claim
        the batch again; silently converting it to queued would permit a
        duplicate assignment.
        """
        return {
            "schema_version": 1,
            "batches": dict(self._batches),
            "assignments": {
                assignment_id: list(batch_ids)
                for assignment_id, batch_ids in self._assignments.items()
            },
        }

    @classmethod
    def from_checkpoint(cls, payload: object) -> "AssignmentLedger":
        """Restore a validated ownership checkpoint without changing status."""
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("invalid assignment checkpoint")
        raw_batches = payload.get("batches")
        raw_assignments = payload.get("assignments")
        if not isinstance(raw_batches, dict) or not isinstance(raw_assignments, dict):
            raise ValueError("assignment checkpoint is missing ownership maps")
        valid_statuses = {"queued", "in_flight", "completed", "terminal_failed", "requeued"}
        if any(
            not isinstance(batch_id, str)
            or not batch_id.strip()
            or not isinstance(status, str)
            or status not in valid_statuses
            for batch_id, status in raw_batches.items()
        ):
            raise ValueError("assignment checkpoint contains invalid batch status")

        ledger = cls()
        ledger._batches = dict(raw_batches)  # type: ignore[assignment]
        assigned_batch_ids: set[str] = set()
        for assignment_id, raw_batch_ids in raw_assignments.items():
            if (
                not isinstance(assignment_id, str)
                or not assignment_id.strip()
                or not isinstance(raw_batch_ids, list)
                or not raw_batch_ids
                or len(set(raw_batch_ids)) != len(raw_batch_ids)
                or any(not isinstance(batch_id, str) or not batch_id.strip() for batch_id in raw_batch_ids)
            ):
                raise ValueError("assignment checkpoint contains invalid assignment")
            batch_ids = tuple(raw_batch_ids)
            if any(batch_id not in ledger._batches for batch_id in batch_ids):
                raise ValueError("assignment checkpoint references an unknown batch")
            if assigned_batch_ids.intersection(batch_ids):
                raise ValueError("assignment checkpoint assigns a batch more than once")
            if any(ledger._batches[batch_id] != "in_flight" for batch_id in batch_ids):
                raise ValueError("assignment checkpoint owns a non-in-flight batch")
            assigned_batch_ids.update(batch_ids)
            ledger._assignments[assignment_id] = batch_ids
        in_flight_batch_ids = {
            batch_id
            for batch_id, status in ledger._batches.items()
            if status == "in_flight"
        }
        if assigned_batch_ids != in_flight_batch_ids:
            raise ValueError("assignment checkpoint in-flight ownership does not reconcile")
        return ledger

    def register(self, batch_ids: tuple[str, ...]) -> None:
        if not batch_ids or len(set(batch_ids)) != len(batch_ids):
            raise ValueError("batch_ids must be non-empty and unique")
        for batch_id in batch_ids:
            if not batch_id.strip():
                raise ValueError("batch_id must be non-empty")
            if batch_id in self._batches:
                raise ValueError(f"batch is already registered: {batch_id}")
        for batch_id in batch_ids:
            self._batches[batch_id] = "queued"

    def claim(self, assignment_id: str, batch_ids: tuple[str, ...]) -> None:
        if not assignment_id.strip():
            raise ValueError("assignment_id must be non-empty")
        if assignment_id in self._assignments:
            raise ValueError(f"assignment_id already exists: {assignment_id}")
        if not batch_ids or len(set(batch_ids)) != len(batch_ids):
            raise ValueError("batch_ids must be non-empty and unique")
        for batch_id in batch_ids:
            status = self._batches.get(batch_id)
            if status not in {None, "queued", "requeued"}:
                raise ValueError(f"batch is already owned or terminal: {batch_id}")
        for batch_id in batch_ids:
            self._batches[batch_id] = "in_flight"
        self._assignments[assignment_id] = batch_ids

    def complete(self, assignment_id: str) -> None:
        for batch_id in self._assignment(assignment_id):
            self._require_status(batch_id, "in_flight")
            self._batches[batch_id] = "completed"

    def fail(self, assignment_id: str, *, requeue: bool) -> None:
        next_status: BatchStatus = "requeued" if requeue else "terminal_failed"
        for batch_id in self._assignment(assignment_id):
            self._require_status(batch_id, "in_flight")
            self._batches[batch_id] = next_status

    def accounting(self) -> AssignmentAccounting:
        counts = {
            status: 0
            for status in ("queued", "completed", "terminal_failed", "requeued", "in_flight")
        }
        for status in self._batches.values():
            counts[status] += 1
        result = AssignmentAccounting(
            input_batches=len(self._batches),
            completed=counts["completed"],
            terminal_failed=counts["terminal_failed"],
            requeued=counts["requeued"],
            still_in_flight=counts["queued"] + counts["in_flight"],
        )
        if not result.balanced:
            raise AssertionError("assignment accounting is unbalanced")
        return result

    def _assignment(self, assignment_id: str) -> tuple[str, ...]:
        try:
            return self._assignments[assignment_id]
        except KeyError as exc:
            raise ValueError(f"unknown assignment_id: {assignment_id}") from exc

    def _require_status(self, batch_id: str, expected: BatchStatus) -> None:
        if self._batches.get(batch_id) != expected:
            raise ValueError(f"batch {batch_id} is not {expected}")


@dataclass(slots=True)
class _WorkerState:
    identity: WorkerIdentity
    status: WorkerStatus = "idle"
    draining_active: bool = False
    last_failure_kind: str = ""
    quarantined_at_s: float | None = None
    recovery_attempts: int = 0


class AdaptiveWorkerScheduler:
    """Deterministic one-step-at-a-time adaptive capacity policy."""

    def __init__(self, identities: tuple[WorkerIdentity, ...], config: SchedulerConfig) -> None:
        if len(identities) < config.max_workers:
            raise ValueError("identities must cover max_workers")
        self._validate_identities(identities)
        self.config = config
        self._workers = {item.worker_id: _WorkerState(item) for item in identities}
        self._target_workers = config.initial_workers
        self._last_scale_s: float | None = None
        self._transitions: list[Transition] = []
        self._active_assignments: dict[int, str] = {}

    @staticmethod
    def _validate_identities(identities: tuple[WorkerIdentity, ...]) -> None:
        if len({item.worker_id for item in identities}) != len(identities):
            raise ValueError("worker_id values must be unique")
        for field_name in ("profile", "notebook_title"):
            values = [getattr(item, field_name).strip() for item in identities]
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} values must be unique")
        for field_name in ("state_path", "client_namespace"):
            values = [
                os.path.normcase(os.path.normpath(getattr(item, field_name).strip()))
                for item in identities
            ]
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} values must be unique")

    @property
    def target_workers(self) -> int:
        return self._target_workers

    @property
    def transitions(self) -> tuple[Transition, ...]:
        return tuple(self._transitions)

    def status(self, worker_id: int) -> WorkerStatus:
        return self._workers[worker_id].status

    def choose(self, snapshot: SchedulerSnapshot) -> ScaleDecision:
        """Choose at most one target increment/decrement for this observation."""
        target = self._target_workers
        reason = "at_max"
        if snapshot.backlog_batches >= self.config.scale_up_backlog and target < self.config.max_workers:
            if not snapshot.active_worker_ids:
                reason = "workers_not_busy"
            elif self._cooldown_active(snapshot.now_s):
                reason = "cooldown"
            elif not self._health_allows_scale_up(snapshot.health_samples):
                reason = "health_missing_or_failure"
            else:
                target += 1
                reason = "backlog_high"
        elif snapshot.backlog_batches <= self.config.scale_down_backlog and target > self.config.min_workers:
            if not snapshot.input_closed:
                reason = "input_open"
            elif self._cooldown_active(snapshot.now_s):
                reason = "cooldown"
            else:
                target -= 1
                reason = "backlog_low"
        elif target == self.config.min_workers:
            reason = "at_min"
        elif snapshot.backlog_batches < self.config.scale_up_backlog:
            reason = "backlog_below_scale_up"
        return ScaleDecision(target, reason, target != self._target_workers)

    def apply(self, decision: ScaleDecision, *, now_s: float) -> tuple[Transition, ...]:
        if decision.target_workers < self.config.min_workers or decision.target_workers > self.config.max_workers:
            raise ValueError("decision target is outside configured bounds")
        if decision.target_workers == self._target_workers:
            return ()
        old_target = self._target_workers
        self._target_workers = decision.target_workers
        self._last_scale_s = now_s
        events: list[Transition] = [
            Transition(now_s, 0, "capacity_changed", decision.reason, self._target_workers)
        ]
        if self._target_workers < old_target:
            candidates = sorted(
                (
                    worker_id
                    for worker_id, worker in self._workers.items()
                    if worker_id > self._target_workers and worker.status == "active"
                ),
                reverse=True,
            )
            if not candidates:
                candidates = sorted(
                    (
                        worker_id
                        for worker_id, worker in self._workers.items()
                        if worker_id > self._target_workers and worker.status == "idle"
                    ),
                    reverse=True,
                )
            if candidates:
                worker_id = candidates[0]
                worker = self._workers[worker_id]
                worker.draining_active = worker.status == "active"
                worker.status = "draining"
                events.append(Transition(now_s, worker_id, "worker_draining", decision.reason, self._target_workers))
        else:
            for worker_id in range(old_target + 1, self._target_workers + 1):
                worker = self._workers[worker_id]
                if worker.status == "draining" and not worker.draining_active:
                    worker.status = "idle"
        self._transitions.extend(events)
        return tuple(events)

    def eligible_worker_ids(self) -> tuple[int, ...]:
        return tuple(
            worker_id
            for worker_id in sorted(self._workers)
            if worker_id <= self._target_workers and self._workers[worker_id].status == "idle"
        )

    def mark_started(self, worker_id: int, *, now_s: float, assignment_id: str) -> Transition:
        worker = self._workers[worker_id]
        if worker.status != "idle":
            raise ValueError(f"worker {worker_id} is not idle")
        if not assignment_id.strip():
            raise ValueError("assignment_id must be non-empty")
        worker.status = "active"
        self._active_assignments[worker_id] = assignment_id
        transition = Transition(now_s, worker_id, "assignment_started", assignment_id, self._target_workers)
        self._transitions.append(transition)
        return transition

    def mark_completed(self, worker_id: int, *, now_s: float, assignment_id: str) -> Transition:
        worker = self._workers[worker_id]
        if worker.status not in {"active", "draining"}:
            raise ValueError(f"worker {worker_id} is not active")
        self._require_assignment_owner(worker_id, assignment_id)
        worker.status = "idle"
        worker.draining_active = False
        worker.last_failure_kind = ""
        worker.quarantined_at_s = None
        worker.recovery_attempts = 0
        del self._active_assignments[worker_id]
        transition = Transition(now_s, worker_id, "assignment_completed", assignment_id, self._target_workers)
        self._transitions.append(transition)
        return transition

    def mark_failed(self, worker_id: int, *, now_s: float, assignment_id: str, failure_kind: str) -> Transition:
        worker = self._workers[worker_id]
        if worker.status not in {"active", "draining"}:
            raise ValueError(f"worker {worker_id} is not active")
        self._require_assignment_owner(worker_id, assignment_id)
        worker.status = "quarantined"
        worker.draining_active = False
        worker.last_failure_kind = failure_kind
        worker.quarantined_at_s = now_s
        del self._active_assignments[worker_id]
        transition = Transition(now_s, worker_id, "assignment_failed", assignment_id, self._target_workers)
        self._transitions.append(transition)
        return transition

    def recover_quarantined(self, *, now_s: float) -> tuple[Transition, ...]:
        """Return boundedly quarantined workers to idle after cooldown.

        A worker process is recreated for each assignment, so a transient
        launch/worker failure should not permanently remove its capacity. The
        recovery budget prevents a persistent failure from becoming an
        infinite retry loop; a successful assignment resets that budget.
        """
        events: list[Transition] = []
        for worker_id, worker in sorted(self._workers.items()):
            if worker.status != "quarantined":
                continue
            if worker.recovery_attempts >= self.config.max_recovery_attempts:
                continue
            if worker.quarantined_at_s is None:
                continue
            if now_s - worker.quarantined_at_s < self.config.cooldown_s:
                continue
            worker.status = "idle"
            worker.draining_active = False
            worker.recovery_attempts += 1
            event = Transition(
                now_s,
                worker_id,
                "worker_recovered",
                worker.last_failure_kind or "quarantine_cooldown",
                self._target_workers,
            )
            self._transitions.append(event)
            events.append(event)
        return tuple(events)

    def checkpoint(self) -> dict[str, object]:
        """Return worker and assignment ownership needed after a restart."""
        return {
            "schema_version": 1,
            "target_workers": self._target_workers,
            "last_scale_s": self._last_scale_s,
            "workers": [
                {
                    "worker_id": worker_id,
                    "status": worker.status,
                    "draining_active": worker.draining_active,
                    "last_failure_kind": worker.last_failure_kind,
                    "quarantined_at_s": worker.quarantined_at_s,
                    "recovery_attempts": worker.recovery_attempts,
                    "assignment_id": self._active_assignments.get(worker_id),
                }
                for worker_id, worker in sorted(self._workers.items())
            ],
            "transitions": [
                {
                    "now_s": transition.now_s,
                    "worker_id": transition.worker_id,
                    "event": transition.event,
                    "reason": transition.reason,
                    "target_workers": transition.target_workers,
                }
                for transition in self._transitions
            ],
        }

    @classmethod
    def from_checkpoint(
        cls,
        identities: tuple[WorkerIdentity, ...],
        config: SchedulerConfig,
        payload: object,
    ) -> "AdaptiveWorkerScheduler":
        """Restore scheduler state and reject incomplete ownership metadata."""
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("invalid scheduler checkpoint")
        scheduler = cls(identities, config)
        target_workers = payload.get("target_workers")
        if (
            isinstance(target_workers, bool)
            or not isinstance(target_workers, int)
            or not config.min_workers <= target_workers <= config.max_workers
        ):
            raise ValueError("scheduler checkpoint target is outside configured bounds")
        raw_workers = payload.get("workers")
        if not isinstance(raw_workers, list) or len(raw_workers) != len(scheduler._workers):
            raise ValueError("scheduler checkpoint worker set is incomplete")
        seen: set[int] = set()
        for raw_worker in raw_workers:
            if not isinstance(raw_worker, dict):
                raise ValueError("scheduler checkpoint contains an invalid worker")
            worker_id = raw_worker.get("worker_id")
            if (
                isinstance(worker_id, bool)
                or not isinstance(worker_id, int)
                or worker_id not in scheduler._workers
                or worker_id in seen
            ):
                raise ValueError("scheduler checkpoint contains an invalid worker identity")
            status = raw_worker.get("status")
            if status not in {"idle", "active", "draining", "quarantined"}:
                raise ValueError("scheduler checkpoint contains an invalid worker status")
            assignment_id = raw_worker.get("assignment_id")
            if status in {"active", "draining"} and (
                not isinstance(assignment_id, str) or not assignment_id.strip()
            ):
                raise ValueError("active worker is missing assignment ownership")
            if status not in {"active", "draining"} and assignment_id is not None:
                raise ValueError("inactive worker has assignment ownership")
            worker = scheduler._workers[worker_id]
            worker.status = status
            worker.draining_active = bool(raw_worker.get("draining_active"))
            worker.last_failure_kind = str(raw_worker.get("last_failure_kind", ""))
            quarantined_at_s = raw_worker.get("quarantined_at_s")
            if quarantined_at_s is not None and (
                isinstance(quarantined_at_s, bool)
                or not isinstance(quarantined_at_s, (int, float))
            ):
                raise ValueError("scheduler checkpoint has invalid quarantine time")
            worker.quarantined_at_s = quarantined_at_s
            recovery_attempts = raw_worker.get("recovery_attempts", 0)
            if (
                isinstance(recovery_attempts, bool)
                or not isinstance(recovery_attempts, int)
                or recovery_attempts < 0
            ):
                raise ValueError("scheduler checkpoint has invalid recovery attempts")
            worker.recovery_attempts = recovery_attempts
            if status == "quarantined" and quarantined_at_s is None:
                raise ValueError("quarantined worker is missing quarantine time")
            if recovery_attempts > config.max_recovery_attempts:
                raise ValueError("scheduler checkpoint recovery attempts exceed configured bound")
            if assignment_id is not None:
                scheduler._active_assignments[worker_id] = assignment_id
            seen.add(worker_id)
        if seen != set(scheduler._workers):
            raise ValueError("scheduler checkpoint worker set is incomplete")
        last_scale_s = payload.get("last_scale_s")
        if last_scale_s is not None and (
            isinstance(last_scale_s, bool) or not isinstance(last_scale_s, (int, float))
        ):
            raise ValueError("scheduler checkpoint has invalid scale time")
        scheduler._target_workers = target_workers
        scheduler._last_scale_s = last_scale_s
        return scheduler

    def _require_assignment_owner(self, worker_id: int, assignment_id: str) -> None:
        if self._active_assignments.get(worker_id) != assignment_id:
            raise ValueError(f"worker {worker_id} assignment ownership mismatch")

    def _cooldown_active(self, now_s: float) -> bool:
        return self._last_scale_s is not None and now_s - self._last_scale_s < self.config.cooldown_s

    def _health_allows_scale_up(self, samples: tuple[HealthSample, ...]) -> bool:
        # Health samples from slots above the current target must not authorize
        # a new capacity step. Those slots may be draining, idle after a
        # scale-down, or otherwise outside the capacity currently being
        # exercised. The window still consists of completed results, but only
        # from identities enabled by the current target.
        enabled_samples = tuple(
            item
            for item in samples
            if item.worker_id in self._workers and item.worker_id <= self._target_workers
        )
        if len(enabled_samples) < self.config.health_window:
            return False
        recent = enabled_samples[-self.config.health_window :]
        return all(item.success and not item.failure_kind for item in recent)
