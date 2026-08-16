"""Account-scoped pacing gate for NotebookLM source mutations.

The active industrial path runs one subprocess per worker.  The ordinary
``threading`` lock in ``nlm_batch`` therefore protects only one worker's
client, not concurrent source-add calls made by other workers for the same
account.  This module provides an opt-in interprocess gate for experiments
that need to test account/provider pacing.  A zero pacing value disables it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Iterator

import fasteners


DEFAULT_SOURCE_ADD_GATE_ROOT = Path(r"P:/.data/yt-is/source-add-gates")
DEFAULT_SOURCE_ADD_GATE_TIMEOUT_S = 300.0
_GATE_SCHEMA_VERSION = 1


class SourceAddGateError(RuntimeError):
    """The account-scoped source-add gate could not be acquired safely."""


@dataclass(frozen=True)
class SourceAddGateLease:
    """Receipt data for one granted source-add mutation slot."""

    enabled: bool
    account_profile: str
    lock_path: str | None = None
    state_path: str | None = None
    wait_elapsed_s: float = 0.0
    pacing_s: float = 0.0
    scheduled_at_epoch: float | None = None


def _account_slug(account_profile: str) -> str:
    value = str(account_profile or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not value or not slug:
        raise SourceAddGateError("source-add pacing requires a non-empty account profile")
    return slug


def _read_last_start(state_path: Path, account_profile: str) -> float | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceAddGateError(f"invalid source-add gate state: {state_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _GATE_SCHEMA_VERSION:
        raise SourceAddGateError(f"unsupported source-add gate state: {state_path}")
    if payload.get("account_profile") != account_profile:
        raise SourceAddGateError(f"source-add gate account mismatch: {state_path}")
    value = payload.get("last_start_at_epoch")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceAddGateError(f"invalid source-add gate timestamp: {state_path}")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise SourceAddGateError(f"invalid source-add gate timestamp: {state_path}")
    return value


def _write_last_start(state_path: Path, account_profile: str, scheduled_at_epoch: float) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    payload = {
        "schema_version": _GATE_SCHEMA_VERSION,
        "account_profile": account_profile,
        "last_start_at_epoch": scheduled_at_epoch,
    }
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, state_path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SourceAddGateError(f"could not persist source-add gate state: {state_path}") from exc


@contextmanager
def account_source_add_gate(
    account_profile: str,
    *,
    pacing_s: float,
    timeout_s: float = DEFAULT_SOURCE_ADD_GATE_TIMEOUT_S,
    root: Path | str = DEFAULT_SOURCE_ADD_GATE_ROOT,
) -> Iterator[SourceAddGateLease]:
    """Reserve one account-scoped source-add start slot.

    The lock is held through the caller's mutation.  The timestamp is written
    before yielding, so a process crash can at worst impose one extra pacing
    interval; it cannot cause another worker to bypass the gate.  The default
    zero value is deliberately a no-op so production behavior is unchanged
    until a decision packet opts into the mechanism.
    """
    if isinstance(pacing_s, bool) or not isinstance(pacing_s, (int, float)):
        raise ValueError("pacing_s must be a finite number >= 0")
    pacing_s = float(pacing_s)
    if not math.isfinite(pacing_s) or pacing_s < 0:
        raise ValueError("pacing_s must be a finite number >= 0")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError("timeout_s must be a finite number >= 0")
    timeout_s = float(timeout_s)
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise ValueError("timeout_s must be a finite number >= 0")
    if pacing_s == 0:
        yield SourceAddGateLease(
            enabled=False,
            account_profile=str(account_profile or "").strip(),
            pacing_s=0.0,
        )
        return

    account_profile = str(account_profile or "").strip()
    slug = _account_slug(account_profile)
    root_path = Path(root).expanduser()
    try:
        root_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SourceAddGateError(f"could not create source-add gate root: {root_path}") from exc
    lock_path = root_path / f"{slug}.lock"
    state_path = root_path / f"{slug}.json"
    lock = fasteners.InterProcessLock(str(lock_path))
    acquired_at = time.monotonic()
    try:
        acquired = lock.acquire(blocking=True, timeout=timeout_s)
    except Exception as exc:
        raise SourceAddGateError(f"could not acquire source-add gate: {lock_path}") from exc
    if not acquired:
        raise SourceAddGateError(f"timed out acquiring source-add gate: {lock_path}")

    try:
        last_start = _read_last_start(state_path, account_profile)
        now = time.time()
        if last_start is not None:
            delay_s = max(0.0, last_start + pacing_s - now)
            if delay_s > 0:
                time.sleep(delay_s)
        scheduled_at_epoch = time.time()
        _write_last_start(state_path, account_profile, scheduled_at_epoch)
        yield SourceAddGateLease(
            enabled=True,
            account_profile=account_profile,
            lock_path=str(lock_path),
            state_path=str(state_path),
            wait_elapsed_s=round(time.monotonic() - acquired_at, 3),
            pacing_s=pacing_s,
            scheduled_at_epoch=scheduled_at_epoch,
        )
    finally:
        lock.release()
