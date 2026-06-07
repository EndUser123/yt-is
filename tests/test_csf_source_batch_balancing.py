from __future__ import annotations

from collections import deque
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


def _load_csf_source_module():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "bin" / "csf-source"
    loader = SourceFileLoader("csf_source_batch_balancing_test", str(path))
    spec = spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load csf-source")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drain_balanced_pending_batches_interleaves_worker_windows():
    mod = _load_csf_source_module()
    pending_buffer = deque((f"vid{i:03d}", "src") for i in range(6))

    batches = mod._drain_balanced_pending_batches(
        pending_buffer,
        batch_size=2,
        worker_count=3,
    )

    assert batches == [
        [("vid000", "src"), ("vid003", "src")],
        [("vid001", "src"), ("vid004", "src")],
        [("vid002", "src"), ("vid005", "src")],
    ]
    assert len(pending_buffer) == 0


def test_drain_balanced_pending_batches_keeps_single_worker_contiguous():
    mod = _load_csf_source_module()
    pending_buffer = deque((f"vid{i:03d}", "src") for i in range(4))

    batches = mod._drain_balanced_pending_batches(
        pending_buffer,
        batch_size=4,
        worker_count=1,
    )

    assert batches == [[
        ("vid000", "src"),
        ("vid001", "src"),
        ("vid002", "src"),
        ("vid003", "src"),
    ]]
    assert len(pending_buffer) == 0


def test_drain_balanced_pending_batches_waits_for_minimum_pending_batches():
    mod = _load_csf_source_module()
    pending_buffer = deque((f"vid{i:03d}", "src") for i in range(4))

    batches = mod._drain_balanced_pending_batches(
        pending_buffer,
        batch_size=2,
        worker_count=3,
        min_pending_batches=3,
    )

    assert batches == []
    assert len(pending_buffer) == 4

    pending_buffer.extend((f"vid{i:03d}", "src") for i in range(4, 6))
    batches = mod._drain_balanced_pending_batches(
        pending_buffer,
        batch_size=2,
        worker_count=3,
        min_pending_batches=3,
    )

    assert batches == [
        [("vid000", "src"), ("vid003", "src")],
        [("vid001", "src"), ("vid004", "src")],
        [("vid002", "src"), ("vid005", "src")],
    ]
    assert len(pending_buffer) == 0


def test_drain_balanced_pending_batches_rotates_worker_assignment():
    mod = _load_csf_source_module()
    batch_queue = [[f"vid{i:03d}"] for i in range(3)]

    groups = mod._take_industrial_dispatch_groups(
        batch_queue,
        worker_slots=3,
        batches_per_worker=1,
        rotate_by=1,
    )

    assert groups == [
        [["vid001"]],
        [["vid002"]],
        [["vid000"]],
    ]
    assert batch_queue == []


def test_order_industrial_free_slots_prefers_healthier_slots():
    mod = _load_csf_source_module()

    free_slots = [1, 2, 3]
    slot_health = {
        1: {"failure_rate": 0.569, "avg_elapsed_s": 0.900},
        2: {"failure_rate": 0.509, "avg_elapsed_s": 0.800},
        3: {"failure_rate": 0.062, "avg_elapsed_s": 0.200},
    }

    ordered = mod._order_industrial_free_slots(free_slots, slot_health)

    assert ordered == [3, 2, 1]


def test_order_industrial_free_slots_penalizes_probe_activity_before_failure_rate():
    mod = _load_csf_source_module()

    free_slots = [1, 2, 3]
    slot_health = {
        1: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 120.0,
            "failure_rate": 0.01,
            "avg_elapsed_s": 0.100,
        },
        2: {
            "source_list_probe_count": 0.0,
            "source_list_probe_elapsed_s_total": 0.0,
            "failure_rate": 0.90,
            "avg_elapsed_s": 1.500,
        },
    }

    ordered = mod._order_industrial_free_slots(free_slots, slot_health)

    assert ordered == [2, 1, 3]


def test_order_industrial_free_slots_prefers_lower_probe_cost_when_probe_count_matches():
    mod = _load_csf_source_module()

    free_slots = [1, 2]
    slot_health = {
        1: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 180.0,
            "failure_rate": 0.10,
            "avg_elapsed_s": 0.500,
        },
        2: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 12.0,
            "failure_rate": 0.10,
            "avg_elapsed_s": 0.500,
        },
    }

    ordered = mod._order_industrial_free_slots(free_slots, slot_health)

    assert ordered == [2, 1]


def test_order_industrial_free_slots_prefers_lower_command_latency_when_failure_rates_match():
    mod = _load_csf_source_module()

    free_slots = [1, 2]
    slot_health = {
        1: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 12.0,
            "failure_rate": 0.10,
            "content_fetch_command_elapsed_s_avg": 19.0,
            "avg_elapsed_s": 0.500,
        },
        2: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 12.0,
            "failure_rate": 0.10,
            "content_fetch_command_elapsed_s_avg": 2.0,
            "avg_elapsed_s": 0.500,
        },
    }

    ordered = mod._order_industrial_free_slots(free_slots, slot_health)

    assert ordered == [2, 1]


def test_order_industrial_free_slots_keeps_failure_rate_ahead_of_avg_elapsed():
    mod = _load_csf_source_module()

    free_slots = [1, 2]
    slot_health = {
        1: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 12.0,
            "failure_rate": 0.80,
            "avg_elapsed_s": 0.900,
        },
        2: {
            "source_list_probe_count": 1.0,
            "source_list_probe_elapsed_s_total": 12.0,
            "failure_rate": 0.10,
            "avg_elapsed_s": 0.100,
        },
    }

    ordered = mod._order_industrial_free_slots(free_slots, slot_health)

    assert ordered == [2, 1]
