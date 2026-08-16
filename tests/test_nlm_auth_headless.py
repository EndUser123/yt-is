from __future__ import annotations

from types import SimpleNamespace

import pytest

from csf import nlm_auth_headless
from csf.nlm_client import AccountSessionProbe


def _probe(ok: bool, reason: str = "expired") -> AccountSessionProbe:
    return AccountSessionProbe(
        account_profile="a.hominidae",
        worker_id="test",
        expected_email="a.hominidae@gmail.com",
        storage_path="P:/storage_state.json",
        ok=ok,
        reason="ok" if ok else reason,
        observed_email="a.hominidae@gmail.com",
    )


def test_master_token_path_is_account_scoped_and_rejects_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(nlm_auth_headless, "master_token_root", lambda: tmp_path)

    assert nlm_auth_headless.master_token_path_for_account("a.hominidae") == (
        tmp_path / "a.hominidae.json"
    )
    with pytest.raises(ValueError):
        nlm_auth_headless.master_token_path_for_account("P:/other-account")


def test_validate_cdp_url_requires_loopback_with_explicit_port():
    assert nlm_auth_headless._validate_cdp_url("http://127.0.0.1:9222") == (
        "http://127.0.0.1:9222"
    )
    assert nlm_auth_headless._validate_cdp_url("ws://[::1]:9222") == (
        "ws://[::1]:9222"
    )

    for value in (
        "http://example.test:9222",
        "http://127.0.0.1",
        "http://user:secret@127.0.0.1:9222",
    ):
        with pytest.raises(nlm_auth_headless.HeadlessAuthError):
            nlm_auth_headless._validate_cdp_url(value)


def test_verify_cdp_account_requires_exact_single_account(monkeypatch):
    monkeypatch.setattr(
        nlm_auth_headless,
        "_enumerate_cdp_account_emails",
        lambda **kwargs: ("other@example.com",),
    )

    with pytest.raises(nlm_auth_headless.HeadlessAuthError, match="CDP account mismatch"):
        nlm_auth_headless._verify_cdp_account(
            cdp_url="http://127.0.0.1:9222",
            expected_email="a.hominidae@gmail.com",
            timeout_s=1,
        )

    monkeypatch.setattr(
        nlm_auth_headless,
        "_enumerate_cdp_account_emails",
        lambda **kwargs: ("a.hominidae@gmail.com", "other@example.com"),
    )
    with pytest.raises(nlm_auth_headless.HeadlessAuthError, match="ambiguity"):
        nlm_auth_headless._verify_cdp_account(
            cdp_url="http://127.0.0.1:9222",
            expected_email="a.hominidae@gmail.com",
            timeout_s=1,
        )


def test_verify_cdp_account_waits_for_empty_context_until_exact_account(monkeypatch):
    observed = iter([(), ("a.hominidae@gmail.com",)])
    timeouts = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "_enumerate_cdp_account_emails",
        lambda **kwargs: timeouts.append(kwargs["timeout_s"]) or next(observed),
    )
    monkeypatch.setattr(nlm_auth_headless.time, "sleep", lambda seconds: None)

    nlm_auth_headless._verify_cdp_account(
        cdp_url="http://127.0.0.1:18870",
        expected_email="a.hominidae@gmail.com",
        timeout_s=5,
    )

    assert len(timeouts) == 2
    assert all(1.0 <= timeout <= 5.0 for timeout in timeouts)


def test_interactive_verify_treats_fresh_unsigned_context_as_pending(monkeypatch):
    observed = iter(
        [
            nlm_auth_headless.HeadlessAuthError("authentication expired or invalid"),
            ("brsthomson@hotmail.com",),
        ]
    )

    def next_account_observation(**kwargs):
        value = next(observed)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        nlm_auth_headless,
        "_enumerate_cdp_account_emails",
        next_account_observation,
    )
    monkeypatch.setattr(nlm_auth_headless.time, "sleep", lambda seconds: None)

    nlm_auth_headless._verify_cdp_account(
        cdp_url="http://127.0.0.1:18872",
        expected_email="brsthomson@hotmail.com",
        timeout_s=5,
        allow_pending_sign_in=True,
    )


def test_ensure_account_session_rejects_remote_cdp_before_probe(monkeypatch):
    probed = []
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(
        nlm_client,
        "probe_account_session",
        lambda profile, *, worker_id: probed.append(profile),
    )

    with pytest.raises(nlm_auth_headless.HeadlessAuthError, match="non-loopback"):
        nlm_auth_headless.ensure_account_session(
            "a.hominidae",
            cdp_url="http://example.test:9222",
        )
    assert probed == []


def test_refresh_account_from_master_token_mints_into_canonical_storage(monkeypatch, tmp_path):
    storage = tmp_path / "storage_state.json"
    token_path = tmp_path / "a.hominidae.json"
    monkeypatch.setattr(
        nlm_auth_headless,
        "storage_path_for_account_profile",
        lambda profile: storage,
    )
    monkeypatch.setattr(nlm_auth_headless, "master_token_path_for_account", lambda profile: token_path)
    monkeypatch.setattr(
        nlm_auth_headless,
        "_validated_master_record",
        lambda profile, path: {
            "email": "a.hominidae@gmail.com",
            "master_token": "opaque-token",
            "android_id": "android-id",
        },
    )
    minted = []

    async def fake_mint(email, token, android_id):
        minted.append((email, token, android_id))
        return "jar"

    persisted = []
    monkeypatch.setattr(nlm_auth_headless, "_mint_cookies", fake_mint)
    monkeypatch.setattr(
        nlm_auth_headless,
        "_persist_cookies",
        lambda path, jar, *, email: persisted.append((path, jar, email)),
    )

    assert nlm_auth_headless.refresh_account_from_master_token("a.hominidae") == storage
    assert minted == [("a.hominidae@gmail.com", "opaque-token", "android-id")]
    assert persisted == [(storage, "jar", "a.hominidae@gmail.com")]


def test_master_record_rejects_cross_account_token(monkeypatch, tmp_path):
    monkeypatch.setattr(
        nlm_auth_headless,
        "_read_master_record",
        lambda path: {
            "email": "troup.hominidae@gmail.com",
            "master_token": "opaque-token",
            "android_id": "android-id",
        },
    )
    with pytest.raises(nlm_auth_headless.HeadlessAuthError, match="account mismatch"):
        nlm_auth_headless._validated_master_record("a.hominidae", tmp_path / "token.json")


def test_ensure_account_session_refreshes_without_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(nlm_auth_headless, "master_token_root", lambda: tmp_path)
    monkeypatch.setattr(
        nlm_auth_headless,
        "inspect_account_storage",
        lambda profile: SimpleNamespace(observed_email="a.hominidae@gmail.com"),
    )
    states = iter([_probe(False), _probe(False), _probe(True)])
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(nlm_client, "probe_account_session", lambda profile, *, worker_id: next(states))
    refreshed = []
    bootstrapped = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "refresh_account_from_master_token",
        lambda profile: refreshed.append(profile),
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "bootstrap_account_from_headless_cdp",
        lambda profile, *, timeout_s: bootstrapped.append(profile),
    )

    result = nlm_auth_headless.ensure_account_session("a.hominidae", timeout_s=1)

    assert result.ok is True
    assert refreshed == ["a.hominidae"]
    assert bootstrapped == []


def test_active_token_only_repair_never_bootstraps_after_repair_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(nlm_auth_headless, "master_token_root", lambda: tmp_path)
    monkeypatch.setattr(
        nlm_auth_headless,
        "inspect_account_storage",
        lambda profile: SimpleNamespace(observed_email="a.hominidae@gmail.com"),
    )
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(nlm_client, "probe_account_session", lambda profile, *, worker_id: _probe(False))
    monkeypatch.setattr(
        nlm_auth_headless,
        "refresh_account_from_master_token",
        lambda profile: (_ for _ in ()).throw(nlm_auth_headless.HeadlessAuthError("no token")),
    )
    bootstrapped = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "bootstrap_account_from_headless_cdp",
        lambda profile, **kwargs: bootstrapped.append((profile, kwargs)),
    )

    result = nlm_auth_headless.ensure_account_session(
        "a.hominidae",
        worker_id="worker-01",
        allow_bootstrap=False,
    )

    assert result.ok is False
    assert "noninteractive_repair_failed" in result.reason
    assert bootstrapped == []


def test_ensure_account_session_restores_missing_storage_before_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(nlm_auth_headless, "master_token_root", lambda: tmp_path)
    monkeypatch.setattr(
        nlm_auth_headless,
        "inspect_account_storage",
        lambda profile: SimpleNamespace(observed_email="", reason="storage_missing"),
    )
    states = iter([_probe(False), _probe(False), _probe(True)])
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(
        nlm_client,
        "probe_account_session",
        lambda profile, *, worker_id: next(states),
    )
    restored = []
    refreshed = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "restore_account_from_backup",
        lambda profile: restored.append(profile) or True,
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "refresh_account_from_master_token",
        lambda profile: refreshed.append(profile),
    )

    result = nlm_auth_headless.ensure_account_session("a.hominidae", timeout_s=1)

    assert result.ok is True
    assert restored == ["a.hominidae"]
    assert refreshed == []


def test_ensure_account_session_refuses_wrong_static_identity(monkeypatch):
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(nlm_client, "probe_account_session", lambda profile, *, worker_id: _probe(False))
    monkeypatch.setattr(
        nlm_auth_headless,
        "inspect_account_storage",
        lambda profile: SimpleNamespace(observed_email="troup.hominidae@gmail.com"),
    )
    called = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "refresh_account_from_master_token",
        lambda profile: called.append(profile),
    )

    result = nlm_auth_headless.ensure_account_session("a.hominidae")

    assert result.ok is False
    assert "account_email_mismatch" in result.reason
    assert called == []


def test_ensure_account_session_threads_explicit_cdp_only_to_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(nlm_auth_headless, "master_token_root", lambda: tmp_path)
    monkeypatch.setattr(
        nlm_auth_headless,
        "inspect_account_storage",
        lambda profile: SimpleNamespace(observed_email="a.hominidae@gmail.com"),
    )
    states = iter([_probe(False), _probe(False), _probe(True)])
    import csf.nlm_client as nlm_client

    monkeypatch.setattr(
        nlm_client,
        "probe_account_session",
        lambda profile, *, worker_id: next(states),
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "refresh_account_from_master_token",
        lambda profile: (_ for _ in ()).throw(
            nlm_auth_headless.HeadlessAuthError("no token")
        ),
    )
    bootstrapped = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "bootstrap_account_from_headless_cdp",
        lambda profile, **kwargs: bootstrapped.append((profile, kwargs)),
    )

    result = nlm_auth_headless.ensure_account_session(
        "a.hominidae",
        timeout_s=1,
        cdp_url="http://127.0.0.1:9222",
    )

    assert result.ok is True
    assert bootstrapped == [
        ("a.hominidae", {"timeout_s": 1, "cdp_url": "http://127.0.0.1:9222"})
    ]


def test_bootstrap_uses_established_headless_family_and_restores_environment(monkeypatch, tmp_path):
    family = SimpleNamespace(cdp_browser_root="P:/cdp", cdp_port=18870)
    monkeypatch.setattr(nlm_auth_headless, "_family_for_account", lambda profile: family)
    monkeypatch.setattr(
        nlm_auth_headless,
        "master_token_path_for_account",
        lambda profile: tmp_path / "token.json",
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "storage_path_for_account_profile",
        lambda profile: tmp_path / "storage.json",
    )
    monkeypatch.setattr(nlm_auth_headless, "_read_master_record", lambda path: None)
    monkeypatch.setattr(nlm_auth_headless, "_capture_oauth_token", lambda **kwargs: "one-use-oauth")
    monkeypatch.setattr(nlm_auth_headless, "_verify_cdp_account", lambda **kwargs: None)
    monkeypatch.setattr(nlm_auth_headless, "_exchange_master_token", lambda email, token, android: "durable-token")
    monkeypatch.setattr(nlm_auth_headless, "_write_master_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(nlm_auth_headless, "refresh_account_from_master_token", lambda profile: None)
    monkeypatch.setattr(
        nlm_auth_headless,
        "_prepare_cdp_family",
        lambda family, *, timeout_s, allow_sign_in=False: None,
    )
    monkeypatch.setattr(nlm_auth_headless.nlm_worker_auth, "_chrome_pids_for_root", lambda root: set())
    monkeypatch.setattr(nlm_auth_headless.nlm_worker_auth, "_stop_chrome_pids", lambda pids: None)
    monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "original")

    result = nlm_auth_headless.bootstrap_account_from_headless_cdp("a.hominidae", timeout_s=1)

    assert result == tmp_path / "storage.json"
    assert nlm_auth_headless.os.environ["YTIS_NLM_AUTH_NONINTERACTIVE"] == "original"


def test_bootstrap_explicit_cdp_skips_dedicated_family_and_validates_before_capture(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(nlm_auth_headless, "master_token_path_for_account", lambda profile: tmp_path / "token.json")
    monkeypatch.setattr(nlm_auth_headless, "storage_path_for_account_profile", lambda profile: tmp_path / "storage.json")
    monkeypatch.setattr(nlm_auth_headless, "_read_master_record", lambda path: None)
    verified = []
    captured = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "_verify_cdp_account",
        lambda **kwargs: verified.append(kwargs),
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "_capture_oauth_token",
        lambda **kwargs: captured.append(kwargs) or "one-use-oauth",
    )
    monkeypatch.setattr(nlm_auth_headless, "_exchange_master_token", lambda email, token, android: "durable-token")
    monkeypatch.setattr(nlm_auth_headless, "_write_master_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(nlm_auth_headless, "refresh_account_from_master_token", lambda profile: None)
    family_called = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "_family_for_account",
        lambda profile: family_called.append(profile),
    )

    result = nlm_auth_headless.bootstrap_account_from_headless_cdp(
        "a.hominidae",
        timeout_s=1,
        cdp_url="http://127.0.0.1:9222",
    )

    assert result == tmp_path / "storage.json"
    assert family_called == []
    assert verified == [
        {
            "cdp_url": "http://127.0.0.1:9222",
            "expected_email": "a.hominidae@gmail.com",
            "timeout_s": 1,
        }
    ]
    assert captured == [{"cdp_url": "http://127.0.0.1:9222", "timeout_s": 1}]


def test_bootstrap_wrong_cdp_account_cannot_capture_or_write(monkeypatch, tmp_path):
    monkeypatch.setattr(
        nlm_auth_headless,
        "master_token_path_for_account",
        lambda profile: tmp_path / "token.json",
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "storage_path_for_account_profile",
        lambda profile: tmp_path / "storage.json",
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "_verify_cdp_account",
        lambda **kwargs: (_ for _ in ()).throw(
            nlm_auth_headless.HeadlessAuthError("wrong account")
        ),
    )
    captured = []
    written = []
    monkeypatch.setattr(
        nlm_auth_headless,
        "_capture_oauth_token",
        lambda **kwargs: captured.append(kwargs) or "one-use-oauth",
    )
    monkeypatch.setattr(
        nlm_auth_headless,
        "_write_master_record",
        lambda *args, **kwargs: written.append((args, kwargs)),
    )

    with pytest.raises(nlm_auth_headless.HeadlessAuthError, match="wrong account"):
        nlm_auth_headless.bootstrap_account_from_headless_cdp(
            "a.hominidae",
            timeout_s=1,
            cdp_url="http://127.0.0.1:9222",
        )

    assert captured == []
    assert written == []
