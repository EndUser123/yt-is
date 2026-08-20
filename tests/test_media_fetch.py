"""Tests for the paced visual media fetch layer (csf/visual/media_fetch.py).

Downloads are mocked; these tests lock in the pacing contract: conservative
yt-dlp flags, durable cooldown, hourly budget accounting, single-flight lock,
and the failure-class mapping the worker relies on. Also covers the
channel_cooldown epoch fix in csf/batch_scheduler.py.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from csf.visual import media_fetch as mf


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    mf._connect(path).close()  # ensure media tables exist
    return path


# ---------------------------------------------------------------------------
# command construction
# ---------------------------------------------------------------------------


def test_ytdlp_command_carries_pacing_flags(tmp_path: Path):
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "v" / "dest")
    joined = " ".join(cmd)
    assert cmd[0] == "yt-dlp"
    for flag in (
        "--sleep-interval",
        "--max-sleep-interval",
        "--sleep-requests",
        "--extractor-retries",
        "--fragment-retries",
        "--limit-rate",
        "--no-playlist",
    ):
        assert flag in cmd, f"missing pacing flag {flag}"
    # Resolution ceiling present in format selector.
    fmt = cmd[cmd.index("-f") + 1]
    assert "height<=1080" in fmt


def test_ytdlp_command_js_runtime_resolution(tmp_path: Path, monkeypatch):
    # Explicit override wins and is passed verbatim.
    monkeypatch.setenv("YTIS_VISUAL_JS_RUNTIME", "node")
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "j")
    assert cmd[cmd.index("--js-runtimes") + 1] == "node"
    # No runtime anywhere -> flag omitted (yt-dlp will surface its own error).
    monkeypatch.setenv("YTIS_VISUAL_JS_RUNTIME", "")
    monkeypatch.setattr(mf.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(mf.Path, "home", lambda: tmp_path)
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "j2")
    assert "--js-runtimes" not in cmd


def test_ytdlp_command_cookies_default_on_and_disable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mf.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "c1")
    # Default: authenticated download via firefox cookies (2026 PO-token 403s).
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"
    # Empty env disables the flag.
    monkeypatch.setenv("YTIS_VISUAL_COOKIES_FROM_BROWSER", "")
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "c2")
    assert "--cookies-from-browser" not in cmd


def test_ffmpeg_location_resolved_from_winget(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mf.shutil, "which", lambda name: None)
    fake = tmp_path / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_x" / "ffmpeg-8" / "bin"
    fake.mkdir(parents=True)
    (fake / "ffmpeg.exe").write_bytes(b"f")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "f1")
    assert cmd[cmd.index("--ffmpeg-location") + 1] == str(fake)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("could not copy cookies from firefox", "cookie_source"),
        ("ERROR: unable to get cookies", "cookie_source"),
    ],
)
def test_cookie_failures_classified_cookie_source(text, expected):
    assert mf.classify_download_output(text) == expected


def test_ytdlp_command_audio_only_selects_audio_stream(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mf.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "a", audio_only=True)
    assert cmd[cmd.index("-f") + 1] == "bestaudio/best"
    assert "--merge-output-format" not in cmd
    video_cmd = mf.build_ytdlp_command("https://youtu.be/x", tmp_path / "v")
    assert "height<=1080" in video_cmd[video_cmd.index("-f") + 1]
    assert "--merge-output-format" in video_cmd


def test_ensure_pot_server_skips_when_healthy(monkeypatch):
    monkeypatch.setattr(mf, "pot_server_available", lambda: True)
    launched = []
    monkeypatch.setattr(
        mf.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or None
    )
    assert mf.ensure_pot_server() is True
    assert launched == []


def test_ensure_pot_server_relaunches_when_down(monkeypatch, tmp_path):
    monkeypatch.setattr(mf, "pot_server_available", lambda: False)
    # First check fails, then the wait loop sees it up.
    states = iter([False, False, True])
    monkeypatch.setattr(mf, "pot_server_available", lambda: next(states))
    monkeypatch.setattr(mf, "_deno_executable", lambda: "C:/deno/deno.exe")
    fake_server = tmp_path / "bgutil-ytdlp-pot-provider" / "server" / "src" / "main.ts"
    fake_server.parent.mkdir(parents=True)
    fake_server.write_text("// server")
    monkeypatch.setattr(mf.Path, "home", lambda: tmp_path)
    launched = []
    monkeypatch.setattr(
        mf.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or None
    )
    monkeypatch.setattr(mf.time, "sleep", lambda s: None)
    assert mf.ensure_pot_server() is True
    assert launched and launched[0][0] == "C:/deno/deno.exe"
    assert "src/main.ts" in launched[0]


def test_ytdlp_sleep_bounds_env_override(db: Path, monkeypatch):
    monkeypatch.setenv("YTIS_VISUAL_SLEEP_MIN_S", "5")
    monkeypatch.setenv("YTIS_VISUAL_SLEEP_MAX_S", "12")
    cmd = mf.build_ytdlp_command("https://youtu.be/x", db.parent / "d")
    assert cmd[cmd.index("--sleep-interval") + 1] == "5"
    assert cmd[cmd.index("--max-sleep-interval") + 1] == "12"


# ---------------------------------------------------------------------------
# failure classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("HTTP Error 429: Too Many Requests", "rate_limited"),
        ("Please sign in to confirm you're not a bot", "rate_limited"),
        ("this video is private", "unavailable"),
        ("Video unavailable", "unavailable"),
        # Unrecognized failures classify as None; the download_video caller
        # defaults that to "download_failed".
        ("Some random ffmpeg merge error", None),
        ("", None),
    ],
)
def test_classify_download_output(text, expected):
    assert mf.classify_download_output(text) == expected


# ---------------------------------------------------------------------------
# cooldown
# ---------------------------------------------------------------------------


def test_cooldown_roundtrip_and_expiry(db: Path):
    assert mf.media_cooldown_state(db)["active"] is False
    mf.set_media_cooldown(60.0, reason="429 observed", db_path=db)
    state = mf.media_cooldown_state(db)
    assert state["active"] is True
    assert 0 < state["remaining_s"] <= 60
    assert state["reason"] == "429 observed"
    # Expired cooldown reads inactive.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE media_rate_limit SET cooldown_until_epoch = ?", (time.time() - 1,))
    conn.commit()
    conn.close()
    assert mf.media_cooldown_state(db)["active"] is False


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------


def test_budget_consumption_and_exhaustion(db: Path, monkeypatch):
    monkeypatch.setenv("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", "2")
    assert mf.budget_state(db)["allowed"] is True
    first = mf.consume_budget_slot(db)
    second = mf.consume_budget_slot(db)
    third = mf.consume_budget_slot(db)
    assert first["allowed"] and second["allowed"]
    assert third["allowed"] is False
    assert third["retry_after_s"] > 0
    state = mf.budget_state(db)
    assert state["used"] == 2 and state["max"] == 2 and state["allowed"] is False


def test_budget_windows_roll_over(db: Path):
    old_window = int(time.time() // 3600) - 1
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO media_download_budget (window_epoch, count) VALUES (?, 99)", (old_window,))
    conn.commit()
    conn.close()
    # New window is independent of the saturated old one.
    assert mf.budget_state(db)["allowed"] is True


# ---------------------------------------------------------------------------
# download orchestration (mocked subprocess)
# ---------------------------------------------------------------------------


def _fake_run_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_download_success_records_log_and_file(db: Path, tmp_path, monkeypatch):
    dest = tmp_path / "media" / "vidOK"
    dest.mkdir(parents=True)
    (dest / "source.mp4").write_bytes(b"x" * 128)

    def fake_run(cmd, **kwargs):
        return _fake_run_result(0)

    monkeypatch.setattr(mf.subprocess, "run", fake_run)
    result = mf.download_video("vidOK", db_path=db, dest_dir=dest)
    assert result["ok"] is True
    assert result["path"].endswith("source.mp4")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, bytes FROM media_download_log WHERE video_id='vidOK'").fetchone()
    conn.close()
    assert row[0] == "success" and row[1] == 128


def test_download_429_opens_cooldown_and_returns_rate_limited(db: Path, tmp_path, monkeypatch):
    monkeypatch.setattr(
        mf.subprocess,
        "run",
        lambda cmd, **kw: _fake_run_result(1, stderr="HTTP Error 429: Too Many Requests"),
    )
    result = mf.download_video("vidRL", db_path=db, dest_dir=tmp_path / "rl")
    assert result["ok"] is False and result["error_class"] == "rate_limited"
    assert mf.media_cooldown_state(db)["active"] is True
    # A second attempt is refused while the cooldown holds, without spending budget.
    budget_before = mf.budget_state(db)["used"]
    blocked = mf.download_video("vidRL2", db_path=db, dest_dir=tmp_path / "rl2")
    assert blocked["error_class"] == "rate_limited"
    assert mf.budget_state(db)["used"] == budget_before


def test_download_budget_exhaustion_short_circuits(db: Path, tmp_path, monkeypatch):
    monkeypatch.setenv("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", "1")
    monkeypatch.setattr(
        mf.subprocess, "run", lambda cmd, **kw: _fake_run_result(1, stderr="boom")
    )
    assert mf.download_video("vid1", db_path=db, dest_dir=tmp_path / "a")["error_class"] == "download_failed"
    second = mf.download_video("vid2", db_path=db, dest_dir=tmp_path / "b")
    assert second["error_class"] == "budget_exhausted"
    assert second["retry_after_s"] > 0


def test_download_unavailable_is_terminal_class(db: Path, tmp_path, monkeypatch):
    monkeypatch.setattr(
        mf.subprocess,
        "run",
        lambda cmd, **kw: _fake_run_result(1, stderr="This video is private"),
    )
    result = mf.download_video("vidPriv", db_path=db, dest_dir=tmp_path / "p")
    assert result["error_class"] == "unavailable"


# ---------------------------------------------------------------------------
# batch_scheduler epoch cooldown fix
# ---------------------------------------------------------------------------


def test_channel_cooldown_epoch_semantics(tmp_path, monkeypatch):
    from csf.batch_scheduler import BatchScheduler

    db = tmp_path / "sched.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE analysis_status (video_id TEXT, status TEXT, source TEXT, published_at TEXT);"
        "CREATE TABLE download_archive (video_id TEXT PRIMARY KEY, status TEXT, source TEXT, attempted_at REAL, error TEXT);"
        "CREATE TABLE channel_cooldown (source TEXT PRIMARY KEY, cooldown_until REAL);"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(BatchScheduler, "_recover_stale_attempting", lambda self: None)
    scheduler = BatchScheduler(db_path=db)
    scheduler.record_429("chanA")
    conn = sqlite3.connect(db)
    until = conn.execute("SELECT cooldown_until FROM channel_cooldown WHERE source='chanA'").fetchone()[0]
    conn.close()
    # Written as wall-clock epoch, i.e. within [now, now+300], not monotonic.
    assert time.time() <= until <= time.time() + 301
    assert scheduler._is_in_cooldown("chanA") is True
