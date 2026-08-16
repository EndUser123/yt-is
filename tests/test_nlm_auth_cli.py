from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "bin" / "csf-nlm-auth"


def _load_cli():
    loader = SourceFileLoader("csf_nlm_auth_cli", str(CLI_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_repairs_all_exact_accounts_without_exposing_credentials(monkeypatch, capsys):
    cli = _load_cli()
    calls = []

    def fake_ensure(profile, *, worker_id, timeout_s, **kwargs):
        calls.append((profile, worker_id, timeout_s, kwargs))
        return SimpleNamespace(ok=True, reason="ok", storage_path=f"P:/{profile}.json")

    monkeypatch.setattr(cli, "ensure_account_session", fake_ensure)
    monkeypatch.setattr(sys, "argv", [str(CLI_PATH), "--all", "--timeout", "12"])

    assert cli.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert [item["profile"] for item in payload["accounts"]] == list(cli.ACCOUNT_PROFILES)
    assert calls == [
        (profile, "csf-nlm-auth", 12.0, {"interactive_bootstrap": True})
        for profile in cli.ACCOUNT_PROFILES
    ]
    assert "token" not in output.lower()


def test_cli_passes_cdp_url_only_for_one_profile(monkeypatch, capsys):
    cli = _load_cli()
    calls = []

    def fake_ensure(profile, **kwargs):
        calls.append((profile, kwargs))
        return SimpleNamespace(ok=True, reason="ok", storage_path=f"P:/{profile}.json")

    monkeypatch.setattr(cli, "ensure_account_session", fake_ensure)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CLI_PATH),
            "--profile",
            "a.hominidae",
            "--cdp-url",
            "http://127.0.0.1:9222",
        ],
    )

    assert cli.main() == 0
    capsys.readouterr()
    assert calls == [
        (
            "a.hominidae",
            {
                "worker_id": "csf-nlm-auth",
                "timeout_s": 180.0,
                "cdp_url": "http://127.0.0.1:9222",
                "interactive_bootstrap": True,
            },
        )
    ]


def test_cli_rejects_shared_cdp_for_all_accounts(monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(CLI_PATH), "--all", "--cdp-url", "http://127.0.0.1:9222"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
