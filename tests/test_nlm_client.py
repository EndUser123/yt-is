"""Tests for ``csf/nlm_client.py`` — Phase 1 scaffolding.

These tests deliberately avoid real auth and network I/O. The async client is
either a ``MagicMock`` (for direct-construction tests) or injected by patching
``csf.nlm_client.NotebookLMClient`` (for ``from_storage``/``get_sync_client``
tests). No real ``storage_state.json`` is read.
"""

from __future__ import annotations

import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from csf import nlm_client
from csf.nlm_client import (
    DEFAULT_PROFILE_STORAGE_ROOT,
    PROFILE_TO_ACCOUNT,
    NLMSyncClient,
    _get_active_profile,
    get_sync_client,
)

# =============================================================================
# Expected profile inventory (mirrors nlm_worker_auth.DEFAULT_FAMILIES + legacy)
# =============================================================================

_EXPECTED_PROFILES = {
    # pro (5)
    "ytis-pro-worker-01",
    "ytis-pro-worker-02",
    "ytis-pro-worker-03",
    "ytis-pro-worker-04",
    "ytis-pro-worker-05",
    # free1 (5)
    "ytis-free1-worker-01",
    "ytis-free1-worker-02",
    "ytis-free1-worker-03",
    "ytis-free1-worker-04",
    "ytis-free1-worker-05",
    # free2 (4)
    "ytis-free2-worker-01",
    "ytis-free2-worker-02",
    "ytis-free2-worker-03",
    "ytis-free2-worker-04",
    # legacy (6)
    "ytis-worker-01",
    "ytis-worker-02",
    "ytis-worker-03",
    "ytis-worker-04",
    "ytis-worker-05",
    "ytis-worker-06",
}

_KNOWN_ACCOUNTS = {
    "ytis-pro-account",
    "ytis-free1-account",
    "ytis-free2-account",
}


# =============================================================================
# PROFILE_TO_ACCOUNT coverage
# =============================================================================


class TestProfileToAccount:
    def test_covers_every_expected_profile_no_extras(self):
        """The map keys must exactly match the documented profile inventory."""
        assert set(PROFILE_TO_ACCOUNT.keys()) == _EXPECTED_PROFILES

    def test_no_orphan_profiles_all_values_are_known_accounts(self):
        """Every value must be one of the 3 known accounts — no orphan typos."""
        values = set(PROFILE_TO_ACCOUNT.values())
        assert values <= _KNOWN_ACCOUNTS
        assert values == _KNOWN_ACCOUNTS  # all 3 accounts are used

    def test_pro_workers_route_to_pro_account(self):
        for i in range(1, 6):
            assert PROFILE_TO_ACCOUNT[f"ytis-pro-worker-{i:02d}"] == "ytis-pro-account"

    def test_free1_workers_route_to_free1_account(self):
        for i in range(1, 6):
            assert PROFILE_TO_ACCOUNT[f"ytis-free1-worker-{i:02d}"] == "ytis-free1-account"

    def test_free2_workers_route_to_free2_account(self):
        for i in range(1, 5):
            assert PROFILE_TO_ACCOUNT[f"ytis-free2-worker-{i:02d}"] == "ytis-free2-account"

    def test_legacy_workers_route_to_pro_account(self):
        for i in range(1, 7):
            assert PROFILE_TO_ACCOUNT[f"ytis-worker-{i:02d}"] == "ytis-pro-account"

    def test_default_profile_storage_root_matches_library(self):
        """Storage root must match notebooklm-py's ~/.notebooklm/profiles default."""
        assert DEFAULT_PROFILE_STORAGE_ROOT == (
            __import__("pathlib").Path.home() / ".notebooklm" / "profiles"
        )


# =============================================================================
# NLMSyncClient.run — sync/async bridge
# =============================================================================


def _make_wrapper(client: MagicMock | None = None) -> NLMSyncClient:
    """Build a wrapper around a fake client without touching real auth."""
    fake = client or MagicMock()
    fake.is_connected.return_value = True
    return NLMSyncClient(client=fake, profile="ytis-pro-worker-01", account="ytis-pro-account")


class TestRunBridge:
    def test_run_returns_coroutine_result(self):
        async def add(a: int, b: int) -> int:
            return a + b

        wrapper = _make_wrapper()
        try:
            assert wrapper.run(add(2, 3)) == 5
        finally:
            wrapper.close()

    def test_run_can_be_called_multiple_times_on_same_loop(self):
        """Loop-affinity: the same persistent loop must serve every call."""

        async def square(x: int) -> int:
            return x * x

        wrapper = _make_wrapper()
        try:
            assert wrapper.run(square(2)) == 4
            assert wrapper.run(square(3)) == 9
            assert wrapper.run(square(4)) == 16
        finally:
            wrapper.close()

    def test_run_propagates_exception_from_coroutine(self):
        async def boom() -> None:
            raise ValueError("coroutine failed")

        wrapper = _make_wrapper()
        try:
            with pytest.raises(ValueError, match="coroutine failed"):
                wrapper.run(boom())
        finally:
            wrapper.close()

    def test_run_raises_when_closed(self):
        async def f() -> int:
            return 1

        wrapper = _make_wrapper()
        wrapper.close()
        coro = f()
        try:
            with pytest.raises(RuntimeError, match="closed"):
                wrapper.run(coro)
        finally:
            # ``run`` raised before awaiting — close the coro to avoid a
            # "coroutine was never awaited" ResourceWarning.
            coro.close()


# =============================================================================
# NLMSyncClient.is_connected / context manager / close
# =============================================================================


class TestLifecycle:
    def test_is_connected_reflects_client_state_true(self):
        fake = MagicMock()
        fake.is_connected.return_value = True
        wrapper = NLMSyncClient(client=fake)
        try:
            assert wrapper.is_connected() is True
        finally:
            wrapper.close()

    def test_is_connected_reflects_client_state_false(self):
        fake = MagicMock()
        fake.is_connected.return_value = False
        wrapper = NLMSyncClient(client=fake)
        try:
            assert wrapper.is_connected() is False
        finally:
            wrapper.close()

    def test_is_connected_false_after_close(self):
        fake = MagicMock()
        fake.is_connected.return_value = True
        wrapper = NLMSyncClient(client=fake)
        wrapper.close()
        assert wrapper.is_connected() is False
        assert wrapper.closed is True

    def test_context_manager_closes_client_and_loop(self):
        fake = MagicMock()
        fake.is_connected.return_value = True
        fake.close = AsyncMock()
        wrapper = NLMSyncClient(client=fake)
        loop = wrapper._ensure_loop()
        with wrapper:
            assert wrapper.closed is False
        # On exit the client's async close was awaited and the owned loop closed.
        fake.close.assert_awaited_once()
        assert wrapper.closed is True
        assert loop.is_closed()

    def test_close_is_idempotent(self):
        fake = MagicMock()
        fake.close = AsyncMock()
        wrapper = NLMSyncClient(client=fake)
        wrapper.close()
        wrapper.close()  # second close must not raise
        assert fake.close.await_count == 1

    def test_sources_and_notebooks_forward_to_client(self):
        fake = MagicMock()
        fake.sources = object()
        fake.notebooks = object()
        wrapper = NLMSyncClient(client=fake)
        try:
            assert wrapper.sources is fake.sources
            assert wrapper.notebooks is fake.notebooks
            assert wrapper.client is fake
        finally:
            wrapper.close()


# =============================================================================
# from_storage + domain-error translation (mocked NotebookLMClient)
# =============================================================================


def _patched_nlm_client(fake_client: MagicMock) -> mock._patch:
    """Patch ``csf.nlm_client.NotebookLMClient`` so ``from_storage`` yields ``fake_client``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=fake_client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    patcher = mock.patch("csf.nlm_client.NotebookLMClient")
    mock_cls = patcher.start()
    mock_cls.from_storage.return_value = ctx
    return patcher


class TestFromStorage:
    def test_from_storage_resolves_profile_to_account_and_enters_context(self):
        fake_client = MagicMock()
        fake_client.is_connected.return_value = True
        patcher = _patched_nlm_client(fake_client)
        try:
            wrapper = NLMSyncClient.from_storage("ytis-free2-worker-03")
            try:
                # The library was asked to load the resolved account, not the routing label.
                nlm_client.NotebookLMClient.from_storage.assert_called_once_with(
                    profile="ytis-free2-account"
                )
                assert wrapper.client is fake_client
                assert wrapper.profile == "ytis-free2-worker-03"
                assert wrapper.account == "ytis-free2-account"
                assert wrapper.is_connected() is True
            finally:
                wrapper.close()
        finally:
            patcher.stop()

    def test_from_storage_raises_key_error_for_unknown_profile(self):
        with pytest.raises(KeyError, match="unknown yt-is NotebookLM profile"):
            NLMSyncClient.from_storage("not-a-real-profile")

    def test_from_storage_translates_missing_storage_file_to_runtime_error(self):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(
            side_effect=FileNotFoundError("Storage file not found: .../storage_state.json")
        )
        with mock.patch("csf.nlm_client.NotebookLMClient") as mock_cls:
            mock_cls.from_storage.return_value = ctx
            with pytest.raises(RuntimeError) as excinfo:
                NLMSyncClient.from_storage("ytis-pro-worker-01")

        msg = str(excinfo.value)
        assert "notebooklm-py storage file missing for profile 'ytis-pro-worker-01'" in msg
        assert "python -m notebooklm login -p ytis-pro-account" in msg
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)

    def test_from_storage_does_not_leak_loop_when_open_fails(self):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        with mock.patch("csf.nlm_client.NotebookLMClient") as mock_cls:
            mock_cls.from_storage.return_value = ctx
            with pytest.raises(RuntimeError, match="boom"):
                NLMSyncClient.from_storage("ytis-pro-worker-01")


# =============================================================================
# _get_active_profile env-var resolution
# =============================================================================


class TestGetActiveProfile:
    def test_returns_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
        assert _get_active_profile() == "default"

    def test_returns_env_value_when_set(self, monkeypatch):
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        assert _get_active_profile() == "ytis-pro-worker-02"

    def test_returns_empty_string_env_value_as_is(self, monkeypatch):
        # An explicitly-empty value is not "unset"; the operator cleared it.
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "")
        assert _get_active_profile() == ""


# =============================================================================
# get_sync_client
# =============================================================================


class TestGetSyncClient:
    def test_uses_explicit_profile_and_resolves_account(self):
        fake_client = MagicMock()
        fake_client.is_connected.return_value = True
        patcher = _patched_nlm_client(fake_client)
        try:
            wrapper = get_sync_client("ytis-free1-worker-04")
            try:
                nlm_client.NotebookLMClient.from_storage.assert_called_once_with(
                    profile="ytis-free1-account"
                )
                assert wrapper.client is fake_client
                assert wrapper.profile == "ytis-free1-worker-04"
            finally:
                wrapper.close()
        finally:
            patcher.stop()

    def test_uses_active_profile_when_profile_is_none(self, monkeypatch):
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-free2-worker-02")
        fake_client = MagicMock()
        fake_client.is_connected.return_value = True
        patcher = _patched_nlm_client(fake_client)
        try:
            wrapper = get_sync_client(None)
            try:
                nlm_client.NotebookLMClient.from_storage.assert_called_once_with(
                    profile="ytis-free2-account"
                )
                assert wrapper.profile == "ytis-free2-worker-02"
            finally:
                wrapper.close()
        finally:
            patcher.stop()

    def test_propagates_key_error_for_unmapped_active_profile(self, monkeypatch):
        # An env var pointing at an unmapped profile surfaces a KeyError, not silent auth.
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "totally-unknown")
        with pytest.raises(KeyError, match="unknown yt-is NotebookLM profile"):
            get_sync_client(None)
