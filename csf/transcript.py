"""Transcript fetching with full fallback chain.

Fallback order:
oEmbed → ytdlp → ytdlp_ejs → direct_api → notebooklm → selenium → whisper.
Each method returns: (success: bool, transcript: str | None, error: str | None).
"""

import glob
import argparse
from contextvars import ContextVar
import json
import logging
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Literal, TYPE_CHECKING

from csf.nlm_config import (
    NLMConfig,
    get_nlm_config,
    get_transcript_worker_jitter_bounds,
    set_nlm_config,
)
from csf.batch_status import (
    get_source as _get_source_for_video,
    mark_failed as _mark_failed_video,
    set_negative_cache as _set_negative_cache,
)
from csf.batch_scheduler import BatchScheduler
from csf.cache import set_cached_transcript
from csf.csf_logging import log_action
from csf import nlm_auth_guard
from csf.youtube_auth import get_browser_cookies

if TYPE_CHECKING:
    from csf.nlm_scraper import NLMIndustrialScraper


# Module-level singleton — avoids repeated _recover_stale_attempting() +
# PRAGMA wal_checkpoint overhead when many 429s/successes fire under concurrency.
_scheduler: BatchScheduler | None = None

_NEGATIVE_CACHE_SOFT_TTL_SECONDS = 24 * 3600
_NEGATIVE_CACHE_TERMINAL_TTL_SECONDS = 3650 * 24 * 3600


def _get_scheduler() -> BatchScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BatchScheduler()
    return _scheduler


def _get_nlm_login_profile_args() -> list[str]:
    """Return CLI args that target the active NotebookLM auth profile."""
    profile = os.environ.get("NOTEBOOKLM_PROFILE", "").strip()
    if not profile:
        return []
    return nlm_auth_guard.get_login_profile_args(profile)


# Module-level NLM scraper singleton — one terminal-local staging notebook
# reused across all _fetch_via_notebooklm calls within this process.
_nlm_scraper: "NLMIndustrialScraper | None" = None


def _write_json_result_atomically(path: Path, payload: dict[str, object]) -> None:
    """Publish a worker result only after the complete JSON is on disk."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _get_nlm_scraper() -> "NLMIndustrialScraper":
    global _nlm_scraper
    if _nlm_scraper is None:
        _ensure_nlm_auth()
        from csf.nlm_scraper import NLMIndustrialScraper

        _nlm_scraper = NLMIndustrialScraper(headless=True, browser_cfg=get_nlm_config())
    else:
        # Refresh auth check on every call to catch mid-session expiry
        _ensure_nlm_auth()
    return _nlm_scraper


# Validation
_VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Source labels
_SOURCE_CLI = "cli"
_SOURCE_YOUTUBE_TRANSCRIPT_API = "youtube_transcript_api"
_SOURCE_YOUTUBEI = "youtubei"
_SOURCE_SDK = "sdk"
_SOURCE_YTDLP = "ytdlp"
_SOURCE_YTDLP_EJS = "ytdlp_ejs"
_SOURCE_WHISPER = "whisper"
_SOURCE_SELENIUM = "selenium"
_SOURCE_NLM = "notebooklm"
_SOURCE_EXTERNAL = "external"
_SOURCE_DIRECT_API = "direct_api"

_DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S = 300.0
_DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S = 900.0
_MIN_WHISPER_TRANSCRIPTION_RESERVE_S = 30.0
_TRANSCRIPT_DEADLINE_CHECK_MARGIN_S = 2.0
_TRANSCRIPT_DEADLINE_MONOTONIC: ContextVar[float | None] = ContextVar(
    "transcript_deadline_monotonic",
    default=None,
)

_WHISPER_STRONG_NON_SPEECH_PHRASES = (
    "official audio",
    "music video",
    "instrumental",
    "karaoke",
    "lyrics",
    "live performance",
)

_WHISPER_WEAK_NON_SPEECH_TOKENS = (
    "cover",
    "remix",
    "dance",
    "performance",
    "song",
)

# Source-stage versioning for transcript provenance
# When NotebookLM changes its JSON response structure, source_stage increments.
# Re-fetches with higher source_stage win over stale lower-stage content.
STAGE_VERSION_YTDLP = 1
STAGE_VERSION_EJS = 1
STAGE_VERSION_SELENIUM = 1
STAGE_VERSION_NOTEBOOKLM = 1
STAGE_VERSION_DIRECT_API = 2

# Pluggable external transcript provider hook — called after all built-in
# stages (yt-dlp → cookies → direct_api → NLM → Selenium → Whisper) have failed.
# Set via register_external_transcript_provider().
# Signature: (video_id: str, lang: str) -> tuple[bool, str | None, str | None]
# Returns: (success, transcript_text, error)
_external_provider: Callable[[str, str], tuple[bool, str | None, str | None]] | None = None


def register_external_transcript_provider(provider: Callable[[str, str], tuple[bool, str | None, str | None]]) -> None:
    """Register an external transcript provider as the final fallback.

    The provider is called after all built-in stages fail.
    It must have signature: (video_id: str, lang: str)
    -> tuple[bool, str | None, str | None]  (success, transcript, error)

    Args:
        provider: A callable that takes (video_id, lang) and returns
            (success: bool, transcript: str | None, error: str | None).
            Return (False, None, error) on failure.
    """
    global _external_provider
    _external_provider = provider

# BCP-47 validation regex: language is [a-z]{2}, region is [A-Z]{2} optional
_BCP47_PATTERN = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

# Per-source circuit breaker state
import threading

_consecutive_429: dict[str, int] = {}
_source_cooldown_until: dict[str, float] = {}
_circuit_lock = threading.Lock()

_CIRCUIT_OPEN_THRESHOLD = 3  # consecutive 429s before skipping source
_COOLDOWN_SECONDS = 300  # 5 minutes
_BACKOFF_BASE = 2  # jitter multiplier per consecutive 429
_MAX_BACKOFF_MULTIPLIER = 32  # cap jitter at 32x to prevent pathological sleeps

# Minimum transcript content length used by the existing caption/NLM extractors.
# Fallback observability reports this boundary as a length band; it does not
# silently reject fallback output because short real videos can be legitimate.
_NLM_MIN_CONTENT_CHARS = 21
TRANSCRIPT_QUALITY_MIN_CHARS = _NLM_MIN_CONTENT_CHARS

# Whisper fallback — set YTIS_WHISPER_ENABLED=false to disable

# Whisper audio download prefers broad selectors so we do not fail valid
# videos just because a particular extension is unavailable.
_WHISPER_AUDIO_FORMATS: tuple[str | None, ...] = (
    "bestaudio/best",
    "bestaudio",
    "best",
    None,  # let yt-dlp choose when the explicit -f best selector is rejected
)

# Cookie file cache - avoid repeated Firefox cookies.sqlite extraction per video
_cookie_cache: dict[str, str | int | float] = {}  # {path: str, refcount: int, expiry: float}
_cookie_lock = threading.Lock()
COOKIE_CACHE_TTL = 300  # 5 minutes


# AuthRateLimiter — per-process singleton for call-frequency tracking
_auth_rate_limiter_lock = threading.Lock()
_auth_rate_limiter: "AuthRateLimiter | None" = None


class AuthRateLimiter:
    """Tracks NLM auth call frequency and enforces cooldown on failures.

    Thread-safe per-process singleton. Blocks after auth_max_calls_per_window
    calls within auth_check_interval seconds. Triggers auth_cooldown seconds
    cooldown after 3 consecutive --force login failures.

    Fail-closed on lock error: if lock acquisition fails, is_allowed() returns
    False and the call is blocked.
    """

    def __init__(self) -> None:
        self._call_timestamps: list[float] = []
        self._cooldown_until: float = 0.0
        self._consecutive_failures: int = 0
        self._lock = threading.Lock()

    def _is_in_cooldown(self) -> bool:
        """Return True if currently in cooldown period."""
        return time.monotonic() < self._cooldown_until

    def is_allowed(self) -> bool:
        """Return True if auth call is allowed. Fail-closed on lock error."""
        try:
            acquired = self._lock.acquire(timeout=0.1)
        except Exception:
            # Fail-closed: block the call and log error
            logging.error("[AuthRateLimiter] lock acquisition failed — blocking call")
            return False
        if not acquired:
            logging.error("[AuthRateLimiter] could not acquire lock — blocking call")
            return False
        try:
            if self._is_in_cooldown():
                return False
            config = get_nlm_config()
            now = time.monotonic()
            window_start = now - config.auth_check_interval
            self._call_timestamps = [ts for ts in self._call_timestamps if ts > window_start]
            if len(self._call_timestamps) >= config.auth_max_calls_per_window:
                return False
            return True
        finally:
            self._lock.release()

    def record_call(self) -> None:
        """Record an auth call timestamp. Thread-safe."""
        with self._lock:
            self._call_timestamps.append(time.monotonic())

    def record_auth_failure(self) -> None:
        """Record a --force login failure. Triggers cooldown after 3 consecutive."""
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._cooldown_until = time.monotonic() + get_nlm_config().auth_cooldown
                logging.warning(
                    f"[AuthRateLimiter] 3 consecutive auth failures — entering "
                    f"{get_nlm_config().auth_cooldown}s cooldown"
                )

    def record_auth_success(self) -> None:
        """Reset failure counter on successful --force login."""
        with self._lock:
            self._consecutive_failures = 0

    def remaining(self) -> int:
        """Return the number of auth calls remaining in the current window.

        Returns 0 if in cooldown or if the window is exhausted.
        Thread-safe.
        """
        with self._lock:
            if self._is_in_cooldown():
                return 0
            config = get_nlm_config()
            now = time.monotonic()
            window_start = now - config.auth_check_interval
            active = [ts for ts in self._call_timestamps if ts > window_start]
            return max(0, config.auth_max_calls_per_window - len(active))


def _get_auth_rate_limiter() -> AuthRateLimiter:
    """Return the AuthRateLimiter per-process singleton."""
    global _auth_rate_limiter
    with _auth_rate_limiter_lock:
        if _auth_rate_limiter is None:
            _auth_rate_limiter = AuthRateLimiter()
        return _auth_rate_limiter


# CookieFreshnessTracker — per-process singleton for active cookie probe
_cookie_freshness_tracker_lock = threading.Lock()
_cookie_freshness_tracker: "CookieFreshnessTracker | None" = None


class CookieFreshnessTracker:
    """Tracks cookie freshness using active probe, not just TTL.

    TTL (300s) is a fast-path optimization. When TTL expires, an active
    `nlm login --check` probe (30s timeout) is the authoritative check.
    On probe timeout or failure, invalidate() is called to force re-auth.
    """

    def __init__(self) -> None:
        self._last_check: float = 0.0
        self._ttl: float = 300.0
        self._lock = threading.Lock()

    def is_fresh(self) -> bool:
        """Return True if cookie is fresh (TTL not expired or active probe passes).

        If TTL has expired, runs `nlm login --check` (30s timeout) as authoritative.
        On probe failure or timeout, calls invalidate() and returns False.
        """
        with self._lock:
            if time.monotonic() - self._last_check <= self._ttl:
                return True

        # TTL expired — run active probe
        try:
            check = nlm_auth_guard.run_nlm(["login", "--check", *_get_nlm_login_profile_args()], timeout_s=30)
            if check.returncode == 0:
                with self._lock:
                    self._last_check = time.monotonic()
                return True
            # Probe failed — invalidate and fall through
            self.invalidate()
            return False
        except subprocess.TimeoutExpired:
            logging.warning("[CookieFreshnessTracker] probe timed out after 30s — invalidating")
            self.invalidate()
            return False
        except Exception:
            self.invalidate()
            return False

    def invalidate(self) -> None:
        """Force re-auth on next _ensure_nlm_auth call."""
        with self._lock:
            self._last_check = 0.0


def _get_cookie_freshness_tracker() -> CookieFreshnessTracker:
    """Return the CookieFreshnessTracker per-process singleton."""
    global _cookie_freshness_tracker
    with _cookie_freshness_tracker_lock:
        if _cookie_freshness_tracker is None:
            _cookie_freshness_tracker = CookieFreshnessTracker()
        return _cookie_freshness_tracker


@dataclass
class LanguageConfig:
    """Language configuration for transcript fetching and translation.

    Attributes:
        prefer_lang: BCP-47 language code (e.g. "en", "es", "pt-BR").
            Defaults to "en".
        allow_translation: If True and preferred language is unavailable,
            translate from the returned language to prefer_lang using Gemini SDK.
            Defaults to False (SEC-001: explicit opt-in required).
        translation_provider: Which provider to use for translation.
            Currently only "gemini" is supported.
    """

    prefer_lang: str = "en"
    allow_translation: bool = False
    translation_provider: Literal["gemini"] = "gemini"


@dataclass
class TranscriptResult:
    """Result of a transcript fetch operation.

    Attributes:
        video_id: YouTube video ID.
        lang: The language that was requested (prefer_lang from config).
        raw_lang: The language the transcript was actually returned in,
            or None if no transcript was available.
        was_translated: True if the transcript was translated from raw_lang
            to prefer_lang. False if the original language matched or no
            translation was performed.
        transcript: The transcript text, in prefer_lang (after translation
            if was_translated=True). Empty string if no transcript found.
        source: Which fetch method succeeded ('ytdlp', 'ytdlp_ejs', 'selenium',
            'notebooklm', 'direct_api', 'none').
        source_stage: Versioned provenance tag. None means pre-versioning era
            (records from before this field existed). Higher values indicate
            more recent source format versions. Stage versions: ytdlp/ejs/selenium/
            notebooklm=1, direct_api=2.
        detected_lang: The detected language of the returned transcript,
            or None if language detection failed or no transcript available.
        error: The error message from the last failed source, or None if no
            error occurred or transcript was successfully fetched.
        last_stage: Which stage in the chain was reached ('ytdlp', 'ytdlp_ejs',
            'selenium', 'notebooklm', 'direct_api'). None on success — the
            successful source is in the `source` field.
        failure_reason: Classified reason for final failure ('region_block',
            'no_transcript', 'quota_exceeded', 'auth_failed', 'captcha',
            'unavailable', 'timeout', 'unknown'). None if not yet determined
            or if transcript was successfully fetched.
    """

    video_id: str
    lang: str
    raw_lang: str | None
    was_translated: bool
    transcript: str
    source: str
    source_stage: int | None = None
    detected_lang: str | None = None
    error: str | None = None
    last_stage: str | None = None
    failure_reason: str | None = None
    # YouTube engagement + content quality signals (populated during transcript fetch)
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    duration: int | None = None
    video_title: str | None = None
    video_description: str | None = None


def _extract_video_metadata(info: dict) -> dict:
    """Pull engagement and content-quality fields from a yt-dlp info dict.

    yt-dlp's extract_info returns a full video metadata dict on every call.
    Capturing it here avoids re-fetching for quality metrics.

    Returns a flat dict with only populated fields.
    """
    if not info:
        return {}
    return {
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "duration": info.get("duration"),
        "title": info.get("title"),
        "description": info.get("description"),
    }


def build_transcript_quality_metrics(transcript: str | None) -> dict[str, object]:
    """Return length evidence without claiming semantic transcript quality.

    A non-empty fallback result is operationally complete, but that alone does
    not establish that downstream consumers will find it useful. Persisting
    this small, deterministic evidence lets readiness and quality reviews
    distinguish short observations from ordinary-length output without adding
    a behavior-changing rejection threshold.
    """
    raw_text = str(transcript or "")
    normalized = " ".join(raw_text.split())
    chars = len(raw_text)
    return {
        "transcript_chars": chars,
        "transcript_normalized_chars": len(normalized),
        "transcript_words": len(normalized.split()),
        "transcript_length_threshold_chars": TRANSCRIPT_QUALITY_MIN_CHARS,
        "transcript_length_band": (
            "below_existing_minimum"
            if chars < TRANSCRIPT_QUALITY_MIN_CHARS
            else "meets_existing_minimum"
        ),
    }


def build_transcript_cache_metadata(
    result: TranscriptResult, extra: dict[str, object] | None = None
) -> dict[str, object]:
    """Build a lossless metadata payload for the transcript cache."""
    metadata = {field.name: getattr(result, field.name, None) for field in fields(TranscriptResult)}
    metadata.pop("transcript", None)
    metadata.update(build_transcript_quality_metrics(result.transcript))
    if extra:
        metadata.update(extra)
    return metadata


def _validate_bcp47(lang: str) -> None:
    """Validate a BCP-47 language code.

    Raises ValueError if the code does not match the pattern.
    Valid formats: "en", "pt-BR", "zh-CN".
    """
    if not _BCP47_PATTERN.match(lang):
        raise ValueError(
            f"Invalid BCP-47 language code: {lang!r}. "
            "Expected format: 'en', 'es', 'pt-BR', 'zh-CN', etc."
        )


def _translate_text(text: str, from_lang: str, to_lang: str, provider: str) -> str:
    """Translate text from from_lang to to_lang using Gemini SDK.

    BLOCKER-1 resolved: trans! npm not installed; Gemini SDK is sole provider.

    Translation failures are NON-FATAL (FM-003): returns original text on failure.

    Args:
        text: The text to translate.
        from_lang: Source BCP-47 language code.
        to_lang: Target BCP-47 language code.
        provider: Translation provider (only "gemini" supported).

    Returns:
        Translated text, or original text if translation fails.
    """
    if provider != "gemini":
        # Currently only gemini is supported
        return text

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        import logging

        logging.warning(
            "GEMINI_API_KEY not set; cannot translate, returning original text."
        )
        return text

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                f"Translate the following text from {from_lang} to {to_lang}. "
                f"Return ONLY the translated text, nothing else.\n\n{text}"
            ],
        )
        if response.text:
            return response.text.strip()
        return text
    except Exception:
        import logging

        logging.warning(
            f"Translation failed ({from_lang} -> {to_lang}); returning original text. "
            "Set allow_translation=False to suppress this message."
        )
        return text


def _validate_video_id(video_id: str) -> bool:
    """Validate video_id format.

    Returns True if valid (11 chars, alphanumeric + hyphen/underscore).
    Returns False otherwise.
    """
    return bool(_VIDEO_ID_PATTERN.match(video_id))


def _get_worker_jitter_bounds() -> tuple[float, float]:
    """Return the worker jitter bounds used by transcript fetch loops."""
    return get_transcript_worker_jitter_bounds()


def _apply_jitter() -> None:
    """Apply random jitter between parallel fetch attempts to avoid rate limiting."""
    jitter_min_s, jitter_max_s = _get_worker_jitter_bounds()
    jitter = random.uniform(jitter_min_s, jitter_max_s)
    time.sleep(jitter)


def _is_source_rate_limited(source: str) -> bool:
    """Return True if source is in circuit-open cooldown."""
    return (
        source in _source_cooldown_until
        and time.monotonic() < _source_cooldown_until[source]
    )


def _record_source_429(source: str, video_id: str | None = None) -> None:
    """Record a 429 for a source. Opens circuit after threshold.

    Also writes cross-terminal cooldown state to BatchScheduler when video_id is provided.
    """
    with _circuit_lock:
        _consecutive_429[source] = _consecutive_429.get(source, 0) + 1
        count = _consecutive_429[source]
    if count >= _CIRCUIT_OPEN_THRESHOLD:
        with _circuit_lock:
            _source_cooldown_until[source] = time.monotonic() + _COOLDOWN_SECONDS
        import logging

        logging.warning(
            f"[transcript] Circuit breaker OPEN for '{source}' "
            f"({count} consecutive 429s, cooldown={_COOLDOWN_SECONDS}s)"
        )
    # Cross-terminal cooldown: resolve channel URL and record in shared SQLite.
    # COMP-001: _record_source_429 is called with method tokens (e.g. _SOURCE_WHISPER='whisper')
    # but BatchScheduler expects channel_url as PRIMARY KEY. Resolve via get_source(video_id).
    if video_id is not None:
        channel_url = _get_source_for_video(video_id)
        if channel_url is not None:
            try:
                _get_scheduler().record_429(channel_url)
            except Exception as e:
                logging.warning(f"[transcript] Cross-terminal sync failed: {e}")


def _record_source_success(source: str, video_id: str | None = None) -> None:
    """Reset 429 counter on any success. Clears cross-terminal channel cooldown."""
    with _circuit_lock:
        _consecutive_429[source] = 0
    # Cross-terminal cooldown clear: resolve channel URL and clear in shared SQLite.
    if video_id is not None:
        channel_url = _get_source_for_video(video_id)
        if channel_url is not None:
            try:
                _get_scheduler().record_success(channel_url)
            except Exception as e:
                logging.warning(f"[transcript] Cross-terminal sync failed: {e}")


def _apply_jitter_with_backoff(source: str) -> None:
    """Apply jitter with backoff multiplier based on consecutive failures, capped at MAX."""
    with _circuit_lock:
        count = _consecutive_429.get(source, 0)
    multiplier = (
        min(_BACKOFF_BASE**count, _MAX_BACKOFF_MULTIPLIER) if count > 0 else 1.0
    )
    jitter_min_s, jitter_max_s = _get_worker_jitter_bounds()
    jitter = random.uniform(jitter_min_s, jitter_max_s) * multiplier
    time.sleep(jitter)


def _fetch_via_gemini_cli(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using gemini CLI transcript command.

    Uses `timeout -k 1s 300s gemini transcript <video_id>`.
    """
    gemini_path = shutil.which("gemini")
    if not gemini_path:
        return (False, None, "gemini CLI not found")

    try:
        cmd = [gemini_path, "transcript", video_id, "--lang", lang]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return (False, None, "gemini CLI timed out after 300s")
    except Exception as e:
        return (False, None, f"gemini CLI error: {e}")

    if proc.returncode != 0:
        return (False, None, f"gemini CLI failed: {stderr.strip()}")

    return (True, stdout.strip(), None)


def _fetch_via_youtube_transcript_api(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using youtube-transcript-api Python package."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return (False, None, "youtube_transcript_api not installed")

    try:
        from youtube_transcript_api._errors import (
            TranscriptsDisabled,
            NoTranscriptFound,
            VideoUnavailable,
        )

        def _fetch() -> str:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, languages=[lang])
            return " ".join(snippet.text for snippet in transcript.snippets)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch)
            try:
                text = future.result(timeout=30)
            except TimeoutError:
                return (False, None, "youtube_transcript_api timeout (>30s)")
            except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
                return (False, None, f"youtube_transcript_api error: {e}")
        return (True, text, None)
    except ImportError:
        return (False, None, "youtube_transcript_api not installed")
    except Exception as e:
        return (False, None, f"youtube_transcript_api error: {e}")


def _fetch_via_youtubei(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using direct YouTube API call with cookie auth.

    Note: youtubei does not support language parameter specification.
    This method returns English transcripts only — there is no way to request
    a specific language via this API. The lang parameter is accepted for
    interface consistency but ignored.
    """
    try:
        import youtubei
    except ImportError:
        return (False, None, "youtubei not installed")

    def _fetch() -> tuple[bool, str | None, str | None]:
        try:
            client = youtubei.get_client()
            video = client.get_video(video_id)
            transcript_data = video.get_transcript()
            if transcript_data is None:
                return (False, None, "No transcript available")
            text = " ".join(item["text"] for item in transcript_data)
            return (True, text, None)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "rate limit" in msg:
                return (False, None, "youtubei rate limited (429)")
            return (False, None, f"youtubei error: {e}")

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch)
            return future.result(timeout=60)
    except TimeoutError:
        return (False, None, "youtubei timeout (>15s)")


def _fetch_via_ytdlp(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None, dict]:
    """Fetch transcript using yt-dlp Python API with Chrome TLS impersonation.

    Uses yt-dlp's Python API (not CLI subprocess) with WEB client + curl-cffi
    Chrome impersonation to bypass YouTube's TLS fingerprinting bot detection.
    The "Sign in to confirm you're not a bot" error is a TLS handshake rejection —
    curl-cffi makes the request look like Chrome, bypassing it.

    Falls back gracefully if curl-cffi is not installed.

    Returns:
        (success, transcript, error, info_dict) — info_dict contains video metadata
        (view_count, like_count, comment_count, duration, title, description) on success.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts: dict = {
        "skip_download": True,
        "writeautomaticsubs": True,
        "writesubtitles": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "json3",
        "quiet": True,
        "no_warnings": True,
        # Rate limiting: humanize requests to avoid detection
        "sleep_interval": 15,
        "max_sleep_interval": 60,
        # Retry logic with exponential backoff
        "extractor_retries": 5,
        "fragment_retries": 10,
        "ignoreerrors": False,
        # WEB client avoids bot-detection on public videos. No cookies needed.
        # Age-restricted videos require auth — handled by second attempt below.
        "extractor_args": {
            "youtube": {
                "client_name": "WEB",
                "client_version": "2.20210721.01.00",
                "player_client": "web",
                # User region for geolocation context
                "UACountry": "CA",
            }
        },
        # HTTP headers to mimic browser requests
        "http_headers": {
            "Referer": "https://www.youtube.com/",
            "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        },
    }

    try:
        import yt_dlp

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        # Get subtitle entries from automatic_captions (prefer) or subtitles
        subs = (
            info.get("automatic_captions", {}).get(lang)
            or info.get("subtitles", {}).get(lang)
            or info.get("automatic_captions", {}).get("en")
            or info.get("subtitles", {}).get("en")
        )

        if not subs or len(subs) == 0:
            return (False, None, "no subtitles available")

        sub_url = subs[0].get("url")
        if not sub_url:
            return (False, None, "no subtitle URL in yt-dlp response")

        # Fetch the timedtext JSON3 using curl-cffi with Chrome impersonation.
        # This is the actual HTTP call — curl-cffi bypasses TLS fingerprinting.
        try:
            from curl_cffi import requests as curl_requests

            resp = curl_requests.get(
                sub_url,
                impersonate="chrome",
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            )
            data = json.loads(resp.content.decode("utf-8"))
        except ImportError:
            # Fall back to urllib.request (module-level import) — will likely get bot-checked
            req = urllib.request.Request(
                sub_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))

        # Parse timedtext JSON3 format into plain text
        # JSON3 format: {"events": [{"segs": [{"utf8": "text"}, ...]}, ...]}
        text_parts = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                text = seg.get("utf8", "").strip()
                if text:
                    text_parts.append(text)
            # Add newline between subtitle blocks for readability
            if event.get("segs"):
                text_parts.append("\n")

        full_text = " ".join(t for t in text_parts if t != "\n")
        if not full_text.strip():
            return (False, None, "subtitle file was empty", {})

        return (True, full_text.strip(), None, info)

    except urllib.error.HTTPError as e:
        if e.code == 429:
            return (False, None, "rate limited (429)", {})
        return (False, None, f"yt-dlp HTTP error: {e.code}", {})
    except subprocess.TimeoutExpired:
        return (False, None, "yt-dlp timed out", {})
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "too many requests" in err_str:
            return (False, None, "rate limited (429)", {})
        if "no subtitles" in err_str or "does not have any subtitles" in err_str:
            return (False, None, "no subtitles available", {})
        if "sign in to confirm" in err_str or "not a bot" in err_str:
            # Bot-check triggered — try age-restricted approach with cookies + default extractor.
            # This is a second attempt inside the same function rather than a separate method.
            return _fetch_via_ytdlp_with_cookies(video_id, lang)
        return (False, None, f"yt-dlp error: {e}", {})


def _fetch_via_ytdlp_with_cookies(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None, dict]:
    """Second-attempt transcript fetch with browser cookies for age-restricted videos.

    Called by _fetch_via_ytdlp when bot-check fires on the WEB client approach.
    Uses the default yt-dlp extractor (not WEB client) with Firefox browser cookies.
    Falls back gracefully if cookies are unavailable or extraction fails.

    Returns:
        (success, transcript, error, info_dict) — info_dict has video metadata on success.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # Get cached cookie file (or create new one) with reference counting
    cookie_file = _get_cookie_file()
    if not cookie_file:
        return (False, None, "no firefox cookie file")

    ydl_opts: dict = {
        "skip_download": True,
        "writeautomaticsubs": True,
        "writesubtitles": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "json3",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": cookie_file,
        # Rate limiting: even more conservative with cookies (account-level risk)
        "sleep_interval": 20,
        "max_sleep_interval": 90,
        # Retry logic with exponential backoff
        "extractor_retries": 5,
        "fragment_retries": 10,
        "ignoreerrors": False,
        # EJS github component resolves YouTube's JS challenge for age-restricted videos.
        # Works alongside cookies to authenticate and extract transcripts.
        "extractor_args": {
            "youtube": {
                "external_downloader": "ejs:github",
                "player_client": "web",
                # User region for geolocation context
                "UACountry": "CA",
            }
        },
        # HTTP headers to mimic browser requests
        "http_headers": {
            "Referer": "https://www.youtube.com/",
            "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        },
    }

    try:
        import yt_dlp

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        subs = (
            info.get("automatic_captions", {}).get(lang)
            or info.get("subtitles", {}).get(lang)
            or info.get("automatic_captions", {}).get("en")
            or info.get("subtitles", {}).get("en")
        )

        if not subs or len(subs) == 0:
            _release_cookie_file(cookie_file)
            return (False, None, "no subtitles available", {})

        sub_url = subs[0].get("url")
        if not sub_url:
            _release_cookie_file(cookie_file)
            return (False, None, "no subtitle URL in yt-dlp response", {})

        # Fetch subtitle URL with curl_cffi Chrome impersonation
        try:
            from curl_cffi import requests as curl_requests

            resp = curl_requests.get(
                sub_url,
                impersonate="chrome",
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            )
            data = json.loads(resp.content.decode("utf-8"))
        except ImportError:
            req = urllib.request.Request(
                sub_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))

        text_parts = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t:
                    text_parts.append(t)
            if event.get("segs"):
                text_parts.append("\n")

        full_text = " ".join(t for t in text_parts if t != "\n")
        _release_cookie_file(cookie_file)
        if not full_text.strip():
            return (False, None, "subtitle file was empty", {})

        return (True, full_text.strip(), None, info)

    except urllib.error.HTTPError as e:
        _release_cookie_file(cookie_file)
        if e.code == 429:
            return (False, None, "rate limited (429)", {})
        return (False, None, f"yt-dlp-with-cookies HTTP error: {e.code}", {})
    except subprocess.TimeoutExpired:
        _release_cookie_file(cookie_file)
        return (False, None, "yt-dlp-with-cookies timed out", {})
    except Exception as e:
        _release_cookie_file(cookie_file)
        err_str = str(e).lower()
        if "429" in err_str or "too many requests" in err_str:
            return (False, None, "rate limited (429)", {})
        if "sign in" in err_str or "age" in err_str or "login" in err_str:
            return (False, None, "age-restricted or requires login", {})
        return (False, None, f"yt-dlp-with-cookies error: {e}", {})


def _get_firefox_cookie_file() -> str | None:
    """Export live Firefox YouTube cookies to a temp Netscape cookie file.

    Copies cookies.sqlite from the live Firefox profile to bypass Windows
    file locking, then exports YouTube/Google/Googlevideo cookies to
    Netscape format. The caller is responsible for deleting the temp file.

    Returns:
        Path to temp cookie file, or None if Firefox is not running / no cookies found.
    """
    appdata = os.environ.get("APPDATA") or ""
    profile_base = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
    profiles = glob.glob(os.path.join(profile_base, "*.default*"))
    if not profiles:
        return None

    # Prefer the release profile (has active YouTube session)
    release = next((p for p in profiles if "release" in p), profiles[0])
    cookie_db = os.path.join(release, "cookies.sqlite")
    if not os.path.exists(cookie_db):
        return None

    tmp_db = tempfile.mktemp(suffix=".sqlite")
    try:
        shutil.copy2(cookie_db, tmp_db)
    except Exception:
        return None

    try:
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT host, name, value, path, expiry, isSecure FROM moz_cookies "
            'WHERE host LIKE "%youtube.com" OR host LIKE "%google.com" OR host LIKE "%googlevideo.com"'
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        os.unlink(tmp_db)
        return None

    if not rows:
        os.unlink(tmp_db)
        return None

    cookie_file = tempfile.mktemp(suffix=".txt")
    try:
        with open(cookie_file, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for row in rows:
                h, n, v, p, exp, sec = (
                    row["host"],
                    row["name"],
                    row["value"],
                    row["path"],
                    row["expiry"],
                    row["isSecure"],
                )
                flag = "TRUE" if h.startswith(".") else "FALSE"
                p = p or "/"
                sec_str = "TRUE" if sec else "FALSE"
                v = v.replace("\n", "%0A")
                f.write(f"{h}\t{flag}\t{p}\t{sec_str}\t{exp}\t{n}\t{v}\n")
        return cookie_file
    except Exception:
        try:
            os.unlink(cookie_file)
        except Exception:
            pass
        os.unlink(tmp_db)
        return None
    finally:
        try:
            os.unlink(tmp_db)
        except Exception:
            pass


def _get_cookie_file() -> str | None:
    """Get cached cookie file with reference counting.

    Returns cached cookie file if available and valid, otherwise generates
    a new one. Uses reference counting to ensure the file is not deleted
    while still in use by concurrent requests.

    Returns:
        Cookie file path, or None if unavailable.
    """
    global _cookie_cache

    with _cookie_lock:
        # Check cache validity
        if _cookie_cache:
            path = _cookie_cache.get("path")
            expiry = _cookie_cache.get("expiry", 0)
            if path and os.path.exists(path) and time.time() < expiry:
                _cookie_cache["refcount"] = _cookie_cache.get("refcount", 0) + 1
                return path
            else:
                # Cleanup stale cache
                _cleanup_cookie_cache()

        # Generate new cookie file using existing function
        cookie_file = _get_firefox_cookie_file()
        if cookie_file:
            _cookie_cache = {
                "path": cookie_file,
                "refcount": 1,
                "expiry": time.time() + COOKIE_CACHE_TTL
            }
        return cookie_file


def _release_cookie_file(cookie_file: str) -> None:
    """Release reference to cached cookie file.

    Decrements reference count; cleans up cookie file when refcount reaches zero.

    Args:
        cookie_file: Path to the cookie file being released.
    """
    global _cookie_cache

    with _cookie_lock:
        if _cookie_cache.get("path") == cookie_file:
            _cookie_cache["refcount"] = _cookie_cache.get("refcount", 1) - 1
            if _cookie_cache["refcount"] <= 0:
                _cleanup_cookie_cache()


def _cleanup_cookie_cache() -> None:
    """Cleanup cached cookie file and reset cache.

    Deletes the cookie file if it exists and resets the module-level cache.
    Logs a warning if deletion fails (instead of silently ignoring).
    """
    global _cookie_cache

    path = _cookie_cache.get("path")
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception as e:
            logging.warning(f"Failed to cleanup cookie file {path}: {e}")
    _cookie_cache = {}


def _parse_srt(srt_content: str) -> str:
    """Parse SRT subtitle content into plain transcript text."""
    import re

    entries = re.split(r"\n\d+\n", srt_content)
    text_parts = []
    for entry in entries:
        lines = entry.strip().split("\n")
        for line in lines:
            # Skip numeric timecodes (00:00:00,000 --> 00:00:00,000)
            if "-->" in line:
                continue
            # Skip HTML tags
            line = re.sub(r"<[^>]+>", "", line)
            line = line.strip()
            if line:
                text_parts.append(line)
    return " ".join(text_parts)


def _fetch_via_sdk(video_id: str, lang: str) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using Gemini SDK as last resort."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return (False, None, "GEMINI_API_KEY not set")

    try:
        from google import genai
    except ImportError:
        return (False, None, "google-genai not installed")

    def _fetch() -> tuple[bool, str | None, str | None]:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    f"Get the transcript for YouTube video {video_id} in language {lang}"
                ],
            )
            text = response.text.strip() if response.text else ""
            return (True, text, None)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg:
                return (False, None, "SDK rate limited (429)")
            return (False, None, f"SDK error: {e}")

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch)
            return future.result(timeout=60)
    except TimeoutError:
        return (False, None, "SDK timeout (>60s)")


def _whisper_transcription_timeout_s() -> float:
    """Return a finite per-video deadline for the process-isolated Whisper stage."""
    raw = os.getenv("YTIS_WHISPER_TRANSCRIPTION_TIMEOUT_S", "")
    if not raw.strip():
        return _DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logging.warning(
            "[transcript] invalid YTIS_WHISPER_TRANSCRIPTION_TIMEOUT_S=%r; "
            "using %.1fs",
            raw,
            _DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S,
        )
        return _DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S
    if not math.isfinite(value) or value <= 0:
        logging.warning(
            "[transcript] YTIS_WHISPER_TRANSCRIPTION_TIMEOUT_S must be finite and > 0; "
            "using %.1fs",
            _DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S,
        )
        return _DEFAULT_WHISPER_TRANSCRIPTION_TIMEOUT_S
    return value


def _whisper_audio_download_timeout_s() -> float:
    """Return the total budget shared by all yt-dlp audio selectors."""
    raw = os.getenv("YTIS_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S", "")
    if not raw.strip():
        return _DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logging.warning(
            "[transcript] invalid YTIS_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S=%r; "
            "using %.1fs",
            raw,
            _DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S,
        )
        return _DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S
    if not math.isfinite(value) or value <= 0:
        logging.warning(
            "[transcript] YTIS_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S must be finite "
            "and > 0; using %.1fs",
            _DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S,
        )
        return _DEFAULT_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S
    return value


def _remaining_transcript_deadline_s() -> float | None:
    """Return the remaining coordinator-owned child deadline, if one exists."""
    deadline = _TRANSCRIPT_DEADLINE_MONOTONIC.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _transcript_deadline_exhausted() -> bool:
    remaining_s = _remaining_transcript_deadline_s()
    return remaining_s is not None and remaining_s <= _TRANSCRIPT_DEADLINE_CHECK_MARGIN_S


def _run_whisper_transcription_subprocess(
    audio_file: str,
    lang: str,
    *,
    timeout_s: float,
) -> tuple[bool, str | None, str | None]:
    """Run the CPU-heavy model outside the pipeline thread with a hard deadline."""
    result_fd, result_name = tempfile.mkstemp(prefix="whisper_result_", suffix=".json")
    os.close(result_fd)
    result_path = Path(result_name)
    command = [
        sys.executable,
        "-m",
        "csf.whisper_worker",
        "--audio-file",
        audio_file,
        "--lang",
        lang,
        "--result-path",
        str(result_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result_path.unlink(missing_ok=True)
        return (
            False,
            None,
            f"whisper transcription timed out (>{timeout_s:g}s)",
        )
    except Exception as exc:
        result_path.unlink(missing_ok=True)
        return False, None, f"whisper worker launch error: {type(exc).__name__}: {exc}"

    try:
        if not result_path.exists():
            detail = (completed.stderr or completed.stdout or "").strip()[:300]
            return (
                False,
                None,
                f"whisper worker exited without result ({completed.returncode})"
                + (f": {detail}" if detail else ""),
            )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False, None, "whisper worker returned a non-object result"
        if payload.get("ok") and isinstance(payload.get("transcript"), str):
            return True, payload["transcript"], None
        return False, None, str(payload.get("error") or "whisper worker failed")
    except (OSError, json.JSONDecodeError) as exc:
        return False, None, f"invalid whisper worker result: {type(exc).__name__}: {exc}"
    finally:
        result_path.unlink(missing_ok=True)


def _fetch_via_whisper(video_id: str, lang: str) -> tuple[bool, str | None, str | None]:
    """Transcribe audio using faster-whisper as final fallback.

    Downloads audio via yt-dlp then transcribes with faster-whisper.
    Used only after all caption-based sources fail — it is slow (~30-90s)
    but can transcribe any video that has audio available.

    Args:
        video_id: YouTube video ID.
        lang: Target language code (used only as hint; faster-whisper
            auto-detects if not in known languages).

    Returns:
        (success, transcript, error).
    """
    import tempfile

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_dir = tempfile.mkdtemp(prefix="whisper_audio_")
    audio_path = os.path.join(tmp_dir, "audio")
    try:
        # Download audio only via yt-dlp. Use broad selectors so we still
        # capture videos that do not expose an m4a stream. All selectors share
        # one total budget; otherwise four 300-second subprocess deadlines can
        # outlive the coordinator's per-item deadline without a stage result.
        last_audio_error: str | None = None
        # Shared machine-wide download gate: this audio path and the visual
        # pipeline are the only two yt-dlp media downloaders, and the operator
        # contract is ONE shared rate ceiling (single-flight lock + hourly
        # budget + durable cooldown). JS runtime resolution is shared too.
        from csf.visual import media_fetch as _media_fetch

        resolved_js = _media_fetch.resolve_js_runtime()
        js_runtime_args = ["--js-runtimes", resolved_js] if resolved_js else []
        _download_lock = _media_fetch.acquire_media_download_lock(
            timeout_s=min(_whisper_audio_download_timeout_s(), 120.0)
        )
        if _download_lock is None:
            return (False, None, "audio download deferred: shared download lock busy")
        _cooldown = _media_fetch.media_cooldown_state()
        if _cooldown["active"]:
            _download_lock.release()
            return (
                False,
                None,
                f"audio download deferred: shared cooldown active ({_cooldown['reason']})",
            )
        _budget = _media_fetch.consume_budget_slot()
        if not _budget["allowed"]:
            _download_lock.release()
            return (
                False,
                None,
                "audio download deferred: shared hourly download budget exhausted",
            )
        audio_budget_s = _whisper_audio_download_timeout_s()
        remaining_s = _remaining_transcript_deadline_s()
        if remaining_s is not None:
            audio_budget_s = min(
                audio_budget_s,
                max(0.1, remaining_s - _MIN_WHISPER_TRANSCRIPTION_RESERVE_S),
            )
        audio_deadline = time.monotonic() + audio_budget_s
        for audio_format in _WHISPER_AUDIO_FORMATS:
            selector_remaining_s = audio_deadline - time.monotonic()
            if selector_remaining_s <= 0:
                return (
                    False,
                    None,
                    f"audio download timed out (>{audio_budget_s:g}s total budget)",
                )
            format_args = ["-f", audio_format] if audio_format else []
            cmd = [
                "yt-dlp",
                *get_browser_cookies("firefox"),
                *js_runtime_args,
                *format_args,
                "--extract-audio",
                "--audio-format",
                "mp3",
                "--output",
                audio_path,
                video_url,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=selector_remaining_s,
                )
            except subprocess.TimeoutExpired:
                return (
                    False,
                    None,
                    f"audio download timed out (>{audio_budget_s:g}s total budget)",
                )
            if proc.returncode == 0:
                break

            stderr_lower = (proc.stderr or "").lower()
            if "429" in proc.stderr or "too many requests" in stderr_lower:
                _media_fetch.set_media_cooldown(
                    reason=f"whisper audio 429 for {video_id}"
                )
                return (False, None, "audio download rate limited (429)")
            if "not found" in stderr_lower or "video unavailable" in stderr_lower:
                return (False, None, "video unavailable for audio download")

            last_audio_error = f"audio download failed: {proc.stderr.strip()[:200]}"
            if any(
                hint in stderr_lower
                for hint in (
                    "requested format is not available",
                    "format is not available",
                    "no such format",
                    "no formats available",
                )
            ):
                continue

            if any(
                hint in stderr_lower
                for hint in (
                    "sign in to confirm",
                    "not a bot",
                    "challenge solving failed",
                    "selects the best pre-merged format",
                )
            ):
                continue

            return (False, None, last_audio_error)
        else:
            return (False, None, last_audio_error or "audio download failed")

        # Find the downloaded audio file
        audio_files = list(Path(tmp_dir).glob("*.mp3"))
        if not audio_files:
            return (False, None, "no audio file produced")

        audio_file = str(audio_files[0])

        transcription_timeout_s = _whisper_transcription_timeout_s()
        remaining_s = _remaining_transcript_deadline_s()
        if remaining_s is not None:
            transcription_timeout_s = min(
                transcription_timeout_s,
                max(0.1, remaining_s - 1.0),
            )
        return _run_whisper_transcription_subprocess(
            audio_file,
            lang,
            timeout_s=transcription_timeout_s,
        )

    except subprocess.TimeoutExpired:
        return (False, None, "audio download timed out (stage budget exhausted)")
    except Exception as e:
        return (False, None, f"whisper transcription error: {e}")
    finally:
        import shutil as _shutil

        try:
            _download_lock.release()
        except Exception:
            pass
        try:
            _shutil.rmtree(tmp_dir)
        except Exception:
            pass


def _summarize_whisper_empty_result(segments: list[object]) -> str:
    """Describe an empty Whisper result with a conservative speech-vs-music hint.

    We cannot prove that the audio is music, but faster-whisper exposes
    per-segment `no_speech_prob`. When that is high across the returned
    segments, the audio was likely silence, music, or otherwise speech-free.
    """

    no_speech_probs: list[float] = []
    for segment in segments:
        try:
            prob = getattr(segment, "no_speech_prob", None)
        except Exception:
            prob = None
        if prob is not None:
            try:
                no_speech_probs.append(float(prob))
            except (TypeError, ValueError):
                continue

    max_no_speech_prob = max(no_speech_probs) if no_speech_probs else None
    segment_count = len(segments)

    if max_no_speech_prob is not None and max_no_speech_prob >= 0.75:
        return (
            "whisper no speech detected (likely music or silence; "
            f"segments={segment_count}, max_no_speech_prob={max_no_speech_prob:.2f})"
        )
    if max_no_speech_prob is not None:
        return (
            "whisper produced empty transcript "
            f"(segments={segment_count}, max_no_speech_prob={max_no_speech_prob:.2f})"
        )
    return f"whisper produced empty transcript (segments={segment_count})"


def _normalize_admission_text(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _coerce_positive_int(value: object | None) -> int | None:
    try:
        if value is None:
            return None
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _whisper_admission_check(
    admission_metadata: dict[str, object] | None,
) -> tuple[bool, str | None, str | None]:
    """Decide whether Whisper should run for a no-caption candidate.

    Returns:
        (should_attempt, failure_reason, error_message)
    """

    if not admission_metadata:
        return (True, None, None)

    privacy_status = _normalize_admission_text(admission_metadata.get("privacy_status"))
    upload_status = _normalize_admission_text(admission_metadata.get("upload_status"))
    unavailable_reason = _normalize_admission_text(admission_metadata.get("unavailable_reason"))
    is_live_content = bool(admission_metadata.get("is_live_content"))

    if is_live_content or upload_status in {"live", "live_stream", "premiere"}:
        return (
            False,
            "unavailable",
            "whisper admission skipped: unavailable live or premiere metadata",
        )
    if privacy_status in {"private", "deleted"}:
        return (
            False,
            "unavailable",
            f"whisper admission skipped: terminal privacy metadata ({privacy_status})",
        )
    if unavailable_reason in {"deleted", "private", "removed", "unavailable"}:
        return (
            False,
            "unavailable",
            f"whisper admission skipped: terminal unavailable metadata ({unavailable_reason})",
        )

    title = _normalize_admission_text(admission_metadata.get("title"))
    description = _normalize_admission_text(admission_metadata.get("description"))
    channel_title = _normalize_admission_text(admission_metadata.get("channel_title"))
    text_blob = " ".join(part for part in (title, channel_title, description) if part)

    if not text_blob:
        return (True, None, None)

    if any(phrase in text_blob for phrase in _WHISPER_STRONG_NON_SPEECH_PHRASES):
        return (
            False,
            "no_transcript",
            "whisper admission skipped: likely music or silence",
        )

    duration = _coerce_positive_int(admission_metadata.get("duration"))
    if duration is not None and duration <= 15:
        if any(token in text_blob for token in _WHISPER_WEAK_NON_SPEECH_TOKENS):
            return (
                False,
                "no_transcript",
                f"whisper admission skipped: likely music or silence (duration={duration})",
            )

    return (True, None, None)


def _fetch_via_selenium_firefox(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using Selenium-driven Firefox with real browser TLS.

    This is a fallback that bypasses YouTube's TLS fingerprinting bot detection
    by running an actual Firefox browser with your real browser session (cookies).
    It is slow (~15-30s per video) but reliable when yt-dlp fails due to bot detection.

    Args:
        video_id: YouTube video ID.
        lang: BCP-47 language code (currently unused — Firefox returns
            the transcript in whatever language YouTube provides, usually en).

    Returns:
        (success, transcript_text, error)
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
        from selenium.webdriver.firefox.firefox_profile import FirefoxProfile
        from selenium.webdriver.common.by import By
    except ImportError:
        return (False, None, "selenium not installed")

    firefox_profile_path = None
    try:
        # Use dedicated download profile (ProfileForDownloading) with YouTube login
        import glob as _glob

        appdata = os.environ.get("APPDATA") or ""
        profile_base = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
        # Prefer dedicated download profile, fall back to first non-default profile
        profiles = _glob.glob(os.path.join(profile_base, "*.Profile 1*"))
        if not profiles:
            # Fallback: use any profile that's not the default/release
            all_profiles = _glob.glob(os.path.join(profile_base, "*"))
            profiles = [p for p in all_profiles if ".default" not in os.path.basename(p)]
        opts = Options()
        opts.add_argument("--headless=new")

        # Don't use existing profile - it conflicts with Selenium's preference setting
        # For age-restricted videos requiring cookies, use yt-dlp with cookies instead
        driver = webdriver.Firefox(service=Service(), options=opts)

        try:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            driver.get(video_url)
            time.sleep(3)

            # Scroll down to expose the transcript button, then click it via JS
            driver.execute_script("window.scrollBy(0, 400)")
            time.sleep(0.5)

            transcript_clicked = False
            buttons = driver.find_elements(By.TAG_NAME, "button")
            for btn in buttons:
                aria_label = btn.get_attribute("aria-label") or ""
                if "transcript" in aria_label.lower():
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", btn
                    )
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", btn)
                    transcript_clicked = True
                    time.sleep(3)
                    break

            if not transcript_clicked:
                return (False, None, "transcript button not found")

            transcript_text, extraction_error = _extract_selenium_transcript_text(
                driver, By
            )
            if transcript_text is None:
                return (False, None, extraction_error or "transcript panel was empty")

            return (True, transcript_text, None)

        finally:
            driver.quit()

    except Exception as e:
        return (False, None, f"selenium error: {e}")


def _extract_selenium_transcript_text(
    driver: object, by: object
) -> tuple[str | None, str | None]:
    """Extract transcript text from transcript DOM nodes, never page chrome.

    YouTube renders the transcript in custom elements after the transcript
    button is clicked.  The page body's text also contains titles, controls,
    comments, and sign-in UI, so it is not a valid transcript fallback.
    """
    selectors = (
        "ytd-transcript-segment-renderer",
        "ytd-transcript-segment-list-renderer",
        "ytd-transcript-renderer",
        "[target-id='engagement-panel-searchable-transcript']",
    )
    for selector in selectors:
        elements = driver.find_elements(by.CSS_SELECTOR, selector)
        texts = []
        for element in elements:
            text = str(getattr(element, "text", "") or "").strip()
            if text:
                texts.append(text)
        if texts:
            transcript_text = "\n".join(texts).strip()
            if len(transcript_text) >= 20:
                return (transcript_text, None)

    return (None, "transcript segments not found")


def _ensure_nlm_auth() -> bool:
    """Check NLM auth and auto-recover if expired.

    Account-aware production workers use the canonical token-backed YTIS auth
    path. The legacy CLI login/check ladder remains only for compatibility
    callers that do not provide ``YTIS_NLM_ACCOUNT_PROFILE``.
    """
    account_profile = os.environ.get("YTIS_NLM_ACCOUNT_PROFILE", "").strip()
    if account_profile:
        from csf.nlm_client import ensure_account_session

        worker_id = os.environ.get("YTIS_NLM_WORKER_ID", "transcript").strip() or "transcript"
        probe = ensure_account_session(
            account_profile,
            worker_id=worker_id,
            allow_bootstrap=False,
        )
        log_action(
            "nlm_auth_checked",
            {
                "component": "transcript",
                "account_profile": probe.account_profile,
                "expected_email": probe.expected_email,
                "storage_path": probe.storage_path,
                "status": "ok" if probe.ok else "failed",
                "probe_reason": probe.reason,
                "mode": "canonical_account_session",
            },
        )
        return probe.ok

    rate_limiter = _get_auth_rate_limiter()

    # 1. AuthRateLimiter gate — block if rate limit exceeded or in cooldown
    if not rate_limiter.is_allowed():
        logging.warning("[_ensure_nlm_auth] blocked by AuthRateLimiter")
        return False

    freshness = _get_cookie_freshness_tracker()

    # 2. CookieFreshnessTracker — if stale, force re-auth
    if not freshness.is_fresh():
        logging.info("[_ensure_nlm_auth] cookie stale, forcing re-auth")

    # 3. Run --check probe (for freshness tracker to record success)
    try:
        check = nlm_auth_guard.run_nlm(["login", "--check", *_get_nlm_login_profile_args()], timeout_s=30)
        if check.returncode == 0:
            log_action("nlm_auth_checked", {"component": "transcript", "status": "ok"})
            rate_limiter.record_call()
            return True
    except Exception:
        pass

    # 4. Auth expired — auto-recover with force login
    try:
        rate_limiter.record_call()
        login_started = time.perf_counter()
        log_action(
            "nlm_login_started",
            {"component": "transcript", "mode": "force", "status": "started"},
        )
        login = nlm_auth_guard.run_nlm(["login", "--force", *_get_nlm_login_profile_args()], timeout_s=120)
        login_elapsed = round(time.perf_counter() - login_started, 3)
        if login.returncode == 0:
            log_action(
                "nlm_login_completed",
                {
                    "component": "transcript",
                    "mode": "force",
                    "status": "ok",
                    "elapsed_s": login_elapsed,
                },
            )
            log_action("nlm_auth_refreshed", {"component": "transcript", "status": "ok"})
            rate_limiter.record_auth_success()
            return True
        # Only --force failures count toward cooldown trigger
        log_action(
            "nlm_login_failed",
            {
                "component": "transcript",
                "mode": "force",
                "status": "failed",
                "elapsed_s": login_elapsed,
                "returncode": login.returncode,
            },
        )
        log_action(
            "nlm_auth_failed",
            {"component": "transcript", "status": "refresh_failed"},
        )
        rate_limiter.record_auth_failure()
        return False
    except Exception:
        login_elapsed = round(time.perf_counter() - login_started, 3) if "login_started" in locals() else None
        log_action(
            "nlm_login_failed",
            {
                "component": "transcript",
                "mode": "force",
                "status": "exception",
                "elapsed_s": login_elapsed,
            },
        )
        log_action(
            "nlm_auth_failed",
            {"component": "transcript", "status": "refresh_exception"},
        )
        rate_limiter.record_auth_failure()
        return False


def _parse_notebook_id(output: str) -> str | None:
    """Parse notebook ID from nlm notebook create output."""
    for line in output.strip().split("\n"):
        if "ID:" in line:
            return line.split("ID: ")[-1].strip()
    return None


def _extract_video_id_from_url(url: str) -> str | None:
    """Extract video ID from YouTube URL."""
    import re
    match = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else None


def _fetch_via_notebooklm_batch(
    video_ids: list[str],
) -> dict[str, tuple[bool, str | None, str | None]]:
    """Fetch transcripts for multiple videos using Industrial NLM batch ingest.

    Uses NLMBatchIngestor (parallel nlm source content CLI) for ~18K v/hr,
    falling back to the Selenium scraper if that path is unavailable.

    Args:
        video_ids: List of YouTube video IDs (11 chars each)

    Returns:
        dict mapping video_id -> (success, transcript_text, error)
    """
    from csf.nlm_batch import process_industrial_batch

    return process_industrial_batch(video_ids)


def _fetch_via_notebooklm(
    video_id: str, lang: str
) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using terminal-local staging notebook.

    Reuses a single persistent staging notebook across calls within the
    same process, clearing and recreating when the 300-source limit is
    approached.
    """
    scraper = _get_nlm_scraper()
    results = scraper.scrape_with_staging([video_id])
    success, transcript, error = results.get(
        video_id, (False, None, "scraper returned no result")
    )
    return (success, transcript, error)


def _fetch_via_direct_api(video_id: str) -> tuple[bool, str | None, str | None]:
    """Fetch transcript using youtube-transcript-api directly (non-Google fallback).

    This is the final fallback after all Google-adjacent sources (yt-dlp,
    Selenium, NotebookLM) have failed. youtube-transcript-api scrapes YouTube
    captions directly and may succeed where Google's ecosystem fails.

    Returns:
        (success, transcript_text, error)
    """
    def _summarize_direct_api_error(error: Exception | str) -> str:
        raw = str(error).strip()
        low = raw.lower()
        if "subtitles are disabled" in low or "no subtitles" in low:
            return "direct_api no_transcript: subtitles disabled"
        if "removed by the uploader" in low:
            return "direct_api unavailable: removed by uploader"
        if "not available in your country" in low or "geo" in low:
            return "direct_api unavailable: not available in your country"
        if "unplayable" in low or "video unavailable" in low or "private video" in low:
            return "direct_api unavailable: video unavailable"
        if "transcript could not be retrieved" in low or "no transcript available" in low:
            return "direct_api no_transcript: transcript unavailable"
        if "could not retrieve a transcript" in low:
            return "direct_api no_transcript: could not retrieve transcript"
        if "youtube transcript api" in low and "error" in low:
            return "direct_api error: youtube transcript api failure"
        if "transcript" in low and "not" in low:
            return f"direct_api no_transcript: {raw}"
        if "429" in low or "rate limit" in low or "quota" in low:
            return f"direct_api quota_exceeded: {raw}"
        return f"direct_api error: {raw}"

    try:
        import youtube_transcript_api
    except ImportError:
        logging.warning("[_fetch_via_direct_api] youtube_transcript_api not installed")
        return (False, None, "no_transcript")

    try:
        api = youtube_transcript_api.YouTubeTranscriptApi()
        api_type = type(api)
        # Prefer the installed API shape: list(video_id) returns a TranscriptList.
        # Older/newer releases have used slightly different names here, so we
        # gracefully adapt rather than pinning the whole fallback path to one
        # package version.
        if callable(getattr(api_type, "list_transcripts", None)):
            transcripts = api.list_transcripts(video_id)
        elif callable(getattr(api_type, "list", None)):
            transcripts = api.list(video_id)
        else:
            fetched = api.fetch(video_id, languages=["en"])
            transcript_text = " ".join(
                segment["text"] for segment in fetched.fetch()
            )
            if len(transcript_text) >= _NLM_MIN_CONTENT_CHARS:
                return (True, transcript_text, None)
            return (False, None, "no_transcript")

        # List available transcripts to find a non-generated English one first
        for transcript in transcripts:
            # Prefer English, non-generated
            if transcript.language_code == "en" and not transcript.is_generated:
                text_parts = []
                for segment in transcript.fetch():
                    text_parts.append(segment["text"])
                transcript_text = " ".join(text_parts)
                if len(transcript_text) >= _NLM_MIN_CONTENT_CHARS:
                    return (True, transcript_text, None)
        # Fallback: any available non-generated transcript
        for transcript in transcripts:
            if not transcript.is_generated:
                text_parts = []
                for segment in transcript.fetch():
                    text_parts.append(segment["text"])
                transcript_text = " ".join(text_parts)
                if len(transcript_text) >= _NLM_MIN_CONTENT_CHARS:
                    return (True, transcript_text, None)
        return (False, None, "no_transcript")
    except Exception as e:
        return (False, None, _summarize_direct_api_error(e))


def _persist_terminal_failure(video_id: str, error: str | None, last_stage: str | None) -> None:
    """Persist an early terminal/unavailable result so future scans skip it."""
    source = None
    try:
        source = _get_source_for_video(video_id)
    except Exception:
        source = None
    try:
        _get_scheduler().archive_finalize(video_id, "failed", None, error)
    except Exception as e:
        logging.warning(f"[transcript] Failed to archive terminal failure for {video_id}: {e}")
    try:
        _mark_failed_video(video_id, source=source, failure_reason="unavailable")
    except Exception as e:
        logging.warning(f"[transcript] Failed to mark terminal failure for {video_id}: {e}")
    try:
        _set_negative_cache(
            video_id,
            "unavailable",
            source=source,
            last_stage=last_stage,
            ttl_seconds=_NEGATIVE_CACHE_TERMINAL_TTL_SECONDS,
        )
    except Exception as e:
        logging.warning(f"[transcript] Failed to set terminal negative cache for {video_id}: {e}")


def _record_soft_negative(
    video_id: str,
    reason: str,
    *,
    last_stage: str | None,
    error: str | None,
) -> None:
    """Record a temporary negative cache entry without permanently failing the video."""
    source = None
    try:
        source = _get_source_for_video(video_id)
    except Exception:
        source = None
    try:
        _get_scheduler().archive_finalize(video_id, "failed", None, error)
    except Exception as e:
        logging.warning(f"[transcript] Failed to archive soft failure for {video_id}: {e}")
    try:
        _set_negative_cache(
            video_id,
            reason,
            source=source,
            last_stage=last_stage,
            ttl_seconds=_NEGATIVE_CACHE_SOFT_TTL_SECONDS,
        )
    except Exception as e:
        logging.warning(f"[transcript] Failed to set soft negative cache for {video_id}: {e}")


def _probe_oembed(video_id: str) -> tuple[bool, str | None]:
    """Cheap reachability probe for obvious unavailable/private/removed videos.

    Side effect: when the probe succeeds, caches the channel handle (from
    author_url) so callers that need channel info for this video_id can
    retrieve it without an additional API call.
    """
    oembed_url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "format": "json",
        }
    )
    req = urllib.request.Request(
        oembed_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if getattr(resp, "status", 200) == 200:
                import json as _json
                body = resp.read().decode()
                data = _json.loads(body)
                _cache_oembed_channel(video_id, data)
                return (True, None)
            return (False, f"oembed unavailable: HTTP {getattr(resp, 'status', 'unknown')}")
    except urllib.error.HTTPError as e:
        if e.code in {401, 403, 404, 410}:
            return (False, f"oembed unavailable: HTTP {e.code}")
        if e.code == 429:
            return (False, "oembed rate limited (429)")
        return (False, f"oembed error: HTTP {e.code}")
    except Exception as e:
        return (False, f"oembed error: {e}")


def _cache_oembed_channel(video_id: str, oembed_data: dict) -> None:
    """Cache channel info from oEmbed response into analysis_status.

    oEmbed returns author_name and author_url (channel handle). This stores
    the channel info so it's available without a separate API call. Called
    as a side effect of _probe_oembed — never blocks the transcript chain.
    """
    author_url = oembed_data.get("author_url", "")
    if not author_url:
        return
    try:
        import re as _re
        from csf.batch_status import _get_default_db_path
        import sqlite3 as _sqlite3
        # Extract channel handle or UC ID from author_url
        uc_match = _re.search(r"/channel/(UC[a-zA-Z0-9_-]{22})", author_url)
        handle_match = _re.search(r"/(@[a-zA-Z0-9_.-]+)", author_url)
        channel_id = uc_match.group(1) if uc_match else None
        author_name = oembed_data.get("author_name", "")
        db_path = _get_default_db_path()
        if not db_path.exists():
            return
        conn = _sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            if channel_id:
                conn.execute(
                    "UPDATE analysis_status SET channel_id = ? WHERE video_id = ? AND (channel_id IS NULL OR channel_id = '')",
                    (channel_id, video_id),
                )
            # Store author_name as title fallback if missing (not overwrite)
            if author_name:
                conn.execute(
                    "UPDATE analysis_status SET source = COALESCE(source, ?) WHERE video_id = ?",
                    (f"oembed:{author_name}", video_id),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Side effect must never break the transcript chain.
        pass


def _log_transcript_chain_event(action: str, video_id: str, **data: object) -> None:
    """Emit a compact transcript-chain trace event."""
    payload = {"component": "transcript", "video_id": video_id}
    payload.update({key: value for key, value in data.items() if value is not None})
    log_action(action, payload)


_EXPENSIVE_SOURCES = frozenset({_SOURCE_SELENIUM, _SOURCE_WHISPER})


def _classify_failure(error: str | None, stage: str) -> str:
    """Classify an error string into a structured failure reason.

    Extracted from fetch_transcript_chain so it can be unit-tested independently.
    """
    if not error:
        return "unknown"
    err_lower = error.lower()
    classification_text = err_lower.replace("_", " ")
    if "429" in classification_text or "rate limit" in classification_text or "quota" in classification_text:
        return "quota_exceeded"
    if "region" in classification_text or "not available" in classification_text or "geo" in classification_text:
        return "region_block"
    if "auth" in classification_text or "login" in classification_text or "credential" in classification_text:
        return "auth_failed"
    if "captcha" in classification_text or "bot detection" in classification_text:
        return "captcha"
    if "timeout" in classification_text or "timed out" in classification_text:
        return "timeout"
    if "no transcript" in classification_text or "transcript unavailable" in classification_text:
        return "no_transcript"
    if "source add failed" in classification_text or "could not add url source" in classification_text:
        return "source_add_failed"
    if "no speech detected" in classification_text or "likely music or silence" in classification_text:
        return "no_transcript"
    if "whisper produced empty transcript" in classification_text:
        return "no_transcript"
    if "unavailable" in classification_text or "deleted" in classification_text or "private" in classification_text:
        return "unavailable"
    if "not found" in classification_text or "404" in classification_text:
        return "unavailable"
    return "unknown"


def _build_none_result(
    video_id: str,
    prefer_lang: str,
    last_err: str | None = None,
    last_stage: str | None = None,
) -> TranscriptResult:
    """Build a standard 'no transcript' TranscriptResult."""
    return TranscriptResult(
        video_id=video_id,
        lang=prefer_lang,
        raw_lang=None,
        was_translated=False,
        transcript="",
        source="none",
        source_stage=None,
        detected_lang=None,
        error=last_err,
        last_stage=last_stage,
        failure_reason=_classify_failure(last_err, last_stage or ""),
    )


def _archive_failed_result(
    video_id: str,
    prefer_lang: str,
    chain_started_at: float,
    last_err: str | None,
    last_stage: str | None,
) -> TranscriptResult:
    """Log the chain failure, persist negative outcome, and return a none result."""
    failure_reason = _classify_failure(last_err, last_stage or "")
    _log_transcript_chain_event(
        "transcript_chain_failed",
        video_id,
        last_stage=last_stage,
        failure_reason=failure_reason,
        error=last_err,
        elapsed_s=round(time.perf_counter() - chain_started_at, 3),
    )
    if failure_reason == "unavailable":
        _persist_terminal_failure(video_id, last_err, last_stage)
    else:
        _record_soft_negative(
            video_id,
            failure_reason,
            last_stage=last_stage,
            error=last_err,
        )
    return _build_none_result(video_id, prefer_lang, last_err, last_stage)


def _log_stage_started(
    video_id: str,
    source: str,
    lang: str | None = None,
) -> float:
    """Log that a stage started and return the start timestamp."""
    started_at = time.perf_counter()
    _log_transcript_chain_event(
        "transcript_stage_started",
        video_id,
        stage=source,
        lang=lang,
        expensive=source in _EXPENSIVE_SOURCES,
    )
    return started_at


def _log_stage_completed(
    video_id: str,
    source: str,
    started_at: float,
    *,
    success: bool,
    error: str | None = None,
    chars: int = 0,
    lang: str | None = None,
    skipped: bool = False,
    skip_reason: str | None = None,
) -> None:
    """Log that a stage completed (success, failure, or skipped)."""
    _log_transcript_chain_event(
        "transcript_stage_completed",
        video_id,
        stage=source,
        lang=lang,
        status="skipped" if skipped else "success" if success else "failed",
        success=success,
        skipped=skipped,
        skip_reason=skip_reason,
        failure_reason=None if success else _classify_failure(error, source),
        error=error,
        chars=chars,
        elapsed_s=round(time.perf_counter() - started_at, 3),
        expensive=source in _EXPENSIVE_SOURCES,
    )
    # Persist timing to SQLite for the adaptive chain selector
    _record_stage_timing(video_id, source, time.perf_counter() - started_at, success, skipped)


def _finalize_success(
    *,
    video_id: str,
    prefer_lang: str,
    source: str,
    stage: int | None,
    transcript: str,
    raw_lang: str | None,
    config: LanguageConfig,
    info_dict: dict | None = None,
) -> TranscriptResult:
    """Build, cache, and return a successful TranscriptResult.

    Centralizes the shared success path that was previously duplicated
    across the NLM, direct_api, generic, and external provider branches.
    Handles translation, metadata extraction, and cache write.
    """
    detected_lang = raw_lang
    final_transcript = transcript
    was_translated = False

    if raw_lang is not None and raw_lang != prefer_lang and config.allow_translation:
        final_transcript = _translate_text(
            transcript, raw_lang, prefer_lang, config.translation_provider
        )
        was_translated = True

    video_metadata = _extract_video_metadata(info_dict) if info_dict else {}

    extra_metadata: dict[str, object] | None = (
        {"yt_dlp_info_dict": info_dict} if info_dict else None
    )

    result = TranscriptResult(
        video_id=video_id,
        lang=prefer_lang,
        raw_lang=raw_lang,
        was_translated=was_translated,
        transcript=final_transcript,
        source=source,
        source_stage=stage,
        detected_lang=detected_lang,
        error=None,
        last_stage=source,
        failure_reason=None,
        view_count=video_metadata.get("view_count"),
        like_count=video_metadata.get("like_count"),
        comment_count=video_metadata.get("comment_count"),
        duration=video_metadata.get("duration"),
        video_title=video_metadata.get("title"),
        video_description=video_metadata.get("description"),
    )
    set_cached_transcript(
        video_id,
        prefer_lang,
        source,
        final_transcript,
        metadata=build_transcript_cache_metadata(result, extra=extra_metadata),
    )
    return result


def _check_oembed(
    video_id: str,
    prefer_lang: str,
    chain_started_at: float,
    *,
    skip_oembed: bool = False,
) -> TranscriptResult | None:
    """Run the oEmbed reachability probe. Returns a failure result if the video
    is unavailable, or None to continue the chain.

    An explicit fallback-only recovery may bypass this cheap probe because a
    provider-side oEmbed 403 is not sufficient evidence that the transcript
    itself is unavailable. The default route remains unchanged.
    """
    oembed_enabled = os.getenv("YTIS_OEMBED_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if not oembed_enabled:
        return None
    if skip_oembed:
        _log_transcript_chain_event(
            "transcript_oembed_bypassed",
            video_id,
            enabled=True,
            reason="explicit_fallback_only",
        )
        return None
    oembed_started_at = time.perf_counter()
    oembed_ok, oembed_error = _probe_oembed(video_id)
    _log_transcript_chain_event(
        "transcript_oembed_checked",
        video_id,
        enabled=True,
        ok=oembed_ok,
        error=oembed_error,
        elapsed_s=round(time.perf_counter() - oembed_started_at, 3),
    )
    if not oembed_ok and oembed_error and "oembed unavailable" in oembed_error.lower():
        _log_transcript_chain_event(
            "transcript_chain_failed",
            video_id,
            last_stage="oembed",
            failure_reason="unavailable",
            error=oembed_error,
            elapsed_s=round(time.perf_counter() - chain_started_at, 3),
        )
        _persist_terminal_failure(video_id, oembed_error, "oembed")
        return _build_none_result(video_id, prefer_lang, oembed_error, "oembed")
    return None


def _check_whisper_admission(
    video_id: str,
    prefer_lang: str,
    admission_metadata: dict[str, object] | None,
) -> TranscriptResult | None:
    """Check Whisper admission policy. Returns a failure result to abort the chain,
    or None to proceed with the Whisper stage."""
    should_attempt, failure_reason, error = _whisper_admission_check(admission_metadata)
    if should_attempt:
        return None
    _log_transcript_chain_event(
        "transcript_whisper_admission_skipped",
        video_id,
        last_stage="whisper_admission",
        failure_reason=failure_reason,
        error=error,
    )
    if failure_reason == "unavailable":
        _persist_terminal_failure(video_id, error, "whisper_admission")
    else:
        _record_soft_negative(
            video_id,
            "no_transcript",
            last_stage="whisper_admission",
            error=error,
        )
    return _build_none_result(video_id, prefer_lang, error, "whisper_admission")


def _try_external_provider(
    video_id: str, prefer_lang: str, config: LanguageConfig,
) -> tuple[TranscriptResult | None, str | None]:
    """Try the external provider hook. Returns (result, last_error)."""
    if _external_provider is None:
        return None, None
    success, transcript, error = _external_provider(video_id, prefer_lang)
    if success and transcript:
        return _finalize_success(
            video_id=video_id, prefer_lang=prefer_lang,
            source=_SOURCE_EXTERNAL, stage=None,
            transcript=transcript, raw_lang=prefer_lang,
            config=config,
        ), None
    return None, error


def _try_source(
    source: str,
    fetch_fn,
    stage: int | None,
    video_id: str,
    prefer_lang: str,
    config: LanguageConfig,
    lang_fallbacks: list[str | None],
    chain_started_at: float,
    whisper_on_notebooklm_add_failed: bool,
) -> tuple[TranscriptResult | None, str | None]:
    """Try a single transcript source.

    Returns (result, last_error):
    - result: TranscriptResult to return (success or terminal failure), or None to continue.
    - last_error: error from the last attempt for the final failure summary.
    """
    if source == _SOURCE_NLM:
        return _try_nlm(source, fetch_fn, stage, video_id, prefer_lang, config,
                        chain_started_at, whisper_on_notebooklm_add_failed)
    elif source == _SOURCE_DIRECT_API:
        return _try_direct_api(source, fetch_fn, stage, video_id, prefer_lang, config,
                               chain_started_at)
    else:
        return _try_generic(source, fetch_fn, stage, video_id, prefer_lang, config,
                            lang_fallbacks)


def _try_nlm(
    source: str, fetch_fn, stage: int | None,
    video_id: str, prefer_lang: str, config: LanguageConfig,
    chain_started_at: float, whisper_on_notebooklm_add_failed: bool,
) -> tuple[TranscriptResult | None, str | None]:
    stage_started_at = _log_stage_started(video_id, source, "en")
    success, transcript, error = fetch_fn(video_id, "en")
    if success and transcript:
        _log_stage_completed(video_id, source, stage_started_at,
                             success=True, chars=len(transcript), lang="en")
        _record_source_success(source, video_id)
        return _finalize_success(
            video_id=video_id, prefer_lang=prefer_lang,
            source=source, stage=stage,
            transcript=transcript, raw_lang="en", config=config,
        ), None
    _log_stage_completed(video_id, source, stage_started_at, success=False, error=error, lang="en")
    if (error and not whisper_on_notebooklm_add_failed
            and _classify_failure(error, source) == "source_add_failed"):
        return _archive_failed_result(video_id, prefer_lang, chain_started_at, error, source), error
    return None, error


def _try_direct_api(
    source: str, fetch_fn, stage: int | None,
    video_id: str, prefer_lang: str, config: LanguageConfig,
    chain_started_at: float,
) -> tuple[TranscriptResult | None, str | None]:
    stage_started_at = _log_stage_started(video_id, source)
    success, transcript, error = fetch_fn(video_id)
    if success and transcript:
        _log_stage_completed(video_id, source, stage_started_at,
                             success=True, chars=len(transcript))
        _record_source_success(source, video_id)
        return _finalize_success(
            video_id=video_id, prefer_lang=prefer_lang,
            source=source, stage=stage,
            transcript=transcript, raw_lang=prefer_lang, config=config,
        ), None
    _log_stage_completed(video_id, source, stage_started_at, success=False, error=error)
    if error and ("unavailable" in error.lower() or "removed" in error.lower()
                  or "private" in error.lower()):
        _log_transcript_chain_event(
            "transcript_chain_failed", video_id,
            last_stage=source, failure_reason="unavailable", error=error,
            elapsed_s=round(time.perf_counter() - chain_started_at, 3),
        )
        _persist_terminal_failure(video_id, error, source)
        return _build_none_result(video_id, prefer_lang, error, source), error
    return None, error


def _try_generic(
    source: str, fetch_fn, stage: int | None,
    video_id: str, prefer_lang: str, config: LanguageConfig,
    lang_fallbacks: list[str | None],
) -> tuple[TranscriptResult | None, str | None]:
    last_error: str | None = None
    for lang in lang_fallbacks:
        if _transcript_deadline_exhausted():
            last_error = "transcript fallback deadline exhausted"
            break
        try_lang = lang if lang is not None else "en"
        stage_started_at = _log_stage_started(video_id, source, try_lang)
        result = fetch_fn(video_id, try_lang)
        # Normalize 3-tuple vs 4-tuple (yt-dlp carries video metadata)
        if len(result) == 4:
            success, transcript, error, info_dict = result
        else:
            success, transcript, error = result
            info_dict = {}

        if success and transcript:
            _log_stage_completed(video_id, source, stage_started_at,
                                 success=True, chars=len(transcript), lang=try_lang)
            _record_source_success(source, video_id)
            return _finalize_success(
                video_id=video_id, prefer_lang=prefer_lang,
                source=source, stage=stage,
                transcript=transcript, raw_lang=lang,
                config=config, info_dict=info_dict,
            ), None

        last_error = error
        _log_stage_completed(video_id, source, stage_started_at,
                             success=False, error=error, lang=try_lang)
        if error and ("429" in error.lower() or "rate limited" in error.lower()):
            _record_source_429(source, video_id)
            _apply_jitter_with_backoff(source)
            break  # try next method, not next lang
        else:
            _apply_jitter()
    return None, last_error


# ---------------------------------------------------------------------------
# Adaptive chain ordering — picks the fastest chain order based on batch context
# ---------------------------------------------------------------------------

# Empirical per-method parameters (from benchmark corpus + cache composition analysis)
# These are conservative estimates; actual timings vary by video length, network, etc.
# Updated at runtime from stage_timing table in transcripts.sqlite.
_METHOD_LATENCY = {
    "ytdlp":      {"per_video_s": 2.5,  "max_serial_videos": 50,  "coverage_without_captions": False},
    "ytdlp_ejs":  {"per_video_s": 3.0,  "max_serial_videos": 50,  "coverage_without_captions": False},
    "direct_api": {"per_video_s": 0.5,  "max_serial_videos": 200, "coverage_without_captions": False},
    "notebooklm": {"per_video_s": 0.95, "max_serial_videos": 99999, "coverage_without_captions": True},  # 3788 VPH / 3600 = 0.95s equiv at 3-worker throughput
    "selenium":   {"per_video_s": 20.0, "max_serial_videos": 99999, "coverage_without_captions": True},
    "whisper":    {"per_video_s": 60.0, "max_serial_videos": 99999, "coverage_without_captions": True},
}


def _ensure_stage_timing_table(conn) -> None:
    """Create the stage_timing table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stage_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            elapsed_s REAL NOT NULL,
            success INTEGER NOT NULL,
            skipped INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stage_timing_source ON stage_timing(source)"
    )
    conn.commit()


def _record_stage_timing(video_id: str, source: str, elapsed: float, success: bool, skipped: bool) -> None:
    """Record a stage timing to the SQLite stage_timing table.

    This feeds the adaptive chain selector with empirical data, replacing
    the static _METHOD_LATENCY estimates with real measurements over time.
    Never blocks the transcript chain on failure.
    """
    try:
        from csf.cache import _connect_shared_db
        conn = _connect_shared_db()
        _ensure_stage_timing_table(conn)
        from datetime import datetime, timezone
        conn.execute(
            "INSERT INTO stage_timing (source, elapsed_s, success, skipped, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (source, round(elapsed, 3), 1 if success else 0, 1 if skipped else 0,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Timing recording must never block the chain


def _get_empirical_latencies() -> dict[str, float]:
    """Read median per-source elapsed_s from stage_timing table.

    Returns source→median_seconds for sources with ≥10 samples.
    Falls back to _METHOD_LATENCY defaults for sources with insufficient data.
    """
    try:
        from csf.cache import _connect_shared_db, get_shared_db_path
        db_path = get_shared_db_path()
        if not db_path.exists():
            return {}
        conn = _connect_shared_db()
        _ensure_stage_timing_table(conn)
        rows = conn.execute(
            """SELECT source, elapsed_s FROM stage_timing
               WHERE skipped = 0 AND success = 1
               ORDER BY source, elapsed_s"""
        ).fetchall()
        conn.close()
        if not rows:
            return {}

        from collections import defaultdict
        by_source = defaultdict(list)
        for source, elapsed in rows:
            by_source[source].append(elapsed)

        result = {}
        for source, times in by_source.items():
            if len(times) >= 10:
                result[source] = times[len(times) // 2]  # median
        return result
    except Exception:
        return {}


def estimate_chain_time(
    method: str,
    batch_size: int,
    has_captions_ratio: float = 0.0,
    nlm_workers: int = 3,
) -> float:
    """Estimate wall-clock time for processing batch_size videos through one method.

    Args:
        method: Source method name (ytdlp, notebooklm, etc.)
        batch_size: Number of videos to process
        has_captions_ratio: Fraction of videos with has_captions=1 (0.0-1.0)
        nlm_workers: Number of NLM workers (affects throughput for notebooklm)

    Returns:
        Estimated wall-clock seconds
    """
    params = _METHOD_LATENCY.get(method)
    if not params:
        return float("inf")

    # Coverage gate: methods that need captions get penalized for no-caption videos
    if not params["coverage_without_captions"] and has_captions_ratio < 0.5:
        # Method needs captions but most videos don't have them.
        # The time spent is wasted (no transcripts produced), so it's pure overhead
        # before the real work starts. Penalize heavily so universal methods go first.
        effective_videos = min(batch_size, params["max_serial_videos"])
        return float("inf")  # never try caption-gated methods first for no-caption videos

    if method == "notebooklm":
        # NLM throughput scales with workers: 3788 VPH at 3 workers
        vph = 3788 * (nlm_workers / 3) if nlm_workers > 0 else 3788
        per_video_at_scale = 3600 / vph
        # For small batches, per-source overhead is lower (may already be in notebook)
        setup_s = 5.0 if batch_size <= 10 else 15.0
        return setup_s + batch_size * per_video_at_scale

    # Serial method
    effective_videos = min(batch_size, params["max_serial_videos"])
    return effective_videos * params["per_video_s"]


def build_adaptive_chain(
    skip_notebooklm: bool = False,
    batch_size: int = 1,
    has_captions_ratio: float = 0.0,
    nlm_workers: int = 3,
) -> list[str]:
    """Build the optimal transcript source chain for the current batch.

    Orders sources by estimated total time. For large batches with low
    caption ratios (the common case: 99.86% of backlog has no captions),
    NotebookLM goes first because it has the highest throughput and doesn't
    require captions.

    Uses empirical timing data from the stage_timing table when available
    (≥10 samples per source), falling back to static estimates otherwise.

    Args:
        skip_notebooklm: If True, exclude NotebookLM from the chain
        batch_size: Number of videos to process in this batch
        has_captions_ratio: Fraction of videos with has_captions=1
        nlm_workers: Number of NLM workers available

    Returns:
        Ordered list of source method names
    """
    # Refresh latencies from empirical data
    empirical = _get_empirical_latencies()
    for source, median_s in empirical.items():
        if source in _METHOD_LATENCY and median_s > 0:
            _METHOD_LATENCY[source]["per_video_s"] = median_s

    all_methods = [
        _SOURCE_YTDLP,
        _SOURCE_YTDLP_EJS,
        _SOURCE_DIRECT_API,
        _SOURCE_SELENIUM,
        _SOURCE_WHISPER,
    ]
    if not skip_notebooklm:
        all_methods.append(_SOURCE_NLM)

    # Estimate time for each method and sort by ascending estimated time
    timed = [
        (m, estimate_chain_time(m, batch_size, has_captions_ratio, nlm_workers))
        for m in all_methods
    ]
    timed.sort(key=lambda x: x[1])

    return [m for m, _ in timed]


def _build_methods_to_try(
    skip_notebooklm: bool,
    has_captions_ratio: float = 0.0,
) -> list[tuple]:
    """Build the ordered (source, fetch_fn, stage) list for the transcript chain.

    Uses build_adaptive_chain to pick the optimal order, then maps to
    (source, fetch_fn, stage_version) tuples.
    """
    chain_order = build_adaptive_chain(
        skip_notebooklm=skip_notebooklm,
        batch_size=1,  # Per-video call — batch context is handled by the caller
        has_captions_ratio=has_captions_ratio,
    )

    _FETCH_MAP = {
        _SOURCE_YTDLP: (_fetch_via_ytdlp, STAGE_VERSION_YTDLP),
        _SOURCE_YTDLP_EJS: (_fetch_via_ytdlp_with_cookies, STAGE_VERSION_EJS),
        _SOURCE_DIRECT_API: (_fetch_via_direct_api, STAGE_VERSION_DIRECT_API),
        _SOURCE_NLM: (_fetch_via_notebooklm, STAGE_VERSION_NOTEBOOKLM),
        _SOURCE_SELENIUM: (_fetch_via_selenium_firefox, STAGE_VERSION_SELENIUM),
        _SOURCE_WHISPER: (_fetch_via_whisper, None),
    }

    methods = []
    for source in chain_order:
        if source in _FETCH_MAP:
            fetch_fn, stage = _FETCH_MAP[source]
            methods.append((source, fetch_fn, stage))

    return methods


def _lookup_has_captions(video_id: str) -> bool | None:
    """Look up has_captions from analysis_status for this video.

    Returns True/False if the field is set, or None if the video isn't
    in analysis_status or the column doesn't exist. Used to activate
    the adaptive chain without requiring callers to pass has_captions.
    Never blocks — wraps all DB access in try/except.
    """
    try:
        from csf.batch_status import _get_default_db_path
        import sqlite3 as _sql
        db_path = _get_default_db_path()
        if not db_path.exists():
            return None
        conn = _sql.connect(str(db_path), timeout=3.0)
        conn.execute("PRAGMA busy_timeout=2000")
        try:
            cursor = conn.execute(
                "SELECT has_captions FROM analysis_status WHERE video_id = ?",
                (video_id,),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                return bool(row[0])
            return None
        finally:
            conn.close()
    except Exception:
        return None


def fetch_transcript_chain(
    video_id: str,
    config: LanguageConfig,
    *,
    skip_notebooklm: bool = False,
    skip_oembed: bool = False,
    admission_metadata: dict[str, object] | None = None,
    has_captions: bool | int | None = None,
) -> TranscriptResult:
    """Fetch transcript using the full fallback chain.

    Chain order is **adaptive**: build_adaptive_chain picks the fastest order
    based on whether the video has captions and the batch context. For the
    common case (no captions, large backlog), NotebookLM goes first because
    it has the highest throughput (3,788 VPH at 3 workers) and doesn't
    require captions.

    Args:
        video_id: YouTube video ID (must be 11 chars)
        config: LanguageConfig specifying prefer_lang and allow_translation
        skip_notebooklm: If True, skip the NotebookLM stage and fall back to
            Selenium, Whisper, and direct API only.
        skip_oembed: If True, bypass the oEmbed preflight. This is reserved for
            an explicit fallback-only recovery route; the default is False.
        admission_metadata: Optional cheap metadata used to decide whether
            Whisper should run for this candidate.

    Returns:
        TranscriptResult with all fields populated including detected_lang.
        On complete failure, returns TranscriptResult with empty transcript,
        source='none', and was_translated=False.
    """
    prefer_lang = config.prefer_lang

    # Validate video_id
    if not _validate_video_id(video_id):
        return TranscriptResult(
            video_id=video_id,
            lang=prefer_lang,
            raw_lang=None,
            was_translated=False,
            transcript="",
            source="none",
            source_stage=None,
            detected_lang=None,
            error="invalid video_id format",
        )

    # BLOCKER-13: Validate BCP-47 before any API calls
    try:
        _validate_bcp47(prefer_lang)
    except ValueError:
        return TranscriptResult(
            video_id=video_id,
            lang=prefer_lang,
            raw_lang=None,
            was_translated=False,
            transcript="",
            source="none",
            source_stage=None,
            detected_lang=None,
            error=f"invalid BCP-47 language code: {prefer_lang!r}",
            last_stage=None,
            failure_reason="invalid_config",
        )

    chain_started_at = time.perf_counter()

    whisper_enabled = os.getenv("YTIS_WHISPER_ENABLED", "true").lower() == "true"

    oembed_result = _check_oembed(
        video_id,
        prefer_lang,
        chain_started_at,
        skip_oembed=skip_oembed,
    )
    if oembed_result is not None:
        return oembed_result

    # Language fallback order: prefer_lang → en → None (any available)
    lang_fallbacks: list[str | None] = [prefer_lang]
    if prefer_lang != "en":
        lang_fallbacks.append("en")
    lang_fallbacks.append(None)  # Any available language

    last_error: str | None = None
    last_stage_reached: str | None = None
    runtime_config = get_nlm_config()
    expensive_fallback_enabled = runtime_config.transcript_expensive_fallback_enabled
    whisper_on_notebooklm_add_failed = runtime_config.whisper_on_notebooklm_add_failed

    # Adaptive chain: order methods by estimated total time.
    # For no-caption videos (99.86% of backlog), NotebookLM goes first.
    # For captioned videos, yt-dlp goes first (faster for small batches).
    # When has_captions is None (unknown), try to look it up from analysis_status.
    # If lookup also fails (no DB, test mode, video not found), use old chain.
    if has_captions is None:
        has_captions = _lookup_has_captions(video_id)

    if has_captions is not None:
        captions_ratio = 1.0 if has_captions in (True, 1) else 0.0
        methods_to_try = _build_methods_to_try(
            skip_notebooklm=skip_notebooklm,
            has_captions_ratio=captions_ratio,
        )
    else:
        # Caption status unknown — use old hardcoded order (backward compat)
        methods_to_try = [
            (_SOURCE_YTDLP, _fetch_via_ytdlp, STAGE_VERSION_YTDLP),
            (_SOURCE_YTDLP_EJS, _fetch_via_ytdlp_with_cookies, STAGE_VERSION_EJS),
            (_SOURCE_DIRECT_API, _fetch_via_direct_api, STAGE_VERSION_DIRECT_API),
            (_SOURCE_SELENIUM, _fetch_via_selenium_firefox, STAGE_VERSION_SELENIUM),
            (_SOURCE_WHISPER, _fetch_via_whisper, None),
        ]
        if not skip_notebooklm:
            methods_to_try.insert(3, (_SOURCE_NLM, _fetch_via_notebooklm, STAGE_VERSION_NOTEBOOKLM))

    for source, fetch_fn, stage in methods_to_try:
        if _transcript_deadline_exhausted():
            last_error = "transcript fallback deadline exhausted"
            break
        if _is_source_rate_limited(source):
            continue
        if source in _EXPENSIVE_SOURCES and not expensive_fallback_enabled:
            stage_started_at = _log_stage_started(video_id, source)
            _log_stage_completed(
                video_id, source, stage_started_at,
                success=False, skipped=True,
                skip_reason="expensive_fallback_disabled",
            )
            continue
        if source == _SOURCE_WHISPER:
            if not whisper_enabled:
                continue
            whisper_result = _check_whisper_admission(video_id, prefer_lang, admission_metadata)
            if whisper_result is not None:
                return whisper_result

        last_stage_reached = source

        result, error = _try_source(
            source, fetch_fn, stage, video_id, prefer_lang, config,
            lang_fallbacks, chain_started_at, whisper_on_notebooklm_add_failed,
        )
        if result is not None:
            return result
        if error:
            last_error = error

    # Do not start another provider after an expensive child deadline expires.
    # The remaining margin belongs to terminal classification and result
    # serialization, not another network/model attempt.
    if _transcript_deadline_exhausted():
        return _archive_failed_result(
            video_id,
            prefer_lang,
            chain_started_at,
            last_error or "transcript fallback deadline exhausted",
            last_stage_reached,
        )

    # External provider hook — last chance before giving up
    ext_result, ext_error = _try_external_provider(video_id, prefer_lang, config)
    if ext_result is not None:
        return ext_result
    if ext_error:
        last_error = ext_error
        last_stage_reached = _SOURCE_EXTERNAL

    # All methods failed; persist the final negative outcome using the same
    # terminal/soft-cache contract as early failure exits.
    return _archive_failed_result(video_id, prefer_lang, chain_started_at, last_error, last_stage_reached)


def _transcript_worker_main(argv: list[str] | None = None) -> int:
    """Run one transcript chain for the coordinator-owned item boundary."""
    parser = argparse.ArgumentParser(description="Run one transcript fallback item")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    parser.add_argument("--skip-notebooklm", action="store_true")
    parser.add_argument("--skip-oembed", action="store_true")
    parser.add_argument("--admission-metadata", default="null")
    parser.add_argument(
        "--deadline-s",
        type=float,
        default=None,
        help="Coordinator-owned child deadline used to budget expensive fallback stages",
    )
    args = parser.parse_args(argv)
    deadline_token = None
    if args.deadline_s is not None and math.isfinite(args.deadline_s) and args.deadline_s > 0:
        deadline_token = _TRANSCRIPT_DEADLINE_MONOTONIC.set(
            time.monotonic() + args.deadline_s
        )
    try:
        admission_metadata = json.loads(args.admission_metadata)
        if not isinstance(admission_metadata, dict):
            admission_metadata = None
        result = fetch_transcript_chain(
            args.video_id,
            LanguageConfig(prefer_lang="en", allow_translation=False),
            skip_notebooklm=args.skip_notebooklm,
            skip_oembed=args.skip_oembed,
            admission_metadata=admission_metadata,
        )
        payload = {field.name: getattr(result, field.name) for field in fields(TranscriptResult)}
        _write_json_result_atomically(args.result_path, payload)
        return 0
    except Exception as exc:
        _write_json_result_atomically(
            args.result_path,
            {
                "video_id": args.video_id,
                "lang": "en",
                "raw_lang": None,
                "was_translated": False,
                "transcript": "",
                "source": "none",
                "error": f"transcript worker error: {type(exc).__name__}: {exc}",
                "failure_reason": "unknown",
                "last_stage": "transcript_fallback",
            },
        )
        return 1
    finally:
        if deadline_token is not None:
            _TRANSCRIPT_DEADLINE_MONOTONIC.reset(deadline_token)


if __name__ == "__main__":
    raise SystemExit(_transcript_worker_main())
