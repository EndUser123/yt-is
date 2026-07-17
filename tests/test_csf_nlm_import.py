"""Tests for NotebookLM transcript importer routing."""

from __future__ import annotations

import subprocess

from csf import csf_nlm_import


# --- C2 trust-floor: real-id resolution + refusal ---


def test_extract_real_video_id_from_title_finds_real_id():
    """End-of-title YouTube ID should be recovered."""
    assert (
        csf_nlm_import._extract_real_video_id_from_title(
            "Some Video Title (dQw4w9WgXcQ)"
        )
        == "dQw4w9WgXcQ"
    )
    assert (
        csf_nlm_import._extract_real_video_id_from_title(
            "Talk - aB3cDeF-GhI extra"
        )
        == "aB3cDeF-GhI"
    )


def test_extract_real_video_id_from_title_rejects_md5_hex():
    """All-uppercase-hex 11-char strings are likely MD5(source_id); refuse."""
    md5_like = "0123456789A"  # 11 chars, all hex, all uppercase
    assert csf_nlm_import._extract_real_video_id_from_title(f"T ({md5_like})") is None


def test_extract_real_video_id_from_title_empty_returns_none():
    assert csf_nlm_import._extract_real_video_id_from_title("") is None
    assert csf_nlm_import._extract_real_video_id_from_title("no id here") is None


def test_import_notebook_transcripts_refuses_unbound(monkeypatch, capsys):
    """Videos whose title has no real YouTube ID must be counted as refused
    and MUST NOT trigger a synthetic cache write."""
    from csf import cache

    calls: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(csf_nlm_import, "ensure_auth", lambda: None)

    def fake_get_video_list(_nb):
        return [
            {"source_id": "nlm-src-1", "title": "Talk With No ID"},
            {"source_id": "nlm-src-2", "title": "Real Title (dQw4w9WgXcQ)"},
        ]

    monkeypatch.setattr(
        csf_nlm_import, "get_video_list", fake_get_video_list
    )
    monkeypatch.setattr(
        csf_nlm_import, "extract_transcript", lambda *a, **kw: "transcript text"
    )

    def fake_set(video_id, lang, source, transcript, **kwargs):
        calls.append((video_id, kwargs.get("metadata")))

    monkeypatch.setattr(csf_nlm_import, "set_cached_transcript", fake_set)
    monkeypatch.setattr(csf_nlm_import, "has_cached_transcript", lambda *a, **kw: False)

    stats = csf_nlm_import.import_notebook_transcripts(
        "Test Notebook", "nb-test", dry_run=False
    )
    out = capsys.readouterr().out

    assert stats["total"] == 2
    assert stats["refused"] == 1
    # Only the bound video gets to set_cached_transcript.
    written_ids = [v for v, _ in calls]
    assert written_ids == ["dQw4w9WgXcQ"]
    assert "REFUSED" in out


def test_set_cached_transcript_refuses_unbound_write(monkeypatch, tmp_path):
    """bind_verified=False must NOT write to the shared transcript cache."""
    from csf import cache

    # Force a tmp cache DB so this test doesn't pollute the real one.
    monkeypatch.setenv(
        "YTIS_TRANSCRIPT_CACHE_DB_PATH", str(tmp_path / "cache.sqlite")
    )
    # Clear any cached storage handle so the new env path is honored.
    cache.clear_all_storages()
    try:
        cache.set_cached_transcript(
            "ABCDEFGHIJK",  # valid 11-char shape but unbound
            "en",
            "notebooklm",
            "transcript text",
            bind_verified=False,
        )
        # has_cached should be False because the write was refused.
        assert cache.has_cached_transcript("ABCDEFGHIJK") is False
    finally:
        cache.clear_all_storages()


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
