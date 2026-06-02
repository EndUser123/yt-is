"""Tests for the pytest live NotebookLM process guard."""

from __future__ import annotations

from pytest_live_process_guard import is_live_notebooklm_command


def test_guard_blocks_worker_main_launch():
    assert is_live_notebooklm_command([
        "C:\\Python314\\python.exe",
        "-m",
        "dev.worker_pool.worker_main",
        "--input",
        "batches.json",
    ])


def test_guard_blocks_forced_notebooklm_login():
    assert is_live_notebooklm_command([
        "C:\\Users\\brsth\\AppData\\Roaming\\uv\\tools\\notebooklm-mcp-cli\\Scripts\\nlm.exe",
        "login",
        "--force",
        "--profile",
        "ytis-worker-01",
    ])


def test_guard_allows_non_forced_cli_calls():
    assert not is_live_notebooklm_command([
        "C:\\Users\\brsth\\AppData\\Roaming\\uv\\tools\\notebooklm-mcp-cli\\Scripts\\nlm.exe",
        "login",
        "--check",
        "--profile",
        "ytis-worker-01",
    ])


def test_guard_allows_fake_nlm_cmd_shims():
    assert not is_live_notebooklm_command([
        "C:\\Users\\brsth\\AppData\\Local\\Temp\\pytest-of-brsth\\pytest-3391\\bin\\nlm.cmd",
        "login",
        "--force",
        "--profile",
        "ytis-free1-worker-01",
    ])
