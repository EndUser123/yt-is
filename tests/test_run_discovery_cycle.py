from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_discovery_cycle as mod


def _settings(tmp_path: Path, **overrides: object) -> Path:
    base = {
        "cookies_browser": "firefox:test-profile",
        "auto_import": True,
        "min_watchlater_videos": 2,
        "min_history_videos": 2,
        "categorize_workers": 4,
        "excluded_categories": [],
        "run_sync": True,
    }
    base.update(overrides)
    path = tmp_path / "discovery-settings.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


def test_load_settings_missing_file_is_config_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod.load_settings(tmp_path / "absent.json")


def test_load_settings_rejects_missing_keys_and_bad_types(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"cookies_browser": "firefox:x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        mod.load_settings(path)
    path.write_text(
        json.dumps(
            {
                "cookies_browser": "firefox:x",
                "auto_import": True,
                "min_watchlater_videos": 2,
                "min_history_videos": 2,
                "categorize_workers": 4,
                "excluded_categories": "News",
                "run_sync": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="excluded_categories must be a list"):
        mod.load_settings(path)


def test_cycle_runs_steps_in_order_and_writes_receipts(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: list[str] = []

    def fake_run_step(name, cmd, run_dir):
        calls.append(name)
        (run_dir / f"{name}.stdout.log").write_text("out", encoding="utf-8")
        return {"step": name, "command": cmd, "returncode": 0, "ok": True}

    monkeypatch.setattr(mod, "_run_step", fake_run_step)
    monkeypatch.setattr(mod, "build_page_path", lambda: tmp_path / "review.html")
    settings = json.loads(_settings(tmp_path).read_text(encoding="utf-8"))
    ok, report = mod.run_cycle(settings, run_dir, open_review_page=False)
    assert ok is True
    assert calls == [
        "refresh_cookies",
        "watchlater_dryrun",
        "watchlater_import",
        "history_dryrun",
        "history_import",
        "categorize",
        "sync",
        "build_review_page",
    ]
    assert any(s.get("step") == "promote_excluded" and s.get("skipped") for s in report["steps"])


def test_cycle_skips_import_and_sync_when_configured(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: list[str] = []

    def fake_run_step(name, cmd, run_dir):
        calls.append(name)
        return {"step": name, "command": cmd, "returncode": 0, "ok": True}

    monkeypatch.setattr(mod, "_run_step", fake_run_step)
    monkeypatch.setattr(mod, "build_page_path", lambda: tmp_path / "review.html")
    settings = json.loads(
        _settings(tmp_path, auto_import=False, run_sync=False).read_text(encoding="utf-8")
    )
    ok, report = mod.run_cycle(settings, run_dir, open_review_page=False)
    assert ok is True
    assert "watchlater_import" not in calls and "history_import" not in calls
    assert "sync" not in calls
    assert "categorize" in calls
    assert "build_review_page" in calls  # page still built for scheduled runs


def test_cycle_promotes_excluded_categories(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    commands: list[list[str]] = []

    def fake_run_step(name, cmd, run_dir):
        commands.append(cmd)
        return {"step": name, "command": cmd, "returncode": 0, "ok": True}

    monkeypatch.setattr(mod, "_run_step", fake_run_step)
    settings = json.loads(
        _settings(tmp_path, excluded_categories=["News", "Entertainment"]).read_text(
            encoding="utf-8"
        )
    )
    ok, report = mod.run_cycle(settings, run_dir)
    assert ok is True
    promote_cmds = [c for c in commands if "promote_excluded_categories.py" in " ".join(c)]
    assert len(promote_cmds) == 1
    assert promote_cmds[0][promote_cmds[0].index("--exclude") + 1] == "News,Entertainment"
    assert "--apply" in promote_cmds[0]


def test_cycle_fails_closed_on_unknown_excluded_category(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(
        mod,
        "_run_step",
        lambda name, cmd, run_dir: {"step": name, "command": cmd, "returncode": 0, "ok": True},
    )
    settings = json.loads(
        _settings(tmp_path, excluded_categories=["Nws"]).read_text(encoding="utf-8")
    )
    ok, report = mod.run_cycle(settings, run_dir)
    assert ok is False
    assert "unknown categories" in report["error"]


def test_cycle_stops_discovery_import_when_dryrun_fails(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: list[str] = []

    def fake_run_step(name, cmd, run_dir):
        calls.append(name)
        ok = name != "watchlater_dryrun"
        return {"step": name, "command": cmd, "returncode": 0 if ok else 1, "ok": ok}

    monkeypatch.setattr(mod, "_run_step", fake_run_step)
    settings = json.loads(_settings(tmp_path).read_text(encoding="utf-8"))
    ok, _ = mod.run_cycle(settings, run_dir)
    assert ok is False
    # Failed dry-run: no blind import of watchlater, later steps still attempted
    # only if their own inputs succeeded — categorize is skipped because a
    # prior step failed.
    assert "watchlater_import" not in calls
    assert "categorize" not in calls
    assert "history_dryrun" in calls


def test_main_rejects_missing_settings(tmp_path, capsys):
    code = mod.main(["--settings", str(tmp_path / "absent.json")])
    assert code == 2
    assert "discovery-settings.example.json" in capsys.readouterr().err
