from __future__ import annotations

import json
import sys
import subprocess
from types import SimpleNamespace
from pathlib import Path

from csf import nlm_auth_check, nlm_keepalive


def _status(account_profile: str, path: Path, *, ok: bool, reason: str = "ok"):
    return nlm_auth_check.AccountStorageStatus(
        account_profile=account_profile,
        expected_email=nlm_auth_check.expected_email_for_account_profile(account_profile),
        storage_path=path,
        ok=ok,
        reason=reason,
        observed_email=(
            nlm_auth_check.expected_email_for_account_profile(account_profile)
            if ok
            else ""
        ),
    )


def test_keepalive_probes_and_backs_up_all_canonical_accounts(monkeypatch):
    paths = {
        account: Path(f"P:/.data/yt-is/nlm-auth/{account}.json")
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    monkeypatch.setattr(nlm_keepalive, "ACCOUNT_STORAGE_PATHS", paths)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(account, paths[account], ok=True),
    )
    probed: list[str] = []

    def fake_ensure(account):
        probed.append(account)
        return SimpleNamespace(ok=True, reason="ok")

    backed_up: list[tuple[str, ...]] = []
    monkeypatch.setattr(nlm_keepalive, "_ensure_account_session", fake_ensure)
    monkeypatch.setattr(
        nlm_keepalive,
        "_push_backup",
        lambda accounts: backed_up.append(tuple(accounts)) or True,
    )
    monkeypatch.setattr(sys, "argv", ["nlm_keepalive"])

    assert nlm_keepalive.main() == 0
    assert probed == list(nlm_keepalive.ACCOUNT_PROFILES)
    assert backed_up == [nlm_keepalive.ACCOUNT_PROFILES]


def test_keepalive_fails_before_probe_when_an_account_cannot_be_restored(monkeypatch):
    paths = {
        account: Path(f"P:/.data/yt-is/nlm-auth/{account}.json")
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    monkeypatch.setattr(nlm_keepalive, "ACCOUNT_STORAGE_PATHS", paths)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(
            account,
            paths[account],
            ok=account != "troup.hominidae",
            reason="storage_missing" if account == "troup.hominidae" else "ok",
        ),
    )
    probed: list[str] = []

    def fake_ensure(account):
        probed.append(account)
        if account == "troup.hominidae":
            return SimpleNamespace(ok=False, reason="noninteractive_repair_failed")
        return SimpleNamespace(ok=True, reason="ok")

    monkeypatch.setattr(nlm_keepalive, "_ensure_account_session", fake_ensure)
    monkeypatch.setattr(sys, "argv", ["nlm_keepalive"])

    assert nlm_keepalive.main() == 2
    assert probed == list(nlm_keepalive.ACCOUNT_PROFILES)


def test_keepalive_probes_remaining_accounts_and_returns_session_failure(monkeypatch):
    paths = {
        account: Path(f"P:/.data/yt-is/nlm-auth/{account}.json")
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    monkeypatch.setattr(nlm_keepalive, "ACCOUNT_STORAGE_PATHS", paths)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(
            account,
            paths[account],
            ok=True,
        ),
    )
    probed: list[str] = []

    def fake_ensure(account):
        probed.append(account)
        return SimpleNamespace(ok=account != "troup.hominidae", reason="session_probe_failed")

    monkeypatch.setattr(nlm_keepalive, "_ensure_account_session", fake_ensure)
    monkeypatch.setattr(sys, "argv", ["nlm_keepalive"])

    assert nlm_keepalive.main() == 3
    assert probed == list(nlm_keepalive.ACCOUNT_PROFILES)


def test_keepalive_backs_up_healthy_accounts_before_reporting_other_failures(monkeypatch):
    paths = {
        account: Path(f"P:/.data/yt-is/nlm-auth/{account}.json")
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    monkeypatch.setattr(nlm_keepalive, "ACCOUNT_STORAGE_PATHS", paths)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(account, paths[account], ok=True),
    )
    backed_up: list[tuple[str, ...]] = []

    def fake_ensure(account):
        return SimpleNamespace(ok=account != "brsthomson", reason="expired")

    monkeypatch.setattr(nlm_keepalive, "_ensure_account_session", fake_ensure)
    monkeypatch.setattr(
        nlm_keepalive,
        "_push_backup",
        lambda accounts: backed_up.append(tuple(accounts)) or True,
    )
    monkeypatch.setattr(sys, "argv", ["nlm_keepalive"])

    assert nlm_keepalive.main() == 3
    assert backed_up == [("a.hominidae", "troup.hominidae")]


def test_keepalive_log_file_is_append_only_and_contains_outcome(monkeypatch, tmp_path):
    paths = {
        account: Path(f"P:/.data/yt-is/nlm-auth/{account}.json")
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    monkeypatch.setattr(nlm_keepalive, "ACCOUNT_STORAGE_PATHS", paths)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(account, paths[account], ok=True),
    )
    monkeypatch.setattr(
        nlm_keepalive,
        "_ensure_account_session",
        lambda account: SimpleNamespace(ok=True, reason="ok"),
    )
    monkeypatch.setattr(nlm_keepalive, "_push_backup", lambda accounts: True)
    log_path = tmp_path / "keepalive.log"
    monkeypatch.setattr(sys, "argv", ["nlm_keepalive", "--log-file", str(log_path)])

    assert nlm_keepalive.main() == 0
    contents = log_path.read_text(encoding="utf-8")
    assert "keepalive start" in contents
    assert "keepalive complete" in contents


def test_push_backup_writes_exact_identity_files_to_local_bare_repo(tmp_path, monkeypatch):
    backup_repo = tmp_path / "backup.git"
    subprocess.run(["git", "init", "--bare", str(backup_repo)], check=True, capture_output=True)
    paths = {
        account: tmp_path / f"{account.replace('.', '_')}.json"
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    for account, path in paths.items():
        path.write_text(
            json.dumps(
                {
                    "cookies": [],
                    "origins": [],
                    "notebooklm": {
                        "account": {
                            "email": nlm_auth_check.expected_email_for_account_profile(account)
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(nlm_keepalive, "BACKUP_REPO", backup_repo)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(account, paths[account], ok=True),
    )

    assert nlm_keepalive._push_backup(nlm_keepalive.ACCOUNT_PROFILES) is True

    names = subprocess.run(
        ["git", "--git-dir", str(backup_repo), "ls-tree", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert names == [
        "storage_state.json",
        "storage_state_brsthomson.json",
        "storage_state_troup_hominidae.json",
    ]


def test_push_backup_adopts_existing_history_without_force_push(tmp_path, monkeypatch):
    backup_repo = tmp_path / "backup.git"
    subprocess.run(["git", "init", "--bare", str(backup_repo)], check=True, capture_output=True)
    paths = {
        account: tmp_path / f"{account.replace('.', '_')}.json"
        for account in nlm_keepalive.ACCOUNT_PROFILES
    }
    for account, path in paths.items():
        path.write_text(
            json.dumps(
                {
                    "cookies": [],
                    "origins": [],
                    "notebooklm": {
                        "account": {
                            "email": nlm_auth_check.expected_email_for_account_profile(account)
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(nlm_keepalive, "BACKUP_REPO", backup_repo)
    monkeypatch.setattr(
        nlm_keepalive,
        "inspect_account_storage",
        lambda account, storage_path=None: _status(account, paths[account], ok=True),
    )

    assert nlm_keepalive._push_backup(nlm_keepalive.ACCOUNT_PROFILES) is True
    paths["brsthomson"].write_text(
        paths["brsthomson"].read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    assert nlm_keepalive._push_backup(nlm_keepalive.ACCOUNT_PROFILES) is True

    history = subprocess.run(
        ["git", "--git-dir", str(backup_repo), "log", "--format=%s", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(history) == 2
