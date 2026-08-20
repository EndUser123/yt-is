"""Tests for csf/config.py — centralized environment variable settings."""

from __future__ import annotations

import os
import pytest

from csf.config import Settings, get_settings, settings


def test_default_settings_load():
    """Default settings object has expected values."""
    assert settings.visual_max_downloads_per_hour == 30
    assert settings.visual_sleep_min_s == 20.0
    assert settings.visual_cookies_from_browser == "firefox"
    assert settings.whisper_model == "large-v3-turbo"
    assert settings.ocr_gpu == "auto"


def test_env_override(monkeypatch):
    """Environment variables override defaults."""
    monkeypatch.setenv("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", "50")
    monkeypatch.setenv("YTIS_WHISPER_MODEL", "medium")
    s = get_settings()
    assert s.visual_max_downloads_per_hour == 50
    assert s.whisper_model == "medium"


def test_invalid_env_falls_back_to_default(monkeypatch):
    """Non-numeric values fall back to defaults without crashing."""
    monkeypatch.setenv("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", "not_a_number")
    s = Settings()
    assert s.visual_max_downloads_per_hour == 30


def test_bounds_clamping(monkeypatch):
    """Values are clamped to valid ranges."""
    monkeypatch.setenv("YTIS_VISUAL_MAX_DOWNLOADS_PER_HOUR", "-5")
    s = Settings()
    assert s.visual_max_downloads_per_hour >= 1  # clamped to min


def test_bool_parsing(monkeypatch):
    """Boolean env vars parse true/false/1/0 correctly."""
    monkeypatch.setenv("YTIS_WHISPER_ENABLED", "false")
    monkeypatch.setenv("YTIS_OEMBED_ENABLED", "true")
    s = get_settings()
    assert s.whisper_enabled is False
    assert s.oembed_enabled is True

    monkeypatch.setenv("YTIS_WHISPER_ENABLED", "1")
    s2 = get_settings()
    assert s2.whisper_enabled is True


def test_frozen():
    """Settings object is immutable (frozen dataclass)."""
    with pytest.raises(AttributeError):
        settings.visual_max_downloads_per_hour = 99


def test_ocr_and_clip_shutdown():
    """GPU singleton cleanup functions exist and are callable."""
    from csf.ocr_client import shutdown as ocr_shutdown
    from csf.clip_client import shutdown as clip_shutdown
    assert callable(ocr_shutdown)
    assert callable(clip_shutdown)
    # Calling them should not raise even when models aren't loaded
    ocr_shutdown()
    clip_shutdown()
