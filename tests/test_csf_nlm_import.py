"""Tests for NotebookLM transcript importer routing."""

from __future__ import annotations

import subprocess

from csf import csf_nlm_import


def test_run_nlm_query_pins_active_profile(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, '{"ok": true}', "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setattr(csf_nlm_import.nlm_auth_guard, "run_nlm", fake_run)

    result = csf_nlm_import.run_nlm_query("nb-1", "prompt text")

    assert result == {"ok": True}
    assert calls == [["notebook", "query", "nb-1", "prompt text", "--json", "--profile", "worker-01"]]


def test_check_auth_uses_login_check_and_active_profile(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "Account: worker@example.com\n", "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setattr(csf_nlm_import.nlm_auth_guard, "run_nlm", fake_run)

    assert csf_nlm_import.check_auth() is True
    assert calls == [["login", "--check", "--profile", "worker-01"]]


def test_ensure_auth_reauthenticates_with_profile(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["login", "--check"]:
            return subprocess.CompletedProcess(cmd, 1, "", "expired")
        return subprocess.CompletedProcess(cmd, 0, "Account: worker@example.com\n", "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setattr(csf_nlm_import.nlm_auth_guard, "run_nlm", fake_run)

    csf_nlm_import.ensure_auth()

    assert calls == [
        ["login", "--check", "--profile", "worker-01"],
        ["login", "--force", "--profile", "worker-01"],
    ]


def test_check_auth_rejects_mismatched_expected_account(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "Account: wrong@example.com\n", "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker@example.com")
    monkeypatch.setattr(csf_nlm_import.nlm_auth_guard, "run_nlm", fake_run)

    assert csf_nlm_import.check_auth() is False
    assert calls == [["login", "--check", "--profile", "worker-01"]]


def test_ensure_auth_rejects_mismatched_expected_account(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["login", "--check"]:
            return subprocess.CompletedProcess(cmd, 1, "", "expired")
        return subprocess.CompletedProcess(cmd, 0, "Account: wrong@example.com\n", "")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
    monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker@example.com")
    monkeypatch.setattr(csf_nlm_import.nlm_auth_guard, "run_nlm", fake_run)

    try:
        csf_nlm_import.ensure_auth()
    except RuntimeError as exc:
        assert "expected worker@example.com" in str(exc)
    else:
        raise AssertionError("ensure_auth should reject mismatched accounts")

    assert calls == [
        ["login", "--check", "--profile", "worker-01"],
        ["login", "--force", "--profile", "worker-01"],
    ]
