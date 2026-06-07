"""Workspace artifact retention audit helpers.

This module reports space-heavy browser roots and sharded lane run roots that
are safe candidates for cleanup once their conclusions have been promoted into
docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


ACTIVE_BROWSER_ROOT_NAMES = {"notebooklm-pro", "notebooklm-free"}
DEFAULT_BROWSER_ROOT = Path(r"P:\.data\yt-is\browser")
DEFAULT_SHARDED_LANE_ROOT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series")
DEFAULT_DOCS_ROOTS = (
    Path(r"P:\packages\yt-is\docs\operations\test-registry.md"),
    Path(r"P:\packages\yt-is\docs\operations\hot-path-throughput-next-test-plan.md"),
)

_RUN_ROOT_PATTERN = re.compile(r"^(?:hotel|fresh_state|pro_free|sweep|optimal|free_only|combined|two_plus_two|one_plus_one|bakeoff|repro|clean|small_subbatch|verified|fallback|throughput|best|highest|fresh|pro|free|tmp|run).*$", re.IGNORECASE)


@dataclass(frozen=True)
class ArtifactAuditRow:
    path: Path
    kind: str
    status: str
    reason: str
    size_bytes: int
    last_write_time: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "size_gb": round(self.size_bytes / (1024**3), 3),
            "last_write_time": self.last_write_time,
        }


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _docs_mention_name(name: str, docs_paths: Iterable[Path]) -> bool:
    for doc_path in docs_paths:
        try:
            text = doc_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name in text:
            return True
    return False


def classify_browser_root(path: Path) -> ArtifactAuditRow:
    size_bytes = _directory_size_bytes(path)
    mtime = None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        pass
    if path.name in ACTIVE_BROWSER_ROOT_NAMES:
        return ArtifactAuditRow(path, "browser_root", "keep", "active benchmark browser root", size_bytes, mtime)
    return ArtifactAuditRow(path, "browser_root", "candidate", "stale browser root", size_bytes, mtime)


def classify_run_root(path: Path, docs_paths: Iterable[Path]) -> ArtifactAuditRow:
    size_bytes = _directory_size_bytes(path)
    mtime = None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        pass
    if path.name.endswith("_current"):
        return ArtifactAuditRow(path, "run_root", "keep", "current benchmark root", size_bytes, mtime)
    if _docs_mention_name(path.name, docs_paths):
        return ArtifactAuditRow(path, "run_root", "candidate", "completed and documented elsewhere", size_bytes, mtime)
    return ArtifactAuditRow(path, "run_root", "keep", "not yet promoted into docs", size_bytes, mtime)


def scan_browser_roots(browser_root: Path = DEFAULT_BROWSER_ROOT) -> list[ArtifactAuditRow]:
    if not browser_root.exists():
        return []
    rows = [classify_browser_root(path) for path in browser_root.iterdir() if path.is_dir()]
    return sorted(rows, key=lambda row: row.size_bytes, reverse=True)


def scan_run_roots(
    sharded_lane_root: Path = DEFAULT_SHARDED_LANE_ROOT,
    docs_paths: Iterable[Path] = DEFAULT_DOCS_ROOTS,
) -> list[ArtifactAuditRow]:
    if not sharded_lane_root.exists():
        return []
    rows: list[ArtifactAuditRow] = []
    for path in sharded_lane_root.iterdir():
        if not path.is_dir():
            continue
        if not _RUN_ROOT_PATTERN.match(path.name):
            continue
        rows.append(classify_run_root(path, docs_paths))
    return sorted(rows, key=lambda row: row.size_bytes, reverse=True)


def build_space_audit(
    *,
    browser_root: Path = DEFAULT_BROWSER_ROOT,
    sharded_lane_root: Path = DEFAULT_SHARDED_LANE_ROOT,
    docs_paths: Iterable[Path] = DEFAULT_DOCS_ROOTS,
) -> dict[str, list[dict[str, object]]]:
    return {
        "browser_roots": [row.as_dict() for row in scan_browser_roots(browser_root)],
        "run_roots": [row.as_dict() for row in scan_run_roots(sharded_lane_root, docs_paths)],
    }

