"""Paced video downloads for the visual pipeline.

Rate-limit avoidance is the first-priority design constraint:

- one download machine-wide at any moment (cross-process fasteners lock);
- conservative yt-dlp pacing: 20-90 s per-request sleeps, ``--sleep-requests``,
  extractor/fragment retry caps, bandwidth limit, 1080p height ceiling;
- a durable hourly download budget (default 30/hour, env-tunable);
- a wall-clock cooldown tripped by 429 / bot-check signals, persisted in the
  batch DB so every worker process respects it.

This module deliberately does NOT write ``download_archive``: that table feeds
legacy transcript selection (a success row makes BatchScheduler skip the video
for transcripts), so a visual download must never suppress transcript
eligibility. The visual-owned ledger is ``media_download_log``.

Tables created here are idempotent and live in the batch status DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Iterator

import fasteners

from csf.batch_status import _get_batch_status_storage

# -- pacing knobs (all env-overridable) --------------------------------------

DEFAULT_SLEEP_MIN_S = 20.0
DEFAULT_SLEEP_MAX_S = 90.0
DEFAULT_SLEEP_REQUESTS_S = 2.0
DEFAULT_LIMIT_RATE = "8M"
DEFAULT_MAX_DOWNLOADS_PER_HOUR = 30
DEFAULT_DOWNLOAD_TIMEOUT_S = 900.0
DEFAULT_COOLDOWN_S = 300.0
DEFAULT_MAX_HEIGHT = 1080

_ENV = {
    "sleep_min_s": "YTIS_VISUAL_SLEEP_MIN_S",
    "sleep_max_s": "YTIS_VISUAL_SLEEP_MAX_S",
    "sleep_requests_s": "YTIS_VISUAL_SLEEP_REQUESTS_S",
    "limit_rate": "YTIS_VISUAL_LIMIT_RATE",
    "max_downloads_per_hour": "YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR",
    "download_timeout_s": "YTIS_VISUAL_DOWNLOAD_TIMEOUT_S",
    "cooldown_s": "YTIS_VISUAL_COOLDOWN_S",
    "max_height": "YTIS_VISUAL_MAX_HEIGHT",
    "media_root": "YTIS_VISUAL_MEDIA_ROOT",
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class MediaBudgetExhausted(RuntimeError):
    """The hourly download budget is spent."""


class MediaRateLimited(RuntimeError):
    """A durable rate-limit cooldown is active."""


def media_root(db_path: Path | None = None) -> Path:
    override = os.environ.get(_ENV["media_root"], "").strip()
    if override:
        return Path(override)
    base = Path(db_path) if db_path else _get_batch_status_storage()._db_path
    return Path(base).parent / "visual"


def _ensure_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_rate_limit (
            kind TEXT PRIMARY KEY,
            cooldown_until_epoch REAL NOT NULL,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS media_download_budget (
            window_epoch INTEGER PRIMARY KEY,
            count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS media_download_log (
            video_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            error TEXT,
            path TEXT,
            bytes INTEGER
        );
        """
    )
    conn.commit()


_tables_ensured_paths: set[str] = set()


def _connect(db_path: Path | None = None):
    import sqlite3

    if db_path is None:
        db_path = _get_batch_status_storage()._db_path
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # DDL once per db path per process (review F-8): CREATE TABLE IF NOT
    # EXISTS x3 on every call was pure churn for the worker's per-job cycle.
    path_key = str(Path(db_path).resolve())
    if path_key not in _tables_ensured_paths:
        _ensure_tables(conn)
        _tables_ensured_paths.add(path_key)
    return conn


@contextmanager
def media_download_lock(
    db_path: Path | None = None, *, timeout_s: float | None = None
) -> Iterator[None]:
    """Serialize video downloads across all processes on this machine."""
    lock = acquire_media_download_lock(db_path, timeout_s=timeout_s)
    if lock is None:
        raise TimeoutError("visual media download lock could not be acquired")
    try:
        yield
    finally:
        lock.release()


def acquire_media_download_lock(
    db_path: Path | None = None, *, timeout_s: float | None = None
):
    """Acquire the machine-wide download lock, or return None if not gotten.

    Manual-acquire variant for call sites that cannot re-indent an existing
    block under a ``with`` (e.g. the transcript Whisper path). The caller is
    responsible for calling ``lock.release()`` on every path.
    """
    base = Path(db_path) if db_path else _get_batch_status_storage()._db_path
    lock_dir = Path(base).parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = fasteners.InterProcessLock(str(lock_dir / "visual_media_download.lock"))
    if timeout_s is None:
        timeout_s = _env_float(_ENV["download_timeout_s"], DEFAULT_DOWNLOAD_TIMEOUT_S) * 2
    acquired = lock.acquire(blocking=True, timeout=timeout_s)
    return lock if acquired else None


# -- rate-limit detection ------------------------------------------------------

_RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "sign in to confirm",       # YouTube bot-check framing
    "confirm you're not a bot",
    "no longer available due to a copyright claim",
)
_RATE_LIMIT_RE = re.compile("|".join(re.escape(m) for m in _RATE_LIMIT_MARKERS), re.IGNORECASE)

_COOKIE_MARKERS = (
    "could not copy cookies",
    "unable to get cookies",
    "could not find cookies",
    "failed to read cookies",
    "cookies.sqlite",
)

_UNAVAILABLE_MARKERS = (
    "private video",
    "is private",
    "video unavailable",
    "this video is not available",
    "has been removed",
    "does not exist",
    "404",
    "members-only",
)


def classify_download_output(combined: str) -> str | None:
    """Map yt-dlp output to a coarse failure class, or None on success.

    Cookie failures classify as ``cookie_source`` (our-side config expiry),
    never ``unavailable`` — an expired cookie jar must not terminalize or
    negative-cache videos.
    """
    if not combined:
        return None
    lowered = combined.lower()
    if _RATE_LIMIT_RE.search(combined):
        return "rate_limited"
    for marker in _COOKIE_MARKERS:
        if marker in lowered:
            return "cookie_source"
    for marker in _UNAVAILABLE_MARKERS:
        if marker in lowered:
            return "unavailable"
    return None


def media_cooldown_state(db_path: Path | None = None, *, kind: str = "visual_media") -> dict:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT cooldown_until_epoch, reason FROM media_rate_limit WHERE kind = ?",
            (kind,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"active": False, "remaining_s": 0.0, "reason": None}
    remaining = max(0.0, float(row[0]) - time.time())
    return {"active": remaining > 0, "remaining_s": remaining, "reason": row[1]}


def set_media_cooldown(
    seconds: float | None = None, *, reason: str, db_path: Path | None = None,
    kind: str = "visual_media",
) -> float:
    """Open (or extend) the durable rate-limit cooldown; returns epoch end."""
    if seconds is None:
        seconds = _env_float(_ENV["cooldown_s"], DEFAULT_COOLDOWN_S)
    until = time.time() + max(0.0, seconds)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO media_rate_limit (kind, cooldown_until_epoch, reason) "
            "VALUES (?, ?, ?)",
            (kind, until, reason),
        )
        conn.commit()
    finally:
        conn.close()
    return until


def budget_state(db_path: Path | None = None, *, now_epoch: float | None = None) -> dict:
    """Read-only hourly budget state (does not consume)."""
    now = now_epoch if now_epoch is not None else time.time()
    window = int(now // 3600)
    max_downloads = _env_int(_ENV["max_downloads_per_hour"], DEFAULT_MAX_DOWNLOADS_PER_HOUR)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT count FROM media_download_budget WHERE window_epoch = ?", (window,)
        ).fetchone()
    finally:
        conn.close()
    used = int(row[0]) if row else 0
    next_window_epoch = (window + 1) * 3600
    return {
        "window_epoch": window,
        "used": used,
        "max": max_downloads,
        "allowed": used < max_downloads,
        "retry_after_s": max(0.0, next_window_epoch - now),
    }


def consume_budget_slot(db_path: Path | None = None, *, now_epoch: float | None = None) -> dict:
    """Consume one download slot in the current hourly window.

    Must be called while holding the media download lock so the check+increment
    is race-free even across worker processes.
    """
    now = now_epoch if now_epoch is not None else time.time()
    window = int(now // 3600)
    max_downloads = _env_int(_ENV["max_downloads_per_hour"], DEFAULT_MAX_DOWNLOADS_PER_HOUR)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT count FROM media_download_budget WHERE window_epoch = ?", (window,)
        ).fetchone()
        used = int(row[0]) if row else 0
        if used >= max_downloads:
            conn.commit()
            return {
                "allowed": False,
                "used": used,
                "max": max_downloads,
                "retry_after_s": max(0.0, (window + 1) * 3600 - now),
            }
        conn.execute(
            "INSERT INTO media_download_budget (window_epoch, count) VALUES (?, 1) "
            "ON CONFLICT(window_epoch) DO UPDATE SET count = count + 1",
            (window,),
        )
        conn.commit()
        return {"allowed": True, "used": used + 1, "max": max_downloads, "retry_after_s": 0.0}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_POT_SERVER_PING = "http://127.0.0.1:4416/ping"


def pot_server_available() -> bool:
    """Is the local bgutil PO-token server answering?"""
    import urllib.request

    try:
        with urllib.request.urlopen(_POT_SERVER_PING, timeout=2.0) as response:
            return response.status == 200
    except Exception:
        return False


def _deno_executable() -> str | None:
    found = shutil.which("deno")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "deno.exe"
    return str(candidate) if candidate.exists() else None


def ensure_pot_server() -> bool:
    """Ensure the local PO-token server is up; relaunch detached if down.

    The server is the primary defense against YouTube's PO-token enforcement
    (anonymous downloads work with locally generated tokens, verified
    2026-08-19). If it is down, downloads still work via the firefox-cookie
    fallback, so a failed relaunch is degraded-but-functional, not fatal.
    """
    if pot_server_available():
        return True
    deno = _deno_executable()
    server_dir = Path.home() / "bgutil-ytdlp-pot-provider" / "server"
    if deno is None or not (server_dir / "src" / "main.ts").exists():
        return False
    subprocess.Popen(
        [
            deno, "run",
            "--allow-env", "--allow-net", "--allow-ffi=.", "--allow-read=.",
            "src/main.ts",
        ],
        cwd=str(server_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,  # CREATE_NO_WINDOW: hidden console inherited by grandchildren
    )
    for _ in range(15):
        time.sleep(1.0)
        if pot_server_available():
            return True
    return False


def resolve_js_runtime() -> str | None:
    """Resolve the JS runtime yt-dlp needs for YouTube extraction (2026.7+).

    yt-dlp deprecated JS-runtime-less YouTube extraction; deno is its default.
    Resolution order: explicit ``YTIS_VISUAL_JS_RUNTIME`` (e.g. ``deno`` or
    ``deno:C:/path/deno.exe``), then PATH, then the winget install locations.
    """
    override = os.environ.get("YTIS_VISUAL_JS_RUNTIME", "").strip()
    if override:
        return override
    found = shutil.which("deno")
    if found:
        return f"deno:{found}"
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft" / "WinGet" / "Links" / "deno.exe",
        Path.home() / ".deno" / "bin" / "deno.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return f"deno:{candidate}"
    return None


def resolve_ffmpeg_location() -> str | None:
    """Directory containing ffmpeg for yt-dlp's merger (PATH or WinGet Gyan).

    ffmpeg is winget-installed on this host but often not on the agent-shell
    PATH; without an explicit location yt-dlp silently skips format merging.
    """
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for candidate in Path(local).glob(
            "Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg*/bin"
        ):
            if (candidate / "ffmpeg.exe").exists():
                return str(candidate)
    return None


def build_ytdlp_command(video_url: str, dest_dir: Path, *, audio_only: bool = False) -> list[str]:
    """Construct the paced yt-dlp download command (pure; unit-tested).

    ``audio_only`` fetches just the audio stream (transcript-recovery jobs:
    the need is the audio, not the video — no frames, ~10x less bandwidth).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    max_height = _env_int(_ENV["max_height"], DEFAULT_MAX_HEIGHT)

    def _flag(name: str, default: float) -> str:
        return f"{_env_float(name, default):g}"

    if audio_only:
        format_selector = "bestaudio/best"
        merge_args: list[str] = []
    else:
        format_selector = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
        merge_args = ["--merge-output-format", "mp4"]
    command = [
        "yt-dlp",
        video_url,
        "-o", str(dest_dir / "source.%(ext)s"),
        "-f", format_selector,
        *merge_args,
        "--sleep-interval", _flag(_ENV["sleep_min_s"], DEFAULT_SLEEP_MIN_S),
        "--max-sleep-interval", _flag(_ENV["sleep_max_s"], DEFAULT_SLEEP_MAX_S),
        "--sleep-requests", _flag(_ENV["sleep_requests_s"], DEFAULT_SLEEP_REQUESTS_S),
        "--extractor-retries", "5",
        "--fragment-retries", "10",
        "--limit-rate", os.environ.get(_ENV["limit_rate"], "").strip() or DEFAULT_LIMIT_RATE,
        "--no-playlist",
        "--no-progress",
    ]
    js_runtime = resolve_js_runtime()
    if js_runtime:
        command.extend(["--js-runtimes", js_runtime])
    ffmpeg_dir = resolve_ffmpeg_location()
    if ffmpeg_dir:
        command.extend(["--ffmpeg-location", ffmpeg_dir])
    # Authenticated downloads: the 2026 PO-token enforcement 403s anonymous
    # media URLs. PO tokens are now generated locally (bgutil server), with
    # the firefox-cookie path as fallback. Set the env var empty to disable.
    cookies_browser = os.environ.get(
        "YTIS_VISUAL_COOKIES_FROM_BROWSER", "firefox"
    ).strip()
    if cookies_browser:
        command.extend(["--cookies-from-browser", cookies_browser])
    return command


def _log_download(db_path, video_id: str, status: str, *, error=None, path=None, size_bytes=None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO media_download_log "
            "(video_id, status, attempted_at, error, path, bytes) VALUES (?, ?, ?, ?, ?, ?)",
            (
                video_id,
                status,
                datetime.now(timezone.utc).isoformat(),
                str(error)[:2000] if error else None,
                str(path) if path else None,
                int(size_bytes) if size_bytes is not None else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def download_video(
    video_id: str,
    *,
    db_path: Path | None = None,
    dest_dir: Path | None = None,
    audio_only: bool = False,
) -> dict:
    """Download one video (or audio-only stream) under full pacing, budget,
    and cooldown control.

    Returns a result dict; ``ok`` distinguishes success. Error classes:
    ``rate_limited`` (cooldown opened), ``budget_exhausted`` (wait or exit),
    ``unavailable`` (terminal; caller writes the negative cache),
    ``download_failed`` (retryable harness/download error), ``timeout``.
    """
    db_path = Path(db_path) if db_path else _get_batch_status_storage()._db_path
    target_dir = Path(dest_dir) if dest_dir else media_root(db_path) / video_id
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Best effort: keep the PO-token server alive; cookie fallback covers
    # the degraded case if this returns False.
    ensure_pot_server()

    with media_download_lock(db_path):
        cooldown = media_cooldown_state(db_path)
        if cooldown["active"]:
            return {
                "ok": False, "error_class": "rate_limited",
                "retry_after_s": cooldown["remaining_s"], "reason": cooldown["reason"],
            }
        budget = consume_budget_slot(db_path)
        if not budget["allowed"]:
            return {
                "ok": False, "error_class": "budget_exhausted",
                "retry_after_s": budget["retry_after_s"],
                "budget": {"used": budget["used"], "max": budget["max"]},
            }
        command = build_ytdlp_command(url, target_dir, audio_only=audio_only)
        timeout_s = _env_float(_ENV["download_timeout_s"], DEFAULT_DOWNLOAD_TIMEOUT_S)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            _log_download(db_path, video_id, "failed", error="timeout")
            return {
                "ok": False, "error_class": "timeout",
                "retry_after_s": 60.0, "elapsed_s": time.monotonic() - started,
            }
        elapsed_s = time.monotonic() - started
        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if proc.returncode == 0:
            files = sorted(
                p for p in target_dir.glob("source.*")
                if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".m4a", ".mp3"}
            )
            if not files:
                _log_download(db_path, video_id, "failed", error="no output file")
                return {"ok": False, "error_class": "download_failed",
                        "error": "yt-dlp exited 0 but no source.* file found",
                        "elapsed_s": elapsed_s}
            video_file = max(files, key=lambda p: p.stat().st_size)
            _log_download(
                db_path, video_id, "success", path=video_file,
                size_bytes=video_file.stat().st_size,
            )
            return {
                "ok": True, "path": str(video_file),
                "bytes": video_file.stat().st_size, "elapsed_s": elapsed_s,
            }
        failure_class = classify_download_output(combined) or "download_failed"
        if failure_class == "rate_limited":
            set_media_cooldown(reason=f"yt-dlp 429/bot-check for {video_id}", db_path=db_path)
        # Remove partial artifacts so a stale half-file can never be mistaken
        # for a complete download by this or a later attempt.
        for partial in target_dir.glob("source.*"):
            try:
                partial.unlink()
            except OSError:
                pass
        _log_download(db_path, video_id, "failed", error=combined[-2000:])
        return {
            "ok": False,
            "error_class": failure_class,
            "retry_after_s": 300.0 if failure_class == "rate_limited" else 30.0,
            "elapsed_s": elapsed_s,
            "error_tail": combined[-500:],
        }
