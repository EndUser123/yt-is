"""Tests for shared NotebookLM auth command routing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from csf import nlm_auth_guard


@pytest.mark.parametrize(
    "args, expected",
    [
        (["source", "list", "nb-1"], ["source", "list", "nb-1", "--profile", "worker-01"]),
        (["notebook", "query", "nb-1", "prompt"], ["notebook", "query", "nb-1", "prompt", "--profile", "worker-01"]),
        (["audio", "create", "nb-1", "--confirm"], ["audio", "create", "nb-1", "--confirm", "--profile", "worker-01"]),
    ],
)
def test_add_profile_args_pins_non_login_commands(monkeypatch, args, expected):
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")

    assert nlm_auth_guard.add_profile_args(args) == expected


def test_add_profile_args_leaves_login_commands_unpinned(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")

    assert nlm_auth_guard.add_profile_args(["login", "profile", "list"]) == ["login", "profile", "list"]
    assert nlm_auth_guard.add_profile_args(["login", "switch"]) == ["login", "switch"]


def test_add_profile_args_pins_login_auth_commands(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")

    assert nlm_auth_guard.add_profile_args(["login", "--check"]) == ["login", "--check", "--profile", "worker-01"]
    assert nlm_auth_guard.add_profile_args(["login", "--force"]) == ["login", "--force", "--profile", "worker-01"]


def _browser_health_sample(
    *,
    default_pids=None,
    unexpected=None,
    unexpected_rss_bytes_total=0,
    chrome_process_count=0,
    chrome_rss_bytes_total=0,
):
    return {
        "allowed_browser_roots": [r"P:\\\\\\.data\yt-is\browser\notebooklm-pro"],
        "allowed_profile_pid_count": 0,
        "allowed_profile_pid_counts_by_root": {r"P:\\\\\\.data\yt-is\browser\notebooklm-pro": 0},
        "chrome_process_count": chrome_process_count,
        "chrome_rss_bytes_total": chrome_rss_bytes_total,
        "default_profile_pids": list(default_pids or []),
        "unexpected_processes": list(unexpected or []),
        "unexpected_process_rss_bytes_total": unexpected_rss_bytes_total,
    }


def _escape_backslashes(path: str, count: int) -> str:
    return path.replace("\\", "\\" * count)


@pytest.mark.parametrize("slash_count", [2, 3, 4, 5])
def test_normalize_cmdline_path_collapses_escaped_user_data_dir_backslashes(slash_count):
    root = r"P:\\\.data\yt-is\browser\notebooklm-pro"
    escaped_root = _escape_backslashes(root, slash_count)
    cmdline = f"chrome.exe --type=renderer --user-data-dir={escaped_root} --lang=en-US"

    assert nlm_auth_guard._normalize_path_for_matching(root) in nlm_auth_guard._normalize_cmdline_path(cmdline)


def test_sample_browser_health_counts_escaped_allowed_profile_subprocess(monkeypatch):
    root = r"P:\\\.data\yt-is\browser\notebooklm-pro"
    escaped_root = _escape_backslashes(root, 4)
    monkeypatch.setattr(
        nlm_auth_guard,
        "_collect_chrome_process_records",
        lambda: [
            {
                "pid": 111,
                "cmdline": f"chrome.exe --type=renderer --user-data-dir={escaped_root} --lang=en-US",
                "rss_bytes": 100,
            }
        ],
    )

    report = nlm_auth_guard._sample_browser_health([Path(root)])

    assert report["allowed_profile_pid_count"] == 1
    assert report["allowed_profile_pid_counts_by_root"][str(Path(root))] == 1
    assert report["default_profile_pids"] == []
    assert report["unexpected_processes"] == []


def test_sample_browser_health_counts_escaped_default_profile_subprocess(monkeypatch):
    allowed_root = r"P:\\\.data\yt-is\browser\notebooklm-pro"
    default_root = str(nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT)
    escaped_default_root = _escape_backslashes(default_root, 4)
    monkeypatch.setattr(
        nlm_auth_guard,
        "_collect_chrome_process_records",
        lambda: [
            {
                "pid": 222,
                "cmdline": f"chrome.exe --type=utility --user-data-dir={escaped_default_root} --lang=en-US",
                "rss_bytes": 100,
            }
        ],
    )

    report = nlm_auth_guard._sample_browser_health([Path(allowed_root)])

    assert report["allowed_profile_pid_count"] == 0
    assert report["default_profile_pids"] == [222]
    assert report["unexpected_processes"] == []


def test_browser_health_gate_passes_when_environment_is_clean(monkeypatch):
    monkeypatch.setattr(nlm_auth_guard, "chrome_pids_for_root", lambda root: set())
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", lambda pids: None)
    monkeypatch.setattr(
        nlm_auth_guard,
        "_sample_browser_health",
        lambda allowed_roots: _browser_health_sample(chrome_process_count=2, chrome_rss_bytes_total=1234),
    )

    report = nlm_auth_guard.browser_health_gate(
        [Path(r"P:\\\\\\.data\yt-is\browser\notebooklm-pro")],
        settle_window_s=0.0,
        sample_interval_s=0.0,
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )

    assert report["status"] == "clean"
    assert report["initial_default_profile_detected_count"] == 0
    assert report["default_profile_remaining_count"] == 0
    assert report["unexpected_process_count"] == 0
    assert report["sample_count"] == 2


def test_browser_health_gate_marks_recovered_clean_after_default_profile_cleanup(monkeypatch):
    reaped: list[set[int]] = []
    monkeypatch.setattr(nlm_auth_guard, "chrome_pids_for_root", lambda root: {12345})
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", lambda pids: reaped.append(set(pids)))
    monkeypatch.setattr(
        nlm_auth_guard,
        "_sample_browser_health",
        lambda allowed_roots: _browser_health_sample(chrome_process_count=1, chrome_rss_bytes_total=256),
    )

    report = nlm_auth_guard.browser_health_gate(
        [Path(r"P:\\\\\\.data\yt-is\browser\notebooklm-pro")],
        settle_window_s=0.0,
        sample_interval_s=0.0,
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )

    assert report["status"] == "recovered_clean"
    assert report["initial_default_profile_detected_count"] == 1
    assert report["default_profile_reaped_count"] == 1
    assert report["default_profile_remaining_count"] == 0
    assert reaped == [{12345}]


def test_browser_health_gate_keeps_unrelated_chrome_soft_when_under_budget(monkeypatch):
    monkeypatch.setattr(nlm_auth_guard, "chrome_pids_for_root", lambda root: set())
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", lambda pids: None)
    monkeypatch.setattr(
        nlm_auth_guard,
        "_sample_browser_health",
        lambda allowed_roots: _browser_health_sample(
            unexpected=[{"pid": 222, "cmdline": r"chrome.exe --user-data-dir=C:\Users\brsth\AppData"}],
            unexpected_rss_bytes_total=512,
            chrome_process_count=1,
            chrome_rss_bytes_total=512,
        ),
    )

    report = nlm_auth_guard.browser_health_gate(
        [Path(r"P:\\\\\\.data\yt-is\browser\notebooklm-pro")],
        settle_window_s=0.0,
        sample_interval_s=0.0,
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )

    assert report["status"] == "clean"
    assert report["unexpected_process_count"] == 1
    assert report["warnings"] == []
    assert not report["issues"]


def test_browser_health_gate_marks_unrelated_chrome_degraded_when_over_budget(monkeypatch):
    monkeypatch.setattr(nlm_auth_guard, "chrome_pids_for_root", lambda root: set())
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", lambda pids: None)
    monkeypatch.setattr(
        nlm_auth_guard,
        "_sample_browser_health",
        lambda allowed_roots: _browser_health_sample(
            unexpected=[{"pid": 222, "cmdline": r"chrome.exe --user-data-dir=C:\Users\brsth\AppData"}],
            unexpected_rss_bytes_total=7_000_000_000,
            chrome_process_count=1,
            chrome_rss_bytes_total=7_000_000_000,
        ),
    )

    report = nlm_auth_guard.browser_health_gate(
        [Path(r"P:\\\\\\.data\yt-is\browser\notebooklm-pro")],
        settle_window_s=0.0,
        sample_interval_s=0.0,
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )

    assert report["status"] == "degraded"
    assert report["unexpected_process_count"] == 1
    assert report["warnings"]
    assert not report["issues"]


def test_run_nlm_pins_profile_automatically(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setattr(nlm_auth_guard, "ensure_latest_nlm_cli", lambda: None)
    monkeypatch.setattr(nlm_auth_guard.subprocess, "run", fake_run)

    result = nlm_auth_guard.run_nlm(["notebook", "list"], timeout_s=1)

    assert result.returncode == 0
    assert calls == [[nlm_auth_guard.get_nlm_executable(), "notebook", "list", "--profile", "worker-01"]]


def test_run_nlm_fails_closed_without_profile(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
    monkeypatch.setattr(nlm_auth_guard, "ensure_latest_nlm_cli", lambda: None)
    monkeypatch.setattr(nlm_auth_guard.subprocess, "run", fake_run)

    result = nlm_auth_guard.run_nlm(["notebook", "list"], timeout_s=1)

    assert result.returncode == 1
    assert "profile is required" in result.stderr.lower()
    assert calls == []


def test_run_nlm_uses_profile_from_env_override(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
    monkeypatch.setattr(nlm_auth_guard, "ensure_latest_nlm_cli", lambda: None)
    monkeypatch.setattr(nlm_auth_guard.subprocess, "run", fake_run)

    result = nlm_auth_guard.run_nlm(["notebook", "list"], timeout_s=1, env={"NOTEBOOKLM_PROFILE": "worker-02"})

    assert result.returncode == 0
    assert calls == [[nlm_auth_guard.get_nlm_executable(), "notebook", "list", "--profile", "worker-02"]]
