#!/usr/bin/env python3
"""Read-only health report for the yt-is unattended backlog supervisor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.video_selection_manifest import (  # noqa: E402
    load_video_selection_manifest,
    read_selection_receipt,
    select_manifest_entries,
    verify_selection_receipt,
)


def _pending_count(db_path: Path) -> int:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status = 'pending'"
        ).fetchone()
    return int(row[0] if row else 0)


def _runtime_status(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _pid_is_alive(pid: object) -> bool:
    # psutil, not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, so
    # an os.kill "probe" delivers Ctrl+C to the target and returns success
    # for dead PIDs too — the lease-expiry/orphaned branches below would be
    # unreachable and a live coordinator could be interrupted by a check.
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return psutil.pid_exists(pid)


def _runtime_process_matches(pid: object, output_root: Path) -> bool | None:
    """Confirm a live PID owns the recorded coordinator run.

    PID liveness proves only that some process exists.  Windows PID
    reuse can otherwise make an old runtime receipt appear active forever.
    ``None`` means the process was live but its command line could not be
    inspected, which is intentionally fail-closed for health reporting.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        command_line = psutil.Process(pid).cmdline()
    except (psutil.Error, OSError):
        return None
    if not command_line:
        return False
    normalized_root = os.path.normcase(os.path.normpath(str(output_root.resolve())))
    normalized_root = normalized_root.replace("/", "\\")
    normalized_command = " ".join(command_line).casefold().replace("/", "\\")
    return (
        normalized_root.casefold() in normalized_command
        and "run_multi_account_fetch.py" in normalized_command
    )


def _validate_summary_counts(
    summary: dict[str, object],
    supervisor_status: object,
    issues: list[str],
) -> None:
    """Validate the summary fields used to declare a chunk successful."""
    selected_count = summary.get("selected_count")
    complete_count = summary.get("selected_complete_count")
    status_counts = summary.get("selected_status_counts")
    if isinstance(selected_count, bool) or not isinstance(selected_count, int) or selected_count < 0:
        issues.append("summary_selected_count_invalid")
        return
    if isinstance(complete_count, bool) or not isinstance(complete_count, int) or complete_count < 0:
        issues.append("summary_selected_complete_count_invalid")
    elif complete_count > selected_count:
        issues.append("summary_complete_count_exceeds_selected")
    if not isinstance(status_counts, dict):
        issues.append("summary_status_counts_invalid")
        return
    normalized_counts: dict[str, int] = {}
    for status, count in status_counts.items():
        if not isinstance(status, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            issues.append("summary_status_counts_invalid")
            return
        normalized_counts[status] = count
    if sum(normalized_counts.values()) != selected_count:
        issues.append("summary_status_counts_do_not_reconcile")
    if isinstance(complete_count, int) and normalized_counts.get("complete", 0) != complete_count:
        issues.append("summary_complete_count_mismatch")
    if supervisor_status == "completed" and (
        selected_count != 0 and normalized_counts.get("complete", 0) != selected_count
    ):
        issues.append("completed_summary_not_all_complete")


def _read_status_rows(db_path: Path, video_ids: list[str]) -> dict[str, dict[str, object]]:
    if not video_ids:
        return {}
    rows_by_id: dict[str, dict[str, object]] = {}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            for video_id, status in conn.execute(
                f"SELECT video_id, status FROM analysis_status "
                f"WHERE video_id IN ({placeholders})",
                chunk,
            ).fetchall():
                rows_by_id[str(video_id)] = {
                    "video_id": str(video_id),
                    "status": str(status),
                }
    return rows_by_id


def _read_selection_rows(db_path: Path, video_ids: list[str]) -> dict[str, dict[str, object]]:
    """Read the full selection fingerprint row shape from the active DB."""
    if not video_ids:
        return {}
    rows_by_id: dict[str, dict[str, object]] = {}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                "SELECT video_id, status, source, updated_at, has_captions "
                f"FROM analysis_status WHERE video_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for video_id, status, source, updated_at, has_captions in rows:
                rows_by_id[str(video_id)] = {
                    "video_id": str(video_id),
                    "status": str(status),
                    "source": str(source) if source is not None else None,
                    "updated_at": str(updated_at) if updated_at is not None else None,
                    "has_captions": int(has_captions) if has_captions is not None else None,
                }
    return rows_by_id


def _validate_planned_account_receipts(
    summary: dict[str, object],
    db_path: Path,
    issues: list[str],
    assignments: dict[str, dict[str, object]] | None = None,
    configured_accounts: tuple[str, ...] | None = None,
) -> None:
    """Validate plan-only manifests and receipts against the current DB."""
    account_results = summary.get("account_results")
    if not isinstance(account_results, list) or not account_results:
        issues.append("planned_account_receipts_missing")
        return
    run_id = summary.get("run_id")
    seen_ids: set[str] = set()
    seen_accounts: set[str] = set()
    total_selected = 0
    for index, raw_result in enumerate(account_results):
        prefix = f"planned_account_receipt_{index}"
        if not isinstance(raw_result, dict):
            issues.append(f"{prefix}_invalid")
            continue
        account = raw_result.get("account_profile")
        manifest_value = raw_result.get("manifest_path")
        receipt_value = raw_result.get("receipt_path")
        if not isinstance(account, str) or not account:
            issues.append(f"{prefix}_account_missing")
            continue
        if account in seen_accounts:
            issues.append(f"{prefix}_duplicate_account")
        seen_accounts.add(account)
        if configured_accounts is not None and account not in configured_accounts:
            issues.append(f"{prefix}_unknown_account")
        if not isinstance(manifest_value, str) or not isinstance(receipt_value, str):
            issues.append(f"{prefix}_paths_missing")
            continue
        try:
            manifest_path = Path(manifest_value).resolve()
            receipt_path = Path(receipt_value).resolve()
            manifest = load_video_selection_manifest(manifest_path)
            receipt = read_selection_receipt(receipt_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"{prefix}_unreadable:{type(exc).__name__}")
            continue

        expected_ids = [item.video_id for item in manifest.items]
        if len(set(expected_ids)) != len(expected_ids):
            issues.append(f"{prefix}_duplicate_manifest_ids")
        if seen_ids.intersection(expected_ids):
            issues.append(f"{prefix}_duplicate_across_accounts")
        seen_ids.update(expected_ids)

        criteria = manifest.selection_criteria
        if not isinstance(criteria, dict):
            issues.append(f"{prefix}_manifest_criteria_missing")
        else:
            if criteria.get("account_profile") != account:
                issues.append(f"{prefix}_account_mismatch")
            if run_id is not None and criteria.get("run_id") != run_id:
                issues.append(f"{prefix}_run_id_mismatch")
        if receipt.get("selection_name") != manifest.selection_name:
            issues.append(f"{prefix}_selection_name_mismatch")
        if receipt.get("selection_criteria") != manifest.selection_criteria:
            issues.append(f"{prefix}_selection_criteria_mismatch")
        if receipt.get("manifest_path") != str(manifest_path):
            issues.append(f"{prefix}_manifest_path_mismatch")
        if receipt.get("database_path") != str(db_path.resolve()):
            issues.append(f"{prefix}_database_path_mismatch")
        if receipt.get("selected_ids") != expected_ids:
            issues.append(f"{prefix}_selected_ids_mismatch")
        if receipt.get("manifest_item_count") != len(expected_ids):
            issues.append(f"{prefix}_manifest_item_count_mismatch")
        if receipt.get("selected_count") != len(expected_ids):
            issues.append(f"{prefix}_selected_count_mismatch")
        if receipt.get("dry_run") is not True or receipt.get("plan_only") is not True:
            issues.append(f"{prefix}_not_plan_only")
        if receipt.get("operation_mode") != "plan_only":
            issues.append(f"{prefix}_operation_mode_mismatch")
        if receipt.get("missing_count") != 0:
            issues.append(f"{prefix}_missing_ids")
        if receipt.get("non_pending_count") != 0:
            issues.append(f"{prefix}_non_pending_ids")
        if receipt.get("limit_omitted_count") != 0:
            issues.append(f"{prefix}_limit_omitted_ids")
        total_selected += len(expected_ids)

        assignment = assignments.get(account) if assignments is not None else None
        if assignments is not None and assignment is None:
            issues.append(f"{prefix}_assignment_missing")
        elif assignment is not None:
            if assignment.get("manifest_path") != str(manifest_path):
                issues.append(f"{prefix}_assignment_manifest_mismatch")
            if assignment.get("receipt_path") != str(receipt_path):
                issues.append(f"{prefix}_assignment_receipt_mismatch")
            if assignment.get("video_ids") != expected_ids:
                issues.append(f"{prefix}_assignment_ids_mismatch")

        rows_by_id = _read_selection_rows(db_path, expected_ids)
        if set(rows_by_id) != set(expected_ids):
            issues.append(f"{prefix}_current_ids_mismatch")
            continue
        if any(row.get("status") != "pending" for row in rows_by_id.values()):
            issues.append(f"{prefix}_current_rows_not_pending")
        selection = select_manifest_entries(manifest, rows_by_id)
        if selection.missing_ids or selection.non_pending_by_status:
            issues.append(f"{prefix}_current_selection_invalid")
        else:
            try:
                verify_selection_receipt(receipt, manifest, selection)
            except ValueError:
                issues.append(f"{prefix}_fingerprint_mismatch")

        reported_counts = raw_result.get("selected_status_counts")
        if reported_counts != {"pending": len(expected_ids)}:
            issues.append(f"{prefix}_reported_counts_mismatch")

    if configured_accounts is not None and seen_accounts != set(configured_accounts):
        issues.append("planned_account_assignments_do_not_match_config")
    if summary.get("selected_count") != total_selected:
        issues.append("planned_account_counts_do_not_reconcile")


def _validate_exact_account_receipts(
    summary: dict[str, object],
    db_path: Path,
    issues: list[str],
) -> None:
    """Validate account receipts, not only aggregate summary arithmetic."""
    selected_count = summary.get("selected_count")
    if selected_count == 0:
        return
    account_results = summary.get("account_results")
    if not isinstance(account_results, list) or not account_results:
        issues.append("completed_account_receipts_missing")
        return
    run_id = summary.get("run_id")
    seen_ids: set[str] = set()
    for index, raw_result in enumerate(account_results):
        prefix = f"account_receipt_{index}"
        if not isinstance(raw_result, dict):
            issues.append(f"{prefix}_invalid")
            continue
        account = raw_result.get("account_profile")
        manifest_value = raw_result.get("manifest_path")
        receipt_value = raw_result.get("receipt_path")
        if not isinstance(account, str) or not account:
            issues.append(f"{prefix}_account_missing")
            continue
        if not isinstance(manifest_value, str) or not isinstance(receipt_value, str):
            issues.append(f"{prefix}_paths_missing")
            continue
        manifest_path = Path(manifest_value)
        receipt_path = Path(receipt_value)
        try:
            manifest = load_video_selection_manifest(manifest_path)
            receipt = read_selection_receipt(receipt_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"{prefix}_unreadable:{type(exc).__name__}")
            continue

        criteria = manifest.selection_criteria
        if not isinstance(criteria, dict):
            issues.append(f"{prefix}_manifest_criteria_missing")
        else:
            if criteria.get("account_profile") != account:
                issues.append(f"{prefix}_account_mismatch")
            if run_id is not None and criteria.get("run_id") != run_id:
                issues.append(f"{prefix}_run_id_mismatch")
        if receipt.get("coordinator_snapshot_version") != 1:
            issues.append(f"{prefix}_snapshot_version_missing")
        if receipt.get("run_id") != run_id:
            issues.append(f"{prefix}_receipt_run_id_mismatch")
        if receipt.get("account_profile") != account:
            issues.append(f"{prefix}_receipt_account_mismatch")
        if receipt.get("selection_name") != manifest.selection_name:
            issues.append(f"{prefix}_selection_name_mismatch")
        if receipt.get("manifest_path") != str(manifest_path.resolve()):
            issues.append(f"{prefix}_manifest_path_mismatch")
        if receipt.get("database_path") != str(db_path.resolve()):
            issues.append(f"{prefix}_database_path_mismatch")
        if receipt.get("dry_run") is not False:
            issues.append(f"{prefix}_not_live_receipt")

        expected_ids = [item.video_id for item in manifest.items]
        if len(set(expected_ids)) != len(expected_ids):
            issues.append(f"{prefix}_duplicate_manifest_ids")
        if receipt.get("selected_ids") != expected_ids:
            issues.append(f"{prefix}_selected_ids_mismatch")
        if set(expected_ids) & seen_ids:
            issues.append(f"{prefix}_duplicate_across_accounts")
        seen_ids.update(expected_ids)

        snapshot = receipt.get("database_snapshot_rows")
        if not isinstance(snapshot, list) or len(snapshot) != len(expected_ids):
            issues.append(f"{prefix}_snapshot_rows_missing")
            continue
        snapshot_rows = [row for row in snapshot if isinstance(row, dict)]
        if len(snapshot_rows) != len(snapshot):
            issues.append(f"{prefix}_snapshot_rows_invalid")
            continue
        snapshot_by_id = {str(row.get("video_id")): dict(row) for row in snapshot_rows}
        if list(snapshot_by_id) != expected_ids or any(
            row.get("status") != "pending" for row in snapshot_rows
        ):
            issues.append(f"{prefix}_snapshot_selection_invalid")
            continue
        selection = select_manifest_entries(manifest, snapshot_by_id)
        if selection.missing_ids or selection.non_pending_by_status:
            issues.append(f"{prefix}_snapshot_not_pending")
            continue
        try:
            verify_selection_receipt(receipt, manifest, selection)
        except ValueError:
            issues.append(f"{prefix}_fingerprint_mismatch")

        current_rows = _read_status_rows(db_path, expected_ids)
        if set(current_rows) != set(expected_ids):
            issues.append(f"{prefix}_current_ids_mismatch")
        current_counts: dict[str, int] = {}
        for row in current_rows.values():
            status = str(row.get("status"))
            current_counts[status] = current_counts.get(status, 0) + 1
        reported_counts = raw_result.get("selected_status_counts")
        if isinstance(reported_counts, dict):
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in reported_counts.values()
            ):
                issues.append(f"{prefix}_reported_counts_invalid")
            else:
                normalized = {str(key): value for key, value in reported_counts.items()}
                if normalized != current_counts:
                    issues.append(f"{prefix}_current_counts_mismatch")
        else:
            issues.append(f"{prefix}_reported_counts_missing")


def _validate_latest_summary(
    summary: dict[str, object] | None,
    supervisor_status: object,
    issues: list[str],
) -> None:
    if summary is None:
        if supervisor_status in {"planned", "paused", "completed"}:
            issues.append("latest_summary_missing")
        return
    summary_status = summary.get("status")
    if summary_status not in {"completed", "partial", "failed", "blocked", "planned", "no_work"}:
        issues.append("latest_summary_status_invalid")
        return
    expected = {
        "planned": {"planned", "no_work"},
        "paused": {"completed"},
        "completed": {"completed", "no_work"},
    }.get(supervisor_status)
    if expected is not None and summary_status not in expected:
        issues.append(f"{supervisor_status}_summary_mismatch")
    _validate_summary_counts(summary, supervisor_status, issues)


def _validate_state_config(
    state: dict[str, object],
    db_path: Path,
    issues: list[str],
) -> tuple[str, ...] | None:
    config = state.get("config")
    if not isinstance(config, dict):
        issues.append("state_config_missing")
        return None
    if config.get("db_path") != str(db_path):
        issues.append("state_database_mismatch")
    accounts = config.get("accounts")
    if (
        not isinstance(accounts, list)
        or not accounts
        or any(not isinstance(account, str) or not account.strip() for account in accounts)
        or len(set(accounts)) != len(accounts)
    ):
        issues.append("state_accounts_invalid")
        accounts_tuple = None
    else:
        accounts_tuple = tuple(accounts)
    for field in ("chunk_size", "workers_per_account"):
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            issues.append(f"state_{field}_invalid")
    for field in ("execute", "parallel_accounts", "until_empty"):
        if not isinstance(config.get(field), bool):
            issues.append(f"state_{field}_invalid")
    value = config.get("max_chunks")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append("state_max_chunks_invalid")
    if config.get("until_empty") is True and config.get("execute") is not True:
        issues.append("state_until_empty_requires_execute")
    for path_field, fingerprint_field in (
        ("account_settings_path", "account_settings_file_fingerprint"),
        ("full_backlog_authorization_path", "full_backlog_authorization_file_fingerprint"),
    ):
        path_value = config.get(path_field)
        fingerprint = config.get(fingerprint_field)
        if path_value is None:
            if fingerprint is not None:
                issues.append(f"state_{fingerprint_field}_without_path")
            continue
        if not isinstance(path_value, str) or not path_value.strip():
            issues.append(f"state_{path_field}_invalid")
            continue
        path = Path(path_value)
        if not path.is_file():
            issues.append(f"state_{path_field}_missing")
            continue
        if (
            not isinstance(fingerprint, str)
            or not fingerprint.startswith("sha256:")
            or len(fingerprint) != len("sha256:") + 64
            or fingerprint != "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            issues.append(f"state_{fingerprint_field}_mismatch")
    return accounts_tuple


def inspect_backlog(*, db_path: Path, state_path: Path) -> dict[str, object]:
    db_path = db_path.resolve()
    state_path = state_path.resolve()
    issues: list[str] = []
    if not state_path.is_file():
        issues.append("state_missing")
        state: dict[str, object] = {}
    else:
        try:
            state_value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"state_invalid:{type(exc).__name__}")
            state_value = {}
        state = state_value if isinstance(state_value, dict) else {}
        if not isinstance(state_value, dict):
            issues.append("state_not_object")

    if state.get("schema_version") != 1:
        issues.append("state_schema_invalid")
    configured_accounts = _validate_state_config(state, db_path, issues)

    try:
        pending_count = _pending_count(db_path)
    except (OSError, sqlite3.Error) as exc:
        pending_count = None
        issues.append(f"database_unreadable:{type(exc).__name__}")

    supervisor_status = state.get("status")
    if supervisor_status not in {"planned", "running", "paused", "completed", "failed", "stopped"}:
        issues.append("state_status_invalid")

    chunks = state.get("chunks", [])
    if not isinstance(chunks, list):
        chunks = []
        issues.append("state_chunks_invalid")
    latest = chunks[-1] if chunks else None
    latest_summary: dict[str, object] | None = None
    runtime_receipt: dict[str, object] | None = None
    if isinstance(latest, dict):
        output_root_value = latest.get("output_root")
        if isinstance(output_root_value, str):
            runtime_path = Path(output_root_value) / "supervisor_runtime.json"
            runtime_receipt = _runtime_status(runtime_path)
            if runtime_receipt is not None:
                runtime_status = runtime_receipt.get("status")
                if runtime_status == "running":
                    pid = runtime_receipt.get("pid")
                    pid_alive = _pid_is_alive(pid)
                    lease_until = runtime_receipt.get("lease_until_epoch")
                    if pid_alive:
                        process_matches = _runtime_process_matches(pid, Path(output_root_value))
                        if process_matches is True:
                            issues.append("active_runtime")
                        elif process_matches is None:
                            issues.append("runtime_process_inspection_failed")
                        else:
                            issues.append("runtime_process_mismatch")
                    elif isinstance(lease_until, (int, float)) and float(lease_until) > time.time():
                        issues.append("orphaned_unexpired_lease")
                    else:
                        issues.append("orphaned_runtime")
                elif runtime_status in {"invalid", "launch_failed", "terminated_timeout"}:
                    issues.append(f"runtime_{runtime_status}")
        summary_path_value = latest.get("summary_path")
        if isinstance(summary_path_value, str) and Path(summary_path_value).is_file():
            try:
                value = json.loads(Path(summary_path_value).read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    latest_summary = value
                else:
                    issues.append("latest_summary_invalid")
            except (OSError, json.JSONDecodeError):
                issues.append("latest_summary_invalid")
        else:
            issues.append("latest_summary_missing")
        if runtime_receipt is None and latest_summary is None:
            issues.append("runtime_receipt_missing")

    _validate_latest_summary(latest_summary, supervisor_status, issues)
    if latest_summary is not None and latest_summary.get("status") in {
        "completed",
        "partial",
        "failed",
    }:
        _validate_exact_account_receipts(latest_summary, db_path, issues)
    elif latest_summary is not None and latest_summary.get("status") == "planned":
        raw_assignments = latest.get("assignment_ownership") if isinstance(latest, dict) else None
        assignments: dict[str, dict[str, object]] = {}
        if not isinstance(raw_assignments, list):
            issues.append("planned_assignments_missing")
        else:
            for raw_assignment in raw_assignments:
                if not isinstance(raw_assignment, dict) or not isinstance(
                    raw_assignment.get("account_profile"), str
                ):
                    issues.append("planned_assignment_invalid")
                    continue
                account = str(raw_assignment["account_profile"])
                if account in assignments:
                    issues.append("planned_assignment_duplicate_account")
                assignments[account] = raw_assignment
        _validate_planned_account_receipts(
            latest_summary,
            db_path,
            issues,
            assignments=assignments,
            configured_accounts=configured_accounts,
        )
    if supervisor_status in {"stopped", "failed"}:
        issues.append(f"supervisor_{supervisor_status}")
    if supervisor_status == "completed" and pending_count not in (None, 0):
        issues.append("completed_with_pending_rows")
    if pending_count == 0 and supervisor_status not in {"completed", "planned"}:
        issues.append("no_pending_rows_without_terminal_completion")

    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    readiness = {
        "planned": supervisor_status == "planned",
        "live_bounded": config.get("execute") is True and config.get("until_empty") is False,
        "scheduler_unverified": True,
        "residuals": bool(pending_count not in (None, 0)) or bool(
            latest_summary and latest_summary.get("status") in {"partial", "failed"}
        ),
        # This read-only command does not re-run the supervisor's full gate
        # validation. Presence of until_empty or a receipt must not become an
        # authorization claim.
        "full_authorization": False,
        "full_authorization_status": "not_verified",
    }
    if issues:
        health_status = "needs_attention"
    elif supervisor_status == "planned":
        health_status = "planned"
    elif supervisor_status == "completed":
        health_status = "healthy"
    else:
        health_status = "healthy" if pending_count is not None else "needs_attention"
    return {
        "health_status": health_status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "state_path": str(state_path),
        "supervisor_status": supervisor_status,
        "pending_count": pending_count,
        "chunk_count": len(chunks),
        "latest_chunk": latest,
        "latest_summary": latest_summary,
        "runtime_receipt": runtime_receipt,
        "readiness": readiness,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = inspect_backlog(db_path=args.db_path, state_path=args.state_path)
    except Exception as exc:
        payload = {
            "health_status": "needs_attention",
            "failure_type": type(exc).__name__,
            "failure_reason": str(exc),
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("health_status") in {"healthy", "planned"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
