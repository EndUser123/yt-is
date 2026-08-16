from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import time

import pytest

from csf.source_add_gate import SourceAddGateError, account_source_add_gate


def _hold_source_add_gate(root: str, ready, release, results) -> None:
    try:
        with account_source_add_gate("a.hominidae", pacing_s=0.01, timeout_s=3.0, root=root):
            ready.set()
            if not release.wait(5.0):
                raise RuntimeError("holder release event timed out")
        results.put(("holder", "ok"))
    except BaseException as exc:  # pragma: no cover - surfaced through the result queue
        ready.set()
        results.put(("holder", type(exc).__name__, str(exc)))


def _wait_for_source_add_gate(root: str, attempted, acquired, results) -> None:
    attempted.set()
    started = time.monotonic()
    try:
        with account_source_add_gate("a.hominidae", pacing_s=0.01, timeout_s=3.0, root=root):
            acquired.set()
        results.put(("waiter", "ok", time.monotonic() - started))
    except BaseException as exc:  # pragma: no cover - surfaced through the result queue
        results.put(("waiter", type(exc).__name__, str(exc)))


def test_zero_pacing_is_a_noop(tmp_path: Path) -> None:
    with account_source_add_gate("a.hominidae", pacing_s=0, root=tmp_path) as lease:
        assert lease.enabled is False
    assert list(tmp_path.iterdir()) == []


def test_positive_pacing_writes_account_scoped_state(tmp_path: Path) -> None:
    with account_source_add_gate("a.hominidae", pacing_s=1.0, root=tmp_path) as lease:
        assert lease.enabled is True
        assert lease.account_profile == "a.hominidae"
        assert lease.lock_path == str(tmp_path / "a-hominidae.lock")
        assert lease.state_path == str(tmp_path / "a-hominidae.json")
    payload = json.loads((tmp_path / "a-hominidae.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["account_profile"] == "a.hominidae"
    assert isinstance(payload["last_start_at_epoch"], float)


def test_positive_pacing_creates_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "gate-root"
    with account_source_add_gate("troup.hominidae", pacing_s=0.1, root=root) as lease:
        assert lease.enabled is True
    assert (root / "troup-hominidae.json").is_file()


def test_positive_pacing_serializes_across_processes(tmp_path: Path) -> None:
    """The account lock must cover separate worker subprocesses, not just threads."""
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    attempted = context.Event()
    acquired = context.Event()
    results = context.Queue()
    holder = context.Process(
        target=_hold_source_add_gate,
        args=(str(tmp_path), ready, release, results),
    )
    waiter = context.Process(
        target=_wait_for_source_add_gate,
        args=(str(tmp_path), attempted, acquired, results),
    )
    try:
        holder.start()
        assert ready.wait(10.0), "holder did not acquire the account gate"
        waiter.start()
        assert attempted.wait(10.0), "waiter did not start"
        time.sleep(0.25)
        assert not acquired.is_set(), "waiter bypassed the held account gate"
        release.set()
        holder.join(10.0)
        waiter.join(10.0)
        assert holder.exitcode == 0
        assert waiter.exitcode == 0
        first = results.get(timeout=3.0)
        observed = {first[0]: first}
        # The queue is consumed below without relying on Queue.empty(), which
        # is not reliable across multiprocessing implementations.
        while len(observed) < 2:
            row = results.get(timeout=3.0)
            observed[row[0]] = row
        assert observed["holder"][1] == "ok"
        assert observed["waiter"][1] == "ok"
        assert observed["waiter"][2] >= 0.20
    finally:
        release.set()
        for process in (holder, waiter):
            if process.is_alive():
                process.terminate()
            process.join(5.0)


def test_gate_rejects_corrupt_state(tmp_path: Path) -> None:
    (tmp_path / "a-hominidae.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SourceAddGateError, match="invalid source-add gate state"):
        with account_source_add_gate("a.hominidae", pacing_s=1.0, root=tmp_path):
            pass


def test_gate_rejects_state_for_another_account(tmp_path: Path) -> None:
    (tmp_path / "a-hominidae.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "account_profile": "troup.hominidae",
                "last_start_at_epoch": 1.0,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceAddGateError, match="account mismatch"):
        with account_source_add_gate("a.hominidae", pacing_s=1.0, root=tmp_path):
            pass


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_gate_rejects_invalid_pacing(value: float, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pacing_s"):
        with account_source_add_gate("a.hominidae", pacing_s=value, root=tmp_path):
            pass
