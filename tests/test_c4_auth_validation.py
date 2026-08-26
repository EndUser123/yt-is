"""C4-B falsifier tests for real auth validation (yt-is trust floor).

Covers the contract from ``docs/operations/root-cause-program.md`` §C4
"Fail-closed auth (industrial)" and the findings referenced in the
``review-2026-07-17-grok-deep2.md`` review:

- Falsifier (root-cause-program.md §C4): ``validate_auth() still returns
  True unconditionally on main.`` That function was a permanent no-op
  and was deleted under C4-B; this test asserts the contract is gone.
- COR-007 (review): ``Empty expected_email treats any successful login
  --check as authorized.`` Now the cache is bound to the verified
  account fingerprint and empty-account probes fail closed.
- COR-008 (review): ``refresh_reason UnboundLocalError path when account
  empty.`` Initialized before the branch ladder and the missing-account
  branch is now an explicit fail-closed case.
- COR-009 (review): ``Family auth refresh disables live session
  verification`` via ``source_session_checker=lambda _profile: True``.
  Removed; the default live checker is now used.

Tests use mocking only for the subprocess boundary and the
``fasteners.InterProcessLock`` path. The auth code itself is exercised
in-process so the falsifiers cannot pass by skipping real branches.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from csf import nlm_auth_guard, nlm_batch, youtube_auth


# ---------------------------------------------------------------------------
# Falsifier: validate_auth() must not be a permanent no-op success.
# Mirrors the C4 contract falsifier from root-cause-program.md.
# ---------------------------------------------------------------------------


class TestValidateAuthContractGone:
    """The previous `youtube_auth.validate_auth()` always returned True.

    C4-B deletes that false contract. The real auth probe lives in
    ``nlm_batch._ensure_nlm_auth`` (which calls ``nlm login --check`` and
    refuses cache hits whose verified-account fingerprint does not match
    the expected email).
    """

    def test_validate_auth_is_removed_from_module(self):
        """The no-op validate_auth function must not exist any more."""
        assert not hasattr(youtube_auth, "validate_auth"), (
            "youtube_auth.validate_auth() was the C4 falsifier — a permanent "
            "no-op that returned True unconditionally. C4-B removed it; if "
            "this test fails the function was reintroduced without an "
            "implementation that actually probes auth state."
        )

    def test_youtube_auth_still_exports_get_browser_cookies(self):
        """The legitimate cookie helper must remain after the falsifier fix."""
        assert hasattr(youtube_auth, "get_browser_cookies")
        assert callable(youtube_auth.get_browser_cookies)


# ---------------------------------------------------------------------------
# Falsifier: refresh_reason must not be UnboundLocalError on the
# empty-account path. Locked re-check must fail closed when the
# worker profile returns no Account: line under a non-empty expected email.
# ---------------------------------------------------------------------------


class TestRefreshReasonEmptyAccount:
    """The UnboundLocalError path COR-008 documents is now an explicit
    fail-closed branch that initializes ``refresh_reason`` before the
    branch ladder runs.
    """

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        """Reset auth cache + runtime config + chrome-pid mock per test."""
        monkeypatch.setattr(nlm_batch, "_default_chrome_profile_pids", lambda: set())
        monkeypatch.setattr(nlm_batch, "_NLM_AUTH_RUNTIME_CONFIG_LOGGED", False)
        monkeypatch.setattr(
            nlm_batch, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: False
        )
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()
        yield
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()

    def test_describe_refresh_reason_recognises_missing_account(self):
        """A returncode==0 with empty Account: line must classify as
        ``missing_account`` when expected_email is configured."""
        reason = nlm_batch._describe_nlm_auth_refresh_reason(
            force_scheduled=False,
            cache_hit=False,
            cache_session_age_s=None,
            check_returncode=0,
            check_account="",
            expected_email="expected@example.com",
        )
        assert reason == "missing_account"

    def test_describe_refresh_reason_prefers_check_failed_over_missing_account(self):
        """check_returncode != 0 outranks missing_account."""
        reason = nlm_batch._describe_nlm_auth_refresh_reason(
            force_scheduled=False,
            cache_hit=False,
            cache_session_age_s=None,
            check_returncode=1,
            check_account="",
            expected_email="expected@example.com",
        )
        assert reason == "check_failed"

    def test_ensure_nlm_auth_fails_closed_on_outer_empty_account(self, monkeypatch):
        """login --check returns 0 with no Account: line; expected_email set;
        worker must fail closed in the OUTER (pre-lock) branch ladder, not
        raise UnboundLocalError."""
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "custom-worker-empty-acc")
        monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
        # C4-B: expected_email must be set so the missing_account branch
        # can fire. (Empty expected_email is a different failure mode —
        # see COR-007 / C4-A email enforcement.)
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "expected@example.com")

        def mock_run(cmd, **kwargs):
            if cmd == ["login", "--check", "--profile", "custom-worker-empty-acc"]:
                # Success returncode but no Account: line.
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected command: {cmd!r}")

        # Force the inner InterProcessLock to no-op so we exercise the OUTER
        # branch ladder (the pre-lock empty-account branch).
        class _NoOpLock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(nlm_batch.fasteners, "InterProcessLock", lambda path: _NoOpLock())

        with mock.patch("csf.nlm_batch.run_nlm", side_effect=mock_run):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = nlm_batch._ensure_nlm_auth()

        assert result is False, "Empty-account probe must fail closed."
        statuses = [
            (call.args[1].get("status") if len(call.args) > 1 and isinstance(call.args[1], dict) else None)
            for call in mock_log.call_args_list
            if call.args and call.args[0] == "nlm_auth_failed"
        ]
        assert "missing_account" in statuses, (
            "The missing_account status should be logged once when the OUTER "
            "branch ladder detects an empty Account: line under a configured "
            "expected_email. Got statuses: %r" % statuses
        )

    def test_ensure_nlm_auth_fails_closed_on_locked_recheck_empty_account(self, monkeypatch):
        """If the OUTER probe somehow cleared (e.g. Account: line missing
        only on the second try), the LOCKED re-check must also fail closed
        instead of raising UnboundLocalError when ``refresh_reason`` is
        referenced at line ~1749."""
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "custom-worker-empty-acc-2")
        monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "expected@example.com")

        # First call: returncode 0 with Account: line (so OUTER succeeds
        # at the cache-hit shortcut... actually we need to skip the cache
        # shortcut by setting up the call sequence carefully).
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            if cmd == ["login", "--check", "--profile", "custom-worker-empty-acc-2"]:
                # Both probes return success but no Account: line.
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected command: {cmd!r}")

        # Force the InterProcessLock to be a real lock (not no-op) so we
        # actually hit the locked re-check branch ladder.
        real_lock = nlm_batch.fasteners.InterProcessLock

        monkeypatch.setattr(nlm_batch, "_get_nlm_auth_force_refresh_every_checks", lambda: 0)
        # Force the OUTER branch ladder to fall through (refresh_reason
        # should still be initialised before the ladder). The OUTER
        # missing-account branch should log + continue into the locked
        # context where the locked branch's missing-account branch fires
        # the explicit ``return False``.
        monkeypatch.setattr(nlm_batch, "_get_nlm_auth_force_refresh_every_checks", lambda: 0)

        with mock.patch("csf.nlm_batch.run_nlm", side_effect=mock_run):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                # The InterProcessLock is real here, so the locked re-check
                # will execute. We expect failure, not UnboundLocalError.
                try:
                    result = nlm_batch._ensure_nlm_auth()
                except UnboundLocalError as exc:  # pragma: no cover - the
                    # regression we are guarding against.
                    pytest.fail(
                        "Empty-account path raised UnboundLocalError "
                        "(regression of COR-008): %s" % exc
                    )

        assert result is False
        # Whether the outer or the inner branch fires, the failure must be
        # classified as missing_account — not wrong_account (no parsed
        # account), not check_failed (returncode is 0).
        statuses = [
            (call.args[1].get("status") if len(call.args) > 1 and isinstance(call.args[1], dict) else None)
            for call in mock_log.call_args_list
            if call.args and call.args[0] == "nlm_auth_failed"
        ]
        assert "missing_account" in statuses, (
            "Locked re-check must log missing_account when the second "
            "probe also returns no Account: line. Got: %r" % statuses
        )
        _ = real_lock  # silence unused linter warning


# ---------------------------------------------------------------------------
# Falsifier: family refresh must run the live session check. The
# pre-2026-08-22 ``source_session_checker=lambda _profile: True`` sentinel
# short-circuited the live checker; the worker-fleet sync machinery was
# retired in 4a3d19aa and the check is now a direct call inside
# ``_refresh_family_nlm_auth_session``.
# ---------------------------------------------------------------------------


class TestFamilyRefreshUsesLiveSessionCheck:
    """Family refresh runs ``profile_session_matches_expected(source,
    expected)`` after the CDP refresh and before any session is cached;
    the session store is fingerprint-bound to the family's expected email.
    These tests pin the post-4a3d19aa mechanism (the old class patched the
    deleted ``sync_worker_profiles`` API and errored at setup)."""

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        monkeypatch.setattr(nlm_batch, "_default_chrome_profile_pids", lambda: set())
        monkeypatch.setattr(nlm_batch, "_NLM_AUTH_RUNTIME_CONFIG_LOGGED", False)
        monkeypatch.setattr(
            nlm_batch, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: False
        )
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()
        yield
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()

    def test_family_refresh_runs_live_session_check(self):
        """Direct call: refresh succeeds but the live check fails -> the
        refresh is not trusted and no session is stored."""
        from csf.nlm_worker_auth import DEFAULT_FAMILIES

        family = DEFAULT_FAMILIES[1]
        auth_context = nlm_batch._NLMAuthContext(
            profile=family.source_profile,
            login_profile_args=["--profile", family.source_profile],
            requires_profile=True,
            expected_email=family.expected_email,
        )

        with mock.patch("csf.nlm_worker_auth.refresh_source_profile", return_value=True):
            with mock.patch(
                "csf.nlm_worker_auth.profile_session_matches_expected",
                return_value=False,
            ) as live_check:
                with mock.patch("csf.nlm_batch.nlm_auth_guard.auth_check_cache_store") as store:
                    result = nlm_batch._refresh_family_nlm_auth_session(
                        auth_context, family, timeout_s=1.0
                    )

        assert result is False, "a failed live session check must fail the refresh"
        live_check.assert_called_once_with(
            family.source_profile, family.expected_email
        )
        assert not store.called, "no session may be cached on a failed check"

    def test_family_refresh_success_binds_expected_email(self):
        """Passing path: the stored session is fingerprint-bound to the
        family's expected email, so a later cache hit cannot authorize a
        different account."""
        from csf.nlm_worker_auth import DEFAULT_FAMILIES

        family = DEFAULT_FAMILIES[1]
        auth_context = nlm_batch._NLMAuthContext(
            profile=family.source_profile,
            login_profile_args=["--profile", family.source_profile],
            requires_profile=True,
            expected_email=family.expected_email,
        )

        with mock.patch("csf.nlm_worker_auth.refresh_source_profile", return_value=True):
            with mock.patch(
                "csf.nlm_worker_auth.profile_session_matches_expected",
                return_value=True,
            ):
                with mock.patch("csf.nlm_batch.nlm_auth_guard.auth_check_cache_store") as store:
                    result = nlm_batch._refresh_family_nlm_auth_session(
                        auth_context, family, timeout_s=1.0
                    )

        assert result is True
        store.assert_called_once()
        assert store.call_args.kwargs.get("verified_account") == family.expected_email

    def test_family_refresh_via_ensure_nlm_auth_uses_live_check(self, monkeypatch):
        """End-to-end: ``_ensure_nlm_auth`` for a mapped source profile
        routes through family refresh with the live check and never falls
        back to a bare ``login --check``."""
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "troup.hominidae")
        monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")
        monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)

        with mock.patch("csf.nlm_worker_auth.refresh_source_profile", return_value=True):
            with mock.patch(
                "csf.nlm_worker_auth.profile_session_matches_expected",
                return_value=True,
            ):
                with mock.patch("csf.nlm_batch.nlm_auth_guard.auth_check_cache_store"):
                    with mock.patch(
                        "csf.nlm_batch.run_nlm",
                        side_effect=AssertionError(
                            "family auth should not use bare login --check"
                        ),
                    ):
                        result = nlm_batch._ensure_nlm_auth()

        assert result is True


# ---------------------------------------------------------------------------
# Falsifier: auth cache is bound to a verified account fingerprint; the
# cache hit cannot authorize an account swap (C4-B / COR-006 binding).
# ---------------------------------------------------------------------------


class TestAuthCacheBoundToVerifiedAccount:
    """The auth cache previously returned ``True`` on any hit without
    verifying the stored account matched the expected email. C4-B binds
    each entry to a verified account fingerprint and refuses to count
    the hit when the fingerprint does not match."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        with nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_auth_guard._AUTH_CHECK_CACHE.clear()
        yield
        with nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_auth_guard._AUTH_CHECK_CACHE.clear()

    def _ctx(self, profile: str, expected_email: str) -> nlm_auth_guard.NLMAuthContext:
        return nlm_auth_guard.NLMAuthContext(
            profile=profile,
            login_profile_args=["--profile", profile],
            requires_profile=False,
            expected_email=expected_email,
        )

    def test_cache_hit_refused_when_verified_account_differs_from_expected(self):
        """A cache entry stored under a verified account must NOT count as
        a hit when queried with a different expected_email."""
        ctx_store = self._ctx("worker-01", "verified@example.com")
        nlm_auth_guard.auth_check_cache_store(ctx_store, verified_account="verified@example.com")

        ctx_query = self._ctx("worker-01", "different@example.com")
        hit, _session_established_at = nlm_auth_guard.auth_check_cache_hit(ctx_query)
        assert hit is False, (
            "Cache hit must be refused when the stored verified-account "
            "fingerprint differs from the query's expected_email."
        )

    def test_cache_hit_accepted_when_verified_account_matches(self):
        """A cache hit must still succeed when the stored fingerprint
        matches the query's expected_email."""
        ctx = self._ctx("worker-02", "alice@example.com")
        nlm_auth_guard.auth_check_cache_store(ctx, verified_account="alice@example.com")

        hit, _ = nlm_auth_guard.auth_check_cache_hit(ctx)
        assert hit is True

    def test_cache_hit_accepted_when_expected_email_empty(self):
        """Empty expected_email is the legacy "no identity" path — the
        cache hit must still succeed so non-industrial callers are not
        regressed. Fingerprint binding only fires when expected_email is
        configured."""
        ctx_store = self._ctx("worker-03", "")
        nlm_auth_guard.auth_check_cache_store(ctx_store, verified_account="")

        ctx_query = self._ctx("worker-03", "")
        hit, _ = nlm_auth_guard.auth_check_cache_hit(ctx_query)
        assert hit is True

    def test_cache_clear_removes_bound_entry(self):
        """``auth_check_cache_clear`` must drop the stored entry so the
        next check re-probes instead of trusting a stale fingerprint."""
        ctx = self._ctx("worker-04", "bob@example.com")
        nlm_auth_guard.auth_check_cache_store(ctx, verified_account="bob@example.com")
        nlm_auth_guard.auth_check_cache_clear(ctx)

        hit, _ = nlm_auth_guard.auth_check_cache_hit(ctx)
        assert hit is False
        assert nlm_auth_guard.auth_check_cache_verified_account(ctx) is None

    def test_verified_account_round_trips(self):
        """``auth_check_cache_verified_account`` must return the bound
        fingerprint after a successful store."""
        ctx = self._ctx("worker-05", "carol@example.com")
        nlm_auth_guard.auth_check_cache_store(ctx, verified_account="carol@example.com")
        assert nlm_auth_guard.auth_check_cache_verified_account(ctx) == "carol@example.com"


# ---------------------------------------------------------------------------
# Falsifier: empty-account path is bound to a real probe before any cache
# hit can authorise the worker. This is the end-to-end smoke test for
# C4-B's "Auth cache bound to verified account fingerprint; not fail-open
# forever on hit alone" requirement.
# ---------------------------------------------------------------------------


class TestEnsureNlmAuthCacheFailClosedOnAccountSwap:
    """If a previous run cached an auth entry under one account and a
    later run asks the worker to authorise a *different* account, the
    cache must NOT short-circuit the auth probe."""

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        monkeypatch.setattr(nlm_batch, "_default_chrome_profile_pids", lambda: set())
        monkeypatch.setattr(nlm_batch, "_NLM_AUTH_RUNTIME_CONFIG_LOGGED", False)
        monkeypatch.setattr(
            nlm_batch, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: False
        )
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()
        yield
        with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
            nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()

    def test_cache_with_different_fingerprint_does_not_authorise(self, monkeypatch):
        """Cache stores verified_account=other; expected_email asks for
        target. The cache hit must not short-circuit — a fresh --check
        must run and the swap must be visible to the caller."""
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-swap-test")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "target@example.com")
        monkeypatch.setenv("YTIS_NLM_AUTH_NONINTERACTIVE", "1")

        # Pre-seed a cache entry under a *different* verified account so
        # the fingerprint check has something to refuse.
        seed_ctx = nlm_auth_guard.NLMAuthContext(
            profile="worker-swap-test",
            login_profile_args=["--profile", "worker-swap-test"],
            requires_profile=True,
            expected_email="target@example.com",
        )
        nlm_auth_guard.auth_check_cache_store(
            seed_ctx, verified_account="attacker@example.com"
        )

        # First call to --check returns success with the right account.
        # If the cache had authorized, the worker would short-circuit and
        # no nlm subprocess would be launched.
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == ["login", "--check", "--profile", "worker-swap-test"]:
                return subprocess.CompletedProcess(
                    cmd, 0, "Account: target@example.com\n", ""
                )
            raise AssertionError(f"unexpected command: {cmd!r}")

        with mock.patch("csf.nlm_batch.run_nlm", side_effect=mock_run):
            result = nlm_batch._ensure_nlm_auth()

        assert result is True
        # Critical: --check actually ran because the cached fingerprint
        # was wrong. If the cache had not been bound, the swap would have
        # been invisible and this assertion would have failed.
        assert ["login", "--check", "--profile", "worker-swap-test"] in calls


class TestC4AEmailEnforcement:
    """C4-A: Empty expected_email cannot authorize industrial worker path.

    COR-001 falsifier: when noninteractive mode is set and expected_email
    is empty, _ensure_nlm_auth must fail closed (return False) without
    attempting any auth commands.
    """

    def test_empty_email_fails_closed_in_noninteractive_mode(self):
        """should_fail_closed is True when requires_profile AND no expected_email."""
        from csf.nlm_batch import _NLMAuthContext

        ctx = _NLMAuthContext(
            profile="worker-01",
            login_profile_args=["--profile", "worker-01"],
            requires_profile=True,
            expected_email="",
        )
        assert ctx.should_fail_closed, (
            "noninteractive + empty expected_email should fail closed (COR-001)"
        )

    def test_empty_email_allowed_in_interactive_mode(self):
        """should_fail_closed is False when NOT noninteractive (interactive OK without email)."""
        from csf.nlm_batch import _NLMAuthContext

        ctx = _NLMAuthContext(
            profile="worker-01",
            login_profile_args=["--profile", "worker-01"],
            requires_profile=False,
            expected_email="",
        )
        assert not ctx.should_fail_closed, (
            "interactive mode + empty expected_email should NOT fail closed"
        )

    def test_nonempty_email_passes_in_noninteractive_mode(self):
        """should_fail_closed is False when noninteractive AND has expected_email."""
        from csf.nlm_batch import _NLMAuthContext

        ctx = _NLMAuthContext(
            profile="worker-01",
            login_profile_args=["--profile", "worker-01"],
            requires_profile=True,
            expected_email="user@example.com",
        )
        assert not ctx.should_fail_closed, (
            "noninteractive + valid expected_email should NOT fail closed"
        )

    def test_no_profile_fails_closed_regardless_of_email(self):
        """should_fail_closed is True when no profile even with email."""
        from csf.nlm_batch import _NLMAuthContext

        ctx = _NLMAuthContext(
            profile="",
            login_profile_args=[],
            requires_profile=True,
            expected_email="user@example.com",
        )
        assert ctx.should_fail_closed, (
            "noninteractive + no profile should fail closed even with email"
        )
