from dataclasses import replace

import pytest

from csf.adaptive_worker_scheduler import (
    AdaptiveWorkerScheduler,
    AssignmentLedger,
    HealthSample,
    SchedulerConfig,
    SchedulerSnapshot,
    WorkerIdentity,
)


def identities(count=4):
    return tuple(
        WorkerIdentity(
            worker_id=index,
            profile=f"profile-{index}",
            notebook_title=f"notebook-{index}",
            state_path=f"state-{index}.json",
            client_namespace=f"client-{index}",
        )
        for index in range(1, count + 1)
    )


def scheduler(initial=1, maximum=4, cooldown=60.0):
    return AdaptiveWorkerScheduler(
        identities(),
        SchedulerConfig(initial, maximum, cooldown_s=cooldown, health_window=2),
    )


def test_scale_up_requires_health_and_is_one_step():
    policy = scheduler()
    decision = policy.choose(SchedulerSnapshot(0, 5, active_worker_ids=frozenset({1}), health_samples=()))
    assert decision.reason == "health_missing_or_failure"
    assert decision.target_workers == 1
    decision = policy.choose(SchedulerSnapshot(0, 5, active_worker_ids=frozenset({1}), health_samples=(HealthSample(1, True), HealthSample(1, True))))
    assert decision.target_workers == 2
    policy.apply(decision, now_s=0)
    assert policy.target_workers == 2


def test_cooldown_blocks_immediate_second_scale_up():
    policy = scheduler()
    decision = policy.choose(SchedulerSnapshot(0, 5, active_worker_ids=frozenset({1}), health_samples=(HealthSample(1, True), HealthSample(1, True))))
    policy.apply(decision, now_s=0)
    blocked = policy.choose(SchedulerSnapshot(1, 5, active_worker_ids=frozenset({1}), health_samples=(HealthSample(1, True), HealthSample(1, True))))
    assert blocked.reason == "cooldown"
    assert blocked.target_workers == 2
    allowed = policy.choose(SchedulerSnapshot(60, 5, active_worker_ids=frozenset({1}), health_samples=(HealthSample(1, True), HealthSample(1, True))))
    assert allowed.target_workers == 3


def test_disqualifying_health_reason_blocks_scale_up():
    policy = scheduler()
    decision = policy.choose(
        SchedulerSnapshot(
            0,
            5,
            active_worker_ids=frozenset({1}),
            health_samples=(HealthSample(1, True), HealthSample(1, True, "source_age_cliff")),
        )
    )
    assert decision.reason == "health_missing_or_failure"
    assert decision.target_workers == 1


def test_health_from_slot_above_current_target_cannot_authorize_scale_up():
    policy = scheduler()
    decision = policy.choose(
        SchedulerSnapshot(
            0,
            5,
            active_worker_ids=frozenset({1}),
            health_samples=(HealthSample(2, True), HealthSample(2, True)),
        )
    )
    assert decision.reason == "health_missing_or_failure"
    assert decision.target_workers == 1


def test_scale_down_drains_active_worker_without_killing_it():
    policy = scheduler(initial=2)
    policy.mark_started(2, now_s=0, assignment_id="a")
    decision = policy.choose(SchedulerSnapshot(60, 0))
    assert decision.target_workers == 1
    events = policy.apply(decision, now_s=60)
    assert policy.status(2) == "draining"
    assert policy.eligible_worker_ids() == (1,)
    assert any(event.event == "worker_draining" for event in events)
    policy.mark_completed(2, now_s=61, assignment_id="a")
    assert policy.status(2) == "idle"


def test_streaming_input_does_not_scale_down_before_backlog_is_closed():
    policy = scheduler(initial=2, cooldown=60.0)
    open_input = policy.choose(
        SchedulerSnapshot(
            0,
            0,
            active_worker_ids=frozenset({1, 2}),
            input_closed=False,
        )
    )
    assert open_input.target_workers == 2
    assert open_input.reason == "input_open"
    policy.apply(open_input, now_s=0)

    health = (HealthSample(1, True), HealthSample(2, True))
    scale_up = policy.choose(
        SchedulerSnapshot(
            60,
            5,
            active_worker_ids=frozenset({1, 2}),
            health_samples=health,
            input_closed=False,
        )
    )
    assert scale_up.target_workers == 3
    assert scale_up.reason == "backlog_high"


def test_active_draining_slot_is_not_revived_by_scale_up():
    policy = scheduler(initial=2)
    policy.mark_started(2, now_s=0, assignment_id="a")
    policy.apply(policy.choose(SchedulerSnapshot(60, 0)), now_s=60)
    assert policy.status(2) == "draining"
    policy.apply(policy.choose(SchedulerSnapshot(120, 5, active_worker_ids=frozenset({2}), health_samples=(HealthSample(1, True), HealthSample(1, True)))), now_s=120)
    assert policy.target_workers == 2
    assert policy.status(2) == "draining"
    assert 2 not in policy.eligible_worker_ids()


def test_failure_quarantines_worker_and_prevents_reuse():
    policy = scheduler()
    policy.mark_started(1, now_s=0, assignment_id="a")
    policy.mark_failed(1, now_s=1, assignment_id="a", failure_kind="result_parse")
    assert policy.status(1) == "quarantined"
    assert 1 not in policy.eligible_worker_ids()


def test_quarantined_worker_recovers_after_cooldown_with_bounded_budget():
    policy = scheduler(cooldown=10.0)
    policy.mark_started(1, now_s=0, assignment_id="a")
    policy.mark_failed(1, now_s=1, assignment_id="a", failure_kind="worker_timeout")

    assert policy.recover_quarantined(now_s=10) == ()
    recovered = policy.recover_quarantined(now_s=11)
    assert len(recovered) == 1
    assert recovered[0].event == "worker_recovered"
    assert policy.status(1) == "idle"
    assert 1 in policy.eligible_worker_ids()

    policy.mark_started(1, now_s=12, assignment_id="b")
    policy.mark_failed(1, now_s=13, assignment_id="b", failure_kind="worker_timeout")
    assert policy.recover_quarantined(now_s=22) == ()
    assert len(policy.recover_quarantined(now_s=23)) == 1


def test_quarantine_recovery_budget_can_disable_recovery():
    policy = AdaptiveWorkerScheduler(
        identities(),
        SchedulerConfig(1, 4, cooldown_s=0, max_recovery_attempts=0),
    )
    policy.mark_started(1, now_s=0, assignment_id="a")
    policy.mark_failed(1, now_s=1, assignment_id="a", failure_kind="worker_exception")
    assert policy.recover_quarantined(now_s=2) == ()
    assert policy.status(1) == "quarantined"


def test_identity_collisions_are_rejected():
    items = identities()
    duplicate = items[:3] + (WorkerIdentity(4, "profile-1", "notebook-4", "state-4", "client-4"),)
    try:
        AdaptiveWorkerScheduler(duplicate, SchedulerConfig(1, 4))
    except ValueError as error:
        assert "profile" in str(error)
    else:
        raise AssertionError("duplicate identity was accepted")


@pytest.mark.parametrize(
    "field",
    ["profile", "notebook_title", "state_path", "client_namespace"],
)
def test_all_worker_identity_namespaces_are_unique(field):
    items = identities()
    duplicate = items[:3] + (replace(items[3], **{field: getattr(items[0], field)}),)
    with pytest.raises(ValueError, match=field):
        AdaptiveWorkerScheduler(duplicate, SchedulerConfig(1, 4))


def test_worker_path_identity_is_case_insensitive():
    items = identities()
    duplicate = items[:3] + (replace(items[3], state_path="STATE-1.JSON"),)

    with pytest.raises(ValueError, match="state_path"):
        AdaptiveWorkerScheduler(duplicate, SchedulerConfig(1, 4))


@pytest.mark.parametrize(
    "args",
    [
        (0, 1, {}),
        (2, 1, {}),
        (3, 4, {"min_workers": 4}),
    ],
)
def test_worker_count_ranges_fail_closed(args):
    with pytest.raises(ValueError):
        initial, maximum, kwargs = args
        AdaptiveWorkerScheduler(identities(), SchedulerConfig(initial, maximum, **kwargs))


def test_assignment_state_transitions_are_conservative():
    policy = scheduler()
    policy.mark_started(1, now_s=0, assignment_id="a")
    assert policy.eligible_worker_ids() == ()
    policy.mark_completed(1, now_s=1, assignment_id="a")
    assert policy.eligible_worker_ids() == (1,)


def test_fake_worker_lifecycle_preserves_batch_conservation_across_requeue():
    policy = scheduler()
    ledger = AssignmentLedger()

    policy.mark_started(1, now_s=0, assignment_id="assignment-1")
    ledger.claim("assignment-1", ("batch-a", "batch-b"))
    ledger.fail("assignment-1", requeue=True)
    policy.mark_failed(1, now_s=1, assignment_id="assignment-1", failure_kind="timeout")
    checkpoint = ledger.accounting()
    assert checkpoint.balanced
    assert checkpoint.input_batches == 2
    assert checkpoint.requeued == 2

    policy = scheduler()
    policy.mark_started(1, now_s=2, assignment_id="assignment-2")
    ledger.claim("assignment-2", ("batch-a", "batch-b"))
    ledger.complete("assignment-2")
    policy.mark_completed(1, now_s=3, assignment_id="assignment-2")
    final = ledger.accounting()
    assert final.balanced
    assert final.completed == 2
    assert final.requeued == 0


def test_registered_queue_items_are_in_conservation_accounting_before_dispatch():
    ledger = AssignmentLedger()
    ledger.register(("batch-a", "batch-b"))

    queued = ledger.accounting()
    assert queued.balanced
    assert queued.input_batches == 2
    assert queued.still_in_flight == 2

    ledger.claim("assignment-1", ("batch-a",))
    ledger.complete("assignment-1")
    remaining = ledger.accounting()
    assert remaining.balanced
    assert remaining.completed == 1
    assert remaining.still_in_flight == 1


def test_fake_worker_result_failure_is_terminal_and_duplicate_claim_is_rejected():
    ledger = AssignmentLedger()
    ledger.claim("assignment-1", ("batch-a",))
    ledger.fail("assignment-1", requeue=False)
    with pytest.raises(ValueError, match="already owned or terminal"):
        ledger.claim("assignment-2", ("batch-a",))
    result = ledger.accounting()
    assert result.balanced
    assert result.terminal_failed == 1


def test_ledger_rejects_duplicate_batch_ids_within_one_assignment():
    ledger = AssignmentLedger()

    with pytest.raises(ValueError, match="non-empty and unique"):
        ledger.claim("assignment-1", ("batch-a", "batch-a"))


def test_mixed_outcomes_preserve_conservation_at_final_checkpoint():
    ledger = AssignmentLedger()
    ledger.claim("complete", ("batch-a",))
    ledger.complete("complete")
    ledger.claim("terminal", ("batch-b",))
    ledger.fail("terminal", requeue=False)
    ledger.claim("retry", ("batch-c",))
    ledger.fail("retry", requeue=True)

    checkpoint = ledger.accounting()
    assert checkpoint.balanced
    assert checkpoint.input_batches == 3
    assert checkpoint.completed == 1
    assert checkpoint.terminal_failed == 1
    assert checkpoint.requeued == 1
    assert checkpoint.still_in_flight == 0


def test_launch_failure_quarantines_worker_and_requeues_assignment():
    policy = scheduler()
    ledger = AssignmentLedger()
    policy.mark_started(1, now_s=0, assignment_id="launch-failure")
    ledger.claim("launch-failure", ("batch-a", "batch-b"))

    ledger.fail("launch-failure", requeue=True)
    policy.mark_failed(
        1,
        now_s=1,
        assignment_id="launch-failure",
        failure_kind="launch_failure",
    )

    assert policy.status(1) == "quarantined"
    accounting = ledger.accounting()
    assert accounting.balanced
    assert accounting.requeued == 2


def test_scheduler_checkpoint_preserves_assignment_owner_and_rejects_stale_completion():
    policy = scheduler()
    policy.mark_started(1, now_s=0, assignment_id="assignment-1")
    checkpoint = policy.checkpoint()

    restored = AdaptiveWorkerScheduler.from_checkpoint(
        identities(), policy.config, checkpoint
    )
    assert restored.status(1) == "active"
    with pytest.raises(ValueError, match="ownership mismatch"):
        restored.mark_completed(1, now_s=1, assignment_id="stale-assignment")
    restored.mark_completed(1, now_s=1, assignment_id="assignment-1")
    assert restored.status(1) == "idle"


def test_assignment_checkpoint_keeps_in_flight_work_owned_after_restart():
    ledger = AssignmentLedger()
    ledger.claim("assignment-1", ("batch-a", "batch-b"))

    restored = AssignmentLedger.from_checkpoint(ledger.checkpoint())
    assert restored.accounting().still_in_flight == 2
    with pytest.raises(ValueError, match="already owned"):
        restored.claim("assignment-2", ("batch-a",))


def test_assignment_checkpoint_rejects_duplicate_or_unowned_in_flight_batches():
    duplicate = {
        "schema_version": 1,
        "batches": {"batch-a": "in_flight"},
        "assignments": {
            "assignment-1": ["batch-a"],
            "assignment-2": ["batch-a"],
        },
    }
    with pytest.raises(ValueError, match="more than once"):
        AssignmentLedger.from_checkpoint(duplicate)

    unowned = {
        "schema_version": 1,
        "batches": {"batch-a": "in_flight"},
        "assignments": {},
    }
    with pytest.raises(ValueError, match="does not reconcile"):
        AssignmentLedger.from_checkpoint(unowned)


def test_scheduler_checkpoint_rejects_unbounded_quarantine_state():
    policy = scheduler()
    checkpoint = policy.checkpoint()
    checkpoint["workers"][0]["status"] = "quarantined"
    checkpoint["workers"][0]["quarantined_at_s"] = 0.0
    checkpoint["workers"][0]["recovery_attempts"] = policy.config.max_recovery_attempts + 1
    with pytest.raises(ValueError, match="recovery attempts exceed"):
        AdaptiveWorkerScheduler.from_checkpoint(identities(), policy.config, checkpoint)
