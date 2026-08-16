from __future__ import annotations

from types import SimpleNamespace

from csf import transcript


def test_account_aware_transcript_auth_uses_canonical_session(monkeypatch):
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "troup.hominidae")
    monkeypatch.setenv("YTIS_NLM_WORKER_ID", "troup.hominidae-worker-01")
    calls = []

    def fake_ensure(profile, **kwargs):
        calls.append((profile, kwargs))
        return SimpleNamespace(
            account_profile=profile,
            expected_email="troup.hominidae@gmail.com",
            storage_path="P:/.data/yt-is/nlm-auth/storage_state_troup_hominidae.json",
            ok=True,
            reason="ok",
        )

    monkeypatch.setattr("csf.nlm_client.ensure_account_session", fake_ensure)

    def legacy_login_must_not_run(*args, **kwargs):
        raise AssertionError("account-aware transcript auth reopened legacy nlm login")

    monkeypatch.setattr(transcript.nlm_auth_guard, "run_nlm", legacy_login_must_not_run)
    assert transcript._ensure_nlm_auth() is True
    assert calls == [
        (
            "troup.hominidae",
            {"worker_id": "troup.hominidae-worker-01", "allow_bootstrap": False},
        )
    ]


def test_account_aware_transcript_auth_fails_closed_without_legacy_login(monkeypatch):
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "brsthomson")

    def fake_ensure(profile, **kwargs):
        return SimpleNamespace(
            account_profile=profile,
            expected_email="brsthomson@hotmail.com",
            storage_path="P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json",
            ok=False,
            reason="session_probe_failed:expired",
        )

    monkeypatch.setattr("csf.nlm_client.ensure_account_session", fake_ensure)
    monkeypatch.setattr(
        transcript.nlm_auth_guard,
        "run_nlm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy login must not run on canonical failure")
        ),
    )
    assert transcript._ensure_nlm_auth() is False
