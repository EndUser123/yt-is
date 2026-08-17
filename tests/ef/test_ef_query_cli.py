"""Contract tests for the ef_query CLI wrapper (scripts/ef_query.py).

Covers argument-level validation paths that need no encoder, Qdrant
server, or corpus. Coverage split for the retrieval path: routing and
fusion DECISION logic is unit-covered elsewhere in tests/ef/
(routing/test_zero_literal_contract); the semantic encode->Qdrant->RRF
control flow has a mock-based test in test_query_server_semantic_path.py;
the real encode/Qdrant/reopen stack is live-run-verified only (receipts
in docs/handoffs/yt-is-ef-query-surface-20260817).
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
