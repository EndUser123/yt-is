"""Tests for run_podcast_sync transcription isolation (PyAV abort containment)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_podcast_sync import main, transcribe  # noqa: E402


def test_transcribe_one_child_missing_file_exits_4(tmp_path):
    """Child entry must degrade gracefully (exit 4) on undecodable input,
    never propagate a native abort past its own process boundary."""
    rc = main(["--transcribe-one", str(tmp_path / "nope.m4a")])
    assert rc == 4


def test_parent_transcribe_returns_none_on_child_failure(tmp_path, monkeypatch):
    """Parent converts nonzero child exit into None (loop's failure path)."""
    proc_result = type("P", (), {"returncode": 3221226505,
                                 "stdout": "", "stderr": "aborted"})()
    import scripts.run_podcast_sync as mod

    def fake_run(*a, **k):
        return proc_result

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert transcribe(tmp_path / "x.m4a") is None
