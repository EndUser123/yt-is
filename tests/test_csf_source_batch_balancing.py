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
