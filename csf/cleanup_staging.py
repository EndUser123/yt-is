"""Safely prune staging SQLite artifacts from multi-account experiments.

The multi-account log tree contains both disposable staging databases and
small receipts that explain what happened.  This module only removes SQLite
artifacts after an age/activity check; JSON, Markdown, and text evidence are
retained until the whole experiment directory reaches the retention cutoff.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable

from csf.paths import get_multi_account_log_root

DEFAULT_EXPERIMENT_ROOT = get_multi_account_log_root()
# Keep the former workspace-level location in the default compatibility sweep
# so old receipts and staging files are still reclaimed without moving them.
LEGACY_EXPERIMENT_ROOT = Path("P:/.logs/multi_account_fetch")
DEFAULT_EXPERIMENT_ROOTS = (
    DEFAULT_EXPERIMENT_ROOT,
    LEGACY_EXPERIMENT_ROOT,
)
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_ACTIVE_GRACE_HOURS = 1
STAGING_SQLITE_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")

# This is a durable queue, not a disposable copy of either canonical DB.
PROTECTED_SQLITE_NAMES = frozenset({"transcript-fallback-queue.sqlite"})
PROTECTED_PATHS = (
    Path("P:/.data/yt-is/batch_status.sqlite"),
    Path("P:/.data/yt-is/browser"),
)


def _as_epoch(value: datetime | float | int | None) -> float:
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return float(value)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_root(root: Path) -> Path:
    requested = root.expanduser()
    if requested.is_symlink():
        raise ValueError(f"refusing symlink cleanup root: {requested}")
    resolved = requested.resolve()
    for protected in PROTECTED_PATHS:
        protected_resolved = protected.resolve()
        if resolved == protected_resolved or _is_relative_to(protected_resolved, resolved):
            raise ValueError(
                f"cleanup root would include protected path {protected_resolved}: {resolved}"
            )
    return resolved


def _iter_files(root: Path) -> Iterable[Path]:
    """Yield regular files without following symlinked directories/files."""
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = [
            name for name in dirnames
            if not (directory_path / name).is_symlink()
        ]
        for name in filenames:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def _is_staging_sqlite(path: Path) -> bool:
    return (
        path.name not in PROTECTED_SQLITE_NAMES
        and path.name.endswith(STAGING_SQLITE_SUFFIXES)
    )


def _safe_size(path: Path) -> int:
    try:
        return max(0, int(path.stat().st_size))
    except OSError:
        return 0


def _has_recent_file(root: Path, cutoff_epoch: float) -> bool:
    for path in _iter_files(root):
        try:
            if path.stat().st_mtime >= cutoff_epoch:
                return True
        except OSError:
            # A concurrent delete is not evidence that the root is safe to
            # remove recursively; keep the directory for the next sweep.
            return True
    return False


def _tree_size(root: Path) -> int:
    return sum(_safe_size(path) for path in _iter_files(root))


def _action(
    action: str,
    path: Path,
    reason: str,
    *,
    size_bytes: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "path": str(path),
        "reason": reason,
        "size_bytes": size_bytes,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _experiment_directories(root: Path) -> Iterable[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    return (
        child
        for child in children
        if child.is_dir() and not child.is_symlink()
    )


DEFAULT_SWEEP_LEDGER_PATH = Path("P:/.data/yt-is/cleanup_staging_ledger.jsonl")


def _ledger_append(ledger_path: Path | None, action: dict[str, Any]) -> str | None:
    """Best-effort durable record of what the sweep deleted.

    Without this, the only evidence a chunk root was legitimately swept
    dies with the directory itself, and the pipeline monitor must flag
    in-horizon missing roots as unexpected evidence loss (the standing
    2026-08-20 red). Returns an error string on failure, else None.
    """
    if ledger_path is None:
        return None
    import json
    from datetime import datetime, timezone

    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **action,
        }
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def cleanup_staging(
    root: Path = DEFAULT_EXPERIMENT_ROOT,
    *,
    dry_run: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    active_grace_hours: float = DEFAULT_ACTIVE_GRACE_HOURS,
    now: datetime | float | int | None = None,
    ledger_path: Path | None = DEFAULT_SWEEP_LEDGER_PATH,
) -> dict[str, Any]:
    """Prune eligible experiment staging artifacts.

    The normal sweep is intentionally conservative:

    * an experiment directory younger than ``active_grace_hours`` is skipped;
    * files changed during that grace period are never deleted;
    * directories older than ``max_age_days`` are removed only when no file in
      them is recent;
    * otherwise only old staging SQLite files are removed, preserving receipts.

    ``now`` is injectable for deterministic tests and is not exposed by the
    CLI.  The function never creates the root and treats a missing root as a
    successful no-op.
    """
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 1:
        raise ValueError("max_age_days must be an integer >= 1")
    if active_grace_hours < 0:
        raise ValueError("active_grace_hours must be >= 0")

    resolved_root = _validate_root(Path(root))
    now_epoch = _as_epoch(now)
    active_cutoff = now_epoch - (float(active_grace_hours) * 3600.0)
    old_cutoff = now_epoch - (float(max_age_days) * 86400.0)
    report: dict[str, Any] = {
        "status": "dry_run" if dry_run else "completed",
        "root": str(resolved_root),
        "dry_run": dry_run,
        "max_age_days": max_age_days,
        "active_grace_hours": active_grace_hours,
        "now_epoch": now_epoch,
        "root_exists": resolved_root.exists(),
        "experiments_scanned": 0,
        "experiments_skipped_active": 0,
        "directories_deleted": 0,
        "files_deleted": 0,
        "bytes_deleted_or_planned": 0,
        "actions": [],
        "errors": [],
    }
    if not resolved_root.exists():
        report["status"] = "root_missing"
        return report
    if not resolved_root.is_dir():
        raise ValueError(f"cleanup root is not a directory: {resolved_root}")

    actions: list[dict[str, Any]] = report["actions"]
    errors: list[str] = report["errors"]
    for experiment in _experiment_directories(resolved_root):
        report["experiments_scanned"] += 1
        try:
            experiment_mtime = experiment.stat().st_mtime
        except OSError as exc:
            errors.append(f"{experiment}:stat:{type(exc).__name__}:{exc}")
            continue

        # The directory mtime is a cheap active-run guard.  The per-file guard
        # below covers a receipt or database written without a parent mtime
        # update and is also required before recursive deletion.
        if experiment_mtime >= active_cutoff:
            report["experiments_skipped_active"] += 1
            actions.append(_action("skip_experiment", experiment, "experiment_within_active_grace"))
            continue

        if experiment_mtime < old_cutoff and not _has_recent_file(experiment, active_cutoff):
            size_bytes = _tree_size(experiment)
            actions.append(_action("delete_directory", experiment, "experiment_older_than_retention", size_bytes=size_bytes))
            report["bytes_deleted_or_planned"] += size_bytes
            if dry_run:
                report["directories_deleted"] += 1
                continue
            try:
                shutil.rmtree(experiment)
                report["directories_deleted"] += 1
                err = _ledger_append(
                    ledger_path,
                    {
                        "action": "delete_directory",
                        "path": str(experiment),
                        "reason": "experiment_older_than_retention",
                        "bytes": size_bytes,
                    },
                )
                if err:
                    errors.append(f"{experiment}:ledger:{err}")
            except OSError as exc:
                errors.append(f"{experiment}:rmtree:{type(exc).__name__}:{exc}")
                actions[-1]["action"] = "delete_directory_failed"
                actions[-1]["error"] = f"{type(exc).__name__}: {exc}"
            continue

        for path in _iter_files(experiment):
            if not _is_staging_sqlite(path):
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                errors.append(f"{path}:stat:{type(exc).__name__}:{exc}")
                continue
            if stat.st_mtime >= active_cutoff:
                actions.append(_action("skip_file", path, "file_within_active_grace", size_bytes=stat.st_size))
                continue
            size_bytes = max(0, int(stat.st_size))
            actions.append(_action("delete_file", path, "staging_sqlite_older_than_active_grace", size_bytes=size_bytes))
            report["bytes_deleted_or_planned"] += size_bytes
            if dry_run:
                report["files_deleted"] += 1
                continue
            try:
                # Re-stat immediately before unlinking so a file that became
                # active during traversal is left for the next sweep.
                if path.stat().st_mtime >= active_cutoff:
                    actions[-1]["action"] = "skip_file"
                    actions[-1]["reason"] = "file_became_active_during_sweep"
                    report["files_deleted"] -= 1
                    report["bytes_deleted_or_planned"] -= size_bytes
                    continue
                path.unlink()
                report["files_deleted"] += 1
                err = _ledger_append(
                    ledger_path,
                    {
                        "action": "delete_file",
                        "path": str(path),
                        "reason": "staging_sqlite_older_than_active_grace",
                        "bytes": size_bytes,
                    },
                )
                if err:
                    errors.append(f"{path}:ledger:{err}")
            except OSError as exc:
                errors.append(f"{path}:unlink:{type(exc).__name__}:{exc}")
                actions[-1]["action"] = "delete_file_failed"
                actions[-1]["error"] = f"{type(exc).__name__}: {exc}"

    if errors:
        report["status"] = "completed_with_errors"
    return report


def cleanup_staging_roots(
    roots: Iterable[Path],
    *,
    dry_run: bool = False,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    active_grace_hours: float = DEFAULT_ACTIVE_GRACE_HOURS,
    now: datetime | float | int | None = None,
) -> dict[str, Any]:
    """Sweep multiple experiment roots and return one aggregate report.

    The single-root ``cleanup_staging`` API remains available for callers that
    need a bounded sweep.  The CLI uses this wrapper so a default invocation
    cannot miss experiments written by the package-local direct runner.
    """
    selected_roots = tuple(Path(root) for root in roots)
    if not selected_roots:
        raise ValueError("at least one cleanup root is required")
    shared_now = now if now is not None else datetime.now(timezone.utc)
    reports = [
        cleanup_staging(
            root,
            dry_run=dry_run,
            max_age_days=max_age_days,
            active_grace_hours=active_grace_hours,
            now=shared_now,
        )
        for root in selected_roots
    ]
    count_fields = (
        "experiments_scanned",
        "experiments_skipped_active",
        "directories_deleted",
        "files_deleted",
        "bytes_deleted_or_planned",
    )
    errors = [error for report in reports for error in report["errors"]]
    actions = [action for report in reports for action in report["actions"]]
    root_statuses = [
        {
            "root": report["root"],
            "status": report["status"],
            "root_exists": report["root_exists"],
            "experiments_scanned": report["experiments_scanned"],
            "files_deleted": report["files_deleted"],
            "directories_deleted": report["directories_deleted"],
            "bytes_deleted_or_planned": report["bytes_deleted_or_planned"],
        }
        for report in reports
    ]
    all_missing = all(report["status"] == "root_missing" for report in reports)
    if errors:
        status = "completed_with_errors"
    elif all_missing:
        status = "root_missing"
    elif dry_run:
        status = "dry_run"
    else:
        status = "completed"
    aggregate: dict[str, Any] = {
        "status": status,
        "dry_run": dry_run,
        "max_age_days": max_age_days,
        "active_grace_hours": active_grace_hours,
        "now_epoch": _as_epoch(shared_now),
        "root_count": len(reports),
        "roots": [report["root"] for report in reports],
        "root_statuses": root_statuses,
        "root_exists": any(report["root_exists"] for report in reports),
        "actions": actions,
        "errors": errors,
    }
    for field in count_fields:
        aggregate[field] = sum(int(report[field]) for report in reports)
    if len(reports) == 1:
        # Preserve the useful single-root field for callers using --root once.
        aggregate["root"] = reports[0]["root"]
    return aggregate


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=None,
        metavar="PATH",
        help="Bound cleanup to PATH; repeat for multiple roots (default: both yt-is roots)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report cleanup candidates without deleting")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument(
        "--active-grace-hours",
        type=float,
        default=DEFAULT_ACTIVE_GRACE_HOURS,
        help="Protect experiments/files modified within this interval (default: 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = cleanup_staging_roots(
            args.root or DEFAULT_EXPERIMENT_ROOTS,
            dry_run=args.dry_run,
            max_age_days=args.max_age_days,
            active_grace_hours=args.active_grace_hours,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"dry_run", "completed", "root_missing"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
