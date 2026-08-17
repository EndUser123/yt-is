"""Contract tests for the ef_query CLI wrapper (scripts/ef_query.py).

Only covers argument-level validation paths that need no encoder, Qdrant
server, or corpus — the retrieval path itself is covered by the ef suite
(query_server/routing tests) and was runtime-verified live on 2026-08-17.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ef_query.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=120)


def test_help_exits_zero() -> None:
    r = _run("--help")
    assert r.returncode == 0, r.stderr
    assert "--limit" in r.stdout
    assert "--no-start-server" in r.stdout


def test_empty_query_rejected() -> None:
    r = _run("   ")
    assert r.returncode == 2
    assert "empty query" in r.stderr


def test_limit_out_of_range_rejected() -> None:
    r = _run("anything", "--limit", "0")
    assert r.returncode == 2
    assert "--limit" in r.stderr
    r2 = _run("anything", "--limit", "51")
    assert r2.returncode == 2
