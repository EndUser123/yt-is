from __future__ import annotations

import os
from pathlib import Path

import pytest

from csf.paths import load_workspace_env


def test_load_workspace_env_sets_missing_vars_and_respects_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n"
        "YTIS_TEST_NEW_VAR=alpha\n"
        "YTIS_TEST_EXISTING=file-value\n"
        "export YTIS_TEST_EXPORTED=beta\n"
        "NOT_A_VALID-KEY=skipped\n"
        "NO_EQUALS_LINE\n"
        "YTIS_TEST_EMPTY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YTIS_TEST_EXISTING", "shell-value")
    try:
        loaded = load_workspace_env(env_file)
        assert "YTIS_TEST_NEW_VAR" in loaded
        assert "YTIS_TEST_EXPORTED" in loaded
        assert os.environ["YTIS_TEST_NEW_VAR"] == "alpha"
        assert os.environ["YTIS_TEST_EXPORTED"] == "beta"
        # Existing shell values win; empty values are not set.
        assert os.environ["YTIS_TEST_EXISTING"] == "shell-value"
        assert "YTIS_TEST_EXISTING" not in loaded
        assert "YTIS_TEST_EMPTY" not in os.environ
        assert "NOT_A_VALID-KEY=skipped" not in str(loaded)
    finally:
        # load_workspace_env writes os.environ directly; undo it for later tests.
        for name in ("YTIS_TEST_NEW_VAR", "YTIS_TEST_EXPORTED"):
            os.environ.pop(name, None)


def test_load_workspace_env_missing_file_is_noop(tmp_path):
    assert load_workspace_env(tmp_path / "absent.env") == []


def test_parse_cookies_from_browser_spec():
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader

    loader = SourceFileLoader(
        "csf_source_cookie_spec_test", str(Path(__file__).resolve().parents[1] / "bin" / "csf-source")
    )
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._parse_cookies_from_browser("firefox") == ("firefox", None, None)
    assert module._parse_cookies_from_browser("firefox:default-release") == (
        "firefox",
        "default-release",
        None,
    )
    assert module._parse_cookies_from_browser("chrome+BASICTEXT") == ("chrome", None, "BASICTEXT")
    with pytest.raises(ValueError):
        module._parse_cookies_from_browser(":profile-only")


def test_refresh_script_browser_spec_parser():
    import scripts.refresh_youtube_cookies as mod

    assert mod._parse_browser_spec("firefox") == ("firefox", None, None)
    assert mod._parse_browser_spec("firefox:default-release") == ("firefox", "default-release", None)
    with pytest.raises(ValueError):
        mod._parse_browser_spec(":x")
