"""Centralized yt-is configuration with typed, validated env vars.

Addresses the env-var-sprawl anti-pattern: 20+ YTIS_* variables scattered
across modules with no central validation. This module provides a factory
that reads env vars at call time, plus a default singleton.

Usage:
    from csf.config import get_settings, settings
    s = get_settings()  # reads current env vars
    settings.visual_max_downloads_per_hour  # import-time snapshot
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int, min_val: int = 0, max_val: int = 10**9) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
        return max(min_val, min(max_val, v))
    except ValueError:
        return default


def _float(name: str, default: float, min_val: float = 0.0) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
        return max(min_val, v)
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes")


@dataclass(frozen=True)
class Settings:
    """All yt-is environment-variable-driven settings, validated."""

    visual_max_downloads_per_hour: int = 30
    visual_sleep_min_s: float = 20.0
    visual_sleep_max_s: float = 90.0
    visual_sleep_requests_s: float = 2.0
    visual_limit_rate: str = "8M"
    visual_max_height: int = 1080
    visual_download_timeout_s: float = 900.0
    visual_cooldown_s: float = 300.0
    visual_cookies_from_browser: str = "firefox"
    visual_enqueue_min_score: float = 1.0
    visual_frame_cap: int = 240
    visual_scene_threshold: float = 0.30
    visual_floor_interval_s: float = 20.0
    visual_pass1_width: int = 640
    whisper_model: str = "large-v3-turbo"
    whisper_cpu_model: str = "medium"
    ocr_gpu: str = "auto"
    clip_device: str = "auto"
    visual_js_runtime: str = ""
    whisper_enabled: bool = True
    whisper_audio_download_timeout_s: float = 300.0
    oembed_enabled: bool = False
    transcript_worker_jitter_min_s: float = 2.0
    transcript_worker_jitter_max_s: float = 10.0
    visual_extract_engine: str = "agy-first"
    visual_transcribe_timeout_s: float = 900.0

    def validate(self) -> list[str]:
        """Return a list of validation warnings (empty = all valid)."""
        warnings = []
        if self.visual_sleep_max_s < self.visual_sleep_min_s:
            warnings.append(
                f"visual_sleep_max_s ({self.visual_sleep_max_s}) < "
                f"visual_sleep_min_s ({self.visual_sleep_min_s})"
            )
        if self.visual_scene_threshold > 1.0:
            warnings.append(f"visual_scene_threshold ({self.visual_scene_threshold}) > 1.0")
        if self.transcript_worker_jitter_max_s < self.transcript_worker_jitter_min_s:
            warnings.append("transcript_worker_jitter_max_s < min_s")
        if self.visual_cookies_from_browser and self.visual_cookies_from_browser not in (
            "firefox", "chrome", "brave", "edge", "chromium", "safari", "opera"
        ):
            warnings.append(
                f"visual_cookies_from_browser ({self.visual_cookies_from_browser}) "
                "is not a recognized browser name"
            )
        return warnings


def get_settings() -> Settings:
    """Read current env vars and return a validated Settings instance."""
    return Settings(
        visual_max_downloads_per_hour=_int("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", 30, 1, 1000),
        visual_sleep_min_s=_float("YTIS_VISUAL_SLEEP_MIN_S", 20.0, 1.0),
        visual_sleep_max_s=_float("YTIS_VISUAL_SLEEP_MAX_S", 90.0, 1.0),
        visual_sleep_requests_s=_float("YTIS_VISUAL_SLEEP_REQUESTS_S", 2.0, 0.1),
        visual_limit_rate=_str("YTIS_VISUAL_LIMIT_RATE", "8M"),
        visual_max_height=_int("YTIS_VISUAL_MAX_HEIGHT", 1080, 144, 4320),
        visual_download_timeout_s=_float("YTIS_VISUAL_DOWNLOAD_TIMEOUT_S", 900.0, 30.0),
        visual_cooldown_s=_float("YTIS_VISUAL_COOLDOWN_S", 300.0, 30.0),
        visual_cookies_from_browser=_str("YTIS_VISUAL_COOKIES_FROM_BROWSER", "firefox"),
        visual_enqueue_min_score=_float("YTIS_VISUAL_ENQUEUE_MIN_SCORE", 1.0, 0.0),
        visual_frame_cap=_int("YTIS_VISUAL_FRAME_CAP", 240, 10),
        visual_scene_threshold=_float("YTIS_VISUAL_SCENE_THRESHOLD", 0.30, 0.01),
        visual_floor_interval_s=_float("YTIS_VISUAL_FLOOR_INTERVAL_S", 20.0, 1.0),
        visual_pass1_width=_int("YTIS_VISUAL_PASS1_WIDTH", 640, 320, 1920),
        whisper_model=_str("YTIS_WHISPER_MODEL", "large-v3-turbo"),
        whisper_cpu_model=_str("YTIS_WHISPER_CPU_MODEL", "medium"),
        ocr_gpu=_str("YTIS_OCR_GPU", "auto"),
        clip_device=_str("YTIS_CLIP_DEVICE", "auto"),
        visual_js_runtime=_str("YTIS_VISUAL_JS_RUNTIME", ""),
        whisper_enabled=_bool("YTIS_WHISPER_ENABLED", True),
        whisper_audio_download_timeout_s=_float("YTIS_WHISPER_AUDIO_DOWNLOAD_TIMEOUT_S", 300.0, 30.0),
        oembed_enabled=_bool("YTIS_OEMBED_ENABLED", False),
        transcript_worker_jitter_min_s=_float("YTIS_TRANSCRIPT_WORKER_JITTER_MIN_S", 2.0, 0.0),
        transcript_worker_jitter_max_s=_float("YTIS_TRANSCRIPT_WORKER_JITTER_MAX_S", 10.0, 0.0),
        visual_extract_engine=_str("YTIS_VISUAL_EXTRACT_ENGINE", "agy-first"),
        visual_transcribe_timeout_s=_float("YTIS_VISUAL_TRANSCRIBE_TIMEOUT_S", 900.0, 60.0),
    )


# Import-time snapshot for code that doesn't need fresh env reads
settings = get_settings()

# Log validation warnings
_warnings = settings.validate()
if _warnings:
    import logging
    logging.getLogger(__name__).warning(
        "yt-is config validation warnings: %s", "; ".join(_warnings)
    )
