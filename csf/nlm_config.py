"""Shared NotebookLM runtime configuration for yt-is.

This module centralizes the NotebookLM notebook policy and auth policy so the
rest of the codebase can import a single source of truth for NotebookLM
settings.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


_nlm_config_lock = threading.Lock()
_nlm_config: "NLMConfig | None" = None


@dataclass(frozen=True)
class NLMConfig:
    """Runtime configuration for NotebookLM operations."""

    notebook_batch_size: int = 50
    notebook_source_cap: int = 50
    notebook_source_materialization_timeout_s: int = 600
    max_sources_per_notebook: int = 300
    transcript_worker_jitter_min_s: float = 2.0
    transcript_worker_jitter_max_s: float = 10.0
    auth_check_interval: float = 60.0
    auth_max_calls_per_window: int = 10
    auth_cooldown: float = 300.0
    browser_profile_mode: str = "persistent"
    browser_profile_name: str = "notebooklm"
    browser_profile_seed_root: str = "P:\\\\\\.data/yt-is/notebooklm-browser-session"
    nlm_browser_mode: str = "persistent"
    nlm_browser_profile_root: str = r"P:\\\\\\.data\yt-is\browser\notebooklm"
    nlm_browser_profile_directory: str = ""
    nlm_browser_executable: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    nlm_browser_channel: str = "chrome"
    nlm_browser_bootstrap_headless: bool = False
    nlm_browser_start_timeout_ms: int = 30000
    nlm_preflight_url_timeout_ms: int = 60000
    nlm_preflight_ui_timeout_ms: int = 15000
    selenium_profile_retention_count: int = 8
    selenium_profile_retention_max_age_days: int = 7
    selenium_profile_lease_stale_after_s: int = 900
    source_content_retry_attempts: int = 4
    source_content_retry_initial_delay_s: float = 1.0
    source_content_retry_max_delay_s: float = 8.0
    source_content_retry_budget_s: float = 30.0
    source_content_retry_queue_delay_s: float = 30.0
    source_content_retry_queue_budget_s: float = 30.0
    source_content_retry_queue_age_margin_s: float = 0.0
    source_content_shared_retry_pool_enabled: bool = False
    transcript_expensive_fallback_enabled: bool = True
    whisper_on_notebooklm_add_failed: bool = True
    reusable_cleanup_every_n_batches: int = 1
    reusable_active_window_size: int = 0
    reusable_extract_window_size: int = 0
    reusable_source_age_cadence_enabled: bool = False
    reusable_source_age_cadence_soft_threshold_s: float = 160.0
    reusable_source_age_cadence_hard_threshold_s: float = 190.0
    reusable_source_age_cadence_min_window_size: int = 5


def get_nlm_config() -> NLMConfig:
    """Return the singleton NotebookLM config, initializing from env vars."""
    global _nlm_config
    with _nlm_config_lock:
        if _nlm_config is None:
            run_environment_label = (
                os.environ.get("YTIS_NLM_RUN_ENVIRONMENT_LABEL")
                or os.environ.get("YTIS_RUN_ENVIRONMENT_LABEL")
                or ""
            ).strip().lower()
            shared_retry_pool_env = os.environ.get("YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED")
            shared_retry_pool_enabled = (
                shared_retry_pool_env.strip().lower() in {"1", "true", "yes", "on"}
                if shared_retry_pool_env is not None
                else run_environment_label == "hotel_wifi"
            )
            _nlm_config = NLMConfig(
                notebook_batch_size=int(os.environ.get("YTIS_NLM_BATCH_SIZE", "50")),
                notebook_source_cap=int(os.environ.get("YTIS_NLM_SOURCE_CAP", "50")),
                notebook_source_materialization_timeout_s=int(
                    os.environ.get("YTIS_NLM_SOURCE_MATERIALIZATION_TIMEOUT_S", "600")
                ),
                max_sources_per_notebook=int(
                    os.environ.get("YTIS_NLM_MAX_SOURCES_PER_NOTEBOOK", "300")
                ),
                transcript_worker_jitter_min_s=float(
                    os.environ.get("YTIS_TRANSCRIPT_WORKER_JITTER_MIN_S", "2.0")
                ),
                transcript_worker_jitter_max_s=float(
                    os.environ.get("YTIS_TRANSCRIPT_WORKER_JITTER_MAX_S", "10.0")
                ),
                auth_check_interval=float(os.environ.get("YTIS_NLM_AUTH_CHECK_INTERVAL", "60.0")),
                auth_max_calls_per_window=int(
                    os.environ.get("YTIS_NLM_AUTH_MAX_CALLS_PER_WINDOW", "10")
                ),
                auth_cooldown=float(os.environ.get("YTIS_NLM_AUTH_COOLDOWN", "300.0")),
                browser_profile_mode=os.environ.get("YTIS_NLM_BROWSER_PROFILE_MODE", "persistent").strip().lower()
                or "persistent",
                browser_profile_name=os.environ.get("YTIS_NLM_BROWSER_PROFILE_NAME", "notebooklm").strip()
                or "notebooklm",
                browser_profile_seed_root=os.environ.get(
                    "YTIS_NLM_BROWSER_PROFILE_SEED_ROOT",
                    "P:\\\\\\.data/yt-is/notebooklm-browser-session",
                ).strip()
                or "P:\\\\\\.data/yt-is/notebooklm-browser-session",
                nlm_browser_mode=os.environ.get("YTIS_NLM_BROWSER_MODE", "persistent").strip().lower()
                or "persistent",
                nlm_browser_profile_root=os.environ.get(
                    "YTIS_NLM_BROWSER_PROFILE_ROOT",
                    r"P:\\\\\\.data\yt-is\browser\notebooklm",
                ).strip()
                or r"P:\\\\\\.data\yt-is\browser\notebooklm",
                nlm_browser_profile_directory=os.environ.get(
                    "YTIS_NLM_BROWSER_PROFILE_DIRECTORY",
                    "",
                ).strip(),
                nlm_browser_executable=os.environ.get(
                    "YTIS_NLM_BROWSER_EXECUTABLE",
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                ).strip()
                or r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                nlm_browser_channel=os.environ.get("YTIS_NLM_BROWSER_CHANNEL", "chrome").strip().lower()
                or "chrome",
                nlm_browser_bootstrap_headless=(
                    os.environ.get("YTIS_NLM_BROWSER_BOOTSTRAP_HEADLESS", "false").strip().lower()
                    in {"1", "true", "yes", "on"}
                ),
                nlm_browser_start_timeout_ms=int(
                    os.environ.get("YTIS_NLM_BROWSER_START_TIMEOUT_MS", "30000")
                ),
                nlm_preflight_url_timeout_ms=int(
                    os.environ.get("YTIS_NLM_PRELIGHT_URL_TIMEOUT_MS", os.environ.get("YTIS_NLM_PREFLIGHT_URL_TIMEOUT_MS", "60000"))
                ),
                nlm_preflight_ui_timeout_ms=int(
                    os.environ.get("YTIS_NLM_PREFLIGHT_UI_TIMEOUT_MS", "15000")
                ),
                selenium_profile_retention_count=max(
                    0,
                    int(os.environ.get("YTIS_SELENIUM_PROFILE_RETENTION_COUNT", "8")),
                ),
                selenium_profile_retention_max_age_days=max(
                    0,
                    int(os.environ.get("YTIS_SELENIUM_PROFILE_RETENTION_MAX_AGE_DAYS", "7")),
                ),
                selenium_profile_lease_stale_after_s=max(
                    60,
                    int(os.environ.get("YTIS_SELENIUM_PROFILE_LEASE_STALE_AFTER_S", "900")),
                ),
                source_content_retry_attempts=int(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_ATTEMPTS", "4")
                ),
                source_content_retry_initial_delay_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_INITIAL_DELAY_S", "1.0")
                ),
                source_content_retry_max_delay_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_MAX_DELAY_S", "8.0")
                ),
                source_content_retry_budget_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_BUDGET_S", "30.0")
                ),
                source_content_retry_queue_delay_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", "30.0")
                ),
                source_content_retry_queue_budget_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", "30.0")
                ),
                source_content_retry_queue_age_margin_s=float(
                    os.environ.get("YTIS_NLM_SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S", "0.0")
                ),
                source_content_shared_retry_pool_enabled=shared_retry_pool_enabled,
                transcript_expensive_fallback_enabled=(
                    os.environ.get("YTIS_TRANSCRIPT_EXPENSIVE_FALLBACK_ENABLED", "true")
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "on"}
                ),
                whisper_on_notebooklm_add_failed=(
                    os.environ.get("YTIS_WHISPER_ON_NOTEBOOKLM_ADD_FAILED", "true")
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "on"}
                ),
                reusable_cleanup_every_n_batches=max(
                    1,
                    int(os.environ.get("YTIS_NLM_REUSABLE_CLEANUP_EVERY_N_BATCHES", "1")),
                ),
                reusable_active_window_size=max(
                    0,
                    int(os.environ.get("YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE", "0")),
                ),
                reusable_extract_window_size=max(
                    0,
                    int(os.environ.get("YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE", "0")),
                ),
                reusable_source_age_cadence_enabled=(
                    os.environ.get("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED", "false")
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "on"}
                ),
                reusable_source_age_cadence_soft_threshold_s=float(
                    os.environ.get("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_SOFT_THRESHOLD_S", "160.0")
                ),
                reusable_source_age_cadence_hard_threshold_s=float(
                    os.environ.get("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_HARD_THRESHOLD_S", "190.0")
                ),
                reusable_source_age_cadence_min_window_size=max(
                    1,
                    int(os.environ.get("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_MIN_WINDOW_SIZE", "5")),
                ),
            )
        return _nlm_config


def set_nlm_config(config: NLMConfig) -> None:
    """Set the singleton NotebookLM config (primarily for tests)."""
    global _nlm_config
    with _nlm_config_lock:
        _nlm_config = config


def reset_nlm_config() -> None:
    """Clear the singleton NotebookLM config so it reloads from env on demand."""
    global _nlm_config
    with _nlm_config_lock:
        _nlm_config = None


def get_transcript_worker_jitter_bounds() -> tuple[float, float]:
    """Return the worker jitter bounds used by transcript and scheduler paths."""
    cfg = get_nlm_config()
    bounds = sorted(
        (
            float(cfg.transcript_worker_jitter_min_s),
            float(cfg.transcript_worker_jitter_max_s),
        )
    )
    return bounds[0], bounds[1]
