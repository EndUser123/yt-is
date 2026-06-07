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


def test_browser_health_gate_marks_recovered_clean_after_owned_profile_cleanup(monkeypatch):
    reaped: list[set[int]] = []
    monkeypatch.setattr(nlm_auth_guard, "chrome_pids_for_root", lambda root: {12345})
    def fake_stop(pids):
        reaped.append(set(pids))
        return set(pids)
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", fake_stop)
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


def test_stop_chrome_pids_refuses_unowned_default_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nlm_auth_guard,
        "_collect_chrome_process_records",
        lambda: [
            {
                "pid": 12345,
                "cmdline": r"chrome.exe --user-data-dir=C:\Users\brsth\.notebooklm-mcp-cli\chrome-profile",
                "rss_bytes": 100,
            }
        ],
    )
    monkeypatch.setattr(nlm_auth_guard.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    stopped = nlm_auth_guard.stop_chrome_pids({12345})

    assert stopped == set()
    assert calls == []


def test_stop_chrome_pids_allows_default_notebooklm_session(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nlm_auth_guard,
        "_collect_chrome_process_records",
        lambda: [
            {
                "pid": 12345,
                "cmdline": (
                    r"chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\Users\brsth\.notebooklm-mcp-cli\chrome-profile "
                    r"https://notebooklm.google.com"
                ),
                "rss_bytes": 100,
            }
        ],
    )
    monkeypatch.setattr(
        nlm_auth_guard.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    stopped = nlm_auth_guard.stop_chrome_pids({12345})

    assert stopped == {12345}
    assert calls


def test_stop_chrome_pids_allows_ytis_browser_root(monkeypatch):
    calls = []
    monkeypatch.setattr(
        nlm_auth_guard,
        "_collect_chrome_process_records",
        lambda: [
            {
                "pid": 12345,
                "cmdline": r"chrome.exe --user-data-dir=P:\\.data\yt-is\browser\notebooklm-pro",
                "rss_bytes": 100,
            }
        ],
    )
    monkeypatch.setattr(
        nlm_auth_guard.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    stopped = nlm_auth_guard.stop_chrome_pids({12345})

    assert stopped == {12345}
    assert calls


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


def test_default_chrome_profile_pids_uses_short_cache_ttl(monkeypatch):
    calls: list[Path] = []
    times = iter([100.0, 100.2, 100.8])

    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
    monkeypatch.setenv("YTIS_NLM_CHROME_PID_CACHE_TTL_S", "0.5")
    monkeypatch.setattr(nlm_auth_guard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        nlm_auth_guard,
        "chrome_pids_for_root",
        lambda root: calls.append(Path(root)) or {111},
    )
    monkeypatch.setattr(nlm_auth_guard, "_DEFAULT_CHROME_PROFILE_PIDS_CACHE", None)

    first = nlm_auth_guard.default_chrome_profile_pids()
    second = nlm_auth_guard.default_chrome_profile_pids()
    third = nlm_auth_guard.default_chrome_profile_pids()

    assert first == {111}
    assert second == {111}
    assert third == {111}
    assert calls == [nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT, nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT]


def test_default_chrome_profile_pids_can_disable_cache(monkeypatch):
    calls: list[Path] = []
    times = iter([200.0, 200.1, 200.2])

    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
    monkeypatch.setenv("YTIS_NLM_CHROME_PID_CACHE_TTL_S", "0")
    monkeypatch.setattr(nlm_auth_guard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        nlm_auth_guard,
        "chrome_pids_for_root",
        lambda root: calls.append(Path(root)) or {222},
    )
    monkeypatch.setattr(nlm_auth_guard, "_DEFAULT_CHROME_PROFILE_PIDS_CACHE", None)

    assert nlm_auth_guard.default_chrome_profile_pids() == {222}
    assert nlm_auth_guard.default_chrome_profile_pids() == {222}

    assert calls == [nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT, nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT]


def test_reap_default_chrome_profile_clears_cached_pids(monkeypatch):
    calls: list[Path] = []
    times = iter([300.0, 300.1, 300.2])

    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
    monkeypatch.setenv("YTIS_NLM_CHROME_PID_CACHE_TTL_S", "30")
    monkeypatch.setattr(nlm_auth_guard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        nlm_auth_guard,
        "chrome_pids_for_root",
        lambda root: calls.append(Path(root)) or {333},
    )
    monkeypatch.setattr(nlm_auth_guard, "stop_chrome_pids", lambda pids: set(pids))
    monkeypatch.setattr(nlm_auth_guard, "_DEFAULT_CHROME_PROFILE_PIDS_CACHE", None)

    assert nlm_auth_guard.default_chrome_profile_pids() == {333}
    assert nlm_auth_guard.reap_default_chrome_profile() == {333}
    assert nlm_auth_guard.default_chrome_profile_pids() == {333}

    assert calls == [
        nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT,
        nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT,
        nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT,
    ]


def test_default_chrome_profile_pids_refreshes_cached_dead_pids(monkeypatch):
    calls: list[Path] = []
    times = iter([400.0, 400.1, 400.2])

    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
    monkeypatch.setenv("YTIS_NLM_CHROME_PID_CACHE_TTL_S", "30")
    monkeypatch.setattr(nlm_auth_guard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        nlm_auth_guard,
        "chrome_pids_for_root",
        lambda root: calls.append(Path(root)) or {444},
    )
    monkeypatch.setattr(
        nlm_auth_guard.psutil,
        "pid_exists",
        lambda pid: False if pid == 444 else True,
    )
    monkeypatch.setattr(nlm_auth_guard, "_DEFAULT_CHROME_PROFILE_PIDS_CACHE", None)

    assert nlm_auth_guard.default_chrome_profile_pids() == {444}
    assert nlm_auth_guard.default_chrome_profile_pids() == {444}

    assert calls == [nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT, nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT]


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


def test_auth_check_cache_store_defaults_session_established_at_when_omitted(monkeypatch):
    """A cache store without an explicit session timestamp should still retain age evidence."""
    times = iter([100.0, 103.5, 103.5])
    monkeypatch.setattr(nlm_auth_guard.time, "monotonic", lambda: next(times))

    context = nlm_auth_guard.NLMAuthContext(
        profile="worker-03",
        login_profile_args=["--profile", "worker-03"],
        requires_profile=True,
        expected_email="worker03@example.com",
    )

    nlm_auth_guard.auth_check_cache_store(context)

    assert nlm_auth_guard.auth_check_cache_hit(context) == (True, 100.0)
    assert nlm_auth_guard.auth_check_cache_session_age(context) == 3.5
