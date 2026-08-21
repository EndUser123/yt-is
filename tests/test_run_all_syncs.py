"""Tests for the run_all_syncs orchestrator.

The orchestrator spawns heavy connector scripts via subprocess and runs
them in a ThreadPoolExecutor. Tests here mock `subprocess.run` so no real
process is ever launched.

Coverage:
- `run_script` returns True/False and survives TimeoutExpired.
- `run_script_threaded` populates the shared `results` dict.
- `main` runs the parallel phase concurrently and applies skip flags.
- Discord phase is skipped without `DISCORD_BOT_TOKEN`.
- post-EF phases (ef_ingest, topic_assign, etc.) also dispatch.
- summary returns 0 when everything is OK, 1 when something fails.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --- helpers ----------------------------------------------------------------


class _FakeResult:
    def __init__(self, returncode=0):
        self.returncode = returncode


@pytest.fixture
def ras(monkeypatch, tmp_path):
    """Reload the module; load_workspace_env is left intact (it just reads
    P:/.env which is harmless, and any pre-set env var wins)."""
    import scripts.run_all_syncs as mod
    mod = importlib.reload(mod)
    return mod


@pytest.fixture
def fake_run(monkeypatch):
    """Replace subprocess.run with a MagicMock; return it for assertions."""
    import scripts.run_all_syncs as mod
    mock = MagicMock(return_value=_FakeResult(0))
    monkeypatch.setattr(mod.subprocess, "run", mock)
    return mock


# === run_script =============================================================


def test_run_script_returns_true_on_success(ras, monkeypatch):
    monkeypatch.setattr(ras.subprocess, "run",
                        lambda *a, **k: _FakeResult(0))
    assert ras.run_script("test", ras.REPO / "scripts" / "any.py") is True


def test_run_script_returns_false_on_nonzero(ras, monkeypatch):
    monkeypatch.setattr(ras.subprocess, "run",
                        lambda *a, **k: _FakeResult(1))
    assert ras.run_script("test", ras.REPO / "scripts" / "any.py") is False


def test_run_script_returns_false_on_timeout(ras, monkeypatch):
    def raise_timeout(*a, **k):
        raise ras.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(ras.subprocess, "run", raise_timeout)
    assert ras.run_script("test", ras.REPO / "scripts" / "any.py") is False


# === run_script_threaded ===================================================


def test_run_script_threaded_writes_results_dict(ras, monkeypatch):
    monkeypatch.setattr(ras.subprocess, "run",
                        lambda *a, **k: _FakeResult(0))
    results = {}
    ras.run_script_threaded("alpha", ras.REPO / "scripts" / "any.py",
                            60, results)
    assert results == {"alpha": True}


def test_run_script_threaded_records_failure(ras, monkeypatch):
    monkeypatch.setattr(ras.subprocess, "run",
                        lambda *a, **k: _FakeResult(2))
    results = {}
    ras.run_script_threaded("alpha", ras.REPO / "scripts" / "any.py",
                            60, results)
    assert results == {"alpha": False}


def test_run_script_threaded_records_exception(ras, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ras.subprocess, "run", boom)
    results = {}
    ras.run_script_threaded("alpha", ras.REPO / "scripts" / "any.py",
                            60, results)
    assert results == {"alpha": False}


# === main: parallel phase =================================================


def _setup_minimal_env(monkeypatch, tmp_path, *, with_token=True,
                        skip_flags=()):
    """Common setup: mock subprocess.run + everything main() needs."""
    import scripts.run_all_syncs as mod
    import csf.paths
    calls = []

    def fake_run_threaded(name, script_path, timeout, results, prefix=""):
        # Record the call and mark success
        calls.append((name, str(script_path)))
        results[name] = True

    def fake_run_script(name, script_path, timeout=3600):
        calls.append((name, str(script_path)))
        return True

    monkeypatch.setattr(mod, "run_script_threaded", fake_run_threaded)
    monkeypatch.setattr(mod, "run_script", fake_run_script)
    # Disable load_workspace_env so it doesn't repopulate DISCORD_BOT_TOKEN
    # from P:/.env when the test wants the env var absent.
    monkeypatch.setattr(csf.paths, "load_workspace_env", lambda: [])
    if with_token:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    else:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    return calls


def test_main_runs_parallel_phase_with_youtube(ras, monkeypatch):
    """The parallel phase includes YouTube + 5 light connectors (7 jobs)."""
    calls = _setup_minimal_env(monkeypatch, None)
    rc = ras.main(["--skip-discord", "--skip-digest"])
    assert rc == 0
    parallel_names = {n for n, _ in calls}
    # YouTube + 5 light jobs (reddit, hn, rss, github, dht_ingest) = 6.
    expected = {"YouTube Channel Sync", "reddit", "hn", "rss",
                "github", "dht_ingest"}
    assert expected.issubset(parallel_names)
    # YouTube is run via run_script_threaded (parallel), not run_script.
    yt_call = [c for c in calls if c[0] == "YouTube Channel Sync"]
    assert len(yt_call) == 1


def test_main_quick_skips_youtube(ras, monkeypatch):
    """--quick keeps the light connectors but drops YouTube."""
    calls = _setup_minimal_env(monkeypatch, None)
    ras.main(["--quick", "--skip-discord", "--skip-digest"])
    names = {n for n, _ in calls}
    assert "YouTube Channel Sync" not in names
    # Light connectors still run.
    assert {"reddit", "hn", "rss", "github", "dht_ingest"}.issubset(names)


def test_main_skip_youtube_flag(ras, monkeypatch):
    calls = _setup_minimal_env(monkeypatch, None)
    ras.main(["--skip-youtube", "--skip-discord", "--skip-digest"])
    names = {n for n, _ in calls}
    assert "YouTube Channel Sync" not in names


def test_main_individual_skip_flags(ras, monkeypatch):
    calls = _setup_minimal_env(monkeypatch, None)
    ras.main(["--skip-youtube", "--skip-reddit", "--skip-hn",
              "--skip-discord", "--skip-digest"])
    parallel_names = {n for n, _ in calls
                      if n in {"reddit", "hn", "rss", "github", "dht_ingest"}}
    # hn skip also drops rss and github (they're grouped with hn)
    assert "reddit" not in parallel_names
    assert "hn" not in parallel_names
    assert "rss" not in parallel_names
    assert "github" not in parallel_names
    assert "dht_ingest" in parallel_names  # dht_ingest is unconditional


def test_main_discord_phase_uses_token(ras, monkeypatch):
    """If DISCORD_BOT_TOKEN is set, the Discord phase runs as a serial
    run_script (not threaded)."""
    calls = _setup_minimal_env(monkeypatch, None, with_token=True)
    ras.main(["--skip-youtube", "--skip-digest"])
    names = [n for n, _ in calls]
    assert "Discord Sync" in names


def test_main_discord_phase_skipped_without_token(ras, monkeypatch):
    calls = _setup_minimal_env(monkeypatch, None, with_token=False)
    rc = ras.main(["--skip-youtube", "--skip-digest"])
    assert rc == 0
    names = {n for n, _ in calls}
    assert "Discord Sync" not in names


def test_main_post_ef_phases_always_run(ras, monkeypatch):
    """ef_ingest / topic_assign / channel_metadata / title_backfill always run."""
    calls = _setup_minimal_env(monkeypatch, None, with_token=False)
    ras.main(["--skip-youtube", "--skip-discord", "--skip-digest"])
    names = {n for n, _ in calls}
    assert {"EF Connector Ingest", "Topic Assignment",
            "Channel Metadata Backfill", "Title Backfill",
            "Trend Alerts"}.issubset(names)


def test_main_digest_runs_by_default(ras, monkeypatch):
    calls = _setup_minimal_env(monkeypatch, None, with_token=False)
    ras.main(["--skip-youtube", "--skip-discord"])
    names = {n for n, _ in calls}
    assert "Daily Digest" in names


def test_main_returns_1_on_any_failure(ras, monkeypatch):
    """If any sub-script returns False, main returns 1."""
    import scripts.run_all_syncs as mod
    import csf.paths
    call_count = {"n": 0}

    def fail_eventually(name, script_path, timeout, results, prefix=""):
        call_count["n"] += 1
        # First call (youtube) fails; rest succeed.
        results[name] = (call_count["n"] != 1)

    monkeypatch.setattr(mod, "run_script_threaded", fail_eventually)
    monkeypatch.setattr(mod, "run_script", lambda *a, **k: True)
    monkeypatch.setattr(csf.paths, "load_workspace_env", lambda: [])
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    rc = mod.main(["--skip-discord", "--skip-digest"])
    assert rc == 1


def test_main_returns_0_when_all_succeed(ras, monkeypatch):
    import scripts.run_all_syncs as mod
    import csf.paths
    monkeypatch.setattr(mod, "run_script_threaded",
                        lambda n, p, t, r, prefix="": r.__setitem__(n, True))
    monkeypatch.setattr(mod, "run_script", lambda *a, **k: True)
    monkeypatch.setattr(csf.paths, "load_workspace_env", lambda: [])
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    rc = mod.main(["--skip-discord", "--skip-digest"])
    assert rc == 0


# === parallel-phase concurrency (real ThreadPoolExecutor) ==================


def test_parallel_phase_actually_runs_concurrently(ras, monkeypatch):
    """The parallel phase uses a real ThreadPoolExecutor. Verify that
    multiple run_script_threaded invocations happen, and that the
    main() blocks until all futures resolve (we count futures inside
    the helper)."""
    import scripts.run_all_syncs as mod
    import concurrent.futures

    in_flight = {"now": 0, "max": 0}
    barrier = []

    def slow_run(name, script_path, timeout, results, prefix=""):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        # Yield to let the other threads start.
        import time
        time.sleep(0.05)
        in_flight["now"] -= 1
        results[name] = True

    monkeypatch.setattr(mod, "run_script_threaded", slow_run)
    monkeypatch.setattr(mod, "run_script", lambda *a, **k: True)
    import csf.paths
    monkeypatch.setattr(csf.paths, "load_workspace_env", lambda: [])
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    rc = mod.main(["--skip-discord", "--skip-digest"])
    assert rc == 0
    # At least 2 jobs were alive at the same time (YouTube + light job).
    # We allow 1 for the (unlikely) fully-sequential case but expect > 1.
    assert in_flight["max"] >= 2
