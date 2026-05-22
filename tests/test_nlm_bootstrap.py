"""Tests for NotebookLM CLI auto-bootstrap behavior."""

from __future__ import annotations

import subprocess
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


def _load_nlm_bootstrap_module():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "csf" / "nlm_bootstrap.py"
    loader = SourceFileLoader("nlm_bootstrap_test_module", str(path))
    spec = spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load nlm_bootstrap")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nlm_bootstrap = _load_nlm_bootstrap_module()


def test_ensure_latest_nlm_cli_runs_uv_upgrade_once(monkeypatch):
    calls: list[list[str]] = []
    wrapper = str(Path(__file__).resolve().parents[1] / "bin" / "csf-nlm-wrapper.cmd")

    monkeypatch.setenv("YTIS_NLM_AUTO_UPDATE", "1")
    monkeypatch.delenv("YTIS_NLM_CLI", raising=False)
    monkeypatch.setattr(nlm_bootstrap, "_bootstrap_attempted", False)
    monkeypatch.setattr(nlm_bootstrap.shutil, "which", lambda name: r"C:\Tools\uv.exe" if name == "uv" else r"C:\Users\brsth\.local\bin\nlm.exe" if name == "nlm" else None)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(nlm_bootstrap.subprocess, "run", fake_run)

    nlm_bootstrap.ensure_latest_nlm_cli()
    nlm_bootstrap.ensure_latest_nlm_cli()

    assert calls == [
        [r"C:\Tools\uv.exe", "tool", "install", "--upgrade", "notebooklm-mcp-cli"],
        [wrapper, "login", "--check"],
    ]


def test_ensure_latest_nlm_cli_skips_when_explicit_cli_override_is_set(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setenv("YTIS_NLM_AUTO_UPDATE", "1")
    monkeypatch.setenv("YTIS_NLM_CLI", r"C:\custom\nlm.exe")
    monkeypatch.setattr(nlm_bootstrap, "_bootstrap_attempted", False)
    monkeypatch.setattr(nlm_bootstrap.subprocess, "run", lambda cmd, **kwargs: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""))

    nlm_bootstrap.ensure_latest_nlm_cli()

    assert calls == []


def test_ensure_latest_nlm_cli_falls_back_when_latest_probe_breaks(monkeypatch):
    calls: list[list[str]] = []
    wrapper = str(Path(__file__).resolve().parents[1] / "bin" / "csf-nlm-wrapper.cmd")

    monkeypatch.setenv("YTIS_NLM_AUTO_UPDATE", "1")
    monkeypatch.setenv("YTIS_NLM_FALLBACK_SPEC", "notebooklm-mcp-cli @ git+https://example.invalid/notebooklm.git@deadbeef")
    monkeypatch.delenv("YTIS_NLM_CLI", raising=False)
    monkeypatch.setattr(nlm_bootstrap, "_bootstrap_attempted", False)
    monkeypatch.setattr(nlm_bootstrap.shutil, "which", lambda name: r"C:\Tools\uv.exe" if name == "uv" else r"C:\Users\brsth\.local\bin\nlm.exe" if name == "nlm" else None)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[-1] == "notebooklm-mcp-cli":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "deadbeef" in cmd[-1]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(
            cmd,
            1,
            "",
            "Failed to canonicalize script path: C:\\Users\\brsth\\.local\\bin\\nlm.exe",
        )

    monkeypatch.setattr(nlm_bootstrap.subprocess, "run", fake_run)

    nlm_bootstrap.ensure_latest_nlm_cli()

    assert calls == [
        [r"C:\Tools\uv.exe", "tool", "install", "--upgrade", "notebooklm-mcp-cli"],
        [wrapper, "login", "--check"],
        [
            r"C:\Tools\uv.exe",
            "tool",
            "install",
            "notebooklm-mcp-cli @ git+https://example.invalid/notebooklm.git@deadbeef",
        ],
    ]


def test_ensure_latest_nlm_cli_repairs_malformed_install_before_falling_back(monkeypatch):
    calls: list[list[str]] = []
    wrapper = str(Path(__file__).resolve().parents[1] / "bin" / "csf-nlm-wrapper.cmd")

    monkeypatch.setenv("YTIS_NLM_AUTO_UPDATE", "1")
    monkeypatch.delenv("YTIS_NLM_CLI", raising=False)
    monkeypatch.setattr(nlm_bootstrap, "_bootstrap_attempted", False)
    monkeypatch.setattr(nlm_bootstrap.shutil, "which", lambda name: r"C:\Tools\uv.exe" if name == "uv" else r"C:\Users\brsth\.local\bin\nlm.exe" if name == "nlm" else None)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:5] == [r"C:\Tools\uv.exe", "tool", "install", "--upgrade", "--force"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:4] == [r"C:\Tools\uv.exe", "tool", "install", "--upgrade"]:
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "× Failed to read `more-itertools==11.0.2`\n"
                "├─▶ Failed to read metadata from installed package `more-itertools==11.0.2`",
            )
        if cmd[:4] == [r"C:\Tools\uv.exe", "tool", "uninstall", "notebooklm-mcp-cli"]:
            return subprocess.CompletedProcess(cmd, 0, "Removed dangling environment for `notebooklm-mcp-cli`", "")
        if cmd == [wrapper, "login", "--check"]:
            return subprocess.CompletedProcess(cmd, 0, "Account: expected@example.com", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(nlm_bootstrap.subprocess, "run", fake_run)

    nlm_bootstrap.ensure_latest_nlm_cli()

    assert calls == [
        [r"C:\Tools\uv.exe", "tool", "install", "--upgrade", "notebooklm-mcp-cli"],
        [r"C:\Tools\uv.exe", "tool", "uninstall", "notebooklm-mcp-cli"],
        [r"C:\Tools\uv.exe", "tool", "install", "--upgrade", "--force", "notebooklm-mcp-cli"],
        [wrapper, "login", "--check"],
    ]
