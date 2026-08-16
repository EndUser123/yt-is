from __future__ import annotations

from pathlib import Path

import pytest

from csf import fetch_run_lock as mod


def test_fetch_run_lock_path_is_derived_from_database(tmp_path: Path) -> None:
    db_path = tmp_path / "batch_status.sqlite"
    assert mod.fetch_run_lock_path(db_path) == tmp_path / ".batch_status.sqlite.multi-account-fetch.lock"


def test_coordinator_child_envelope_requires_exact_parent_and_database(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch_status.sqlite"
    monkeypatch.setattr(mod.os, "getppid", lambda: 321)
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", "run01")
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_PID", "321")
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_DB_PATH", str(db_path.resolve()))

    class UnexpectedLock:
        def __init__(self, path: str) -> None:
            raise AssertionError(f"child must not reacquire {path}")

    monkeypatch.setattr(mod.fasteners, "InterProcessLock", UnexpectedLock)
    with mod.fetch_run_lock(db_path):
        pass


def test_coordinator_child_envelope_carries_event_run_identity(tmp_path: Path) -> None:
    envelope = mod.coordinator_child_environment(tmp_path / "batch.sqlite", "run-identity")
    assert envelope["YTIS_INDUSTRIAL_RUN_ID"] == "run-identity"


def test_fetch_run_lock_fails_closed_when_lock_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch_status.sqlite"

    class UnavailableLock:
        def __init__(self, path: str) -> None:
            assert path == str(mod.fetch_run_lock_path(db_path))

        def acquire(self, *, blocking: bool, timeout: float) -> bool:
            assert blocking is True
            assert timeout == 0.0
            return False

        def release(self) -> None:
            raise AssertionError("an unavailable lock must not be released")

    monkeypatch.setattr(mod.fasteners, "InterProcessLock", UnavailableLock)
    with pytest.raises(mod.FetchRunLockError, match="already held"):
        with mod.fetch_run_lock(db_path):
            pass
