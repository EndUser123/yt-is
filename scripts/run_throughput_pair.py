#!/usr/bin/env python3
"""Bounded repeated control/adaptive throughput-pair coordinator.

Planning and reconciliation are read-only with respect to live work.  The
only path that invokes ``run_multi_account_fetch.py`` is ``--execute``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.video_selection_manifest import load_video_selection_manifest, write_video_selection_manifest
from csf.cleanup_staging import cleanup_staging

try:
    from prepare_throughput_pair import (
        ACCOUNTS,
        ARMS,
        CAPTION_STATES,
        PAIRS,
        effective_account_settings,
        file_fingerprint,
        fingerprint,
    load_account_settings_overrides,
        validate_environment_overrides,
        prepare_throughput_pair,
    )
except ModuleNotFoundError:
    import importlib.util

    _PREPARE_PATH = Path(__file__).with_name("prepare_throughput_pair.py")
    _PREPARE_SPEC = importlib.util.spec_from_file_location("prepare_throughput_pair", _PREPARE_PATH)
    if _PREPARE_SPEC is None or _PREPARE_SPEC.loader is None:
        raise ImportError(f"could not load {_PREPARE_PATH}")
    _PREPARE_MODULE = importlib.util.module_from_spec(_PREPARE_SPEC)
    _PREPARE_SPEC.loader.exec_module(_PREPARE_MODULE)
    ACCOUNTS = _PREPARE_MODULE.ACCOUNTS
    ARMS = _PREPARE_MODULE.ARMS
    PAIRS = _PREPARE_MODULE.PAIRS
    CAPTION_STATES = _PREPARE_MODULE.CAPTION_STATES
    prepare_throughput_pair = _PREPARE_MODULE.prepare_throughput_pair
    effective_account_settings = _PREPARE_MODULE.effective_account_settings
    file_fingerprint = _PREPARE_MODULE.file_fingerprint
    fingerprint = _PREPARE_MODULE.fingerprint
    load_account_settings_overrides = _PREPARE_MODULE.load_account_settings_overrides
    validate_environment_overrides = _PREPARE_MODULE.validate_environment_overrides

REQUIRED_EVENT_FAMILIES = {
    "selection": {"fetch_invoked", "fetch_manifest_selection"},
    "live_work": {"first_download_started", "transcript_stage_started", "worker_batch_started"},
    "completion": {"fetch_completed", "fetch_worker_finished", "worker_completed"},
    "cleanup": {"worker_cleanup_completed", "nlm_worker_notebook_cleanup_complete", "worker_cleanup_state_cleared"},
}
DIRECT_THROUGHPUT_CAPTION_STATES = ("captioned", "unknown")
_EXACT_SOURCE_ADD_FAILURES = frozenset({
    "source_add_failed",
    "Source add failed",
    "source add failed",
    "source_add_gate_failed",
})
_EXACT_SOURCE_ADD_RPC9_FAILURES = frozenset({
    "source_add_non_retryable_rpc_code_9",
    "Source add failed: rpc_code=9",
    "source add failed: rpc_code=9",
})


def _load(path: Path | str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _packet_fingerprint(packet: Mapping[str, Any]) -> str:
    payload = dict(packet)
    payload.pop("packet_fingerprint", None)
    return _fingerprint(payload)


def _integrity(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"SQLite file not found: {path}")
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or str(result[0]).lower() != "ok":
        raise ValueError(f"integrity_check failed: {path}")
    return "ok"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a receipt atomically so an interrupted coordinator cannot leave a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _ids_in_cache(path: Path, ids: list[str]) -> set[str]:
    _integrity(path)
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(transcript_cache)")}
        if "video_id" not in columns:
            raise ValueError(f"cache schema missing video_id: {path}")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT DISTINCT video_id FROM transcript_cache WHERE video_id IN ({placeholders})", ids)
    return {str(row[0]) for row in rows}


def _non_empty_cache_ids(path: Path, ids: list[str]) -> set[str]:
    _integrity(path)
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(transcript_cache)")}
        if not {"video_id", "transcript"}.issubset(columns):
            raise ValueError(f"cache schema missing video_id/transcript: {path}")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT video_id, transcript FROM transcript_cache WHERE video_id IN ({placeholders})", ids
        )
    return {str(row[0]) for row in rows if row[1] is not None and str(row[1]).strip()}


def _write_settings(
    stage: Path,
    arm: str,
    *,
    batch_size: int | None = None,
    account_settings: Mapping[str, Mapping[str, Any]] | None = None,
) -> Path:
    settings = effective_account_settings(
        arm,
        batch_size=batch_size,
        account_settings=account_settings,
    )
    path = stage / "account-settings.json"
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _interleaved_ids(account_manifests: Mapping[str, Any]) -> list[str]:
    accounts = tuple(account_manifests)
    lengths = {len(account_manifests[account]) for account in accounts}
    if len(lengths) != 1 or not accounts or next(iter(lengths)) < 1:
        raise ValueError("combined manifest requires equal, non-empty account partitions")
    return [
        str(account_manifests[account][index])
        for index in range(next(iter(lengths)))
        for account in accounts
    ]


def _write_combined_manifest(
    *, stage: Path, pair: str, arm: str, packet: Mapping[str, Any], pair_data: Mapping[str, Any]
) -> tuple[Path, str, list[str]]:
    ids = _interleaved_ids(pair_data["account_manifests"])
    if ids != list(pair_data.get("cohort_ids", [])):
        raise ValueError(f"combined manifest order does not match cohort for {pair}/{arm}")
    payload = {
        "manifest_version": 1,
        "generated_at": packet.get("created_at", "1970-01-01T00:00:00+00:00"),
        "selection_name": f"throughput-pair-{pair}-{arm}",
        "selection_criteria": {
            "status": "pending", "account_profile": "all", "partition": "stable_round_robin",
            "run_id": f"{pair}/{arm}", "total_selected": len(ids),
        },
        "input_database_fingerprint": packet["canonical_fingerprints"]["db"]
        if "canonical_fingerprints" in packet else "fixture",
        "videos": [{"video_id": video_id, "source_note": f"throughput_pair:{pair}/{arm}"} for video_id in ids],
    }
    path = stage / "combined-manifest.json"
    write_video_selection_manifest(path, payload)
    loaded = load_video_selection_manifest(path)
    loaded_ids = [item.video_id for item in loaded.items]
    if loaded_ids != ids:
        raise ValueError(f"combined manifest round-trip changed IDs: {path}")
    return path, loaded.fingerprint, ids


def build_plan(
    *,
    db: Path,
    reference_cache: Path,
    output_root: Path,
    items_per_account: int,
    caption_state: str = "captioned",
    batch_size: int | None = None,
    account_settings: Mapping[str, Mapping[str, Any]] | None = None,
    exclude_video_ids: tuple[str, ...] = (),
    comparison_mode: str = "adaptive",
    environment_overrides: Mapping[str, Mapping[str, str]] | None = None,
    abort_on_source_add_failure: bool = False,
) -> dict[str, Any]:
    if caption_state not in DIRECT_THROUGHPUT_CAPTION_STATES:
        raise ValueError(
            "direct throughput-pair packets require caption_state='captioned' or 'unknown'; "
            "'no-caption' and 'any' require the fallback-aware backlog runner"
        )
    packet = prepare_throughput_pair(
        db_path=db, reference_cache_path=reference_cache, output_root=output_root,
        items_per_account=items_per_account,
        require_adaptive_scale_up=comparison_mode != "environment",
        require_adaptive_workload=comparison_mode != "environment",
        caption_state=caption_state,
        batch_size=batch_size,
        account_settings=account_settings,
        exclude_video_ids=exclude_video_ids,
        comparison_mode=comparison_mode,
        environment_overrides=environment_overrides,
        abort_on_source_add_failure=abort_on_source_add_failure,
    )
    for pair in PAIRS:
        pair_data = packet["pairs"][pair]
        for arm in ARMS:
            arm_data = pair_data["arms"][arm]
            stage = Path(arm_data["staging_db"]).parent
            settings_path = _write_settings(
                stage,
                "control" if comparison_mode == "environment" else arm,
                batch_size=batch_size,
                account_settings=account_settings,
            )
            combined_path, combined_fingerprint, combined_ids = _write_combined_manifest(
                stage=stage, pair=pair, arm=arm, packet=packet, pair_data=pair_data
            )
            arm_data["account_settings_path"] = str(settings_path.resolve())
            arm_data["account_settings_fingerprint"] = file_fingerprint(settings_path)
            arm_data["combined_manifest_path"] = str(combined_path.resolve())
            arm_data["combined_manifest_fingerprint"] = combined_fingerprint
            arm_data.setdefault("artifact_fingerprints", {})["combined_manifest"] = file_fingerprint(combined_path)
            arm_data["combined_manifest_ids"] = combined_ids
            arm_data["expected_account_ids"] = {
                account: list(ids) for account, ids in pair_data["account_manifests"].items()
            }
            arm_data["launch_nonce"] = uuid.uuid4().hex
            arm_data["execution_order"] = ["control", "adaptive"]
            arm_data["required_event_families"] = {
                name: sorted(values) for name, values in REQUIRED_EVENT_FAMILIES.items()
            }
    packet["packet_version"] = 2
    packet["packet_root"] = str(output_root.resolve())
    packet["execution_nonce"] = uuid.uuid4().hex
    packet["coordinator"] = {
        "kind": "repeated_throughput_pair",
        "control_before_adaptive": True,
        "execute_requires_explicit_flag": True,
        "strict_runtime_identity": True,
        "live_launch": False,
        "adaptive_workload_required": comparison_mode != "environment",
        "vph_semantics": "combined_completed_ids * 3600 / max_account_elapsed_s (parallel accounts)",
        "caption_state": caption_state,
        "batch_size": batch_size,
        "account_settings_overrides": dict(account_settings or {}),
        "account_settings_overrides_fingerprint": fingerprint(dict(account_settings or {})),
        "comparison_mode": comparison_mode,
        "environment_overrides": validate_environment_overrides(environment_overrides),
        "environment_overrides_fingerprint": fingerprint(validate_environment_overrides(environment_overrides)),
        "abort_on_source_add_failure": bool(abort_on_source_add_failure),
    }
    packet_path = Path(packet["packet_path"]).resolve()
    packet["packet_path"] = str(packet_path)
    packet["packet_fingerprint"] = _packet_fingerprint(packet)
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def _event_actions(root: Path) -> set[str]:
    actions: set[str] = set()
    for path in root.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, Mapping) and event.get("action"):
                actions.add(str(event["action"]))
    return actions


def _source_add_abort_marker(
    root: Path,
    *,
    expected_execution_nonce: str | None = None,
    expected_accounts: set[str] | None = None,
    expected_video_ids: set[str] | None = None,
) -> str | None:
    """Return an exact, current-arm source-add failure marker.

    The optional envelope filters are mandatory for strict executable packets.
    The unfiltered form remains useful for unit fixtures and legacy offline
    inspection, but it must not be used to authorize a live abort decision.
    """
    def event_value(event: Mapping[str, Any], data: Mapping[str, Any], field: str) -> Any:
        return event.get(field) if field in event else data.get(field)

    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            if expected_execution_nonce is not None and event_value(event, data, "execution_nonce") != expected_execution_nonce:
                continue
            if expected_accounts is not None and event_value(event, data, "account_profile") not in expected_accounts:
                continue
            video_id = event.get("video_id", data.get("video_id"))
            if expected_video_ids is not None and video_id not in expected_video_ids:
                continue
            action = event.get("action")
            failure = event.get("failure_reason", data.get("failure_reason"))
            if action == "nlm_batch_source_add_gate_failed" or failure == "source_add_gate_failed":
                return "source_add_gate_failed"
            if action in _EXACT_SOURCE_ADD_FAILURES or failure in _EXACT_SOURCE_ADD_FAILURES:
                return str(action or failure)
            if failure in _EXACT_SOURCE_ADD_RPC9_FAILURES:
                return "source_add_rpc_code_9"
            reason = data.get("reason")
            error = data.get("error")
            if reason in _EXACT_SOURCE_ADD_RPC9_FAILURES or reason == "rpc_code_9_failed_precondition":
                return "source_add_rpc_code_9"
            if isinstance(error, str) and "rpc_code=9" in error and "sourceadderror" in error.lower().replace(" ", ""):
                return "source_add_rpc_code_9"
            rpc_code = event.get("rpc_code", data.get("rpc_code"))
            if str(rpc_code) == "9" and (
                action in _EXACT_SOURCE_ADD_FAILURES
                or failure in _EXACT_SOURCE_ADD_RPC9_FAILURES
                or action == "nlm_batch_source_add_retry_skipped"
            ):
                return "source_add_rpc_code_9"
    return None


def _owned_descendant_pids(root_pid: int) -> list[int] | None:
    """Enumerate descendants of ``root_pid`` or return ``None`` if unverifiable."""
    if root_pid <= 0:
        return None
    if os.name == "nt":
        command = (
            "$rows = Get-CimInstance Win32_Process | "
            "ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId; "
            "ppid=[int]$_.ParentProcessId} }; "
            "$rows | ConvertTo-Json -Compress"
        )
        query = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        query = ["ps", "-eo", "pid=,ppid="]
    try:
        result = subprocess.run(
            query,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        if os.name == "nt":
            raw = json.loads(result.stdout.strip() or "[]")
            rows = raw if isinstance(raw, list) else [raw]
            parents = {
                int(row["pid"]): int(row["ppid"])
                for row in rows
                if isinstance(row, Mapping) and "pid" in row and "ppid" in row
            }
        else:
            parents = {}
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) == 2:
                    parents[int(fields[0])] = int(fields[1])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
    descendants: list[int] = []
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        children = [pid for pid, ppid in parents.items() if ppid == parent]
        descendants.extend(children)
        frontier.extend(children)
    return sorted(set(descendants))


def _attach_windows_kill_on_close_job(process: subprocess.Popen) -> bool:
    """Put a runner in a Windows job whose close operation kills descendants."""
    if os.name != "nt":
        return True
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return False
        process_handle = kernel32.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process_handle:
            kernel32.CloseHandle(job)
            return False
        try:
            if not kernel32.AssignProcessToJobObject(job, process_handle):
                kernel32.CloseHandle(job)
                return False
        finally:
            kernel32.CloseHandle(process_handle)
        process._ytis_kill_job_handle = job
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _close_windows_kill_on_close_job(process: subprocess.Popen) -> bool:
    """Close a runner's kill-on-close job and report whether the close worked."""
    if os.name != "nt":
        return True
    handle = getattr(process, "_ytis_kill_job_handle", None)
    if not handle:
        return False
    try:
        import ctypes

        closed = bool(ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle))
    except (AttributeError, OSError, TypeError, ValueError):
        closed = False
    process._ytis_kill_job_handle = None
    return closed


def _terminate_process_tree(process: subprocess.Popen) -> dict[str, Any]:
    """Terminate a runner and prove that its owned process tree is gone."""
    pid = getattr(process, "pid", None)
    result: dict[str, Any] = {
        "root_pid": pid,
        "termination_requested": False,
        "termination_command_returncode": None,
        "root_reaped": False,
        "remaining_pids": None,
        "termination_confirmed": False,
        "job_attached": bool(getattr(process, "_ytis_kill_job_handle", None)) if os.name == "nt" else True,
        "job_closed": None,
    }
    if not isinstance(pid, int) or pid <= 0:
        result["failure_reason"] = "invalid_root_pid"
        return result
    root_was_running = process.poll() is None
    result["termination_requested"] = root_was_running
    if os.name == "nt":
        result["job_closed"] = _close_windows_kill_on_close_job(process)
    if os.name == "nt":
        try:
            kill_result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            result["termination_command_returncode"] = kill_result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["failure_reason"] = f"taskkill_{type(exc).__name__}"
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            result["termination_command_returncode"] = 0
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
                result["termination_command_returncode"] = 0
            except OSError as exc:
                result["failure_reason"] = f"terminate_{type(exc).__name__}"
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    result["root_reaped"] = process.poll() is not None
    deadline = time.monotonic() + 5
    remaining: list[int] | None = None
    while time.monotonic() <= deadline:
        remaining = _owned_descendant_pids(pid)
        if remaining == []:
            break
        time.sleep(0.1)
    result["remaining_pids"] = remaining
    result["termination_confirmed"] = bool(
        result["root_reaped"]
        and remaining == []
        and (os.name != "nt" or (result["job_attached"] and result["job_closed"]))
    )
    if not result["termination_confirmed"] and "failure_reason" not in result:
        result["failure_reason"] = "termination_unconfirmed"
    return result


def _launch_arm(
    command: list[str],
    *,
    cwd: str,
    run_root: Path,
    env: Mapping[str, str],
    abort_on_source_add_failure: bool,
    expected_execution_nonce: str | None = None,
    expected_accounts: set[str] | None = None,
    expected_video_ids: set[str] | None = None,
):
    """Run normally for legacy packets, or poll the event tree for opt-in aborts."""
    if not abort_on_source_add_failure:
        return subprocess.run(command, cwd=cwd, check=False, env=dict(env) if env else None), None, None
    popen_kwargs: dict[str, Any] = {"cwd": cwd, "env": dict(env), "shell": False}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    if os.name == "nt":
        _attach_windows_kill_on_close_job(process)
    marker = None
    while process.poll() is None:
        marker = (
            _source_add_abort_marker(
                run_root,
                expected_execution_nonce=expected_execution_nonce,
                expected_accounts=expected_accounts,
                expected_video_ids=expected_video_ids,
            )
            if run_root.exists()
            else None
        )
        if marker:
            return process, marker, _terminate_process_tree(process)
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
    marker = (
        _source_add_abort_marker(
            run_root,
            expected_execution_nonce=expected_execution_nonce,
            expected_accounts=expected_accounts,
            expected_video_ids=expected_video_ids,
        )
        if run_root.exists()
        else None
    )
    if marker:
        return process, marker, _terminate_process_tree(process)
    if os.name == "nt":
        _close_windows_kill_on_close_job(process)
    return process, None, None


def _event_target_workers(root: Path) -> list[int]:
    values: list[int] = []
    for path in root.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = event.get("data") if isinstance(event, Mapping) else None
            if isinstance(data, Mapping) and isinstance(data.get("target_workers"), int):
                values.append(int(data["target_workers"]))
    return values


def _event_provenance_issues(
    root: Path,
    *,
    account: str,
    expected_ids: list[str],
    expected_identity: Mapping[str, Any],
) -> list[str]:
    """Check and require the run/account envelope in executable event logs."""
    issues: list[str] = []
    parseable_event_count = 0
    for path in root.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            parseable_event_count += 1
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            for field, expected in expected_identity.items():
                actual = event.get(field, data.get(field))
                if actual is None:
                    issues.append(f"{account}:event_{field}_missing")
                elif actual != expected:
                    issues.append(f"{account}:event_{field}_mismatch")
            video_id = event.get("video_id", data.get("video_id"))
            if video_id is not None and video_id not in expected_ids:
                issues.append(f"{account}:event_video_id_unexpected")
            for field in ("selected_ids", "video_ids", "expected_ids"):
                actual_ids = event.get(field, data.get(field))
                if actual_ids is not None and actual_ids != expected_ids:
                    issues.append(f"{account}:event_{field}_mismatch")
    if parseable_event_count == 0:
        issues.append(f"{account}:event_log_empty_or_invalid")
    return sorted(set(issues))


def _is_executable_packet(packet: Mapping[str, Any]) -> bool:
    if packet.get("executable") is False:
        return False
    return (
        packet.get("kind") == "offline_uncached_throughput_pair"
        and packet.get("packet_version") == 2
        and packet.get("coordinator", {}).get("execute_requires_explicit_flag") is True
    )


def _validation_provenance_issues(
    packet: Mapping[str, Any],
    pair: str,
    arm: str,
    receipt: Mapping[str, Any],
    *,
    packet_path: Path | None = None,
    receipt_path: Path | None = None,
) -> list[str]:
    """Bind executable validation to the packet and the recorded artifacts."""
    if not _is_executable_packet(packet):
        return []
    issues: list[str] = []
    if packet_path is None:
        raw_packet_path = packet.get("packet_path")
        packet_path = Path(str(raw_packet_path)) if raw_packet_path else None
    if packet_path is None:
        return ["execution_provenance:packet_path_missing"]
    try:
        _validate_execution_provenance(
            packet_path,
            packet,
            check_mutable_staging_fingerprints=False,
        )
    except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
        issues.append(f"execution_provenance:{exc}")
        # Do not inspect packet-owned paths after the packet-level contract has
        # already failed.  Validation must return a failed result, not raise
        # while indexing a missing path from malformed input.
        return sorted(set(issues))

    pair_data = packet.get("pairs", {}).get(pair)
    arm_data = pair_data.get("arms", {}).get(arm) if isinstance(pair_data, Mapping) else None
    packet_root = Path(str(packet.get("packet_root", ""))).resolve()
    if not isinstance(arm_data, Mapping):
        return issues + ["execution_provenance:arm_missing"]
    coordinator = packet.get("coordinator", {})
    strict_identity = isinstance(coordinator, Mapping) and coordinator.get("strict_runtime_identity") is True
    expected_launch_nonce = arm_data.get("launch_nonce") if strict_identity else None
    if strict_identity:
        if not isinstance(expected_launch_nonce, str) or not expected_launch_nonce:
            issues.append("execution_provenance:launch_nonce_missing")
        elif receipt.get("execution_nonce") != expected_launch_nonce:
            issues.append("receipt_execution_nonce_mismatch")
    expected_receipt_path = Path(str(arm_data["staging_db"])).resolve().parent / "throughput_receipt.json"
    if receipt_path is None:
        issues.append("receipt_path_missing")
    elif receipt_path.resolve() != expected_receipt_path.resolve():
        issues.append("receipt_path_mismatch")
    for field, expected in (("packet_path", packet_path), ("packet_root", packet_root)):
        actual = receipt.get(field)
        if not isinstance(actual, str) or Path(actual).resolve() != expected.resolve():
            issues.append(f"receipt_{field}_mismatch")
    summary_path = receipt.get("summary_path")
    if not isinstance(summary_path, str):
        return issues + ["summary_path_missing"]
    summary_file = Path(summary_path)
    try:
        _assert_inside(summary_file, packet_root, f"{pair}/{arm} summary")
        summary = _load(summary_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return issues + [f"summary_provenance:{exc}"]
    if summary.get("summary_path") and Path(str(summary["summary_path"])).resolve() != summary_file.resolve():
        issues.append("summary_path_mismatch")
    summary_run_id = summary.get("run_id")
    if not isinstance(summary_run_id, str) or not summary_run_id:
        issues.append("summary_run_id_missing")

    expected_ids = list(pair_data.get("cohort_ids", [])) if isinstance(pair_data, Mapping) else []
    account_receipts = receipt.get("account_receipts")
    summaries = receipt.get("account_summaries")
    if not isinstance(account_receipts, Mapping) or not isinstance(summaries, Mapping):
        return issues + ["account_receipts_or_summaries_missing"]
    if summaries and any(summary_value != summary for summary_value in summaries.values() if isinstance(summary_value, Mapping)):
        issues.append("summary_identity_mismatch")
    for account, account_ids in (pair_data.get("account_manifests", {}) if isinstance(pair_data, Mapping) else {}).items():
        account_receipt = account_receipts.get(account)
        account_summary = summaries.get(account)
        if not isinstance(account_receipt, Mapping) or not isinstance(account_summary, Mapping):
            continue
        result = next((row for row in account_summary.get("account_results", [])
                       if isinstance(row, Mapping) and row.get("account_profile") == account), None)
        if not isinstance(result, Mapping):
            issues.append(f"{account}:summary_result_missing")
            continue
        for field, expected in (("receipt_path", account_receipt.get("receipt_path")),
                                ("manifest_path", account_receipt.get("manifest_path")),
                                ("batch_db_path", arm_data.get("staging_db"))):
            actual = result.get(field)
            if expected is not None and (not isinstance(actual, str) or Path(actual).resolve() != Path(str(expected)).resolve()):
                issues.append(f"{account}:summary_{field}_mismatch")
        event_root = Path(str(result.get("event_log_dir", "")))
        try:
            _assert_inside(event_root, packet_root, f"{pair}/{arm}/{account} events")
        except ValueError as exc:
            issues.append(f"{account}:event_log_dir_provenance:{exc}")
        issues.extend(_event_provenance_issues(
            event_root,
            account=account,
            expected_ids=list(account_ids),
            expected_identity={
                "run_id": summary_run_id,
                "account_profile": account,
                **({"execution_nonce": expected_launch_nonce} if expected_launch_nonce else {}),
            },
        ))
        if account_receipt.get("selected_ids") != list(account_ids):
            issues.append(f"{account}:receipt_expected_ids_mismatch")
        manifest_path = account_receipt.get("manifest_path")
        if manifest_path is not None:
            try:
                _assert_inside(Path(str(manifest_path)), packet_root, f"{pair}/{arm}/{account} manifest")
            except ValueError as exc:
                issues.append(f"{account}:receipt_manifest_path_provenance:{exc}")
    if receipt.get("selected_ids") != expected_ids:
        issues.append("receipt_expected_ids_mismatch")
    return sorted(set(issues))


def _account_gate(
    account: str,
    expected: list[str],
    summary: Mapping[str, Any],
    receipt: Mapping[str, Any],
    arm: str,
    *,
    adaptive_initial_workers: int | None = None,
) -> list[str]:
    issues: list[str] = []
    if receipt.get("account_profile") not in (None, account):
        issues.append(f"{account}:receipt_account_mismatch")
    actual_ids = receipt.get("selected_ids")
    if not isinstance(actual_ids, list) or actual_ids != expected:
        issues.append(f"{account}:selected_ids_mismatch")
    if len(expected) != len(set(expected)):
        issues.append(f"{account}:duplicate_expected_ids")
    if receipt.get("dry_run") is True or receipt.get("plan_only") is True:
        issues.append(f"{account}:not_live")
    result = next((row for row in summary.get("account_results", []) if row.get("account_profile") == account), None)
    if not isinstance(result, Mapping):
        issues.append(f"{account}:summary_result_missing")
        return issues
    if result.get("returncode") != 0 or result.get("error"):
        issues.append(f"{account}:child_failed")
    if result.get("selected_missing_video_ids"):
        issues.append(f"{account}:selected_missing_ids")
    if result.get("selected_complete_count") != len(expected):
        issues.append(f"{account}:incomplete_selected_ids")
    elapsed = result.get("elapsed_s")
    if not isinstance(elapsed, (int, float)) or elapsed <= 0:
        issues.append(f"{account}:elapsed_s_missing_or_invalid")
    if arm == "adaptive" and adaptive_initial_workers is not None:
        root = Path(str(result.get("event_log_dir", "")))
        if not root.is_dir() or max(_event_target_workers(root) or [0]) <= adaptive_initial_workers:
            issues.append(
                f"{account}:adaptive_target_workers_not_gt_{adaptive_initial_workers}"
            )
    return issues


def validate_arm(
    packet: Mapping[str, Any], pair: str, arm: str, receipt: Mapping[str, Any], *, receipt_path: Path | None = None
) -> dict[str, Any]:
    issues: list[str] = []
    issues.extend(_validation_provenance_issues(packet, pair, arm, receipt, receipt_path=receipt_path))
    pair_data = packet.get("pairs", {}).get(pair)
    if not isinstance(pair_data, Mapping):
        return {"status": "failed", "issues": [f"unknown_pair:{pair}"]}
    expected = pair_data.get("account_manifests")
    if not isinstance(expected, Mapping):
        return {"status": "failed", "issues": [f"missing_account_manifests:{pair}"]}
    comparison_mode = packet.get("comparison_mode", packet.get("coordinator", {}).get("comparison_mode", "adaptive"))
    if comparison_mode not in ("adaptive", "environment"):
        issues.append(f"unsupported_comparison_mode:{comparison_mode}")
    environment_mode = comparison_mode == "environment"
    if receipt.get("pair_id") != pair or receipt.get("arm") != arm:
        issues.append("receipt_identity_mismatch")
    runner_status = str(receipt.get("runner_status", "completed"))
    if runner_status != "completed":
        issues.append(f"runner_not_completed:{runner_status}")
    aborted = receipt.get("abort_status") == "aborted" or bool(receipt.get("abort_reason"))
    if aborted and runner_status == "completed":
        issues.append("aborted_runner_claims_completed")
    if aborted and receipt.get("termination_confirmed") is not True:
        issues.append("aborted_runner_termination_unconfirmed")
    pair_ids = receipt.get("selected_ids")
    if not isinstance(pair_ids, list) or pair_ids != list(pair_data.get("cohort_ids", [])):
        issues.append("pair_selected_ids_mismatch")
    if isinstance(pair_ids, list) and len(set(pair_ids)) != len(pair_ids):
        issues.append("duplicate_pair_selected_ids")
    if receipt.get("db_integrity") != "ok" or receipt.get("cache_integrity") != "ok":
        issues.append("database_or_cache_integrity_failed")
    if receipt.get("selected_cache_absent_before_launch") is not True:
        issues.append("selected_cache_was_not_absent")
    arm_data = pair_data.get("arms", {}).get(arm) if isinstance(pair_data.get("arms"), Mapping) else None
    arm_settings = arm_data.get("effective_account_settings") if isinstance(arm_data, Mapping) else None
    if not isinstance(arm_settings, Mapping):
        # Legacy in-memory fixtures predate per-arm settings. Executable
        # packets remain fail-closed in _validate_execution_provenance.
        arm_settings = effective_account_settings(arm)
    adaptive_targets: dict[str, int] = {}
    if arm == "adaptive" and not environment_mode:
        for account, settings in arm_settings.items():
            if isinstance(settings, Mapping) and settings.get("adaptive_workers") is True:
                initial_workers = settings.get("workers_per_account")
                if isinstance(initial_workers, int) and not isinstance(initial_workers, bool):
                    adaptive_targets[str(account)] = initial_workers
        if not adaptive_targets:
            issues.append("adaptive_no_target_accounts")
    if isinstance(arm_data, Mapping):
        try:
            if _integrity(Path(str(arm_data["staging_db"]))) != "ok":
                issues.append("staging_db_integrity_failed")
            cached = _non_empty_cache_ids(Path(str(arm_data["staging_cache"])), list(pair_ids or []))
            if cached != set(pair_ids or []):
                issues.append("selected_cache_missing_or_empty_after_run")
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            issues.append(f"staging_integrity_read_failed:{type(exc).__name__}")
    if environment_mode and isinstance(arm_data, Mapping):
        control_data = pair_data.get("arms", {}).get("control") if isinstance(pair_data.get("arms"), Mapping) else None
        control_settings = control_data.get("effective_account_settings") if isinstance(control_data, Mapping) else None
        if isinstance(control_settings, Mapping) and arm_settings != control_settings:
            issues.append("environment_mode_settings_not_identical")
    account_receipts = receipt.get("account_receipts")
    summaries = receipt.get("account_summaries")
    if not isinstance(account_receipts, Mapping) or not isinstance(summaries, Mapping):
        issues.append("account_receipts_or_summaries_missing")
        return {"status": "failed", "issues": issues}
    elapsed_by_account: dict[str, float] = {}
    completed_total = 0
    per_account_vph: dict[str, float] = {}
    for account, ids in expected.items():
        account_receipt = account_receipts.get(account)
        summary = summaries.get(account)
        if not isinstance(account_receipt, Mapping) or not isinstance(summary, Mapping):
            issues.append(f"{account}:account_artifact_missing")
            continue
        issues.extend(
            _account_gate(
                account,
                list(ids),
                summary,
                account_receipt,
                arm,
                adaptive_initial_workers=adaptive_targets.get(account),
            )
        )
        result = next((row for row in summary.get("account_results", []) if row.get("account_profile") == account), None)
        if isinstance(result, Mapping):
            elapsed = float(result.get("elapsed_s", 0) or 0)
            completed = int(result.get("selected_complete_count", 0) or 0)
            elapsed_by_account[account] = elapsed
            completed_total += completed
            if elapsed > 0:
                per_account_vph[account] = completed * 3600.0 / elapsed
        event_root = Path(str(result.get("event_log_dir", ""))) if isinstance(result, Mapping) else Path()
        actions = _event_actions(event_root) if event_root.is_dir() else set()
        for family, alternatives in REQUIRED_EVENT_FAMILIES.items():
            if not actions.intersection(alternatives):
                issues.append(f"{account}:missing_event_family:{family}")
    parallel_elapsed = max(elapsed_by_account.values()) if elapsed_by_account else None
    expected_vph = completed_total * 3600.0 / parallel_elapsed if parallel_elapsed else None
    raw_vph_valid = receipt.get("vph_valid")
    if not isinstance(raw_vph_valid, bool):
        issues.append("vph_valid_missing_or_invalid")
        vph_valid = False
    else:
        vph_valid = raw_vph_valid
    if aborted and vph_valid is not False:
        issues.append("aborted_runner_vph_must_be_invalid")
    if vph_valid is False:
        if receipt.get("vph") is not None:
            issues.append("failed_runner_vph_must_be_null")
        if receipt.get("per_account_vph") not in ({}, None):
            issues.append("failed_runner_per_account_vph_must_be_empty")
    else:
        vph = receipt.get("vph")
        if not isinstance(vph, (int, float)) or expected_vph is None or abs(float(vph) - expected_vph) > 0.01:
            issues.append("vph_semantics_mismatch")
        if receipt.get("per_account_vph") != per_account_vph:
            issues.append("per_account_vph_mismatch")
    if arm == "adaptive" and not environment_mode and not receipt.get("adaptive_claimed"):
        issues.append("adaptive_claim_missing")
    receipt_is_valid = vph_valid is not False and not issues
    reported_vph = expected_vph if receipt_is_valid else None
    reported_per_account_vph = per_account_vph if receipt_is_valid else {}
    return {"status": "passed" if not issues else "failed", "issues": issues,
            "completed_ids": completed_total, "elapsed_s": parallel_elapsed,
            "vph": reported_vph, "per_account_vph": reported_per_account_vph}


def validate_packet(packet_path: Path, receipt_paths: list[Path]) -> dict[str, Any]:
    packet = _load(packet_path)
    expected = {(pair, arm) for pair in PAIRS for arm in ARMS}
    receipts: dict[tuple[str, str], dict[str, Any]] = {}
    receipt_paths_by_key: dict[tuple[str, str], Path] = {}
    issues: list[str] = []
    if _is_executable_packet(packet):
        try:
            _validate_execution_provenance(
                packet_path,
                packet,
                check_mutable_staging_fingerprints=False,
            )
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            issues.append(f"execution_provenance:{exc}")
    for path in receipt_paths:
        receipt = _load(path)
        key = (str(receipt.get("pair_id")), str(receipt.get("arm")))
        if key in receipts:
            issues.append(f"duplicate_receipt:{key}")
        receipts[key] = receipt
        receipt_paths_by_key[key] = path
    for key in sorted(expected):
        if key not in receipts:
            issues.append(f"missing_receipt:{key}")
        else:
            result = validate_arm(packet, key[0], key[1], receipts[key], receipt_path=receipt_paths_by_key.get(key))
            issues.extend(f"{key}:{issue}" for issue in result["issues"])
    return {"status": "passed" if not issues else "failed", "issues": issues,
            "validated_receipt_count": len(receipts), "packet_path": str(packet_path.resolve())}


def _command(packet: Mapping[str, Any], pair: str, arm: str, run_root: Path) -> list[str]:
    arm_data = packet["pairs"][pair]["arms"][arm]
    manifest = arm_data["combined_manifest_path"]
    ids = arm_data["combined_manifest_ids"]
    settings = arm_data.get("effective_account_settings") or effective_account_settings(arm)
    if set(settings) != set(ACCOUNTS) or any(
        not isinstance(value, Mapping) or not isinstance(value.get("workers_per_account"), int)
        or isinstance(value.get("workers_per_account"), bool)
        or value.get("workers_per_account", 0) < 1
        for value in settings.values()
    ):
        raise ValueError(f"packet has invalid per-account workers_per_account for {pair}/{arm}")
    global_default_workers = int(settings[next(iter(ACCOUNTS))]["workers_per_account"])
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "run_multi_account_fetch.py"),
            "--limit", str(len(ids)), "--accounts", ",".join(ACCOUNTS), "--workers-per-account", str(global_default_workers),
            "--account-settings", str(arm_data["account_settings_path"]), "--db-path", str(arm_data["staging_db"]),
            "--transcript-cache-db-path", str(arm_data["staging_cache"]), "--video-manifest", manifest,
            "--output-root", str(run_root), "--parallel-accounts"]
    return command


def _assert_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} is outside packet root: {path}") from exc


def _validate_execution_provenance(
    packet_path: Path,
    packet: Mapping[str, Any],
    *,
    check_mutable_staging_fingerprints: bool = True,
) -> None:
    if packet.get("kind") != "offline_uncached_throughput_pair" or packet.get("packet_version") != 2:
        raise ValueError("packet kind/version is not supported for execution")
    packet_root = Path(str(packet.get("packet_root", ""))).resolve()
    if not packet_root.is_dir():
        raise ValueError(f"packet root is missing: {packet_root}")
    if packet_path.resolve() != Path(str(packet.get("packet_path", ""))).resolve():
        raise ValueError("packet path does not match packet metadata")
    _assert_inside(packet_path, packet_root, "packet")
    comparison_mode = packet.get("comparison_mode", packet.get("coordinator", {}).get("comparison_mode", "adaptive"))
    if comparison_mode not in ("adaptive", "environment"):
        raise ValueError(f"unsupported comparison mode: {comparison_mode}")
    packet_environment = validate_environment_overrides(packet.get("environment_overrides"))
    if packet.get("environment_overrides_fingerprint", fingerprint(packet_environment)) != fingerprint(packet_environment):
        raise ValueError("packet environment overrides fingerprint mismatch")
    coordinator = packet.get("coordinator", {})
    strict_identity = isinstance(coordinator, Mapping) and coordinator.get("strict_runtime_identity") is True
    if strict_identity:
        execution_nonce = packet.get("execution_nonce")
        if not isinstance(execution_nonce, str) or not execution_nonce:
            raise ValueError("packet execution nonce is missing")
        if packet.get("packet_fingerprint") != _packet_fingerprint(packet):
            raise ValueError("packet fingerprint mismatch")
    if isinstance(coordinator, Mapping):
        if coordinator.get("comparison_mode", comparison_mode) != comparison_mode:
            raise ValueError("coordinator comparison mode mismatch")
        coordinator_environment = validate_environment_overrides(coordinator.get("environment_overrides"))
        if coordinator_environment != packet_environment:
            raise ValueError("coordinator environment overrides mismatch")
        if coordinator.get("environment_overrides_fingerprint", fingerprint(coordinator_environment)) != fingerprint(coordinator_environment):
            raise ValueError("coordinator environment overrides fingerprint mismatch")
    exclusions = packet.get("exclusions")
    if exclusions is not None:
        if not isinstance(exclusions, Mapping):
            raise ValueError("packet exclusions must be an object")
        excluded_ids = exclusions.get("video_ids")
        if not isinstance(excluded_ids, list) or excluded_ids != sorted(set(str(item) for item in excluded_ids)):
            raise ValueError("packet exclusions are not canonical")
        if exclusions.get("fingerprint") != _fingerprint(excluded_ids):
            raise ValueError("packet exclusions fingerprint mismatch")
        if exclusions.get("reason") != "benchmark_only_offline_exclusion":
            raise ValueError("packet exclusion reason is not benchmark-only")
    for account in ACCOUNTS:
        expected = ACCOUNTS[account]
        actual = packet.get("accounts", {}).get(account)
        if not isinstance(actual, Mapping) or actual.get("account_profile") != account or actual.get("billing_plan") != expected["billing_plan"]:
            raise ValueError(f"account billing plan identity mismatch: {account}")
    for label, key in (("canonical db", "canonical_db"), ("reference cache", "reference_cache")):
        path = Path(str(packet.get(key, "")))
        expected_fp = packet.get("canonical_fingerprints", {}).get("db" if key == "canonical_db" else "reference_cache")
        if not path.is_file() or not expected_fp or file_fingerprint(path) != expected_fp:
            raise ValueError(f"{label} provenance mismatch")
    for pair in PAIRS:
        for arm in ARMS:
            arm_data = packet["pairs"][pair]["arms"][arm]
            if strict_identity:
                launch_nonce = arm_data.get("launch_nonce")
                if not isinstance(launch_nonce, str) or not launch_nonce:
                    raise ValueError(f"{pair}/{arm} launch nonce is missing")
            settings = arm_data.get("effective_account_settings")
            if not isinstance(settings, Mapping):
                raise ValueError(f"effective account settings missing: {pair}/{arm}")
            if arm_data.get("effective_settings_fingerprint") != fingerprint(settings):
                raise ValueError(f"effective account settings fingerprint mismatch: {pair}/{arm}")
            if set(settings) != set(ACCOUNTS):
                raise ValueError(f"effective account settings accounts mismatch: {pair}/{arm}")
            try:
                file_settings = json.loads(
                    Path(str(arm_data["account_settings_path"])).read_text(encoding="utf-8")
                )
            except (KeyError, OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"account settings contents unreadable: {pair}/{arm}") from exc
            if file_settings != settings:
                raise ValueError(f"account settings contents mismatch: {pair}/{arm}")
            for label, raw_path in (("staging db", arm_data.get("staging_db")), ("staging cache", arm_data.get("staging_cache")),
                                    ("settings", arm_data.get("account_settings_path")), ("combined manifest", arm_data.get("combined_manifest_path"))):
                path = Path(str(raw_path))
                _assert_inside(path, packet_root, f"{pair}/{arm} {label}")
                if not path.is_file():
                    raise ValueError(f"{pair}/{arm} {label} is missing")
            artifacts = arm_data.get("artifact_fingerprints", {})
            fingerprinted_artifacts = ("staging_db", "staging_cache", "combined_manifest")
            if not check_mutable_staging_fingerprints:
                fingerprinted_artifacts = ("combined_manifest",)
            for name in fingerprinted_artifacts:
                path_key = name if name != "combined_manifest" else "combined_manifest_path"
                if file_fingerprint(Path(str(arm_data[path_key]))) != artifacts.get(name):
                    raise ValueError(f"{pair}/{arm} {name} provenance mismatch")
            if file_fingerprint(Path(str(arm_data["account_settings_path"]))) != arm_data.get("account_settings_fingerprint"):
                raise ValueError(f"{pair}/{arm} settings provenance mismatch")
            env_overrides = validate_environment_overrides({arm: arm_data.get("environment_overrides", {})})[arm]
            if env_overrides != packet_environment[arm]:
                raise ValueError(f"{pair}/{arm} environment overrides do not match packet")
            if arm_data.get("environment_overrides_fingerprint", fingerprint(env_overrides)) != fingerprint(env_overrides):
                raise ValueError(f"{pair}/{arm} environment overrides fingerprint mismatch")
            for account, manifest in arm_data.get("manifest_templates", {}).items():
                path = Path(str(manifest["manifest_path"]))
                _assert_inside(path, packet_root, f"{pair}/{arm}/{account} manifest")
                if file_fingerprint(path) != artifacts.get("manifests", {}).get(account):
                    raise ValueError(f"{pair}/{arm}/{account} manifest provenance mismatch")
            if comparison_mode == "environment" and arm == "adaptive":
                control_settings = packet["pairs"][pair]["arms"]["control"].get("effective_account_settings")
                if settings != control_settings:
                    raise ValueError(f"{pair}/{arm} environment settings are not identical to control")


def _validate_fresh_runtime_roots(packet: Mapping[str, Any]) -> None:
    """Refuse execution if any packet-owned run root has been used before."""
    packet_root = Path(str(packet["packet_root"])).resolve()
    for pair in PAIRS:
        for arm in ARMS:
            arm_data = packet["pairs"][pair]["arms"][arm]
            run_root = Path(str(arm_data["staging_db"])).resolve().parent / "run"
            _assert_inside(run_root, packet_root, f"{pair}/{arm} runtime root")
            if run_root.exists() or run_root.is_symlink():
                raise ValueError(f"{pair}/{arm} runtime root must be newly created: {run_root}")


def _invalidate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Make a receipt fail closed after any post-run validation failure."""
    normalized = dict(receipt)
    normalized["vph_valid"] = False
    normalized["vph"] = None
    normalized["per_account_vph"] = {}
    return normalized


def execute_packet(packet_path: Path) -> dict[str, Any]:
    packet = _load(packet_path)
    if packet.get("coordinator", {}).get("execute_requires_explicit_flag") is not True:
        raise ValueError("packet is not a repeated-throughput packet")
    _validate_execution_provenance(packet_path, packet)
    _validate_fresh_runtime_roots(packet)
    all_receipts: list[Path] = []
    aggregate: dict[str, Any] = {"packet_path": str(packet_path.resolve()), "arms": {}, "control_before_adaptive": True}
    aborted_by: str | None = None
    for pair in PAIRS:
        pair_dir = Path(packet["pairs"][pair]["arms"]["control"]["staging_db"]).parents[1]
        for arm in ARMS:
            if aborted_by is not None:
                aggregate["arms"][f"{pair}/{arm}"] = {
                    "status": "skipped", "issues": [f"not_launched_after_abort:{aborted_by}"]
                }
                continue
            arm_data = packet["pairs"][pair]["arms"][arm]
            run_root = pair_dir / arm / "run"
            account_summaries: dict[str, Any] = {}
            account_receipts: dict[str, Any] = {}
            if _ids_in_cache(Path(arm_data["staging_cache"]), list(packet["pairs"][pair]["cohort_ids"])):
                raise RuntimeError(f"{pair}/{arm} selected IDs were cached before launch")
            command = _command(packet, pair, arm, run_root)
            env_overrides = validate_environment_overrides({arm: arm_data.get("environment_overrides", {})})[arm]
            child_env = os.environ.copy()
            child_env.update(env_overrides)
            launch_nonce = arm_data.get("launch_nonce")
            if packet.get("coordinator", {}).get("strict_runtime_identity") is True:
                if not isinstance(launch_nonce, str) or not launch_nonce:
                    raise ValueError(f"{pair}/{arm} launch nonce is missing")
                child_env["YTIS_THROUGHPUT_PAIR_EXECUTION_NONCE"] = launch_nonce
            abort_enabled = bool(arm_data.get("abort_on_source_add_failure", packet.get("coordinator", {}).get("abort_on_source_add_failure", False)))
            completed, abort_reason, termination = _launch_arm(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                run_root=run_root,
                env=child_env,
                abort_on_source_add_failure=abort_enabled,
                expected_execution_nonce=launch_nonce if isinstance(launch_nonce, str) else None,
                expected_accounts=set(ACCOUNTS),
                expected_video_ids=set(packet["pairs"][pair]["cohort_ids"]),
            )
            summary_path = run_root / "multi_account_fetch_summary.json"
            summary = _load(summary_path) if summary_path.is_file() else None
            runner_status = (
                "aborted"
                if abort_reason
                else "completed"
                if completed.returncode == 0 and isinstance(summary, Mapping) and summary.get("status") == "completed"
                else "partial"
                if isinstance(summary, Mapping)
                else "runner_failed"
            )
            if not isinstance(summary, Mapping):
                receipt_path = pair_dir / arm / "throughput_receipt.json"
                arm_receipt = {
                    "pair_id": pair,
                    "arm": arm,
                    "selected_ids": list(packet["pairs"][pair]["cohort_ids"]),
                    "selected_cache_absent_before_launch": True,
                    "db_integrity": "unverified",
                    "cache_integrity": "unverified",
                    "account_summaries": {},
                    "account_receipts": {},
                    "adaptive_claimed": arm == "adaptive" and packet.get("comparison_mode", "adaptive") != "environment",
                    "runner_status": runner_status,
                    "abort_status": "aborted" if abort_reason else "not_aborted",
                    "abort_reason": abort_reason,
                    "termination": termination,
                    "termination_confirmed": (
                        termination.get("termination_confirmed") if isinstance(termination, Mapping) else None
                    ),
                    "runner_returncode": completed.returncode,
                    "runner_command": command,
                    "summary_path": str(summary_path.resolve()),
                    "vph_valid": False,
                    "vph": None,
                    "per_account_vph": {},
                    "execution_nonce": launch_nonce,
                }
                arm_receipt["packet_path"] = str(packet_path.resolve())
                arm_receipt["packet_root"] = str(packet["packet_root"])
                _atomic_write_json(receipt_path, arm_receipt)
                all_receipts.append(receipt_path)
                gate = validate_arm(packet, pair, arm, arm_receipt, receipt_path=receipt_path)
                if gate["status"] != "passed":
                    arm_receipt = _invalidate_receipt(arm_receipt)
                    _atomic_write_json(receipt_path, arm_receipt)
                    gate = validate_arm(packet, pair, arm, arm_receipt, receipt_path=receipt_path)
                aggregate["arms"][f"{pair}/{arm}"] = {"receipt_path": str(receipt_path), **gate}
                if abort_reason or gate["status"] != "passed":
                    aborted_by = f"{pair}/{arm}"
                if arm == "control":
                    aggregate["arms"][f"{pair}/adaptive"] = {
                        "status": "failed", "issues": ["adaptive_not_launched_control_runner_failed"]
                    }
                    break
                continue
            for account in ACCOUNTS:
                account_summaries[account] = summary
                result = next((row for row in summary.get("account_results", []) if row.get("account_profile") == account), None)
                if isinstance(result, Mapping) and result.get("receipt_path"):
                    account_receipts[account] = _load(result["receipt_path"])
            ids = list(packet["pairs"][pair]["cohort_ids"])
            arm_receipt = {"pair_id": pair, "arm": arm, "selected_ids": ids,
                           "selected_cache_absent_before_launch": True, "db_integrity": _integrity(Path(arm_data["staging_db"])),
                           "cache_integrity": _integrity(Path(arm_data["staging_cache"])),
                           "account_summaries": account_summaries, "account_receipts": account_receipts,
                           "adaptive_claimed": arm == "adaptive" and packet.get("comparison_mode", "adaptive") != "environment", "runner_status": runner_status,
                           "runner_returncode": completed.returncode, "runner_command": command,
                           "summary_path": str(summary_path.resolve()),
                            "abort_status": "aborted" if abort_reason else "not_aborted",
                            "abort_reason": abort_reason}
            arm_receipt["execution_nonce"] = launch_nonce
            arm_receipt["termination"] = termination
            arm_receipt["termination_confirmed"] = (
                termination.get("termination_confirmed") if isinstance(termination, Mapping) else None
            )
            elapsed_by_account: dict[str, float] = {}
            completed_by_account: dict[str, int] = {}
            for account in ACCOUNTS:
                result = next((row for row in summary.get("account_results", []) if row.get("account_profile") == account), None)
                if isinstance(result, Mapping):
                    elapsed = float(result.get("elapsed_s", 0) or 0)
                    if elapsed > 0:
                        elapsed_by_account[account] = elapsed
                    completed_by_account[account] = int(result.get("selected_complete_count", 0) or 0)
            observed_parallel_elapsed = max(elapsed_by_account.values()) if elapsed_by_account else None
            observed_per_account_vph = {
                account: completed_by_account[account] * 3600.0 / elapsed_by_account[account]
                for account in ACCOUNTS
                if account in elapsed_by_account
            }
            observed_completed_count = sum(completed_by_account.values())
            arm_receipt["observed_per_account_vph"] = observed_per_account_vph
            arm_receipt["observed_parallel_elapsed_s"] = observed_parallel_elapsed
            arm_receipt["observed_completed_count"] = observed_completed_count
            arm_receipt["vph_valid"] = runner_status == "completed" and not abort_reason
            arm_receipt["per_account_vph"] = observed_per_account_vph if arm_receipt["vph_valid"] else {}
            arm_receipt["parallel_elapsed_s"] = observed_parallel_elapsed
            arm_receipt["vph"] = (
                observed_completed_count * 3600.0 / observed_parallel_elapsed
                if arm_receipt["vph_valid"] and observed_parallel_elapsed else None
            )
            receipt_path = pair_dir / arm / "throughput_receipt.json"
            arm_receipt["packet_path"] = str(packet_path.resolve())
            arm_receipt["packet_root"] = str(packet["packet_root"])
            _atomic_write_json(receipt_path, arm_receipt)
            all_receipts.append(receipt_path)
            gate = validate_arm(packet, pair, arm, arm_receipt, receipt_path=receipt_path)
            if gate["status"] != "passed":
                arm_receipt = _invalidate_receipt(arm_receipt)
                _atomic_write_json(receipt_path, arm_receipt)
                gate = validate_arm(packet, pair, arm, arm_receipt, receipt_path=receipt_path)
            aggregate["arms"][f"{pair}/{arm}"] = {"receipt_path": str(receipt_path), **gate}
            if abort_reason or gate["status"] != "passed":
                aborted_by = f"{pair}/{arm}"
            if arm == "control" and (runner_status != "completed" or gate["status"] != "passed"):
                aggregate["arms"][f"{pair}/adaptive"] = {
                    "status": "failed", "issues": ["adaptive_not_launched_control_gate_failed"]
                }
                break
    aggregate["status"] = "passed" if all(item["status"] == "passed" for item in aggregate["arms"].values()) else "failed"
    aggregate["receipt_paths"] = [str(path) for path in all_receipts]
    # All arm receipts and staging integrity checks have completed by this
    # point.  The cleanup module still protects recently modified artifacts,
    # so a just-finished experiment remains available for the one-hour grace
    # period while older experiment roots are reclaimed.
    packet_root = Path(str(packet["packet_root"]))
    try:
        aggregate["staging_cleanup"] = cleanup_staging(packet_root.parent)
    except (OSError, ValueError) as exc:
        aggregate["staging_cleanup"] = {
            "status": "blocked",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--reference-cache", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--items-per-account", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Optional per-account NotebookLM subbatch size recorded in account settings")
    parser.add_argument("--account-settings-json", type=Path, default=None,
                        help="Optional JSON object of per-account settings overrides")
    parser.add_argument("--comparison-mode", choices=("adaptive", "environment"), default="adaptive")
    parser.add_argument("--environment-overrides-json", type=Path, default=None)
    parser.add_argument("--abort-on-source-add-failure", action="store_true")
    parser.add_argument(
        "--caption-state", choices=DIRECT_THROUGHPUT_CAPTION_STATES, default="captioned",
        help="Direct NotebookLM cohort state; known no-caption rows require the fallback-aware backlog runner",
    )
    parser.add_argument("--exclude-video-id", action="append", default=[],
                        help="Offline benchmark-only candidate exclusion; may be repeated")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize live runner launches")
    args = parser.parse_args(argv)
    try:
        if args.execute:
            if not args.packet:
                parser.error("--execute requires --packet")
            result = execute_packet(args.packet)
        elif args.packet:
            result = validate_packet(args.packet, args.receipt or sorted(args.packet.parent.glob("pair-*/adaptive/throughput_receipt.json")) + sorted(args.packet.parent.glob("pair-*/control/throughput_receipt.json")))
        else:
            if not args.db or not args.reference_cache or not args.output_root:
                parser.error("planning requires --db, --reference-cache, and --output-root")
            result = build_plan(
                db=args.db,
                reference_cache=args.reference_cache,
                output_root=args.output_root,
                items_per_account=args.items_per_account,
                caption_state=args.caption_state,
                batch_size=args.batch_size,
                account_settings=load_account_settings_overrides(args.account_settings_json),
                exclude_video_ids=tuple(args.exclude_video_id),
                comparison_mode=args.comparison_mode,
                environment_overrides=json.loads(args.environment_overrides_json.read_text(encoding="utf-8"))
                if args.environment_overrides_json else None,
                abort_on_source_add_failure=args.abort_on_source_add_failure,
            )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status", "planned") in {"planned", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
