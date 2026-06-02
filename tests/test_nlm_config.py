"""Tests for the shared NotebookLM config module."""

from csf import batch, nlm_config, transcript


class TestSharedNlmConfig:
    """The NotebookLM config should live in one shared module."""

    def test_defaults_cover_batch_and_auth_policy(self):
        """The shared config should expose the batch and auth defaults together."""
        cfg = nlm_config.get_nlm_config()
        assert cfg.notebook_batch_size == 50
        assert cfg.notebook_source_cap == 50
        assert cfg.notebook_source_materialization_timeout_s == 600
        assert cfg.max_sources_per_notebook == 300
        assert cfg.transcript_worker_jitter_min_s == 2.0
        assert cfg.transcript_worker_jitter_max_s == 10.0
        assert cfg.auth_check_interval == 60.0
        assert cfg.auth_max_calls_per_window == 10
        assert cfg.auth_cooldown == 300.0
        assert cfg.browser_profile_mode == "persistent"
        assert cfg.browser_profile_name == "notebooklm"
        assert cfg.browser_profile_seed_root.endswith("notebooklm-browser-session")
        assert cfg.nlm_browser_mode == "persistent"
        assert cfg.nlm_browser_profile_root.endswith(r"browser\notebooklm")
        assert cfg.nlm_browser_executable.endswith(r"chrome.exe")
        assert cfg.nlm_browser_channel == "chrome"
        assert cfg.nlm_browser_bootstrap_headless is False
        assert cfg.nlm_browser_start_timeout_ms == 30000
        assert cfg.nlm_preflight_url_timeout_ms == 60000
        assert cfg.nlm_preflight_ui_timeout_ms == 15000
        assert cfg.source_content_retry_attempts == 4
        assert cfg.source_content_retry_initial_delay_s == 1.0
        assert cfg.source_content_retry_max_delay_s == 8.0
        assert cfg.source_content_retry_budget_s == 30.0
        assert cfg.source_content_retry_queue_delay_s == 30.0
        assert cfg.source_content_retry_queue_budget_s == 30.0
        assert cfg.source_content_retry_queue_age_margin_s == 0.0
        assert cfg.source_content_shared_retry_pool_enabled is False
        assert cfg.transcript_expensive_fallback_enabled is True
        assert cfg.whisper_on_notebooklm_add_failed is True
        assert cfg.reusable_cleanup_every_n_batches == 1
        assert cfg.reusable_active_window_size == 0
        assert cfg.reusable_extract_window_size == 0
        assert cfg.reusable_source_age_cadence_enabled is False
        assert cfg.reusable_source_age_cadence_soft_threshold_s == 160.0
        assert cfg.reusable_source_age_cadence_hard_threshold_s == 190.0
        assert cfg.reusable_source_age_cadence_min_window_size == 5
        assert transcript.get_nlm_config() is cfg

    def test_hotel_environment_defaults_shared_retry_pool_on(self, monkeypatch):
        """Hotel-scoped runs should default the shared retry pool on unless overridden."""
        monkeypatch.setenv("YTIS_NLM_RUN_ENVIRONMENT_LABEL", "hotel_wifi")
        monkeypatch.delenv("YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", raising=False)
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.source_content_shared_retry_pool_enabled is True
        finally:
            nlm_config.reset_nlm_config()

    def test_reusable_active_window_size_follows_env(self, monkeypatch):
        """Reusable workers should get their active window size from shared config."""
        monkeypatch.setenv("YTIS_NLM_REUSABLE_ACTIVE_WINDOW_SIZE", "17")
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.reusable_active_window_size == 17
        finally:
            nlm_config.reset_nlm_config()

    def test_reusable_extract_window_size_follows_env(self, monkeypatch):
        """Reusable workers should get their extract window size from shared config."""
        monkeypatch.setenv("YTIS_NLM_REUSABLE_EXTRACT_WINDOW_SIZE", "19")
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.reusable_extract_window_size == 19
        finally:
            nlm_config.reset_nlm_config()

    def test_reusable_source_age_cadence_follows_env(self, monkeypatch):
        """Reusable workers should get the age-aware cadence settings from shared config."""
        monkeypatch.setenv("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED", "true")
        monkeypatch.setenv("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_SOFT_THRESHOLD_S", "155")
        monkeypatch.setenv("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_HARD_THRESHOLD_S", "185")
        monkeypatch.setenv("YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_MIN_WINDOW_SIZE", "7")
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.reusable_source_age_cadence_enabled is True
            assert cfg.reusable_source_age_cadence_soft_threshold_s == 155.0
            assert cfg.reusable_source_age_cadence_hard_threshold_s == 185.0
            assert cfg.reusable_source_age_cadence_min_window_size == 7
        finally:
            nlm_config.reset_nlm_config()

    def test_source_content_retry_queue_age_margin_follows_env(self, monkeypatch):
        """Retry queue age-margin experiments should be named in shared config."""
        monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S", "12.5")
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.source_content_retry_queue_age_margin_s == 12.5
        finally:
            nlm_config.reset_nlm_config()

    def test_jitter_bounds_follow_env_and_stay_shared(self, monkeypatch):
        """Transcript and batch loops should read the same jitter bounds from config."""
        monkeypatch.setenv("YTIS_TRANSCRIPT_WORKER_JITTER_MIN_S", "1.5")
        monkeypatch.setenv("YTIS_TRANSCRIPT_WORKER_JITTER_MAX_S", "8.5")
        nlm_config.reset_nlm_config()
        try:
            cfg = nlm_config.get_nlm_config()
            assert cfg.transcript_worker_jitter_min_s == 1.5
            assert cfg.transcript_worker_jitter_max_s == 8.5
            assert nlm_config.get_transcript_worker_jitter_bounds() == (1.5, 8.5)
            assert transcript._get_worker_jitter_bounds() == (1.5, 8.5)
            assert batch._get_worker_jitter_bounds() == (1.5, 8.5)
        finally:
            nlm_config.reset_nlm_config()

    def test_setter_updates_the_shared_singleton(self):
        """set_nlm_config should affect both modules because they share the singleton."""
        original = nlm_config.get_nlm_config()
        replacement = nlm_config.NLMConfig(
            notebook_batch_size=77,
            notebook_source_cap=88,
            notebook_source_materialization_timeout_s=99,
            max_sources_per_notebook=123,
            transcript_worker_jitter_min_s=1.1,
            transcript_worker_jitter_max_s=2.2,
            auth_check_interval=11.0,
            auth_max_calls_per_window=12,
            auth_cooldown=13.0,
            browser_profile_mode="persistent",
            browser_profile_name="notebooklm-test",
            browser_profile_seed_root="P:\\\\\\.data/yt-is/notebooklm-browser-session-test",
            nlm_browser_mode="persistent",
            nlm_browser_profile_root=r"P:\\\\\\.data\yt-is\browser\notebooklm-test",
            nlm_browser_executable=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            nlm_browser_channel="chrome",
            nlm_browser_bootstrap_headless=False,
            nlm_browser_start_timeout_ms=30000,
            nlm_preflight_url_timeout_ms=60000,
            nlm_preflight_ui_timeout_ms=15000,
            source_content_retry_attempts=4,
            source_content_retry_initial_delay_s=1.0,
            source_content_retry_max_delay_s=8.0,
            source_content_retry_budget_s=30.0,
            source_content_retry_queue_delay_s=30.0,
            source_content_retry_queue_budget_s=30.0,
            source_content_retry_queue_age_margin_s=3.0,
            source_content_shared_retry_pool_enabled=False,
            transcript_expensive_fallback_enabled=True,
            whisper_on_notebooklm_add_failed=True,
            reusable_cleanup_every_n_batches=2,
            reusable_active_window_size=0,
            reusable_extract_window_size=0,
            reusable_source_age_cadence_enabled=False,
            reusable_source_age_cadence_soft_threshold_s=160.0,
            reusable_source_age_cadence_hard_threshold_s=190.0,
            reusable_source_age_cadence_min_window_size=5,
        )
        try:
            nlm_config.set_nlm_config(replacement)
            assert nlm_config.get_nlm_config() is replacement
            assert transcript.get_nlm_config() is replacement
        finally:
            nlm_config.set_nlm_config(original)
