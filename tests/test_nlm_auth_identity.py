from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from csf import nlm_auth_check


def _storage(email: str) -> dict[str, object]:
    return {"cookies": [], "origins": [], "notebooklm": {"account": {"email": email}}}


def test_account_profiles_resolve_only_to_canonical_paths():
    assert nlm_auth_check.storage_path_for_account_profile("a.hominidae").name == "storage_state.json"
    assert nlm_auth_check.storage_path_for_account_profile("troup.hominidae").name == "storage_state_troup_hominidae.json"
    assert nlm_auth_check.storage_path_for_account_profile("brsthomson").name == "storage_state_brsthomson.json"


def test_worker_label_cannot_select_account_storage():
    try:
        nlm_auth_check.storage_path_for_account_profile("ytis-pro-worker-01")
    except ValueError as exc:
        assert "a.hominidae" in str(exc)
    else:
        raise AssertionError("worker labels must not be accepted as account identities")


def test_storage_identity_mismatch_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(_storage("wrong@example.com")), encoding="utf-8")
    monkeypatch.setitem(nlm_auth_check.ACCOUNT_STORAGE_PATHS, "a.hominidae", path)

    result = nlm_auth_check.inspect_account_storage("a.hominidae")

    assert result.ok is False
    assert result.reason == "account_email_mismatch"
    assert result.observed_email == "wrong@example.com"


def test_missing_and_empty_storage_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "storage_state.json"
    monkeypatch.setitem(nlm_auth_check.ACCOUNT_STORAGE_PATHS, "a.hominidae", path)
    assert nlm_auth_check.inspect_account_storage("a.hominidae").reason == "storage_missing"
    path.write_text("", encoding="utf-8")
    assert nlm_auth_check.inspect_account_storage("a.hominidae").reason == "storage_empty"


def test_backup_filename_is_identity_specific_and_rejects_worker_labels():
    assert (
        nlm_auth_check.backup_filename_for_account_profile("troup.hominidae")
        == "storage_state_troup_hominidae.json"
    )
    with pytest.raises(ValueError, match="expected one of"):
        nlm_auth_check.backup_filename_for_account_profile("ytis-free1-worker-01")


def test_restore_account_from_backup_validates_embedded_email(tmp_path, monkeypatch):
    backup_repo = tmp_path / "backup"
    backup_repo.mkdir()
    target = tmp_path / "auth" / "storage_state_troup_hominidae.json"
    monkeypatch.setattr(nlm_auth_check, "BACKUP_REPO", backup_repo)
    monkeypatch.setitem(nlm_auth_check.ACCOUNT_STORAGE_PATHS, "troup.hominidae", target)
    wrong_payload = json.dumps(_storage("a.hominidae@gmail.com"))

    with mock.patch(
        "csf.nlm_auth_check.subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["git"], 0, wrong_payload, ""
        ),
    ) as run:
        assert nlm_auth_check.restore_account_from_backup("troup.hominidae") is False

    run.assert_called_once()
    assert not target.exists()


def test_restore_account_from_backup_writes_only_matching_identity(tmp_path, monkeypatch):
    backup_repo = tmp_path / "backup"
    backup_repo.mkdir()
    target = tmp_path / "auth" / "storage_state_troup_hominidae.json"
    monkeypatch.setattr(nlm_auth_check, "BACKUP_REPO", backup_repo)
    monkeypatch.setitem(nlm_auth_check.ACCOUNT_STORAGE_PATHS, "troup.hominidae", target)
    payload = json.dumps(_storage("troup.hominidae@gmail.com"))

    with mock.patch(
        "csf.nlm_auth_check.subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["git"], 0, payload, ""
        ),
    ):
        assert nlm_auth_check.restore_account_from_backup("troup.hominidae") is True

    assert nlm_auth_check.inspect_account_storage("troup.hominidae").ok is True
