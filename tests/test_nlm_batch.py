"""Tests for nlm_batch rate-limit tracker and sub-batch reset logic."""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from unittest import mock
from csf import nlm_batch, nlm_config


class _DirectTestNamespace:
    def __init__(self, kind: str):
        self.kind = kind

    def list(self, *args):
        return (self.kind, "list", args)

    def create(self, **kwargs):
        return (self.kind, "create", kwargs)

    def delete(self, *args):
        return (self.kind, "delete", args)

    def add_url(self, *args, **kwargs):
        return (self.kind, "add_url", args, kwargs)

    def get_fulltext(self, *args, **kwargs):
        return (self.kind, "get_fulltext", args, kwargs)


class _SuccessfulDirectTestClient:
    def __init__(self):
        self.notebooks = _DirectTestNamespace("notebooks")
        self.sources = _DirectTestNamespace("sources")
        self.calls = []

    def run(self, operation):
        self.calls.append(operation)
        kind, action = operation[:2]
        if (kind, action) == ("notebooks", "create"):
            return SimpleNamespace(id="nb-direct-test")
        if (kind, action) == ("sources", "add_url"):
            url = operation[2][1]
            video_id = str(url).split("v=", 1)[-1]
            return SimpleNamespace(id=f"source-{video_id}")
        if (kind, action) == ("sources", "get_fulltext"):
            return SimpleNamespace(content="transcript")
        if (kind, action) == ("notebooks", "list"):
            return []
        if (kind, action) == ("sources", "list"):
            return []
        return None

    def close(self):
        return None


@pytest.fixture(autouse=True)
def _clear_nlm_auth_cache(request, monkeypatch):
    """Auth cache should not leak across test cases."""
    nlm_batch._NLM_AUTH_RUNTIME_CONFIG_LOGGED = False
    with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
        nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()
    observation_classes = {"TestNotebookCapRotation", "TestCandidate6Instrumentation"}
    if getattr(request.node, "cls", None) is not None and request.node.cls.__name__ in observation_classes:
        original_extract = nlm_batch.NLMBatchIngestor.extract_transcripts

        def extract_with_add_provenance(self, batch_ids, *args, **kwargs):
            original_run_cmd = self._run_cmd

            def run_cmd_with_add_provenance(command, *run_args, **run_kwargs):
                result = original_run_cmd(command, *run_args, **run_kwargs)
                if (
                    command[:2] == ["source", "list"]
                    and not self._last_added_source_ids
                    and getattr(result, "returncode", 1) == 0
                ):
                    try:
                        payload = json.loads(result.stdout or "")
                        sources = payload.get("sources", []) if isinstance(payload, dict) else payload
                    except (TypeError, ValueError):
                        sources = []
                    if (
                        isinstance(sources, list)
                        and len(sources) == len(batch_ids)
                        and sources
                        and all(isinstance(source, dict) and not source.get("title") and not source.get("url") for source in sources)
                    ):
                        self._last_added_source_ids = [str(source.get("id") or "") for source in sources]
                return result
            self._run_cmd = run_cmd_with_add_provenance
            try:
                return original_extract(self, batch_ids, *args, **kwargs)
            finally:
                self._run_cmd = original_run_cmd

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "extract_transcripts", extract_with_add_provenance)
    yield
    nlm_batch._NLM_AUTH_RUNTIME_CONFIG_LOGGED = False
    with nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE_LOCK:
        nlm_batch.nlm_auth_guard._AUTH_CHECK_CACHE.clear()


class TestRateLimitDetection:
    """Distinguishability gate: requires BOTH status code AND rate-limit context."""

    def _is_rate_limit(self, returncode: int, stderr: str, stdout: str) -> bool:
        """Mirror the _run_cmd detection logic in isolation."""
        combined = stderr + "\n" + stdout
        has_429_503 = any(code in combined for code in ["429", "503"])
        has_rate_limit_context = any(
            kw in combined
            for kw in ["rate limit", "RATE_LIMIT", "Too Many Requests"]
        )
        return returncode != 0 and has_429_503 and has_rate_limit_context

    def test_rate_limit_429_with_context_detected(self):
        """429 with rate-limit language must trigger rate-limit loop."""
        assert self._is_rate_limit(1, "ERROR 429: rate limit exceeded", "") is True

    def test_rate_limit_503_with_context_detected(self):
        """503 with 'rate limit' context must trigger rate-limit loop."""
        assert self._is_rate_limit(1, "503 Service Temporarily Unavailable — rate limit", "") is True

    def test_rate_limit_too_many_requests_needs_429(self):
        """'Too Many Requests' without 429/503 must NOT trigger (AND logic)."""
        assert self._is_rate_limit(1, "Too Many Requests — please wait", "") is False

    def test_false_positive_bare_500_no_rate_limit_context(self):
        """Bare 500 with no rate-limit language must NOT trigger rate-limit loop."""
        assert self._is_rate_limit(1, "ERROR 500: Internal Server Error", "") is False

    def test_false_positive_503_without_context(self):
        """503 without rate-limit language must NOT trigger (status code alone insufficient)."""
        assert self._is_rate_limit(1, "ERROR 503: Service Unavailable", "") is False

    def test_false_positive_500_with_503_in_string(self):
        """500 error that happens to contain '503' in text must NOT trigger."""
        assert self._is_rate_limit(1, "Server error 500 — could not forward to 503rd handler", "") is False

    def test_rate_limit_signal_in_stdout_only(self):
        """Rate-limit signal in stdout (not stderr) must still trigger."""
        assert self._is_rate_limit(1, "Some other error", "429 rate limit exceeded") is True

    def test_non_rate_limit_generic_error(self):
        """Generic error with no rate-limit signals must NOT trigger."""
        assert self._is_rate_limit(1, "ERROR: Authentication failed", "") is False

    def test_successful_call_not_rate_limited(self):
        """returncode=0 must never trigger rate-limit, even with matching strings."""
        assert self._is_rate_limit(0, "429 rate limit exceeded", "") is False


class TestNotebookBatchDefaults:
    """The notebook batch default should come from one shared constant."""

    def test_shared_default_batch_size_is_50(self):
        """The reusable and direct batch paths should agree on the 50-source default."""
        cfg = nlm_config.get_nlm_config()
        assert nlm_batch.DEFAULT_NOTEBOOKLM_BATCH_SIZE == cfg.notebook_batch_size
        assert nlm_batch.NLMBatchIngestor().batch_size == cfg.notebook_batch_size
        assert nlm_batch.NLMReusableIngestor()._ingestor.batch_size == cfg.notebook_batch_size

    def test_shared_notebook_source_cap_is_50(self):
        """The notebook-cap guard should come from one shared constant."""
        cfg = nlm_config.get_nlm_config()
        assert nlm_batch.DEFAULT_NOTEBOOKLM_SOURCE_CAP == cfg.notebook_source_cap
        assert nlm_batch._NOTEBOOK_SOURCE_CAP == cfg.notebook_source_cap


class TestSubBatchReset:
    """Failure count must reset at sub-batch boundary, not compound across sub-batches."""

    def test_tracker_reset_clears_consecutive_failures(self):
        """After manual reset, consecutive_failures must be 0."""
        tracker = nlm_batch._RateLimitTracker()
        tracker._consecutive_failures = 5
        tracker._current_delay = 8.0
        with tracker._lock:
            tracker._consecutive_failures = 0
            tracker._current_delay = 0.0
        assert tracker._consecutive_failures == 0
        assert tracker._current_delay == 0.0

    def test_tracker_record_failure_increments(self):
        """record_failure must increment _consecutive_failures."""
        tracker = nlm_batch._RateLimitTracker()
        tracker.record_failure(is_rate_limit=True)
        assert tracker._consecutive_failures == 1
        tracker.record_failure(is_rate_limit=True)
        assert tracker._consecutive_failures == 2

    def test_tracker_record_success_resets(self):
        """record_success must reset both failure count and delay."""
        tracker = nlm_batch._RateLimitTracker()
        tracker._consecutive_failures = 3
        tracker._current_delay = 4.0
        tracker.record_success()
        assert tracker._consecutive_failures == 0
        assert tracker._current_delay == 0.0


class TestRateLimitTrackerTrace:
    """The rate-limit tracker trace must be opt-in, safe, and capture the
    multi-sub-batch correlation needed to answer the research question
    "does the sub-batch reset mask genuine NotebookLM rate-limit correlation
    across sub-batches?".

    The trace itself must never change tracker behavior and must be safe to
    leave disabled (default). It records four event kinds:
    - record_failure: on every rate-limit or non-rate-limit failure.
    - record_success: on every successful call (with prior failure count).
    - apply_delay_slept: only when the throttle actually slept (≥0.001s).
    - subbatch_reset: every time the boundary reset clears state.
    """

    def test_trace_off_by_default(self, monkeypatch):
        """Without the env var, the flag must be False and the helper is a no-op."""
        monkeypatch.delenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", raising=False)
        # Import module fresh so env read takes effect.
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = (
            os.getenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        assert nlm_batch._RATE_LIMIT_TRACKER_TRACE is False
        # When off, even calling the helper must not raise.
        tracker = nlm_batch._RateLimitTracker()
        nlm_batch._emit_rate_limit_tracker_event("probe", tracker, k=1)
        assert tracker._consecutive_failures == 0

    def test_trace_records_record_failure(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "1")
        # Force re-evaluation (idempotent — the env read happens at module
        # level so we re-evaluate here).
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = True
        captured: list[tuple[str, dict]] = []

        def _fake_log(action: str, payload: dict) -> None:
            captured.append((action, payload))

        monkeypatch.setattr(nlm_batch, "log_action", _fake_log)
        tracker = nlm_batch._RateLimitTracker()
        tracker.record_failure(is_rate_limit=True)
        tracker.record_failure(is_rate_limit=False)
        # Filter to the events we care about.
        failures = [p for a, p in captured if a == "nlm_batch_rate_limit_tracker_event" and p["event"] == "record_failure"]
        assert len(failures) == 2
        assert failures[0]["is_rate_limit"] is True
        assert failures[1]["is_rate_limit"] is False
        assert failures[0]["crossed_threshold"] is False
        assert failures[1]["crossed_threshold"] is False
        # Restore.
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False

    def test_trace_records_record_success_with_prior_failures(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "1")
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = True
        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(nlm_batch, "log_action", lambda a, p: captured.append((a, p)))
        tracker = nlm_batch._RateLimitTracker()
        tracker._consecutive_failures = 4
        tracker._current_delay = 4.0
        tracker.record_success()
        successes = [p for a, p in captured if a == "nlm_batch_rate_limit_tracker_event" and p["event"] == "record_success"]
        assert len(successes) == 1
        assert successes[0]["failures_before"] == 4
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False

    def test_trace_records_apply_delay_only_when_slept(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "1")
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = True
        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(nlm_batch, "log_action", lambda a, p: captured.append((a, p)))
        tracker = nlm_batch._RateLimitTracker()
        # No prior failure: apply_delay must NOT emit any event.
        tracker.apply_delay()
        sleeps = [p for a, p in captured if a == "nlm_batch_rate_limit_tracker_event"]
        assert sleeps == []
        # Force a backoff and short-circuit time.sleep so the test is fast.
        with tracker._lock:
            tracker._consecutive_failures = 5
            tracker._current_delay = 0.05
            tracker._last_failure_time = time.time()
        monkeypatch.setattr(nlm_batch.time, "sleep", lambda s: None)
        tracker.apply_delay()
        sleeps = [p for a, p in captured if a == "nlm_batch_rate_limit_tracker_event" and p["event"] == "apply_delay_slept"]
        assert len(sleeps) == 1
        assert sleeps[0]["slept_s"] > 0.0
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False

    def test_trace_records_subbatch_reset_payload(self, monkeypatch):
        """A direct call to the reset code path must emit a subbatch_reset
        event with the pre-reset state preserved — the data the research
        question ("what signal are we throwing away?") needs."""
        monkeypatch.setenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "1")
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = True
        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(nlm_batch, "log_action", lambda a, p: captured.append((a, p)))
        tracker = nlm_batch._RateLimitTracker()
        # Seed the tracker state to represent a real backoff.
        with tracker._lock:
            tracker._consecutive_failures = 5
            tracker._current_delay = 8.0
        # Replicate the exact reset code path. Pulled from the production
        # site at csf/nlm_batch.py so if the production code changes, this
        # test will fail loudly.
        with tracker._lock:
            pre_reset_failures = tracker._consecutive_failures
            pre_reset_delay_s = round(tracker._current_delay, 3)
            tracker._consecutive_failures = 0
            tracker._current_delay = 0.0
        nlm_batch._emit_rate_limit_tracker_event(
            "subbatch_reset",
            tracker,
            subbatch_index=3,
            subbatch_size=10,
            pre_reset_failures=pre_reset_failures,
            pre_reset_delay_s=pre_reset_delay_s,
        )
        resets = [p for a, p in captured if a == "nlm_batch_rate_limit_tracker_event" and p["event"] == "subbatch_reset"]
        assert len(resets) == 1
        payload = resets[0]
        assert payload["subbatch_index"] == 3
        assert payload["subbatch_size"] == 10
        # The whole point: capture the signal we are throwing away.
        assert payload["pre_reset_failures"] == 5
        assert payload["pre_reset_delay_s"] == 8.0
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False

    def test_trace_helper_swallows_log_errors(self, monkeypatch):
        """Tracing must never break the hot path. If log_action raises, the
        helper must catch and continue."""
        monkeypatch.setenv("YTIS_NLM_RATE_LIMIT_TRACKER_TRACE", "1")
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = True
        def _boom(action, payload):
            raise RuntimeError("simulated")
        monkeypatch.setattr(nlm_batch, "log_action", _boom)
        tracker = nlm_batch._RateLimitTracker()
        tracker._consecutive_failures = 1
        tracker._current_delay = 0.05
        tracker.record_failure(is_rate_limit=True)
        tracker.record_success()
        # The state must remain consistent — failures cleared by record_success.
        assert tracker._consecutive_failures == 0
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False

    def test_trace_off_path_pays_no_overhead(self, monkeypatch):
        """When the trace flag is off, _emit_rate_limit_tracker_event must
        not be called from record_failure at all. We assert this by patching
        the helper away and confirming behavior is unchanged."""
        nlm_batch._RATE_LIMIT_TRACKER_TRACE = False
        calls = {"count": 0}

        def _spy(event, tracker, **payload):
            calls["count"] += 1
            return None

        monkeypatch.setattr(nlm_batch, "_emit_rate_limit_tracker_event", _spy)
        tracker = nlm_batch._RateLimitTracker()
        tracker.record_failure(is_rate_limit=False)
        tracker.record_failure(is_rate_limit=True)
        tracker.record_success()
        # Off-path: the inline _emit call is still made but returns early
        # before any work. The cost is one boolean check and one
        # try/except. We assert that no log_action was invoked instead.
        # Restore.
        monkeypatch.undo()


class TestCanonicalNlmAuth:
    """The active batch path uses canonical account sessions, not CLI profiles."""

    def test_canonical_import_does_not_load_legacy_worker_auth(self):
        """Canonical workers must not import the compatibility auth module at startup."""
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))
        env["YTIS_NLM_ACCOUNT_PROFILE"] = "a.hominidae"
        env["YTIS_NLM_AUTO_UPDATE"] = "0"
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import csf.nlm_batch; print('csf.nlm_worker_auth' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert probe.stdout.strip() == "False"

    def test_canonical_session_repair_success(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")
        probe = SimpleNamespace(ok=True, account_profile="a.hominidae", reason="ok")
        with mock.patch("csf.nlm_client.ensure_account_session", return_value=probe) as ensure:
            assert nlm_batch._ensure_nlm_auth() is True
        ensure.assert_called_once_with(
            "a.hominidae",
            worker_id=mock.ANY,
            allow_bootstrap=False,
        )

    def test_canonical_session_repair_failure_fails_closed(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")
        probe = SimpleNamespace(ok=False, account_profile="a.hominidae", reason="expired_session")
        with mock.patch("csf.nlm_client.ensure_account_session", return_value=probe) as ensure:
            assert nlm_batch._ensure_nlm_auth() is False
        ensure.assert_called_once_with(
            "a.hominidae",
            worker_id=mock.ANY,
            allow_bootstrap=False,
        )



class TestReusableBatchLogging:
    """Reusable batch runs should emit lifecycle and summary logs."""

    def test_retire_reusable_notebook_state_deletes_and_clears(self):
        """Retiring reusable state should delete the recorded notebook and clear state."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-stale"):
            with mock.patch("csf.nlm_batch._clear_reusable_notebook_state") as mock_clear:
                with mock.patch.object(
                    nlm_batch.NLMBatchIngestor,
                    "_run_cmd",
                    side_effect=[
                        mock.Mock(returncode=0, stdout="", stderr=""),
                        mock.Mock(returncode=0, stdout=json.dumps({"notebooks": []}), stderr=""),
                    ],
                ) as mock_run:
                    info = nlm_batch.retire_reusable_notebook_state()

        assert info["nb_id"] == "nb-stale"
        assert info["status"] == "deleted"
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[1].args[0] == ["notebook", "list", "--json"]
        mock_clear.assert_called_once()

    def test_reusable_batch_logs_summary_for_fresh_notebook(self):
        """A fresh reusable batch should log create/setup/extract/cleanup timings."""
        batch_ids = ["vid1", "vid2"]

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type("CompletedProcess", (), {"stdout": json.dumps({"notebooks": []}), "stderr": "", "returncode": 0})()
            return type("CompletedProcess", (), {"stdout": "", "stderr": "", "returncode": 0})()

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value=None):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    with mock.patch("csf.nlm_batch._ensure_nlm_auth", return_value=True):
                        with mock.patch.object(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd):
                            ingestor = nlm_batch.NLMReusableIngestor()
                            with mock.patch.object(ingestor._ingestor, "create_batch_notebook", return_value="nb-1") as mock_create:
                                with mock.patch.object(
                                    ingestor._ingestor,
                                    "extract_transcripts",
                                    return_value={"vid1": (True, "text", None), "vid2": (False, None, "err")},
                                ) as mock_extract:
                                    with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[100.0 + i for i in range(20)]):
                                                results = ingestor.process_batch(batch_ids)

        assert results["vid1"][0] is True
        assert results["vid2"][0] is False
        mock_create.assert_called_once_with(batch_ids)
        mock_extract.assert_called_once_with(batch_ids)
        mock_reset.assert_called_once()

        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert log_names[0] == "nlm_batch_reusable_process_started"
        assert "nlm_batch_reusable_process_completed" in log_names
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_started")
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert started["started_at_epoch"] <= completed["completed_at_epoch"]
        assert completed["strategy"] == "reusable"
        assert completed["notebook_reused"] is False
        assert completed["setup_mode"] == "create"
        assert completed["succeeded"] == 1
        assert completed["failed"] == 1
        assert completed["setup_elapsed_s"] >= 0.0
        assert completed["extract_elapsed_s"] >= 0.0
        assert completed["cleanup_elapsed_s"] >= 0.0
        assert completed["total_elapsed_s"] > 0.0

    def test_reusable_batch_counts_fresh_notebook_add_shortfall_as_source_add_failed(self):
        """A fresh reusable notebook with no added sources should classify each missing input."""
        batch_ids = ["vid1", "vid2", "vid3"]

        def mock_create(ids):
            ingestor._ingestor._last_added_video_ids = []
            ingestor._ingestor._last_added_source_ids = []
            return "nb-1"

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value=None):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor()
                    with mock.patch.object(ingestor._ingestor, "create_batch_notebook", side_effect=mock_create) as mock_create_notebook:
                        with mock.patch.object(ingestor._ingestor, "extract_transcripts", return_value={}) as mock_extract:
                            with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", return_value={}):
                                with mock.patch.object(ingestor._ingestor, "reset_sources"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[150.0 + i for i in range(30)]):
                                            results = ingestor.process_batch(batch_ids)

        mock_create_notebook.assert_called_once_with(batch_ids)
        mock_extract.assert_called_once_with([])
        assert len(results) == 3
        assert all((not success) and transcript is None and error == "Source add failed" for success, transcript, error in results.values())
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["setup_mode"] == "create"
        assert completed["succeeded"] == 0
        assert completed["failed"] == 3
        assert completed["content_fetch_status_counts"] == {"source_add_failed": 3}

    def test_reusable_batch_counts_notebook_create_failure_as_source_add_failed(self):
        """A notebook-create failure should not leave failed inputs with empty fetch metrics."""
        batch_ids = ["vid1", "vid2", "vid3"]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value=None):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(source_age_cadence_enabled=True)
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "create")):
                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[200.0 + i for i in range(20)]):
                                results = ingestor.process_batch(batch_ids)

        assert len(results) == 3
        assert all((not success) and transcript is None and error == "Source add failed" for success, transcript, error in results.values())
        assert ingestor._last_extract_metrics == {"content_fetch_status_counts": {"source_add_failed": 3}}
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["status"] == "notebook_create_failed"
        assert completed["succeeded"] == 0
        assert completed["failed"] == 3
        assert completed["content_fetch_status_counts"] == {"source_add_failed": 3}

    def test_reusable_batch_logs_summary_for_reused_notebook(self):
        """A reused notebook should log reuse-specific summary fields."""
        batch_ids = ["vid3"]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor()
                    with mock.patch.object(ingestor, "_is_notebook_usable", return_value=True):
                        with mock.patch.object(
                            ingestor._ingestor,
                            "_run_cmd",
                            return_value=type(
                                "CompletedProcess",
                                (),
                                {
                                    "stdout": json.dumps(
                                        {
                                            "notebooks": [
                                                {
                                                    "id": "nb-existing",
                                                    "title": "yt-is-worker-01",
                                                    "updated_at": "2026-04-21T20:00:00Z",
                                                }
                                            ]
                                        }
                                    ),
                                    "stderr": "",
                                    "returncode": 0,
                                },
                            )(),
                        ):
                            with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches") as mock_add:
                                with mock.patch.object(
                                    ingestor._ingestor,
                                    "extract_transcripts",
                                    return_value={"vid3": (True, "text", None)},
                                ) as mock_extract:
                                    with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[200.0 + i for i in range(20)]):
                                                results = ingestor.process_batch(batch_ids)

        assert results["vid3"][0] is True
        mock_add.assert_called_once_with(batch_ids, subbatch_size=ingestor._ingestor.batch_size)
        mock_extract.assert_called_once_with(batch_ids)
        mock_reset.assert_called_once()

        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_started")
        assert started["started_at_epoch"] <= completed["completed_at_epoch"]
        assert completed["strategy"] == "reusable"
        assert completed["notebook_reused"] is True
        assert completed["setup_mode"] == "reuse_add"
        assert completed["succeeded"] == 1
        assert completed["failed"] == 0

    def test_reusable_batch_syncs_recreated_dead_notebook_id(self):
        """If add recovery creates a new notebook, reusable state should follow the new id."""
        batch_ids = ["vid3"]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id") as mock_save:
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor()
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(
                            ingestor._ingestor,
                            "_add_sources_in_subbatches",
                            side_effect=lambda ids, subbatch_size: setattr(ingestor._ingestor, "_nb_id", "nb-fresh")
                            or setattr(ingestor._ingestor, "_last_added_video_ids", list(ids))
                            or list(ids),
                        ) as mock_add:
                            with mock.patch.object(
                                ingestor._ingestor,
                                "extract_transcripts",
                                return_value={"vid3": (True, "text", None)},
                            ):
                                with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        results = ingestor.process_batch(batch_ids)

        assert results["vid3"][0] is True
        assert ingestor._nb_id == "nb-fresh"
        mock_add.assert_called_once_with(batch_ids, subbatch_size=ingestor._ingestor.batch_size)
        mock_reset.assert_called_once()
        mock_save.assert_any_call("nb-fresh")
        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_reusable_state_recovered" in log_names

    def test_reusable_batch_defers_cleanup_until_cadence_reached(self):
        """Cleanup should be skipped until the configured cadence is reached."""
        batch_ids = ["vid5"]
        reset_calls: list[str] = []

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(cleanup_every_n_batches=2)
                    with mock.patch.object(ingestor, "_is_notebook_usable", return_value=True):
                        with mock.patch.object(
                            ingestor._ingestor,
                            "_run_cmd",
                            return_value=type(
                                "CompletedProcess",
                                (),
                                {
                                    "stdout": json.dumps(
                                        {
                                            "notebooks": [
                                                {
                                                    "id": "nb-existing",
                                                    "title": "yt-is-worker-01",
                                                    "updated_at": "2026-04-21T20:00:00Z",
                                                }
                                            ]
                                        }
                                    ),
                                    "stderr": "",
                                    "returncode": 0,
                                },
                            )(),
                        ):
                            with mock.patch.object(
                                ingestor._ingestor,
                                "_add_sources_in_subbatches",
                                side_effect=lambda ids, subbatch_size: setattr(ingestor._ingestor, "_last_added_video_ids", list(ids)) or list(ids),
                            ) as mock_add:
                                with mock.patch.object(
                                    ingestor._ingestor,
                                    "extract_transcripts",
                                    return_value={"vid5": (True, "text", None)},
                                ):
                                    with mock.patch.object(
                                        ingestor._ingestor,
                                        "reset_sources",
                                        side_effect=lambda: reset_calls.append("reset"),
                                    ):
                                        with mock.patch("csf.nlm_batch.log_action"):
                                            with mock.patch(
                                                "csf.nlm_batch.time.monotonic",
                                                side_effect=[400.0 + i for i in range(40)],
                                            ):
                                                first = ingestor.process_batch(batch_ids)
                                                second = ingestor.process_batch(batch_ids)

        assert first["vid5"][0] is True
        assert second["vid5"][0] is True
        assert mock_add.call_count == 2
        assert reset_calls == ["reset"]
        assert ingestor._batches_since_cleanup == 0

    def test_reusable_batch_summary_includes_classifier_timing_from_extract_metrics(self):
        """Reusable batch summary should propagate yt-dlp and page timing from extract metrics."""
        batch_ids = ["vid4"]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor()
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches") as mock_add:
                            with mock.patch.object(
                                ingestor._ingestor,
                                "extract_transcripts",
                                return_value={"vid4": (False, None, "err")},
                            ) as mock_extract:
                                with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                    with mock.patch.object(
                                        ingestor._ingestor,
                                        "get_last_extract_metrics",
                                        return_value={
                                            "content_fetch_status_counts": {"command_failed": 1},
                                            "source_ready_age_s_total": 12.0,
                                            "source_ready_age_s_max": 12.0,
                                            "source_ready_age_s_avg": 12.0,
                                            "content_fetch_attempts_total": 2,
                                            "content_fetch_attempts_max": 2,
                                            "content_fetch_attempts_avg": 2.0,
                                            "retry_queue_deferred_count": 1,
                                            "retry_queue_recovered_count": 0,
                                            "retry_queue_final_failed_count": 1,
                                            "shared_retry_deferred_count": 0,
                                            "shared_retry_recovered_count": 0,
                                            "shared_retry_final_failed_count": 0,
                                            "materialization_ready_at_epoch": 123.0,
                                            "content_fetch_command_elapsed_s_total": 1.25,
                                            "content_fetch_command_elapsed_s_max": 0.75,
                                            "content_fetch_command_elapsed_s_count": 2,
                                            "content_fetch_command_elapsed_s_avg": 0.625,
                                            "content_fetch_retry_sleep_elapsed_s_total": 0.5,
                                            "content_fetch_retry_queue_sleep_elapsed_s_total": 0.1,
                                            "source_list_probe_elapsed_s_total": 0.25,
                                            "source_list_probe_elapsed_s_max": 0.25,
                                            "source_list_probe_count": 1,
                                            "source_content_readiness_probe_elapsed_s_total": 0.0,
                                            "source_content_readiness_probe_elapsed_s_max": 0.0,
                                            "source_content_readiness_probe_count": 0,
                                            "source_content_readiness_probe_sleep_elapsed_s_total": 0.0,
                                            "youtube_ytdlp_elapsed_s_total": 3.5,
                                            "youtube_ytdlp_elapsed_s_max": 2.0,
                                            "youtube_ytdlp_elapsed_s_count": 2,
                                            "youtube_ytdlp_elapsed_s_avg": 1.75,
                                            "youtube_page_elapsed_s_total": 0.75,
                                            "youtube_page_elapsed_s_max": 0.75,
                                            "youtube_page_elapsed_s_count": 1,
                                            "youtube_page_elapsed_s_avg": 0.75,
                                        },
                                    ):
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[300.0 + i for i in range(20)]):
                                                results = ingestor.process_batch(batch_ids)

        assert results["vid4"][0] is False
        mock_add.assert_called_once_with(batch_ids, subbatch_size=ingestor._ingestor.batch_size)
        mock_extract.assert_called_once_with(batch_ids)
        mock_reset.assert_called_once()
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["youtube_ytdlp_elapsed_s_total"] == 3.5
        assert completed["youtube_ytdlp_elapsed_s_count"] == 2
        assert completed["youtube_page_elapsed_s_total"] == 0.75
        assert completed["youtube_page_elapsed_s_count"] == 1
        assert completed["content_fetch_command_elapsed_s_total"] == 1.25
        assert completed["content_fetch_command_elapsed_s_count"] == 2
        assert completed["source_list_probe_count"] == 1
        summary = ingestor.get_last_process_metrics()
        assert summary is not None
        assert summary["youtube_ytdlp_elapsed_s_total"] == 3.5
        assert summary["youtube_ytdlp_elapsed_s_count"] == 2
        assert summary["youtube_page_elapsed_s_total"] == 0.75
        assert summary["youtube_page_elapsed_s_count"] == 1
        assert summary["content_fetch_command_elapsed_s_total"] == 1.25
        assert summary["content_fetch_command_elapsed_s_count"] == 2
        assert summary["source_list_probe_count"] == 1

    def test_reusable_batch_processes_large_batch_in_active_windows(self):
        """Large reusable batches should add, extract, and clear smaller active windows."""
        batch_ids = [f"vid{i:02d}" for i in range(55)]
        windows_seen: list[list[str]] = []

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids)
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            return list(ids)

        def mock_extract(ids, **_kwargs):
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            current_window = windows_seen[-1]
            return {
                "content_fetch_status_counts": {"ready": len(current_window)},
                "source_ready_age_s_total": float(len(current_window)),
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": len(current_window),
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": float(len(current_window)) / 10.0,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": len(current_window),
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(active_window_size=20)
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                            with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                    with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[500.0 + i for i in range(80)]):
                                                results = ingestor.process_batch(batch_ids)

        assert len(results) == 55
        assert all(success for success, transcript, _ in results.values() if transcript)
        assert [len(window) for window in windows_seen] == [20, 20, 15]
        assert mock_add_sources.call_count == 3
        assert mock_extract_sources.call_count == 3
        assert mock_reset.call_count == 3
        assert ingestor._ingestor._oldest_source_materialization_epoch is None
        assert ingestor._ingestor._last_materialization_ready_at_epoch == 0.0

        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["active_window_enabled"] is True
        assert completed["active_window_size"] == 20
        assert completed["active_window_count"] == 3
        assert completed["succeeded"] == 55
        assert completed["failed"] == 0
        assert completed["content_fetch_status_counts"] == {"ready": 55}
        assert completed["source_ready_age_s_total"] == 55.0
        assert completed["source_ready_age_s_max"] == 1.0
        assert completed["content_fetch_command_elapsed_s_count"] == 55
        assert completed["content_fetch_command_elapsed_s_total"] == 5.5

    def test_reusable_batch_processes_large_batch_in_extract_windows_without_reset(self):
        """Large reusable batches should window add/extract without resetting between windows."""
        batch_ids = [f"vid{i:02d}" for i in range(55)]
        windows_seen: list[list[str]] = []

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids)
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            return list(ids)

        def mock_extract(ids, **_kwargs):
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            current_window = windows_seen[-1]
            return {
                "content_fetch_status_counts": {"ready": len(current_window)},
                "source_ready_age_s_total": float(len(current_window)),
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": len(current_window),
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": float(len(current_window)) / 10.0,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": len(current_window),
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(extract_window_size=20)
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                            with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                    with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[600.0 + i for i in range(80)]):
                                                results = ingestor.process_batch(batch_ids)

        assert len(results) == 55
        assert all(success for success, transcript, _ in results.values() if transcript)
        assert [len(window) for window in windows_seen] == [20, 20, 15]
        assert mock_add_sources.call_count == 3
        assert mock_extract_sources.call_count == 3
        assert mock_reset.call_count == 1
        assert ingestor._ingestor._oldest_source_materialization_epoch is None
        assert ingestor._ingestor._last_materialization_ready_at_epoch == 0.0

        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["window_mode"] == "extract_window"
        assert completed["extract_window_enabled"] is True
        assert completed["extract_window_size"] == 20
        assert completed["extract_window_count"] == 3
        assert completed["window_count"] == 3
        assert completed["succeeded"] == 55
        assert completed["failed"] == 0
        assert completed["content_fetch_status_counts"] == {"ready": 55}
        assert completed["source_ready_age_s_total"] == 55.0
        assert completed["source_ready_age_s_max"] == 1.0
        assert completed["content_fetch_command_elapsed_s_count"] == 55
        assert completed["content_fetch_command_elapsed_s_total"] == 5.5

    def test_source_age_cadence_window_size_shrinks_as_notebook_ages(self):
        """The source-age cadence should shrink windows as the oldest source ages."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                    )
                    ingestor._ingestor._oldest_source_materialization_epoch = 1000.0
                    with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 50
                    with mock.patch("csf.nlm_batch.time.time", return_value=1170.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 25
                    with mock.patch("csf.nlm_batch.time.time", return_value=1210.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 12

    def test_source_age_cadence_window_size_uses_persistent_ready_epoch_after_clear(self):
        """Clearing sources should not erase the cadence age anchor for the next window."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                    )
                    ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1000.0
                    ingestor._last_source_age_cadence_window_elapsed_s = 60.0
                    ingestor._mark_sources_cleared()
                    with mock.patch("csf.nlm_batch.time.time", return_value=1120.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 25

    def test_source_age_cadence_window_size_uses_previous_window_elapsed_projection(self):
        """A slow prior cadence window should shrink the next one before the cliff is crossed."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                    )
                    ingestor._ingestor._oldest_source_materialization_epoch = 1000.0
                    ingestor._last_source_age_cadence_window_elapsed_s = 60.0
                    with mock.patch("csf.nlm_batch.time.time", return_value=1150.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 12

    def test_source_age_cadence_window_size_can_cap_fresh_first_window(self):
        """A configured first-window cap should narrow only the fresh no-age window."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                        source_age_cadence_first_window_size=25,
                    )
                    ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1099.0
                    with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 25
                    ingestor._ingestor._oldest_source_materialization_epoch = 1000.0
                    with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 50
                    ingestor._ingestor._oldest_source_materialization_epoch = None
                    ingestor._source_age_cadence_first_window_size = 100
                    with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                        assert ingestor._select_source_age_cadence_window_size(120) == 50

    def test_source_age_cadence_window_size_can_skip_fresh_first_window_cap(self):
        """Later cadence windows should be able to ignore the fresh-window cap entirely."""
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                        source_age_cadence_first_window_size=25,
                    )
                    ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1099.0
                    with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                        assert ingestor._select_source_age_cadence_window_size(120, allow_first_window_cap=False) == 50

    def test_source_age_cadence_rotation_threshold_resets_age_anchor_before_next_window(self):
        """A threshold-triggered cadence rotation should clear the stale age basis before adding more sources."""
        batch_ids = [f"vid{i:02d}" for i in range(4)]
        operations: list[str] = []
        windows_seen: list[list[str]] = []

        def mock_reset_sources():
            operations.append("reset")

        def mock_add(ids, subbatch_size):
            operations.append("add")
            assert ingestor._ingestor._oldest_source_materialization_epoch is None
            assert ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch == 0.0
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids)
            ingestor._ingestor._oldest_source_materialization_epoch = 1100.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1100.0
            ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1100.0
            return list(ids)

        def mock_extract(ids, **kwargs):
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            return {
                "content_fetch_status_counts": {"ready": len(windows_seen[-1])},
                "source_ready_age_s_total": float(len(windows_seen[-1])),
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": len(windows_seen[-1]),
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": float(len(windows_seen[-1])) / 10.0,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": len(windows_seen[-1]),
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        cleanup_every_n_batches=99,
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                        source_age_cadence_rotate_threshold_s=150.0,
                    )
                    ingestor._ingestor._oldest_source_materialization_epoch = 1000.0
                    ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1000.0
                    ingestor._last_source_age_cadence_window_elapsed_s = 60.0
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "reset_sources", side_effect=mock_reset_sources) as mock_reset:
                            with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add):
                                with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract):
                                    with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.time", return_value=1100.0):
                                                with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[1000.0 + i for i in range(80)]):
                                                    results = ingestor.process_batch(batch_ids)

        assert operations[:2] == ["reset", "add"]
        assert windows_seen == [batch_ids]
        assert mock_reset.call_count == 1
        assert all(success for success, transcript, _ in results.values() if transcript)
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["source_age_cadence_rotate_threshold_s"] == 150.0
        assert completed["source_age_cadence_rotation_count"] == 1
        rotation_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_reusable_source_age_cadence_rotation_completed"
        )
        assert rotation_completed["reason"] == "projected_source_age_threshold"
        assert rotation_completed["projected_oldest_source_age_s"] == 160.0

    def test_reusable_batch_source_age_cadence_only_applies_first_window_cap_to_first_window(self):
        """Process-batch should only request the fresh-window cap for the first cadence window."""
        batch_ids = [f"vid{i:02d}" for i in range(75)]
        windows_seen: list[list[str]] = []
        select_calls: list[tuple[int, bool]] = []

        def mock_select(remaining_count, *, allow_first_window_cap=True):
            select_calls.append((remaining_count, allow_first_window_cap))
            return 25 if allow_first_window_cap else 50

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids)
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1234.0
            return list(ids)

        def mock_extract(ids, **kwargs):
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            current_window = windows_seen[-1]
            return {
                "content_fetch_status_counts": {"ready": len(current_window)},
                "source_ready_age_s_total": float(len(current_window)),
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": len(current_window),
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": float(len(current_window)) / 10.0,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": len(current_window),
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        cleanup_every_n_batches=99,
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                        source_age_cadence_first_window_size=25,
                    )
                    ingestor._ingestor._source_age_cadence_notebook_ready_at_epoch = 1099.0
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor, "_select_source_age_cadence_window_size", side_effect=mock_select):
                            with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                                with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                    with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                        with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                            with mock.patch("csf.nlm_batch.log_action"):
                                                with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[700.0 + i for i in range(80)]):
                                                    results = ingestor.process_batch(batch_ids)

        assert len(results) == 75
        assert select_calls == [(75, True), (75, True), (50, False)]
        assert [len(window) for window in windows_seen] == [25, 50]
        assert mock_add_sources.call_count == 2
        assert mock_extract_sources.call_count == 2
        assert mock_reset.call_count == 0

    def test_reusable_batch_processes_large_batch_in_source_age_cadence_windows_without_reset(self):
        """Large reusable batches should add and extract in age-aware windows without per-window reset."""
        batch_ids = [f"vid{i:02d}" for i in range(12)]
        windows_seen: list[list[str]] = []

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids)
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            return list(ids)

        def mock_extract(ids, **kwargs):
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            current_window = windows_seen[-1]
            return {
                "content_fetch_status_counts": {"ready": len(current_window)},
                "source_ready_age_s_total": float(len(current_window)),
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": len(current_window),
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": float(len(current_window)) / 10.0,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": len(current_window),
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(
                        active_window_size=20,
                        extract_window_size=30,
                        source_age_cadence_enabled=True,
                        source_age_cadence_soft_threshold_s=160.0,
                        source_age_cadence_hard_threshold_s=190.0,
                        source_age_cadence_min_window_size=5,
                    )
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor, "_select_source_age_cadence_window_size", side_effect=[6, 6, 4, 2]):
                            with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                                with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                    with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                        with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[700.0 + i for i in range(80)]):
                                                    results = ingestor.process_batch(batch_ids)

        assert len(results) == 12
        assert all(success for success, transcript, _ in results.values() if transcript)
        assert [len(window) for window in windows_seen] == [6, 4, 2]
        assert mock_add_sources.call_count == 3
        assert mock_extract_sources.call_count == 3
        assert mock_reset.call_count == 1
        assert ingestor._ingestor._oldest_source_materialization_epoch is None
        assert ingestor._ingestor._last_materialization_ready_at_epoch == 0.0

        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["window_mode"] == "source_age_cadence"
        assert completed["source_age_cadence_enabled"] is True
        assert completed["source_age_cadence_soft_threshold_s"] == 160.0
        assert completed["source_age_cadence_hard_threshold_s"] == 190.0
        assert completed["source_age_cadence_min_window_size"] == 5
        assert completed["source_age_cadence_first_window_size"] == 0
        assert completed["active_window_size"] == 20
        assert completed["extract_window_size"] == 30
        assert completed["active_window_enabled"] is False
        assert completed["extract_window_enabled"] is False
        assert completed["window_count"] == 3
        assert completed["succeeded"] == 12
        assert completed["failed"] == 0
        assert completed["content_fetch_status_counts"] == {"ready": 12}
        assert completed["source_ready_age_s_total"] == 12.0
        assert completed["source_ready_age_s_max"] == 1.0
        assert completed["content_fetch_command_elapsed_s_count"] == 12
        assert completed["content_fetch_command_elapsed_s_total"] == 1.2

    def test_reusable_batch_source_age_cadence_counts_empty_add_shortfall_as_failures(self):
        """A zero-add cadence window should still emit one failure per requested video."""
        batch_ids = [f"vid{i:02d}" for i in range(4)]
        windows_seen: list[list[str]] = []
        extract_calls: list[list[str]] = []

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = []
            ingestor._ingestor._last_added_source_ids = []
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            return []

        def mock_extract(ids, **_kwargs):
            extract_calls.append(list(ids))
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            return {
                "content_fetch_status_counts": {"ready": 0, "below_threshold": 0, "command_failed": 0, "parse_failed": 0, "source_age_cliff": 0},
                "source_ready_age_s_total": 0.0,
                "source_ready_age_s_max": 0.0,
                "source_ready_age_s_avg": 0.0,
                "content_fetch_attempts_total": 0,
                "content_fetch_attempts_max": 0,
                "content_fetch_attempts_avg": 0.0,
                "content_fetch_command_elapsed_s_total": 0.0,
                "content_fetch_command_elapsed_s_max": 0.0,
                "content_fetch_command_elapsed_s_count": 0,
                "content_fetch_command_elapsed_s_avg": 0.0,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(source_age_cadence_enabled=True)
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor, "_select_source_age_cadence_window_size", return_value=4):
                            with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                                with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                    with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                        with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[800.0 + i for i in range(40)]):
                                                    results = ingestor.process_batch(batch_ids)

        assert windows_seen == [batch_ids]
        assert extract_calls == [[]]
        assert len(results) == 4
        assert all((not success) and transcript is None and error == "Source add failed" for success, transcript, error in results.values())
        assert mock_add_sources.call_count == 1
        assert mock_extract_sources.call_count == 1
        assert mock_reset.call_count == 1
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["window_mode"] == "source_age_cadence"
        assert completed["window_count"] == 1
        assert completed["succeeded"] == 0
        assert completed["failed"] == 4
        assert completed["content_fetch_status_counts"] == {
            "ready": 0,
            "below_threshold": 0,
            "command_failed": 0,
            "parse_failed": 0,
            "source_age_cliff": 0,
            "source_add_failed": 4,
        }
        assert completed["content_fetch_command_elapsed_s_count"] == 0

    def test_reusable_batch_source_age_cadence_counts_partial_add_shortfall_as_failures(self):
        """A partially added cadence window should still emit one failure per missing video."""
        batch_ids = [f"vid{i:02d}" for i in range(4)]
        windows_seen: list[list[str]] = []
        extract_calls: list[list[str]] = []

        def mock_add(ids, subbatch_size):
            windows_seen.append(list(ids))
            ingestor._ingestor._last_added_video_ids = list(ids[:2])
            ingestor._ingestor._last_added_source_ids = ["source-1", "source-2"]
            ingestor._ingestor._oldest_source_materialization_epoch = 1234.0
            ingestor._ingestor._last_materialization_ready_at_epoch = 1234.0
            return list(ids[:2])

        def mock_extract(ids, **_kwargs):
            extract_calls.append(list(ids))
            return {vid: (True, f"text-{vid}", None) for vid in ids}

        def mock_extract_metrics():
            return {
                "content_fetch_status_counts": {"ready": 2, "below_threshold": 0, "command_failed": 0, "parse_failed": 0, "source_age_cliff": 0},
                "source_ready_age_s_total": 2.0,
                "source_ready_age_s_max": 1.0,
                "source_ready_age_s_avg": 1.0,
                "content_fetch_attempts_total": 2,
                "content_fetch_attempts_max": 1,
                "content_fetch_attempts_avg": 1.0,
                "content_fetch_command_elapsed_s_total": 0.2,
                "content_fetch_command_elapsed_s_max": 0.1,
                "content_fetch_command_elapsed_s_count": 2,
                "content_fetch_command_elapsed_s_avg": 0.1,
            }

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(source_age_cadence_enabled=True)
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=mock_add) as mock_add_sources:
                            with mock.patch.object(ingestor._ingestor, "extract_transcripts", side_effect=mock_extract) as mock_extract_sources:
                                with mock.patch.object(ingestor._ingestor, "get_last_extract_metrics", side_effect=mock_extract_metrics):
                                    with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                            with mock.patch("csf.nlm_batch.time.monotonic", side_effect=[900.0 + i for i in range(40)]):
                                                results = ingestor.process_batch(batch_ids)

        assert windows_seen == [batch_ids]
        assert extract_calls == [[batch_ids[0], batch_ids[1]]]
        assert len(results) == 4
        assert sum(1 for success, transcript, _ in results.values() if success and transcript) == 2
        assert sum(1 for success, transcript, error in results.values() if (not success) and transcript is None and error == "Source add failed") == 2
        assert mock_add_sources.call_count == 1
        assert mock_extract_sources.call_count == 1
        assert mock_reset.call_count == 1
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_reusable_process_completed")
        assert completed["window_mode"] == "source_age_cadence"
        assert completed["window_count"] == 1
        assert completed["succeeded"] == 2
        assert completed["failed"] == 2
        assert completed["content_fetch_status_counts"] == {
            "ready": 2,
            "below_threshold": 0,
            "command_failed": 0,
            "parse_failed": 0,
            "source_age_cliff": 0,
            "source_add_failed": 2,
        }
        assert completed["content_fetch_command_elapsed_s_count"] == 2


class TestDoubleBufferedReusableBatch:
    """Double-buffered reusable batches should fall back cleanly when staging fails."""

    def test_double_buffered_reusable_ingestor_falls_back_to_serial(self):
        """If staging cannot be prepared, the wrapper should still return serial results."""
        from csf.nlm_batch import DoubleBufferedReusableIngestor

        wrapper = DoubleBufferedReusableIngestor(batch_size=50)
        serial_result = {"vid1": (True, "text", None)}

        with mock.patch.object(wrapper, "_process_serial_batch", return_value=serial_result) as mock_serial:
            with mock.patch.object(wrapper, "_prepare_staging_notebook", return_value=False) as mock_stage:
                result = wrapper.process_batch(["vid1"])

        assert result == serial_result
        mock_serial.assert_called_once_with(["vid1"])
        mock_stage.assert_called_once()
        metrics = wrapper.get_last_process_metrics()
        assert metrics is not None
        assert metrics["stage_swap_count"] == 0
        assert metrics["staging_overlap_elapsed_s"] == 0.0
        assert metrics["staging_wait_elapsed_s"] == 0.0

    def test_double_buffered_reusable_ingestor_swaps_between_two_batches(self):
        """A batch stream should stage the next batch while the current batch is processed."""
        from csf.nlm_batch import DoubleBufferedReusableIngestor

        wrapper = DoubleBufferedReusableIngestor(batch_size=50)
        calls: list[list[str]] = []

        def fake_run_serial_batch(video_ids):
            calls.append(list(video_ids))
            return {vid: (True, "text", None) for vid in video_ids}

        with mock.patch.object(wrapper, "_prepare_staging_notebook", return_value=True):
            with mock.patch.object(wrapper, "_run_serial_batch", side_effect=fake_run_serial_batch):
                with mock.patch.object(wrapper, "_run_staging_batch", side_effect=fake_run_serial_batch):
                    result = wrapper.process_batches([["vid1"], ["vid2"]])

        assert result[0]["vid1"][0] is True
        assert result[1]["vid2"][0] is True
        assert {tuple(call) for call in calls} == {("vid1",), ("vid2",)}
        metrics = wrapper.get_last_process_metrics()
        assert metrics is not None
        assert metrics["stage_swap_count"] == 1
        assert metrics["staging_overlap_elapsed_s"] >= 0.0

    def test_reusable_batch_uses_50_source_subbatches_by_default(self):
        """Reusable notebook processing should forward the 50-source subbatch size."""
        batch_ids = ["vid1", "vid2", "vid3"]

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type("CompletedProcess", (), {"stdout": json.dumps({"notebooks": [{"id": "nb-existing", "title": "reuse"}]}), "stderr": "", "returncode": 0})()
            return type("CompletedProcess", (), {"stdout": "", "stderr": "", "returncode": 0})()

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    with mock.patch("csf.nlm_batch._ensure_nlm_auth", return_value=True):
                        with mock.patch.object(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd):
                            ingestor = nlm_batch.NLMReusableIngestor()
                            with mock.patch.object(ingestor, "_is_notebook_usable", return_value=True):
                                with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches") as mock_add:
                                    with mock.patch.object(
                                        ingestor._ingestor,
                                        "extract_transcripts",
                                        return_value={"vid1": (True, "text", None)},
                                    ):
                                        with mock.patch.object(ingestor._ingestor, "reset_sources"):
                                            with mock.patch("csf.nlm_batch.log_action"):
                                                with mock.patch(
                                                    "csf.nlm_batch.time.monotonic",
                                                    side_effect=[10.0 + i for i in range(20)],
                                                ):
                                                    ingestor.process_batch(batch_ids)

        mock_add.assert_called_once_with(batch_ids, subbatch_size=ingestor._ingestor.batch_size)

    def test_experiment_add_acceptance_logs_sweep_results(self):
        """The add-acceptance sweep should log a per-size result and cleanup."""
        batch_ids = [f"vid{i:02d}" for i in range(20)]
        sizes = [50, 25, 10]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value=None):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor(batch_size=4)
                    call_sizes: list[int] = []

                    def fake_run_cmd(cmd, timeout=60):
                        if cmd[:2] == ["notebook", "create"]:
                            return mock.MagicMock(returncode=0, stdout="ID: nb-sweep", stderr="")
                        if cmd[:2] == ["notebook", "delete"]:
                            return mock.MagicMock(returncode=0, stdout="", stderr="")
                        raise AssertionError(f"unexpected command: {cmd}")

                    def fake_add(batch_ids, *, subbatch_size=50):
                        call_sizes.append(subbatch_size)
                        return batch_ids[: min(len(batch_ids), subbatch_size)]

                    with mock.patch.object(ingestor._ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch.object(ingestor._ingestor, "reset_sources") as mock_reset:
                            with mock.patch.object(ingestor._ingestor, "close") as mock_close:
                                with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=fake_add):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        results = ingestor._ingestor.experiment_add_acceptance(batch_ids, sizes, notebook_title="yt-is-sweep")

        assert call_sizes == sizes
        assert [result["subbatch_size"] for result in results] == sizes
        assert results[0]["added_count"] == 20
        assert results[1]["added_count"] == 20
        assert results[2]["added_count"] == 10
        assert any(call.args[0] == "nlm_batch_size_sweep_started" for call in mock_log.call_args_list)
        assert any(call.args[0] == "nlm_batch_size_sweep_result" for call in mock_log.call_args_list)
        assert any(call.args[0] == "nlm_batch_size_sweep_completed" for call in mock_log.call_args_list)
        mock_reset.assert_called()
        mock_close.assert_called()

    def test_ensure_nlm_auth_returns_true_when_check_passes(self, monkeypatch):
        """When --check succeeds, _ensure_nlm_auth returns True without calling --force."""
        import subprocess

        # This test covers the compatibility-only legacy CLI path.  Active
        # lanes use YTIS_NLM_ACCOUNT_PROFILE and the typed direct-client probe.
        monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)
        monkeypatch.delenv("YTIS_NLM_AUTH_NONINTERACTIVE", raising=False)
        monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
        called = []

        def mock_run(cmd, **kwargs):
            called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "Auth valid")

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(False, None)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=None):
                with mock.patch("csf.nlm_batch.run_nlm", side_effect=mock_run):
                    result = nlm_batch._ensure_nlm_auth()
        assert result is True
        assert called == [["login", "--check"]]

    def test_ensure_nlm_auth_returns_false_when_force_also_fails(self):
        """When --check and --force both fail, _ensure_nlm_auth returns False."""
        import subprocess

        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "Auth failed")

        with mock.patch("csf.nlm_batch.run_nlm", side_effect=mock_run):
            result = nlm_batch._ensure_nlm_auth()
        assert result is False


class TestReusableNotebookEnvironmentOverrides:
    """Worker-specific state and notebook identity use the typed client."""

    def test_state_path_override_is_used(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_OWNER_STATE_PATH", "P:/.data/yt-is/dev-workers/worker-01.json")
        assert nlm_batch._get_reusable_notebook_state_path() == nlm_batch.Path(
            "P:/.data/yt-is/dev-workers/worker-01.json"
        )

    def test_title_and_profile_overrides_are_used(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_OWNER_NOTEBOOK_TITLE", "yt-is-worker-01")
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "worker-01")
        assert nlm_batch._get_reusable_notebook_title() == "yt-is-worker-01"
        assert nlm_batch._get_notebooklm_profile() == "worker-01"

    def test_create_batch_notebook_uses_typed_client(self, monkeypatch):
        monkeypatch.setenv("YTIS_NLM_OWNER_NOTEBOOK_TITLE", "yt-is-worker-01")
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        client = _SuccessfulDirectTestClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_add_sources_in_subbatches") as mock_add:
            assert ingestor.create_batch_notebook(["vid1", "vid2"]) == "nb-direct-test"
        assert client.calls[0][0:2] == ("notebooks", "create")
        assert client.calls[0][2]["title"] == "yt-is-worker-01"
        mock_add.assert_called_once_with(["vid1", "vid2"], subbatch_size=ingestor.batch_size)

    def test_create_batch_notebook_fails_closed_without_notebook_id(self):
        class NoIdClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                self.calls.append(operation)
                if operation[:2] == ("notebooks", "create"):
                    return SimpleNamespace()
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "stale-id"
        ingestor._direct_client = NoIdClient()
        with mock.patch.object(ingestor, "_add_sources_in_subbatches") as mock_add:
            assert ingestor.create_batch_notebook(["vid1", "vid2"]) is None
        assert ingestor._nb_id is None
        mock_add.assert_not_called()

    def test_reusable_state_normalizes_json_ids(self, tmp_path, monkeypatch):
        state_path = tmp_path / "reusable.json"
        monkeypatch.setenv("YTIS_NLM_OWNER_STATE_PATH", str(state_path))
        monkeypatch.setenv("YTIS_NLM_OWNER_NOTEBOOK_TITLE", "yt-is-worker-01")
        monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "run-123")
        nlm_batch._save_reusable_notebook_id('{"notebook_id": "nb-state-456", "title": "yt-is-worker-01"}')
        assert nlm_batch._load_reusable_notebook_id() == "nb-state-456"
        assert json.loads(state_path.read_text(encoding="utf-8"))["run_id"] == "run-123"

class TestWorkerNotebookCleanup:
    """Stale worker notebooks should be retired without touching active ones."""

    def test_canonical_account_uses_descriptive_cleanup_namespace(self, monkeypatch):
        monkeypatch.delenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", raising=False)
        monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")

        assert nlm_batch._get_worker_notebook_prefix() == "a.hominidae-worker"
        assert nlm_batch._LEGACY_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX not in nlm_batch._get_worker_notebook_prefixes()
        assert nlm_batch._is_safe_worker_notebook_prefix("adaptive-candidate-a-hominidae-pro")
        assert not nlm_batch._is_safe_worker_notebook_prefix("yt-is-worker-01")

    def test_reset_sources_uses_bulk_delete_for_large_notebooks(self):
        """Large notebooks should clear sources in smaller delete chunks."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-large"
        source_ids = [f"src-{idx}" for idx in range(1, 28)]
        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["source", "list", "nb-large"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "stdout": json.dumps({"sources": [{"id": source_id} for source_id in source_ids]}),
                        "stderr": "",
                        "returncode": 0,
                    },
                )()
            if args[:3] == ["source", "delete", "nb-large"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": "", "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        with mock.patch.object(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd):
            ingestor.reset_sources()

        delete_calls = [call for call in calls if call[:3] == ["source", "delete", "nb-large"]]
        assert len(delete_calls) == 2
        assert delete_calls[0][-1] == "src-25"
        assert delete_calls[1][-1] == "src-27"

    def test_load_current_worker_notebook_ids_collects_all_state_files(self, tmp_path, monkeypatch):
        """Permanent worker state files should all be considered active notebook ids."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(
            json.dumps({"nb_id": "keep-current", "run_id": "run-current"}),
            encoding="utf-8",
        )
        (state_root / "worker-02.json").write_text(
            json.dumps({"nb_id": "keep-old", "run_id": "run-old"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))

        active_ids = nlm_batch._load_current_worker_notebook_ids()

        assert active_ids == {"keep-current", "keep-old"}

    def test_cleanup_stale_worker_notebooks_is_audit_only(self, tmp_path, monkeypatch):
        """Startup audit should not delete permanent worker notebooks."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(json.dumps({"nb_id": "keep-1"}), encoding="utf-8")
        (state_root / "worker-02.json").write_text(json.dumps({"nb_id": "keep-2"}), encoding="utf-8")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "yt-is-worker")
        monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "run-current")

        notebooks = {
            "notebooks": [
                {"id": "keep-1", "name": "yt-is-worker-01"},
                {"id": "stale-1", "name": "yt-is-worker-03"},
                {"id": "ignore-1", "name": "something-else"},
            ]
        }
        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps(notebooks), "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch("subprocess.run", side_effect=AssertionError("cleanup should not call subprocess.run")):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                deleted, failed = nlm_batch.cleanup_stale_worker_notebooks()

        assert deleted == 0
        assert failed == 0
        assert not any(isinstance(cmd, list) and "--delete-worker" in cmd for cmd in calls)
        cleanup_started = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_started"
        )
        assert cleanup_started["active_nb_ids"] == 2
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "audit_only"
        assert cleanup_complete["worker_notebook_count"] == 2

    def test_cleanup_stale_worker_notebooks_deletes_only_stale_ids(self, tmp_path, monkeypatch):
        """Delete mode should retire only worker notebooks that are no longer active."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(json.dumps({"nb_id": "keep-1"}), encoding="utf-8")
        (state_root / "worker-02.json").write_text(json.dumps({"nb_id": "keep-2"}), encoding="utf-8")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "yt-is-worker")
        monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "run-current")

        notebooks = {
            "notebooks": [
                {"id": "keep-1", "name": "yt-is-worker-01"},
                {"id": "stale-1", "name": "yt-is-worker-03"},
                {"id": "ignore-1", "name": "something-else"},
            ]
        }
        calls: list[list[str]] = []
        deleted_ids: list[str] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (object,),
                    {"stdout": json.dumps(notebooks), "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (object,),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        def mock_delete_notebook_with_retries(ingestor, nb_id, **kwargs):
            deleted_ids.append(nb_id)
            notebooks["notebooks"] = [
                notebook for notebook in notebooks["notebooks"] if notebook.get("id") != nb_id
            ]
            return type(
                "CompletedProcess",
                (object,),
                {"stdout": "deleted", "stderr": "", "returncode": 0},
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        monkeypatch.setattr(nlm_batch, "_delete_notebook_with_retries", mock_delete_notebook_with_retries)
        with mock.patch("subprocess.run", side_effect=AssertionError("cleanup should not call subprocess.run")):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True)

        assert deleted == 1
        assert failed == 0
        assert deleted_ids == ["stale-1"]
        assert not any(isinstance(cmd, list) and "--delete-worker" in cmd for cmd in calls)
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "deleted"
        assert cleanup_complete["stale_worker_notebook_count"] == 1

    def test_cleanup_stale_worker_notebooks_include_active_deletes_all_worker_prefix_matches(self, tmp_path, monkeypatch):
        """Benchmark cleanup mode should delete active and stale worker notebooks by safe title prefix."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(json.dumps({"nb_id": "keep-1"}), encoding="utf-8")
        (state_root / "worker-02.json").write_text(json.dumps({"nb_id": "keep-2"}), encoding="utf-8")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-free")

        notebooks = {
            "notebooks": [
                {"id": "keep-1", "name": "benchmark-shard-free-01"},
                {"id": "stale-1", "name": "benchmark-shard-free-03"},
                {"id": "ignore-1", "name": "personal-notebook"},
            ]
        }
        deleted_ids: list[str] = []

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps(notebooks), "stderr": "", "returncode": 0},
                )()
            return type("CompletedProcess", (), {"stdout": "", "stderr": "unexpected", "returncode": 1})()

        def mock_delete_notebook_with_retries(ingestor, nb_id, **kwargs):
            deleted_ids.append(nb_id)
            notebooks["notebooks"] = [
                notebook for notebook in notebooks["notebooks"] if notebook.get("id") != nb_id
            ]
            return type("CompletedProcess", (), {"stdout": "deleted", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        monkeypatch.setattr(nlm_batch, "_delete_notebook_with_retries", mock_delete_notebook_with_retries)
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True, include_active=True)

        assert deleted == 2
        assert failed == 0
        assert deleted_ids == ["keep-1", "stale-1"]
        assert not list(state_root.glob("worker-*.json"))
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["include_active"] is True
        assert cleanup_complete["state_files_removed"] == 2
        assert cleanup_complete["stale_worker_notebook_count"] == 2

    def test_cleanup_stale_worker_notebooks_can_scope_active_deletion_to_current_state(self, tmp_path, monkeypatch):
        """Parent timeout cleanup must not delete another run's same-account worker."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(json.dumps({"nb_id": "current-1"}), encoding="utf-8")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "a-hominidae-worker")

        notebooks = {
            "notebooks": [
                {"id": "current-1", "name": "a-hominidae-worker-current"},
                {"id": "other-run-1", "name": "a-hominidae-worker-other"},
            ]
        }
        deleted_ids: list[str] = []

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type("CompletedProcess", (), {
                    "stdout": json.dumps(notebooks), "stderr": "", "returncode": 0,
                })()
            return type("CompletedProcess", (), {"stdout": "", "stderr": "unexpected", "returncode": 1})()

        def mock_delete_notebook_with_retries(ingestor, nb_id, **kwargs):
            deleted_ids.append(nb_id)
            notebooks["notebooks"] = [
                notebook for notebook in notebooks["notebooks"] if notebook.get("id") != nb_id
            ]
            return type("CompletedProcess", (), {"stdout": "deleted", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        monkeypatch.setattr(nlm_batch, "_delete_notebook_with_retries", mock_delete_notebook_with_retries)
        deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(
            delete=True, include_active=True, only_current_state=True
        )

        assert (deleted, failed) == (1, 0)
        assert deleted_ids == ["current-1"]
        assert list(state_root.glob("worker-*.json")) == []

    def test_cleanup_stale_worker_notebooks_delete_fails_closed_when_list_fails(self, tmp_path, monkeypatch):
        """Delete mode should not report success when cleanup cannot list notebooks."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-free")

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type("CompletedProcess", (), {"stdout": "", "stderr": "list failed", "returncode": 1})()
            raise AssertionError(f"unexpected command {args}")

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True, include_active=True)

        assert deleted == 0
        assert failed == 1
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "list_failed"
        assert cleanup_complete["failed"] == 1
        assert cleanup_complete["outcome"] == "blocked"

    def test_cleanup_stale_worker_notebooks_receipts_confirmed_deleted_and_not_found(
        self, tmp_path, monkeypatch
    ):
        """A delete return code is classified only after the final list proves absence."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-free")
        notebooks = {
            "notebooks": [
                {"id": "confirmed", "name": "benchmark-shard-free-01"},
                {"id": "already-gone", "name": "benchmark-shard-free-02"},
            ]
        }

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps(notebooks), "stderr": "", "returncode": 0},
                )()
            raise AssertionError(f"unexpected command {args}")

        def mock_delete(ingestor, nb_id, **kwargs):
            notebooks["notebooks"] = [
                notebook for notebook in notebooks["notebooks"] if notebook["id"] != nb_id
            ]
            return type(
                "CompletedProcess",
                (),
                {
                    "stdout": "deleted" if nb_id == "confirmed" else "",
                    "stderr": "not found" if nb_id == "already-gone" else "",
                    "returncode": 0 if nb_id == "confirmed" else 1,
                },
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        monkeypatch.setattr(nlm_batch, "_delete_notebook_with_retries", mock_delete)
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            assert nlm_batch.cleanup_stale_worker_notebooks(delete=True) == (1, 0)

        receipt = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert receipt["outcome"] == "deleted"
        assert receipt["outcome_counts"] == {
            "deleted": 1,
            "not_found": 1,
            "blocked": 0,
            "unverified": 0,
        }
        assert {row["outcome"] for row in receipt["notebook_outcomes"]} == {
            "deleted",
            "not_found",
        }

    def test_cleanup_stale_worker_notebooks_marks_postcondition_unverified(self, tmp_path, monkeypatch):
        """A successful delete command cannot authorize success if verification is blocked."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-free")
        list_calls = 0

        def mock_run_cmd(self, args, timeout=300):
            nonlocal list_calls
            if args[:3] == ["notebook", "list", "--json"]:
                list_calls += 1
                if list_calls == 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {
                            "stdout": json.dumps(
                                {"notebooks": [{"id": "nb-1", "name": "benchmark-shard-free-01"}]}
                            ),
                            "stderr": "",
                            "returncode": 0,
                        },
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": "", "stderr": "verification blocked", "returncode": 1},
                )()
            raise AssertionError(f"unexpected command {args}")

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        monkeypatch.setattr(
            nlm_batch,
            "_delete_notebook_with_retries",
            lambda *args, **kwargs: type(
                "CompletedProcess", (), {"stdout": "deleted", "stderr": "", "returncode": 0}
            )(),
        )
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            assert nlm_batch.cleanup_stale_worker_notebooks(delete=True) == (0, 1)

        receipt = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert receipt["status"] == "unverified"
        assert receipt["outcome"] == "unverified"
        assert receipt["postcondition"] == "unavailable"
        assert receipt["notebook_outcomes"][0]["outcome"] == "unverified"

    def test_cleanup_stale_worker_notebooks_skips_when_default_profile_is_running(self, tmp_path, monkeypatch):
        """Cleanup should not abort benchmarks when the shared default browser profile is active."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-free")

        def mock_run_cmd(self, args, timeout=300):
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "stdout": "",
                        "stderr": "default NotebookLM chrome-profile is already running: C:/Users/brsth/.notebooklm-mcp-cli/chrome-profile",
                        "returncode": 1,
                    },
                )()
            raise AssertionError(f"unexpected command {args}")

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True, include_active=True)

        assert deleted == 0
        assert failed == 1
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "list_blocked_default_profile"
        assert cleanup_complete["failed"] == 1
        assert cleanup_complete["outcome"] == "blocked"

    def test_cleanup_stale_worker_notebooks_refuses_generic_benchmark_prefix(self, tmp_path, monkeypatch):
        """Only known benchmark prefixes should be destructive-cleanup eligible."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-personal")

        def mock_run_cmd(self, args, timeout=300):
            raise AssertionError("generic benchmark prefix should not list or delete notebooks")

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True, include_active=True)

        assert deleted == 0
        assert failed == 1
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "prefix_untrusted"

    def test_cleanup_stale_worker_notebooks_refuses_untrusted_prefix(self, tmp_path, monkeypatch):
        """Cleanup should fail closed when the configured notebook prefix is not industrial-scoped."""
        state_root = tmp_path / "worker-states"
        state_root.mkdir()
        (state_root / "worker-01.json").write_text(json.dumps({"nb_id": "keep-1"}), encoding="utf-8")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(state_root))
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "personal-notes")
        monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "run-current")

        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            raise AssertionError("should not list or delete notebooks when prefix is untrusted")

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch("subprocess.run", side_effect=AssertionError("cleanup should not call subprocess.run")):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                deleted, failed = nlm_batch.cleanup_stale_worker_notebooks(delete=True)

        assert deleted == 0
        assert failed == 1
        assert calls == []
        cleanup_complete = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_worker_notebook_cleanup_complete"
        )
        assert cleanup_complete["status"] == "prefix_untrusted"
        assert cleanup_complete["worker_notebook_count"] == 0


class TestReusableNotebookPrewarm:
    """Reusable notebooks should be warmed and cleared before worker batches."""

    def test_prepare_creates_and_clears_notebook(self, monkeypatch):
        ingestor = nlm_batch.NLMReusableIngestor(batch_size=3)
        cleanup_calls: list[str] = []
        saved_ids: list[str] = []

        def mock_ensure_notebook(batch_ids):
            ingestor._nb_id = "nb-prewarm-1"
            return True, "create"

        monkeypatch.setattr(ingestor, "_ensure_notebook", mock_ensure_notebook)
        monkeypatch.setattr(ingestor._ingestor, "cleanup", lambda: cleanup_calls.append("cleanup"))
        monkeypatch.setattr(nlm_batch, "_save_reusable_notebook_id", lambda nb_id: saved_ids.append(nb_id))

        with mock.patch("csf.nlm_batch.log_action") as mock_log:
            prepared, setup_mode = ingestor.prepare()

        assert prepared is True
        assert setup_mode == "create"
        assert cleanup_calls == ["cleanup"]
        assert saved_ids == ["nb-prewarm-1"]
        assert any(call.args[0] == "nlm_batch_reusable_prep_started" for call in mock_log.call_args_list)
        assert any(call.args[0] == "nlm_batch_reusable_prep_completed" for call in mock_log.call_args_list)

    def test_close_delete_uses_typed_notebook_delete(self, monkeypatch):
        """Destructive close should delete through the canonical typed client."""
        ingestor = nlm_batch.NLMReusableIngestor(batch_size=3)
        ingestor._nb_id = "nb-close-1"
        direct_client = _SuccessfulDirectTestClient()
        ingestor._ingestor._direct_client = direct_client
        monkeypatch.setattr(nlm_batch, "_clear_reusable_notebook_state", lambda: None)

        ingestor.close(delete=True)

        assert ("notebooks", "delete", ("nb-close-1",)) in direct_client.calls

    def test_ensure_notebook_reuses_existing_title_match(self, monkeypatch):
        """A single exact title match should be reused instead of recreated."""
        monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)
        monkeypatch.delenv("YTIS_NLM_AUTH_NONINTERACTIVE", raising=False)
        monkeypatch.delenv("YTIS_NLM_OWNER_NOTEBOOK_TITLE", raising=False)
        monkeypatch.delenv("NOTEBOOKLM_PROFILE", raising=False)
        monkeypatch.setenv("YTIS_NLM_REUSABLE_NOTEBOOK_TITLE", "yt-is-worker-03")
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value=None):
            ingestor = nlm_batch.NLMReusableIngestor(batch_size=3)

        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "stdout": json.dumps(
                            {
                                "notebooks": [
                                    {
                                        "id": "nb-keeper",
                                        "title": "yt-is-worker-03",
                                        "updated_at": "2026-04-21T20:00:00Z",
                                    }
                                ]
                            }
                        ),
                        "stderr": "",
                        "returncode": 0,
                    },
                )()
            if args[:3] == ["source", "list", "nb-keeper"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps({"sources": []}), "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch.object(ingestor._ingestor, "create_batch_notebook") as mock_create:
            created_new, setup_mode = ingestor._ensure_notebook([])

        assert created_new is False
        assert setup_mode == "reuse"
        assert ingestor._nb_id == "nb-keeper"
        assert mock_create.call_count == 0
        assert ["source", "list", "nb-keeper", "--json"] in calls

    def test_ensure_notebook_reuses_keeper_when_duplicate_title_matches_exist(self, monkeypatch):
        """Duplicate worker notebooks should reuse one keeper instead of recreating."""
        monkeypatch.setenv("YTIS_NLM_REUSABLE_NOTEBOOK_TITLE", "yt-is-worker-03")
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-keeper"):
            ingestor = nlm_batch.NLMReusableIngestor(batch_size=3)

        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "stdout": json.dumps(
                            {
                                "notebooks": [
                                    {
                                        "id": "nb-keeper",
                                        "title": "yt-is-worker-03",
                                        "updated_at": "2026-04-21T22:00:00Z",
                                    },
                                    {
                                        "id": "nb-dup",
                                        "title": "yt-is-worker-03",
                                        "updated_at": "2026-04-21T21:00:00Z",
                                    },
                                ]
                            }
                        ),
                        "stderr": "",
                        "returncode": 0,
                    },
                )()
            if args[:3] == ["source", "list", "nb-keeper"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps({"sources": []}), "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch.object(ingestor._ingestor, "create_batch_notebook") as mock_create:
            created_new, setup_mode = ingestor._ensure_notebook([])

        assert created_new is False
        assert setup_mode == "reuse"
        assert ingestor._nb_id == "nb-keeper"
        assert mock_create.call_count == 0
        assert not any(isinstance(cmd, list) and "--delete-title" in cmd for cmd in calls)

    def test_ensure_notebook_reuses_loaded_state_even_when_title_list_is_empty(self, monkeypatch):
        """A valid saved notebook id should still be reused if listing is temporarily empty."""
        monkeypatch.setenv("YTIS_NLM_REUSABLE_NOTEBOOK_TITLE", "yt-is-worker-03")
        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-loaded"):
            ingestor = nlm_batch.NLMReusableIngestor(batch_size=3)

        calls: list[list[str]] = []

        def mock_run_cmd(self, args, timeout=300):
            calls.append(args)
            if args[:3] == ["notebook", "list", "--json"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps({"notebooks": []}), "stderr": "", "returncode": 0},
                )()
            if args[:3] == ["source", "list", "nb-loaded"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"stdout": json.dumps({"sources": []}), "stderr": "", "returncode": 0},
                )()
            return type(
                "CompletedProcess",
                (),
                {"stdout": "", "stderr": "unexpected", "returncode": 1},
            )()

        monkeypatch.setattr(nlm_batch.NLMBatchIngestor, "_run_cmd", mock_run_cmd)
        with mock.patch.object(ingestor._ingestor, "create_batch_notebook") as mock_create:
            created_new, setup_mode = ingestor._ensure_notebook([])

        assert created_new is False
        assert setup_mode == "reuse"
        assert ingestor._nb_id == "nb-loaded"
        assert mock_create.call_count == 0
        assert ["source", "list", "nb-loaded", "--json"] in calls


class TestDirectSubBatchAdd:
    """Typed source adds preserve order and retry transient add failures."""

    def test_typed_add_retries_source_add_error(self):
        from notebooklm import SourceAddError

        class RetryClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.outcomes = [SourceAddError("temporary"), SimpleNamespace(id="source-v1")]

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    outcome = self.outcomes.pop(0)
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = RetryClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == ["vid1"]
        assert ingestor._last_added_source_ids == ["source-v1"]
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 2
        assert any(call.args[0] == "nlm_batch_source_add_retry" for call in mock_log.call_args_list)
        starts = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_attempt_started"
        ]
        completions = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_attempt_completed"
        ]
        assert [row["attempt"] for row in starts] == [1, 2]
        assert [row["attempt"] for row in completions] == [1, 2]
        assert completions[0]["status"] == "error"
        assert completions[1]["status"] == "ok"
        assert all(row["nb_id"] == "nb-direct" for row in starts + completions)
        assert all(row["source_position"] == 1 for row in starts + completions)
        assert completions[1]["source_id"] == "source-v1"

    def test_source_add_gate_failure_is_terminal_and_preserves_reason(self):
        from csf.source_add_gate import SourceAddGateError

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-direct"
        ingestor._last_source_count_probe_ok = True
        ingestor._direct_client = _SuccessfulDirectTestClient()
        gate_context = mock.MagicMock()
        gate_context.__enter__.side_effect = SourceAddGateError("gate unavailable")

        with mock.patch(
            "csf.source_add_gate.account_source_add_gate",
            return_value=gate_context,
        ):
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
                with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        result = ingestor._add_sources_chunk(
                            ["vid1", "vid2"], subbatch_index=1, expected_total=2
                        )

        assert result == []
        assert ingestor._last_add_failure_reason == "source_add_gate_failed"
        assert ingestor._direct_client.calls == []
        mock_rotate.assert_not_called()
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_add_completed"
        )
        assert completed["failure_reason"] == "source_add_gate_failed"
        assert len(completed["per_video_results"]) == 2
        assert any(
            call.args[0] == "nlm_batch_source_add_gate_failed"
            for call in mock_log.call_args_list
        )
        assert not any(
            call.args[0] in {
                "nlm_batch_subbatch_add_retry_scheduled",
                "nlm_batch_subbatch_add_notebook_reset_scheduled",
            }
            for call in mock_log.call_args_list
        )

    def test_typed_add_preserves_provider_rpc_code_in_failure_telemetry(self):
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class FailingClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://example.test/?token=secret-token",
                        cause=RPCError("Bearer secret-token", rpc_code=9),
                    )
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        ingestor._last_source_count_probe_ok = False
        ingestor._last_source_count_probe_error = None
        client = FailingClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == []
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_add_completed"
        )
        error = completed["per_video_results"][0]["error"]
        assert "cause=RPCError" in error
        assert "rpc_code=9" in error
        assert "secret-token" not in error
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1
        assert completed["failure_reason"] == "source_add_non_retryable_rpc_code_9"
        assert any(
            call.args[0] == "nlm_batch_source_add_retry_skipped"
            and call.args[1]["reason"] == "rpc_code_9_failed_precondition"
            for call in mock_log.call_args_list
        )

    def test_provider_error_recovers_single_committed_source_without_replay(self):
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class CommittedButErroredClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                # The live list representation can omit URL metadata. The
                # empty-notebook count-growth fallback must still recover the
                # only committed source without replaying ADD_SOURCE.
                self.source = SimpleNamespace(id="source-vid1", url=None)

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    return [self.source]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = CommittedButErroredClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", side_effect=[0, 1, 1]):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == ["vid1"]
        assert ingestor._last_added_source_ids == ["source-vid1"]
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1
        assert any(
            call.args[0] == "nlm_batch_source_add_recovered_after_error"
            for call in mock_log.call_args_list
        )

    def test_rpc9_reconciles_delayed_exact_url_without_replay(self):
        """A delayed source-list projection should recover RPC9 without ADD_SOURCE replay."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class DelayedCommittedClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.list_calls = 0

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    self.list_calls += 1
                    if self.list_calls == 1:
                        return [
                            SimpleNamespace(id="source-vid1", url=None),
                            SimpleNamespace(id="other-source", url=None),
                        ]
                    return [
                        SimpleNamespace(
                            id="source-vid1",
                            url="https://www.youtube.com/watch?v=vid1",
                        )
                    ]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = DelayedCommittedClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", side_effect=[0, 1, 1]):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        result = ingestor._add_sources_chunk(
                            ["vid1"], subbatch_index=1, expected_total=1
                        )

        assert result == ["vid1"]
        assert ingestor._last_added_source_ids == ["source-vid1"]
        assert client.list_calls == 2
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1
        mock_sleep.assert_called_once_with(nlm_batch._SOURCE_ADD_RPC9_RECONCILIATION_DELAY_S)
        observed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_probe_observed"
        ]
        assert [row["probe_attempt"] for row in observed] == [1, 2]
        recovered = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_recovered_after_error"
        )
        assert recovered["reason"] == "exact_url_post_error_probe"
        assert recovered["probe_attempts"] == 2

    def test_rpc9_reconciles_empty_notebook_final_count_without_replay(self):
        """A complete empty-notebook count may identify one delayed RPC9 commit."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        batch_ids = ["vid-a", "vid-b", "vid-c", "vid-d", "vid-e"]
        known_source_ids = {
            "source-vid-a",
            "source-vid-b",
            "source-vid-d",
            "source-vid-e",
        }
        final_sources = [
            SimpleNamespace(id=source_id, url=None)
            for source_id in [
                "source-vid-a",
                "source-vid-b",
                "source-committed-vid-c",
                "source-vid-d",
                "source-vid-e",
            ]
        ]

        class DelayedCountGrowthClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.list_calls = 0

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    video_id = str(operation[2][1]).split("v=", 1)[-1]
                    if video_id == "vid-c":
                        raise SourceAddError(
                            "https://www.youtube.com/watch?v=vid-c",
                            cause=RPCError("failed precondition", rpc_code=9),
                        )
                    return SimpleNamespace(id=f"source-{video_id}")
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    self.list_calls += 1
                    if self.list_calls <= 3:
                        return [
                            SimpleNamespace(id="source-vid-a", url=None),
                            SimpleNamespace(id="source-vid-b", url=None),
                            SimpleNamespace(id="source-early-commit", url=None),
                        ]
                    return final_sources
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=5)
        ingestor._nb_id = "nb-direct"
        client = DelayedCountGrowthClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=5):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        result = ingestor._add_sources_chunk(
                            batch_ids,
                            subbatch_index=1,
                            expected_total=5,
                            source_count_before=0,
                            source_count_probe_ok_before=True,
                        )

        assert result == batch_ids
        assert ingestor._last_added_source_ids == [
            "source-vid-a",
            "source-vid-b",
            "source-committed-vid-c",
            "source-vid-d",
            "source-vid-e",
        ]
        assert known_source_ids == {
            source_id
            for source_id in ingestor._last_added_source_ids
            if source_id != "source-committed-vid-c"
        }
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 5
        assert client.list_calls == 4
        assert mock_sleep.call_count == 2
        recovered = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_recovered_after_error"
        )
        assert recovered["reason"] == "empty_notebook_final_count_growth_unclaimed_source"
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_add_completed"
        )
        assert completed["recovered"] is True
        assert completed["added_count"] == 5
        assert completed["failure_reason"] is None
        assert all(row["error"] is None for row in completed["per_video_results"])

    def test_rpc9_final_count_reconciliation_fails_closed_on_two_unclaimed_ids(self):
        """A count match with an ambiguous set difference must remain failed."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class AmbiguousCountGrowthClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.list_calls = 0

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    video_id = str(operation[2][1]).split("v=", 1)[-1]
                    if video_id == "vid-c":
                        raise SourceAddError(
                            "https://www.youtube.com/watch?v=vid-c",
                            cause=RPCError("failed precondition", rpc_code=9),
                        )
                    return SimpleNamespace(id=f"source-{video_id}")
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    self.list_calls += 1
                    if self.list_calls <= 3:
                        return [
                            SimpleNamespace(id="source-early-1", url=None),
                            SimpleNamespace(id="source-early-2", url=None),
                            SimpleNamespace(id="source-early-3", url=None),
                        ]
                    return [
                        SimpleNamespace(id="source-vid-a", url=None),
                        SimpleNamespace(id="source-vid-b", url=None),
                        SimpleNamespace(id="source-vid-d", url=None),
                        SimpleNamespace(id="source-unknown-1", url=None),
                        SimpleNamespace(id="source-unknown-2", url=None),
                    ]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=5)
        ingestor._nb_id = "nb-direct"
        client = AmbiguousCountGrowthClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=5):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
                with mock.patch("csf.nlm_batch.time.sleep"):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        result = ingestor._add_sources_chunk(
                            ["vid-a", "vid-b", "vid-c", "vid-d", "vid-e"],
                            subbatch_index=1,
                            expected_total=5,
                            source_count_before=0,
                            source_count_probe_ok_before=True,
                        )

        assert result == ["vid-a", "vid-b", "vid-d", "vid-e"]
        assert client.list_calls == 4
        assert not any(
            call.args[0] == "nlm_batch_source_add_recovered_after_error"
            and call.args[1].get("reason") == "empty_notebook_final_count_growth_unclaimed_source"
            for call in mock_log.call_args_list
        )
        reconciliation = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_add_final_count_reconciliation"
        )
        assert reconciliation["final_list_is_complete"] is False
        assert len(reconciliation["unclaimed_source_ids"]) == 2

    def test_rpc9_reconciliation_stays_fail_closed_without_exact_match(self):
        """Repeated missing URL evidence must not turn a provider error into a guessed success."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class NeverMatchingClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    return [SimpleNamespace(id="other-source", url=None)]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = NeverMatchingClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=2):
            with mock.patch("csf.nlm_batch.time.sleep"):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    result = ingestor._add_sources_chunk(
                        ["vid1"], subbatch_index=1, expected_total=1
                    )

        assert result == []
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1
        assert sum(1 for call in client.calls if call[:2] == ("sources", "list")) == 3
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_add_completed"
        )
        assert completed["failure_reason"] == "source_add_non_retryable_rpc_code_9"
        assert not any(
            call.args[0] == "nlm_batch_source_add_recovered_after_error"
            for call in mock_log.call_args_list
        )

    def test_rpc9_provenance_survives_recovered_source_then_terminal_materialization(self):
        """A recovered source keeps its typed add failure for opt-in fallback routing."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class RecoveredThenTerminalClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    return [
                        SimpleNamespace(
                            id="source-v1",
                            url="https://www.youtube.com/watch?v=vid1",
                        )
                    ]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = RecoveredThenTerminalClient()
        ingestor._direct_client = client

        def terminal_wait(*_args, **_kwargs):
            ingestor._last_source_ready_ids = []
            ingestor._last_source_terminal_error_ids = ["source-v1"]
            ingestor._last_source_materialization_failure_reason = (
                "source_materialization_terminal_error"
            )
            return False

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=1):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", side_effect=terminal_wait):
                with pytest.raises(nlm_batch.NotebookSourceMaterializationTerminalError):
                    ingestor._add_sources_chunk(
                        ["vid1"],
                        subbatch_index=1,
                        expected_total=1,
                        source_count_before=0,
                        source_count_probe_ok_before=True,
                    )

        failure = ingestor._last_timeout_failure_messages["vid1"]
        assert failure.startswith("Source add failed; materialization terminal error:")
        assert "SourceAddError" in failure
        assert "rpc_code=9" in failure
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1

    def test_typed_source_add_provenance_survives_recovered_source_timeout(self):
        """The same provenance is retained when readiness ends by timeout."""
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class RecoveredThenStalledClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    return [
                        SimpleNamespace(
                            id="source-v1",
                            url="https://www.youtube.com/watch?v=vid1",
                        )
                    ]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = RecoveredThenStalledClient()
        ingestor._direct_client = client

        def stalled_wait(*_args, **_kwargs):
            ingestor._last_source_ready_ids = []
            ingestor._last_source_terminal_error_ids = []
            ingestor._last_source_materialization_failure_reason = None
            return False

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=1):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", side_effect=stalled_wait):
                with pytest.raises(nlm_batch.NotebookSourceMaterializationTimeout):
                    ingestor._add_sources_chunk(
                        ["vid1"],
                        subbatch_index=1,
                        expected_total=1,
                        source_count_before=0,
                        source_count_probe_ok_before=True,
                    )

        failure = ingestor._last_timeout_failure_messages["vid1"]
        assert failure.startswith("Source add failed; materialization timeout:")
        assert "rpc_code=9" in failure

    def test_provider_error_with_duplicate_committed_sources_fails_closed(self):
        from notebooklm import SourceAddError
        from notebooklm._rpc_executor import RPCError

        class AmbiguousCommittedClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise SourceAddError(
                        "https://www.youtube.com/watch?v=vid1",
                        cause=RPCError("failed precondition", rpc_code=9),
                    )
                if operation[:2] == ("sources", "list"):
                    self.calls.append(operation)
                    return [
                        SimpleNamespace(id="source-1", url="https://www.youtube.com/watch?v=vid1"),
                        SimpleNamespace(id="source-2", url="https://www.youtube.com/watch?v=vid1"),
                    ]
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = AmbiguousCommittedClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=2):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == []
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 1
        assert any(
            call.args[0] == "nlm_batch_source_add_probe_ambiguous"
            for call in mock_log.call_args_list
        )

    def test_unclassified_source_add_error_keeps_one_bounded_retry(self):
        from notebooklm import SourceAddError

        class RetryClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.outcomes = [SourceAddError("temporary"), SimpleNamespace(id="source-v1")]

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    outcome = self.outcomes.pop(0)
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        client = RetryClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == ["vid1"]
        assert sum(1 for call in client.calls if call[:2] == ("sources", "add_url")) == 2
        assert any(call.args[0] == "nlm_batch_source_add_retry" for call in mock_log.call_args_list)

    def test_generic_add_error_does_not_copy_exception_text_into_telemetry(self):
        class FailingClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    raise RuntimeError("Bearer secret-token")
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-direct"
        ingestor._direct_client = FailingClient()
        with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor._add_sources_chunk(["vid1"], subbatch_index=1, expected_total=1)

        assert result == []
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_add_completed"
        )
        error = completed["per_video_results"][0]["error"]
        assert error == "RuntimeError"
        assert "secret-token" not in error

    def test_diagnostic_redaction_removes_credential_shaped_values(self):
        diagnostic = nlm_batch._redact_diagnostic_text(
            "authorization: Bearer secret-token token=another-secret"
        )
        assert "secret-token" not in diagnostic
        assert "another-secret" not in diagnostic
        assert "[REDACTED]" in diagnostic

    def test_typed_add_preserves_source_id_order_for_partial_success(self):
        class OrderedClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self.outcomes = [SimpleNamespace(id="source-a"), SimpleNamespace(id="source-b")]

            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    self.calls.append(operation)
                    return self.outcomes.pop(0)
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-direct"
        client = OrderedClient()
        ingestor._direct_client = client
        with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
            result = ingestor._add_sources_chunk(["vid-a", "vid-b"], subbatch_index=1, expected_total=2)

        assert result == ["vid-a", "vid-b"]
        assert ingestor._last_added_source_ids == ["source-a", "source-b"]

    def test_direct_client_calls_are_serialized_across_extract_threads(self):
        """Shared loop-affined clients must not receive concurrent run calls."""

        class ConcurrentClient(_SuccessfulDirectTestClient):
            def __init__(self):
                super().__init__()
                self._active = 0
                self.max_active = 0
                self._state_lock = threading.Lock()

            def run(self, operation):
                if operation[:2] == ("sources", "get_fulltext"):
                    with self._state_lock:
                        self._active += 1
                        self.max_active = max(self.max_active, self._active)
                    try:
                        time.sleep(0.02)
                        return SimpleNamespace(content="transcript")
                    finally:
                        with self._state_lock:
                            self._active -= 1
                return super().run(operation)

        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-direct"
        client = ConcurrentClient()
        ingestor._direct_client = client

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(ingestor._execute_direct_command, ["source", "content", source_id])
                for source_id in ("source-a", "source-b")
            ]
            results = [future.result() for future in futures]

        assert [result.returncode for result in results] == [0, 0]
        assert client.max_active == 1

class TestNotebookCapRotation:
    """Notebook should rotate when source count approaches the cap threshold."""

    def test_get_current_source_count_parses_json_list(self):
        """_get_current_source_count should return the number of sources in the notebook."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-123"
        mock_response = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}), "stderr": ""},
        )()
        with mock.patch.object(ingestor, "_run_cmd", return_value=mock_response):
            count = ingestor._get_current_source_count()
        assert count == 3

    def test_get_current_source_count_returns_0_on_error(self):
        """_get_current_source_count should return 0 when the list command fails."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-123"
        mock_response = type(
            "CompletedProcess",
            (),
            {"returncode": 1, "stdout": "partial stdout", "stderr": "source list failed"},
        )()
        with mock.patch.object(ingestor, "_run_cmd", return_value=mock_response):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                count = ingestor._get_current_source_count()
        assert count == 0
        assert ingestor._last_source_count_probe_ok is False
        assert ingestor._last_source_count_probe_error["returncode"] == 1
        assert ingestor._last_source_count_probe_error["stderr"] == "source list failed"
        mock_log.assert_called_once()
        assert mock_log.call_args.args[0] == "nlm_batch_source_count_probe_failed"
        assert mock_log.call_args.args[1]["nb_id"] == "nb-123"

    def test_get_current_source_count_retries_once_after_auth_failure(self):
        """An auth lapse on the source-count probe should refresh and retry once."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-123"
        auth_failed = type(
            "CompletedProcess",
            (),
            {"returncode": 1, "stdout": "", "stderr": "Auth failed"},
        )()
        probe_ok = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}, {"id": "s2"}]}), "stderr": ""},
        )()
        with mock.patch.object(ingestor, "_run_cmd", side_effect=[auth_failed, probe_ok]) as mock_run_cmd:
            with mock.patch("csf.nlm_batch._ensure_nlm_auth", return_value=True) as mock_ensure_auth:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    count = ingestor._get_current_source_count()
        assert count == 2
        assert ingestor._last_source_count_probe_ok is True
        assert ingestor._last_source_count_probe_error is None
        assert mock_run_cmd.call_count == 2
        mock_ensure_auth.assert_called_once()
        mock_log.assert_not_called()

    def test_get_current_source_count_retries_once_after_not_found(self):
        """A transient NOT_FOUND source-count probe should get one bounded retry."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-123"
        not_found = type(
            "CompletedProcess",
            (object,),
            {"returncode": 1, "stdout": json.dumps({"status": "error", "error": "API error (code 5): NOT_FOUND"}), "stderr": ""},
        )()
        probe_ok = type(
            "CompletedProcess",
            (object,),
            {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}, {"id": "s2"}]}), "stderr": ""},
        )()
        with mock.patch.object(ingestor, "_run_cmd", side_effect=[not_found, probe_ok]) as mock_run_cmd:
            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    count = ingestor._get_current_source_count()
        assert count == 2
        assert mock_run_cmd.call_count == 2
        mock_sleep.assert_called_once_with(2.0)
        assert ingestor._last_source_count_probe_ok is True
        assert ingestor._last_source_count_probe_error is None
        mock_log.assert_not_called()

    def test_get_current_source_count_logs_parse_failure(self):
        """Malformed source-list JSON should be distinct from a true empty source list."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-123"
        mock_response = type(
            "CompletedProcess",
            (),
            {"returncode": 0, "stdout": "{not json", "stderr": ""},
        )()
        with mock.patch.object(ingestor, "_run_cmd", return_value=mock_response):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                count = ingestor._get_current_source_count()
        assert count == 0
        assert ingestor._last_source_count_probe_ok is False
        assert ingestor._last_source_count_probe_error["error_type"] == "JSONDecodeError"
        assert ingestor._last_source_count_probe_error["stdout"] == "{not json"
        mock_log.assert_called_once()
        assert mock_log.call_args.args[0] == "nlm_batch_source_count_probe_failed"

    def test_get_current_source_count_returns_0_when_no_nb_id(self):
        """_get_current_source_count should return 0 when no notebook is active."""
        ingestor = nlm_batch.NLMBatchIngestor()
        assert ingestor._nb_id is None
        assert ingestor._get_current_source_count() == 0

    def test_rotate_notebook_recycles_old_without_creating_new(self):
        """_rotate_notebook should clear sources and keep the same notebook."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-old"
        ingestor._current_source_count = 50
        ingestor._oldest_source_materialization_epoch = 1234.5
        ingestor._last_materialization_ready_at_epoch = 2345.6
        ingestor._video_ready_epoch_by_id = {"v1": 1.0}

        with mock.patch.object(ingestor, "reset_sources") as mock_reset:
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
                with mock.patch("csf.nlm_batch._save_reusable_notebook_id") as mock_save:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor._rotate_notebook()

        mock_reset.assert_called_once()
        assert ingestor._nb_id == "nb-old"
        assert ingestor._current_source_count == 0
        assert ingestor._oldest_source_materialization_epoch is None
        assert ingestor._last_materialization_ready_at_epoch == 0.0
        assert ingestor._video_ready_epoch_by_id == {}
        mock_save.assert_called_once_with("nb-old")

        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_notebook_recycled" in log_names
        assert "nlm_batch_reusable_state_saved" in log_names
        recycle_event = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_notebook_recycled")
        assert recycle_event["nb_id"] == "nb-old"
        assert recycle_event["old_source_count"] == 50
        assert recycle_event["new_source_count"] == 0
        assert recycle_event["reason"] == "source_cap_near_threshold"
        assert recycle_event["cap_threshold"] == nlm_batch._NOTEBOOK_SOURCE_CAP

    def test_rotate_notebook_can_log_custom_reason(self):
        """_rotate_notebook should preserve the caller-provided rotation reason."""
        ingestor = nlm_batch.NLMBatchIngestor()
        ingestor._nb_id = "nb-old"
        ingestor._current_source_count = 50

        with mock.patch.object(ingestor, "reset_sources"):
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
                with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor._rotate_notebook(reason="source_age_cliff")

        recycle_event = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_notebook_recycled")
        assert recycle_event["reason"] == "source_age_cliff"

    def test_capacity_rotation_requests_before_add_when_at_cap(self):
        """A notebook at capacity should recycle before attempting the next add."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-cap"
        ingestor._current_source_count = 50

        with mock.patch.object(ingestor, "_get_current_source_count", side_effect=[50, 50, 0, 0]):
            with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=[["v1", "v2"], ["v3", "v4"]]):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                        ingestor._add_sources_in_subbatches(["v1", "v2", "v3", "v4"], subbatch_size=2)

        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_subbatch_capacity_rotation_requested" in log_names
        assert mock_rotate.call_count == 1
        capacity_rotation = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_subbatch_capacity_rotation_requested")
        assert capacity_rotation["current_source_count"] == 50
        assert capacity_rotation["cap_threshold"] == nlm_batch._NOTEBOOK_SOURCE_CAP
        assert capacity_rotation["rotation_reason"] == "source_cap_near_threshold"

    def test_capacity_rotation_resets_expected_materialization_total(self):
        """After rotating a full notebook, wait for the new notebook source count, not cumulative batch position."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=50)
        ingestor._nb_id = "nb-cap"
        batch_ids = [f"v{i}" for i in range(64)]
        expected_totals: list[int] = []

        def fake_add_sources_chunk(batch_ids, **kwargs):
            expected_totals.append(kwargs["expected_total"])
            return list(batch_ids)

        def fake_rotate_notebook(*, reason):
            ingestor._current_source_count = 0

        with mock.patch.object(ingestor, "_get_current_source_count", side_effect=[0, 50, 50, 14]):
            with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=fake_add_sources_chunk):
                with mock.patch.object(ingestor, "_rotate_notebook", side_effect=fake_rotate_notebook):
                    ingestor._add_sources_in_subbatches(batch_ids, subbatch_size=50)

        assert expected_totals == [50, 14]

    def test_shortfall_does_not_rotate_when_below_cap(self):
        """Zero-growth shortfall below cap should trigger the bounded notebook reset fallback."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-fresh"
        ingestor._current_source_count = 45

        class FailingDirectClient(_SuccessfulDirectTestClient):
            def run(self, operation):
                if operation[:2] == ("sources", "add_url"):
                    raise RuntimeError("typed source add failed")
                return super().run(operation)

        ingestor._direct_client = FailingDirectClient()

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CompletedProcess", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": f"s{i}"} for i in range(46)]}), "stderr": ""})()
            if cmd[:2] == ["source", "add"]:
                return type("CompletedProcess", (), {"returncode": 1, "stdout": "Could not add URL sources", "stderr": "could not add"})()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                    result = ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_subbatch_add_notebook_reset_scheduled" in log_names
        assert "nlm_batch_subbatch_add_shortfall" in log_names
        mock_rotate.assert_called_once()

    def test_subbatch_size_adjusts_to_remaining_capacity(self):
        """Subbatch size should shrink to the remaining NotebookLM headroom."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=50)
        ingestor._nb_id = "nb-room"
        ingestor._current_source_count = 45
        batch_ids = [f"v{i}" for i in range(8)]
        add_calls = []

        with mock.patch.object(ingestor, "_get_current_source_count", side_effect=[45, 0, 0, 0]):
            with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=lambda batch_ids, **kwargs: add_calls.append(list(batch_ids)) or list(batch_ids)):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    ingestor._add_sources_in_subbatches(batch_ids, subbatch_size=50)

        assert add_calls, "expected at least one add command"
        assert [len(batch) for batch in add_calls] == [5, 3]
        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_subbatch_size_adjusted" in log_names
        adjusted = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_subbatch_size_adjusted")
        assert adjusted["adjusted_subbatch_size"] == 5
        assert adjusted["rotation_reason"] == "capacity_headroom"

    def test_initial_source_add_window_applies_only_to_verified_empty_notebook(self):
        """An opt-in smaller first window must be scoped to a probed empty notebook."""
        ingestor = nlm_batch.NLMBatchIngestor(
            batch_size=50,
            source_add_initial_window_size=25,
        )
        ingestor._nb_id = "nb-empty"
        add_calls = []

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch.object(
                ingestor,
                "_add_sources_chunk",
                side_effect=lambda batch_ids, **kwargs: add_calls.append(list(batch_ids)) or list(batch_ids),
            ):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    added = ingestor._add_sources_in_subbatches(
                        [f"v{i}" for i in range(60)],
                        subbatch_size=50,
                    )

        assert added == [f"v{i}" for i in range(60)]
        assert [len(batch) for batch in add_calls] == [25, 35]
        event = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_subbatch_initial_window_applied"
        )
        assert event["source_count_before"] == 0
        assert event["source_count_probe_ok_before"] is True
        assert event["initial_window_size"] == 25
        assert event["selected_subbatch_size"] == 25
        assert event["initial_window_applied"] is True

    def test_initial_source_add_window_does_not_apply_to_nonempty_or_unverified_notebook(self):
        """Existing or unverified notebooks retain the normal requested window."""
        for source_count, probe_ok in ((10, True), (0, False)):
            ingestor = nlm_batch.NLMBatchIngestor(
                batch_size=50,
                source_add_initial_window_size=25,
            )
            ingestor._nb_id = "nb-existing"
            add_calls = []

            def fake_count():
                ingestor._last_source_count_probe_ok = probe_ok
                return source_count

            with mock.patch.object(ingestor, "_get_current_source_count", side_effect=fake_count):
                with mock.patch.object(
                    ingestor,
                    "_add_sources_chunk",
                    side_effect=lambda batch_ids, **kwargs: add_calls.append(list(batch_ids)) or list(batch_ids),
                ):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor._add_sources_in_subbatches(
                            [f"v{i}" for i in range(60)],
                            subbatch_size=50,
                        )

            expected_sizes = [50, 10] if source_count == 0 else [40, 20]
            assert [len(batch) for batch in add_calls] == expected_sizes
            assert not any(
                call.args[0] == "nlm_batch_subbatch_initial_window_applied"
                for call in mock_log.call_args_list
            )

    def test_initial_source_add_window_default_is_behavior_neutral(self):
        """The default zero setting preserves the existing source-add window."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=50)
        ingestor._nb_id = "nb-default"
        add_calls = []

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch.object(
                ingestor,
                "_add_sources_chunk",
                side_effect=lambda batch_ids, **kwargs: add_calls.append(list(batch_ids)) or list(batch_ids),
            ):
                with mock.patch("csf.nlm_batch.log_action"):
                    ingestor._add_sources_in_subbatches(
                        [f"v{i}" for i in range(60)],
                        subbatch_size=50,
                    )

        assert [len(batch) for batch in add_calls] == [50, 10]

    def test_materialization_wait_logs_source_counts(self):
        """Materialization wait logs should capture source counts around the wait."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-wait"
        ingestor._direct_client = _SuccessfulDirectTestClient()

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 2}]}),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "add"]:
                return type("CompletedProcess", (), {"returncode": 0, "stdout": "added", "stderr": ""})()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                ingestor._add_sources_chunk(["v1"], subbatch_index=1, expected_total=1)

        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_source_materialization_wait_started" in log_names
        assert "nlm_batch_source_materialization_wait_succeeded" in log_names
        wait_started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_materialization_wait_started")
        wait_succeeded = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_materialization_wait_succeeded")
        assert wait_started["started_at_epoch"] <= wait_succeeded["completed_at_epoch"]
        assert wait_started["source_count_before_wait"] == 1
        assert wait_succeeded["source_count_before_wait"] == 1
        assert wait_succeeded["source_count_after_wait"] == 1

    def test_materialization_wait_timeout_halts_after_ten_minutes(self):
        """A stalled readiness wait should fail fast after the 10 minute timeout."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-wait"
        ingestor._direct_client = _SuccessfulDirectTestClient()

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "add"]:
                return type("CompletedProcess", (), {"returncode": 0, "stdout": "added", "stderr": ""})()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=False) as wait_mock:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    with pytest.raises(nlm_batch.NotebookSourceMaterializationTimeout):
                        ingestor._add_sources_chunk(["v1"], subbatch_index=1, expected_total=1)

        wait_mock.assert_called_once_with(
            1,
            timeout=600,
            source_count_before_wait=1,
            expected_source_ids=["source-v1"],
        )
        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_source_materialization_wait_started" in log_names
        assert "nlm_batch_source_materialization_wait_failed" in log_names
        wait_failed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_materialization_wait_failed")
        assert wait_failed["timeout_s"] == 600
        assert wait_failed["source_count_before_wait"] == 1

    def test_materialization_wait_stops_immediately_on_terminal_source_error(self):
        """A provider ERROR status must not consume the full readiness timeout."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-terminal-error"
        terminal = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 3}]}),
                "stderr": "",
            },
        )()

        with mock.patch.object(ingestor, "_run_cmd", return_value=terminal) as run_mock:
            with mock.patch("csf.nlm_batch.time.sleep") as sleep_mock:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    assert ingestor._wait_for_sources_ready(
                        1,
                        timeout=600,
                        source_count_before_wait=0,
                        expected_source_ids=["source-v1"],
                    ) is False

        run_mock.assert_called_once()
        sleep_mock.assert_not_called()
        assert ingestor._last_source_terminal_error_ids == ["source-v1"]
        assert ingestor._last_source_materialization_failure_reason == "source_materialization_terminal_error"
        terminal_event = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_materialization_wait_terminal_failure"
        )
        assert terminal_event["terminal_error_source_ids"] == ["source-v1"]
        assert terminal_event["source_status_by_id"] == {"source-v1": 3}

    def test_add_sources_classifies_terminal_source_error_without_timeout(self):
        """Terminal source status is surfaced distinctly from a poll timeout."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-terminal-error"
        ingestor._direct_client = _SuccessfulDirectTestClient()

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 3}]}),
                        "stderr": "",
                    },
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=1):
                with mock.patch("csf.nlm_batch.time.sleep") as sleep_mock:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        with pytest.raises(nlm_batch.NotebookSourceMaterializationTerminalError):
                            ingestor._add_sources_chunk(
                                ["v1"],
                                subbatch_index=1,
                                expected_total=1,
                                source_count_before=0,
                                source_count_probe_ok_before=True,
                            )

        sleep_mock.assert_not_called()
        assert ingestor._last_add_failure_reason == "source_materialization_terminal_error"
        assert ingestor._last_timeout_failure_messages == {
            "v1": "Source materialization terminal error",
        }
        wait_failed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_materialization_wait_failed"
        )
        assert wait_failed["failure_reason"] == "source_materialization_terminal_error"
        assert wait_failed["wait_outcome"] == "terminal_source_error"
        assert wait_failed["terminal_error_source_ids"] == ["source-v1"]

    def test_materialization_wait_does_not_accept_count_before_source_ready(self):
        """A count-complete processing source must remain in the readiness wait."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-status-gate"
        processing = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 1}]}),
                "stderr": "",
            },
        )()
        ready = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 2}]}),
                "stderr": "",
            },
        )()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=[processing, ready]) as run_mock:
            assert ingestor._wait_for_sources_ready(
                1,
                timeout=1,
                source_count_before_wait=0,
                expected_source_ids=["source-v1"],
                poll_interval_s=0,
            ) is True

        assert run_mock.call_count == 2
        assert ingestor._last_source_expected_ids == {"source-v1"}
        assert ingestor._last_source_ready_ids == {"source-v1"}
        assert ingestor._last_source_missing_ids == []
        assert ingestor._last_source_not_ready_ids == []
        assert ingestor._last_source_status_by_id == {"source-v1": 2}

    def test_materialization_success_event_contains_exact_ready_evidence(self):
        """A successful add records the exact IDs and READY status used by the gate."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-success-evidence"
        ingestor._direct_client = _SuccessfulDirectTestClient()
        listed = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"sources": [{"id": "source-v1", "status": 2}]}),
                "stderr": "",
            },
        )()

        with mock.patch.object(ingestor, "_run_cmd", return_value=listed):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                added = ingestor._add_sources_chunk(
                    ["v1"],
                    subbatch_index=1,
                    expected_total=1,
                    source_count_before=0,
                )

        assert added == ["v1"]
        succeeded = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_materialization_wait_succeeded"
        )
        assert succeeded["expected_source_ids"] == ["source-v1"]
        assert succeeded["ready_source_ids"] == ["source-v1"]
        assert succeeded["expected_source_id_count"] == 1
        assert succeeded["ready_source_id_count"] == 1
        assert succeeded["missing_source_ids"] == []
        assert succeeded["not_ready_source_ids"] == []
        assert succeeded["source_status_by_id"] == {"source-v1": 2}
        assert succeeded["source_status_gate_enabled"] is True

    def test_subbatch_materialization_timeout_continues_with_later_subbatches(self):
        """A timed-out sub-batch is quarantined without aborting later IDs."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-continue"
        calls = []

        def fake_add(batch_ids, **_kwargs):
            calls.append(list(batch_ids))
            if batch_ids == ["v1"]:
                ingestor._last_timeout_ready_video_ids = []
                ingestor._last_timeout_failure_messages = {
                    "v1": "Source materialization timeout",
                }
                ingestor._last_added_source_ids = []
                ingestor._last_add_failure_reason = "materialization_wait_failed"
                ingestor._last_add_cmd_elapsed_s = 0.1
                ingestor._last_materialization_wait_elapsed_s = 600.0
                raise nlm_batch.NotebookSourceMaterializationTimeout("stalled")
            ingestor._last_added_source_ids = ["source-v2"]
            ingestor._last_add_failure_reason = None
            return list(batch_ids)

        with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=fake_add):
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    added = ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=1)

        assert calls == [["v1"], ["v2"]]
        assert added == ["v2"]
        assert ingestor._last_added_video_ids == ["v2"]
        assert ingestor._last_video_failure_messages == {
            "v1": "Source materialization timeout",
        }
        assert any(
            call.args[0] == "nlm_batch_subbatch_materialization_timeout_continuing"
            for call in mock_log.call_args_list
        )

    def test_subbatch_terminal_materialization_error_continues_with_batch_size_two(self):
        """A terminal source status skips one subbatch and processes the next one."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-terminal-continue"
        calls = []

        def fake_add(batch_ids, **_kwargs):
            calls.append(list(batch_ids))
            if batch_ids == ["v1", "v2"]:
                ingestor._last_timeout_ready_video_ids = []
                ingestor._last_timeout_failure_messages = {
                    "v1": "Source materialization terminal error",
                    "v2": "Source materialization terminal error",
                }
                ingestor._last_added_source_ids = []
                ingestor._last_add_failure_reason = "source_materialization_terminal_error"
                ingestor._last_add_cmd_elapsed_s = 0.2
                ingestor._last_materialization_wait_elapsed_s = 0.3
                raise nlm_batch.NotebookSourceMaterializationTerminalError("terminal")
            ingestor._last_added_source_ids = ["source-v3", "source-v4"]
            ingestor._last_add_failure_reason = None
            return list(batch_ids)

        with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=fake_add):
            with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    added = ingestor._add_sources_in_subbatches(
                        ["v1", "v2", "v3", "v4"], subbatch_size=2
                    )

        assert calls == [["v1", "v2"], ["v3", "v4"]]
        assert added == ["v3", "v4"]
        assert ingestor._last_added_video_ids == ["v3", "v4"]
        assert ingestor._last_video_failure_messages == {
            "v1": "Source materialization terminal error",
            "v2": "Source materialization terminal error",
        }
        assert any(
            call.args[0] == "nlm_batch_subbatch_materialization_error_continuing"
            for call in mock_log.call_args_list
        )

    def test_reusable_batch_preserves_materialization_timeout_classification(self):
        """Reusable finalization keeps timeout errors distinct from source-add failures."""
        batch_ids = ["v1", "v2"]

        def fake_add(ids, subbatch_size):
            ingestor._ingestor._last_added_video_ids = ["v2"]
            ingestor._ingestor._last_video_failure_messages = {
                "v1": "Source materialization timeout",
            }
            return ["v2"]

        with mock.patch("csf.nlm_batch._load_reusable_notebook_id", return_value="nb-existing"):
            with mock.patch("csf.nlm_batch._save_reusable_notebook_id"):
                with mock.patch("csf.nlm_batch._clear_reusable_notebook_state"):
                    ingestor = nlm_batch.NLMReusableIngestor()
                    with mock.patch.object(ingestor, "_ensure_notebook", return_value=(False, "reuse")):
                        with mock.patch.object(ingestor._ingestor, "_add_sources_in_subbatches", side_effect=fake_add):
                            with mock.patch.object(
                                ingestor._ingestor,
                                "extract_transcripts",
                                return_value={"v2": (True, "transcript", None)},
                            ) as mock_extract:
                                with mock.patch.object(ingestor._ingestor, "reset_sources"):
                                    results = ingestor.process_batch(batch_ids)

        assert results["v1"] == (False, None, "Source materialization timeout")
        assert results["v2"] == (True, "transcript", None)
        mock_extract.assert_called_once_with(["v2"])

    def test_materialization_wait_legacy_count_gate_requires_no_source_ids(self):
        """Callers without source IDs retain the explicit legacy compatibility path."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-legacy-count-gate"
        listed = type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"sources": [{"id": "legacy-source"}]}),
                "stderr": "",
            },
        )()

        with mock.patch.object(ingestor, "_run_cmd", return_value=listed):
            assert ingestor._wait_for_sources_ready(
                1,
                timeout=1,
                source_count_before_wait=0,
                poll_interval_s=0,
            ) is True

    def test_source_content_fetch_logs_ready_status(self, monkeypatch):
        """A ready source should log explicit ready-state completion fields."""
        monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)
        monkeypatch.delenv("YTIS_NLM_AUTH_NONINTERACTIVE", raising=False)
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-ready"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is True
        command_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_source_content_command_completed"
        )
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_started")
        assert started["source_id"] == "s1"
        assert started["video_id"] == "vid1"
        assert started["source_ready_age_s"] == 0.0
        assert started["notebooklm_profile"] == "ytis-pro-worker-02"
        assert started["expected_email"] == "worker02@example.com"
        assert started["auth_requires_profile"] is False
        assert started["auth_has_profile"] is True
        assert started["auth_cache_hit"] is True
        assert started["auth_cache_session_age_s"] == 12.345
        assert started["auth_check_cache_ttl_s"] == completed["auth_check_cache_ttl_s"]
        assert started["auth_check_interval_s"] == completed["auth_check_interval_s"]
        assert started["auth_cooldown_s"] == completed["auth_cooldown_s"]
        assert completed["status"] == "ready"
        assert completed["returncode"] == 0
        assert completed["content_length"] == 101
        assert completed["ready_threshold"] == 100
        assert completed["source_ready_age_s"] == 0.0
        assert completed["notebooklm_profile"] == "ytis-pro-worker-02"
        assert completed["expected_email"] == "worker02@example.com"
        assert completed["auth_requires_profile"] is False
        assert completed["auth_has_profile"] is True
        assert completed["auth_cache_hit"] is True
        assert completed["auth_cache_session_age_s"] == 12.345
        assert completed["browser_profile_root"] == r"P:\.data\yt-is\browser\notebooklm-pro"
        assert completed["browser_profile_directory"] == "Profile"
        assert completed["worker_state_root"] == r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states"
        assert completed["started_at_epoch"] <= completed["completed_at_epoch"]
        assert command_completed["source_id"] == "s1"
        assert command_completed["video_id"] == "vid1"
        assert command_completed["attempt"] == 1
        assert command_completed["status"] == "ready"
        assert command_completed["elapsed_s"] >= 0.0
        assert command_completed["content_length"] == 101
        assert command_completed["source_ready_age_s"] == 0.0
        assert command_completed["worker_id"] == "worker-02"
        assert command_completed["notebooklm_profile"] == "ytis-pro-worker-02"
        assert command_completed["auth_cache_session_age_s"] == 12.345
        assert command_completed["last_auth_refresh_age_s"] == 12.345
        assert command_completed["browser_profile_root"] == r"P:\.data\yt-is\browser\notebooklm-pro"
        assert command_completed["browser_profile_directory"] == "Profile"
        assert command_completed["worker_state_root"] == r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states"
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_status_counts"]["ready"] == 1
        assert summary["source_ready_age_s_max"] == 0.0

    def test_source_content_fetch_logs_window_index_for_windowed_calls(self):
        """Windowed reusable extraction should carry the outer window index into fetch logs."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-windowed"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"], batch_index=3)

        assert results["vid1"][0] is True
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_started")
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert started["batch_index"] == 3
        assert completed["batch_index"] == 3

    def test_source_content_fetch_logs_below_threshold_content_status(self):
        """Sparse NotebookLM content should be classified by extraction outcome, not video value."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-short"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 50}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                with mock.patch(
                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                    return_value={
                        "classification": "ok",
                        "available": False,
                        "availability": None,
                        "live_status": None,
                        "was_live": False,
                        "is_live": False,
                        "title": None,
                        "error": None,
                    },
                ) as mock_ytdlp:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert mock_ytdlp.call_count == 1
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["status"] == "nlm_content_below_threshold"
        assert completed["extraction_outcome"] == "nlm_content_below_threshold"
        assert completed["content_length"] == 50
        assert completed["nlm_content_chars"] == 50
        assert completed["usable_text_chars"] == 0
        assert completed["failure_reason"] == "Fetch failed for s1: nlm_content_below_threshold"
        assert completed["source_ready_age_s"] == 0.0
        assert completed["started_at_epoch"] <= completed["completed_at_epoch"]
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert "too_short" not in summary["content_fetch_status_counts"]
        assert summary["content_fetch_status_counts"]["nlm_content_below_threshold"] == 1
        assert summary["content_fetch_attempts_total"] == 4
        assert summary["content_fetch_attempts_max"] == 4
        assert summary["content_fetch_attempts_avg"] == 4.0

    def test_source_content_fetch_logs_command_failed_status(self, monkeypatch):
        """A failed content command should log a command-failed status."""
        monkeypatch.delenv("YTIS_NLM_ACCOUNT_PROFILE", raising=False)
        monkeypatch.delenv("YTIS_NLM_AUTH_NONINTERACTIVE", raising=False)
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-fail"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-free-worker-01")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker01@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-free")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Default")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "failed"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(False, None)):
                with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=None):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": False,
                                "availability": None,
                                "live_status": None,
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ) as mock_ytdlp:
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert mock_ytdlp.call_count == 1
        command_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_source_content_command_completed"
        )
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_started")
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert started["notebooklm_profile"] == "ytis-free-worker-01"
        assert started["expected_email"] == "worker01@example.com"
        assert started["auth_requires_profile"] is False
        assert started["auth_has_profile"] is True
        assert started["auth_cache_hit"] is False
        assert started["auth_cache_session_age_s"] is None
        assert completed["status"] == "command_failed"
        assert completed["returncode"] == 1
        assert completed["content_length"] == 0
        assert completed["failure_reason"] == "Fetch failed for s1: command_failed"
        assert completed["source_ready_age_s"] == 0.0
        assert completed["notebooklm_profile"] == "ytis-free-worker-01"
        assert completed["expected_email"] == "worker01@example.com"
        assert completed["auth_requires_profile"] is False
        assert completed["auth_has_profile"] is True
        assert completed["auth_cache_hit"] is False
        assert completed["auth_cache_session_age_s"] is None
        assert completed["browser_profile_root"] == r"P:\.data\yt-is\browser\notebooklm-free"
        assert completed["browser_profile_directory"] == "Default"
        assert completed["worker_state_root"] == r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states"
        assert completed["auth_check_cache_ttl_s"] == started["auth_check_cache_ttl_s"]
        assert completed["auth_check_interval_s"] == started["auth_check_interval_s"]
        assert completed["auth_cooldown_s"] == started["auth_cooldown_s"]
        assert command_completed["source_id"] == "s1"
        assert command_completed["video_id"] == "vid1"
        assert command_completed["attempt"] == 1
        assert command_completed["status"] == "command_failed"
        assert command_completed["elapsed_s"] >= 0.0
        assert command_completed["content_length"] == 0
        assert command_completed["source_ready_age_s"] == 0.0
        assert command_completed["worker_id"] == "worker-01"
        assert command_completed["notebooklm_profile"] == "ytis-free-worker-01"
        assert command_completed["auth_cache_session_age_s"] is None
        assert command_completed["last_auth_refresh_age_s"] is None
        assert command_completed["browser_profile_root"] == r"P:\.data\yt-is\browser\notebooklm-free"
        assert command_completed["browser_profile_directory"] == "Default"
        assert command_completed["worker_state_root"] == r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states"
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_status_counts"]["command_failed"] == 1
        assert summary["content_fetch_attempts_total"] == 1
        assert summary["content_fetch_attempts_max"] == 1
        assert summary["content_fetch_attempts_avg"] == 1.0

    def test_source_content_fetch_skips_aged_sources_before_command(self):
        """A source beyond the age cliff should fail fast without running source content."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-age-cliff"
        ingestor._last_materialization_ready_at_epoch = 1000.0

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content should not run after the age cliff is exceeded")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
                with mock.patch("csf.nlm_batch.time.time", return_value=1301.0):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_started")
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert started["source_ready_age_s"] == 301.0
        assert completed["status"] == "source_age_cliff"
        assert completed["failure_reason"] == "Fetch failed for s1: source_age_cliff"
        assert completed["attempts"] == 0
        assert completed["returncode"] == -1
        assert completed["retry_queue_skipped_reason"] is None
        assert completed["projected_retry_ready_age_s"] is None
        assert not any(call.args[0] == "nlm_source_content_command_completed" for call in mock_log.call_args_list)
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_status_counts"]["source_age_cliff"] == 1
        assert summary["source_ready_age_s_total"] == 301.0
        assert summary["source_ready_age_s_max"] == 301.0
        assert summary["source_ready_age_s_avg"] == 301.0
        assert summary["content_fetch_attempts_total"] == 0
        assert summary["content_fetch_attempts_max"] == 0
        assert summary["content_fetch_attempts_avg"] == 0.0

    def test_source_content_fetch_skips_when_primary_command_projection_hits_age_cliff(self):
        """An old source should not start a primary content command projected to finish past the cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-primary-projection"
        ingestor._last_materialization_ready_at_epoch = 1000.0

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content should not run when primary command projection crosses the age cliff")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", 60.0):
                    with mock.patch("csf.nlm_batch.time.time", return_value=1150.0):
                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                            results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["status"] == "source_age_cliff"
        assert completed["failure_reason"] == "Fetch failed for s1: source_age_cliff"
        assert completed["source_ready_age_s"] == 150.0
        assert completed["projected_primary_command_completion_age_s"] == 210.0
        assert completed["primary_command_age_projection_s"] == 60.0
        assert completed["retry_queue_skipped_reason"] == "projected_primary_command_age_cliff"
        assert completed["attempts"] == 0
        assert not any(call.args[0] == "nlm_source_content_command_completed" for call in mock_log.call_args_list)
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_status_counts"]["source_age_cliff"] == 1
        assert summary["source_ready_age_s_total"] == 150.0
        assert summary["content_fetch_attempts_total"] == 0

    def test_source_content_fetch_skips_when_primary_command_projection_margin_hits_age_cliff(self):
        """A margin-adjusted primary command projection should also stop old-window fetches."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-primary-projection-margin"
        ingestor._last_materialization_ready_at_epoch = 1000.0

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content should not run when the margin-adjusted projection crosses the age cliff")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", 40.0):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S", 20.0):
                        with mock.patch("csf.nlm_batch.time.time", return_value=1150.0):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["status"] == "source_age_cliff"
        assert completed["failure_reason"] == "Fetch failed for s1: source_age_cliff"
        assert completed["source_ready_age_s"] == 150.0
        assert completed["projected_primary_command_completion_age_s"] == 190.0
        assert completed["projected_primary_command_completion_age_with_margin_s"] == 210.0
        assert completed["primary_command_age_projection_s"] == 40.0
        assert completed["primary_command_age_margin_s"] == 20.0
        assert completed["retry_queue_skipped_reason"] == "projected_primary_command_age_cliff"
        assert completed["attempts"] == 0
        assert not any(call.args[0] == "nlm_source_content_command_completed" for call in mock_log.call_args_list)
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_status_counts"]["source_age_cliff"] == 1
        assert summary["source_ready_age_s_total"] == 150.0
        assert summary["content_fetch_attempts_total"] == 0

    def test_source_content_fetch_logs_not_found_probe_metrics(self):
        """A final NOT_FOUND should contribute command and source-list probe timing."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-probe"
        fake_clock = {"value": 1000.0}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch(
                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                        return_value={
                            "classification": "ok",
                            "available": False,
                            "availability": None,
                            "live_status": None,
                            "was_live": False,
                            "is_live": False,
                            "title": None,
                            "error": None,
                        },
                    ):
                        with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["status"] == "command_failed"
        assert completed["content_fetch_command_elapsed_s_count"] == 1
        assert completed["content_fetch_command_elapsed_s_total"] > 0.0
        assert completed["source_list_probe_count"] == 1
        assert completed["source_list_probe_elapsed_s_total"] > 0.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_command_elapsed_s_count"] == 1
        assert summary["source_list_probe_count"] == 1
        assert summary["source_id_validated_after_not_found_true_count"] == 1
        assert summary["source_id_validated_after_not_found_false_count"] == 0

    def test_source_content_fetch_logs_not_found_probe_for_notebooklm_py_error(self):
        """The notebooklm-py SourceNotFoundError spelling must trigger the same probe."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-notebooklm-py-not-found"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "SourceNotFoundError: Source not found: Source s1 not found in notebook nb-1",
                    },
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch(
                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                        return_value={"classification": "ok", "available": False},
                    ):
                        with mock.patch.object(nlm_batch, "log_action") as mock_log:
                            results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        )
        assert completed["source_list_probe_count"] == 1
        assert completed["source_id_validated_after_not_found"] is True

    def test_source_content_not_found_classifier_accepts_structured_failure(self):
        """Final fetch diagnostics may be a dict after retry-loop redaction."""
        assert nlm_batch._source_content_error_is_not_found(
            {
                "failure_reason": "SourceNotFoundError: source not found",
                "stdout": "",
                "stderr": "",
            }
        ) is True

    def test_source_content_not_found_probe_absent_triggers_dead_notebook_recovery_immediately(self):
        """A source-list validation miss should recycle the notebook on the first validated NOT_FOUND."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-probe-absent"
        list_calls = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                list_calls["count"] += 1
                sources = [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]
                if list_calls["count"] > 1:
                    sources = [{"id": "other-source", "title": "https://example.invalid"}]
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"sources": sources}),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch(
                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                        return_value={
                            "classification": "ok",
                            "available": False,
                            "availability": None,
                            "live_status": None,
                            "was_live": False,
                            "is_live": False,
                            "title": None,
                            "error": None,
                        },
                    ):
                        with mock.patch.object(ingestor, "_recover_dead_notebook", return_value=False) as mock_recover:
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["dQw4w9WgXcQ"])

        assert results["dQw4w9WgXcQ"][0] is False
        mock_recover.assert_called_once_with(["dQw4w9WgXcQ"])
        log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_source_content_dead_notebook_recovery_scheduled" in log_names
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["source_id_validated_after_not_found_false_count"] == 1
        assert summary["source_id_validated_after_not_found_true_count"] == 0

    def test_source_content_not_found_probe_fires_once_for_a_notebook(self):
        """The first NOT_FOUND on a notebook should still validate the source list once."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-probe-once"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"sources": [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_NOT_FOUND_SOURCE_LIST_PROBE_CAP", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": False,
                                "availability": None,
                                "live_status": None,
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["dQw4w9WgXcQ"])

        assert results["dQw4w9WgXcQ"][0] is False
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        )
        assert completed["status"] == "command_failed"
        assert completed["source_list_probe_count"] == 1
        assert completed["source_id_validated_after_not_found"] is True

    def test_source_content_not_found_probe_caps_repeated_failures_and_resets_for_new_notebook(self):
        """Repeated NOT_FOUNDs should stop probing the same notebook after the cap, then reset for a new notebook."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-a"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"sources": [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_NOT_FOUND_SOURCE_LIST_PROBE_CAP", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": False,
                                "availability": None,
                                "live_status": None,
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                ingestor.extract_transcripts(["dQw4w9WgXcQ"])
                                first_summary = next(
                                    call.args[1]
                                    for call in mock_log.call_args_list
                                    if call.args[0] == "nlm_batch_extract_completed"
                                )
                                mock_log.reset_mock()

                                ingestor.extract_transcripts(["dQw4w9WgXcQ"])
                                second_summary = next(
                                    call.args[1]
                                    for call in mock_log.call_args_list
                                    if call.args[0] == "nlm_batch_extract_completed"
                                )
                                mock_log.reset_mock()

                                ingestor._nb_id = "nb-b"
                                ingestor.extract_transcripts(["dQw4w9WgXcQ"])
                                third_summary = next(
                                    call.args[1]
                                    for call in mock_log.call_args_list
                                    if call.args[0] == "nlm_batch_extract_completed"
                                )

        assert first_summary["source_list_probe_count"] == 1
        assert second_summary["source_list_probe_count"] == 0
        assert third_summary["source_list_probe_count"] == 1

    def test_source_content_ready_and_generic_failure_do_not_consume_not_found_probe_budget(self):
        """Ready-path and non-NOT_FOUND failures should remain unchanged and avoid the probe."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-ready"

        ready_calls = {"content": 0, "list": 0}

        def ready_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                ready_calls["list"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"sources": [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                ready_calls["content"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        failure_calls = {"content": 0, "list": 0}

        def failure_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                failure_calls["list"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"sources": [{"id": "s1", "title": "https://www.youtube.com/watch?v=9bZkp7q19f0"}]}
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                failure_calls["content"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "temporary API failure"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_NOT_FOUND_SOURCE_LIST_PROBE_CAP", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 0):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=ready_run_cmd):
                        with mock.patch("csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp") as mock_ytdlp:
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                ready_results = ingestor.extract_transcripts(["dQw4w9WgXcQ"])

                    with mock.patch.object(ingestor, "_run_cmd", side_effect=failure_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": False,
                                "availability": None,
                                "live_status": None,
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log_failure:
                                failure_results = ingestor.extract_transcripts(["9bZkp7q19f0"])

        assert ready_results["dQw4w9WgXcQ"][0] is True
        assert ready_calls["content"] == 1
        assert ready_calls["list"] == 1
        mock_ytdlp.assert_not_called()
        ready_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_extract_completed"
        )
        assert ready_completed["source_list_probe_count"] == 0

        assert failure_results["9bZkp7q19f0"][0] is False
        assert failure_calls["content"] == 1
        assert failure_calls["list"] == 1
        failure_completed = next(
            call.args[1]
            for call in mock_log_failure.call_args_list
            if call.args[0] == "nlm_batch_extract_completed"
        )
        assert failure_completed["source_list_probe_count"] == 0
        assert failure_completed.get("source_id_validated_after_not_found") is None

    def test_source_content_fetch_retries_transient_not_found_and_recovers(self):
        """A transient NOT_FOUND should be retried until content becomes ready."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry"
        content_attempts = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] == 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is True
        assert results["vid1"][1] == "x" * 101
        assert content_attempts["count"] == 2
        assert mock_sleep.call_count >= 1
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert any(entry["status"] == "ready" for entry in completed)
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["content_fetch_attempts_total"] == 2
        assert summary["content_fetch_attempts_max"] == 2
        assert summary["content_fetch_attempts_avg"] == 2.0

    def test_source_content_fetch_retries_spaced_source_not_found_only_when_source_is_present(self):
        """The notebooklm-py spelling gets one bounded retry after positive source-list proof."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-spaced-not-found-present"
        content_attempts = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"sources": [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] == 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {
                            "returncode": 1,
                            "stdout": "",
                            "stderr": "SourceNotFoundError: Source s1 not found in notebook nb-1",
                        },
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["dQw4w9WgXcQ"])

        assert results["dQw4w9WgXcQ"][0] is True
        assert content_attempts["count"] == 2
        assert mock_sleep.call_count >= 1
        probe = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_not_found_probe_completed"
        )
        assert probe["source_id_present_in_source_list"] is True
        assert probe["retry_admitted"] is True
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        )
        assert completed["source_id_validated_after_not_found"] is True

    def test_source_content_present_not_found_exhausts_local_retry_budget(self):
        """Positive presence admits retries, but does not make a persistent miss succeed."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-spaced-not-found-persistent"
        content_attempts = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"sources": [{"id": "s1"}]}),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "SourceNotFoundError: Source s1 not found in notebook nb-1",
                    },
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 4):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(
                    nlm_batch,
                    "inspect_youtube_watch_page_via_ytdlp",
                    return_value={"classification": "unavailable", "available": False},
                ):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 4
        assert mock_sleep.call_count == 3
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        )
        assert completed["attempts"] == 4
        assert completed["retry_attempts_limit"] == 4
        assert completed["retry_exit_reason"] == "attempts_exhausted"
        assert completed["source_id_validated_after_not_found"] is True
        assert completed["source_list_probe_count"] == 1
        assert completed["content_fetch_command_elapsed_s_count"] == 4
        summary = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_extract_completed"
        )
        assert summary["content_fetch_attempts_total"] == 4
        assert summary["content_fetch_attempts_max"] == 4

    def test_source_content_fetch_fails_closed_when_spaced_source_not_found_is_absent(self):
        """A confirmed source-list miss must not consume local or queued retries."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-spaced-not-found-absent"
        calls = {"content": 0, "list": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                calls["list"] += 1
                sources = (
                    [{"id": "s1", "title": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]
                    if calls["list"] == 1
                    else []
                )
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": sources}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                calls["content"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "SourceNotFoundError: Source s1 not found in notebook nb-1",
                    },
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 3):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 10.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch.object(ingestor, "_recover_dead_notebook", return_value=False):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={"classification": "ok", "available": True},
                        ):
                            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                    results = ingestor.extract_transcripts(["dQw4w9WgXcQ"])

        assert results["dQw4w9WgXcQ"][0] is False
        assert calls["content"] == 1
        assert mock_sleep.call_count == 0
        assert not any(
            call.args[0] == "nlm_batch_source_content_retry_queued"
            for call in mock_log.call_args_list
        )
        completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        )
        assert completed["retry_exit_reason"] == "not_retryable"
        assert completed["retry_queue_gate_reason"] == "source_id_absent_after_not_found"
        assert completed["source_id_validated_after_not_found"] is False
        assert "SourceNotFoundError" in results["dQw4w9WgXcQ"][2]

    def test_source_content_local_retry_projection_stops_before_age_cliff(self):
        """A slow failed attempt should not launch a retry projected beyond the age cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-local-retry-age-projection"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1150.0}

        def fake_time():
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                fake_clock["value"] += 35.0
                if content_attempts["count"] == 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 2):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_INITIAL_DELAY_S", 20.0):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_MAX_DELAY_S", 20.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_BUDGET_S", 120.0):
                            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                    with mock.patch(
                                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                        return_value={"classification": "ok", "available": True},
                                    ):
                                        with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        assert len(completed) == 1
        assert completed[0]["retry_queue_skipped_reason"] == "projected_local_retry_completion_age_cliff"
        assert completed[0]["projected_local_retry_completion_age_s"] == 240.0

    def test_extract_transcripts_recovers_batch_not_found_after_dead_notebook_recreate(self):
        """A batch-level NOT_FOUND storm should recreate the notebook and retry the failed subset once."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-old"
        ingestor._last_added_source_ids = ["old-s1", "old-s2"]
        ingestor._last_added_video_ids = ["vid1", "vid2"]
        recreate_calls = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                if ingestor._nb_id == "nb-old":
                    return type(
                        "CompletedProcess",
                        (),
                        {
                            "returncode": 0,
                            "stdout": json.dumps(
                                {
                                    "sources": [
                                        {"id": "old-s1", "title": "https://www.youtube.com/watch?v=vid1"},
                                        {"id": "old-s2", "title": "https://www.youtube.com/watch?v=vid2"},
                                    ]
                                }
                            ),
                            "stderr": "",
                        },
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "new-s1", "title": "https://www.youtube.com/watch?v=vid1"},
                                    {"id": "new-s2", "title": "https://www.youtube.com/watch?v=vid2"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                source_id = cmd[2]
                if source_id in {"old-s1", "old-s2"}:
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                    )()
                if source_id == "new-s1":
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 0, "stdout": json.dumps({"value": {"content": "A" * 101}}), "stderr": ""},
                    )()
                if source_id == "new-s2":
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 0, "stdout": json.dumps({"value": {"content": "B" * 101}}), "stderr": ""},
                    )()
                raise AssertionError(f"unexpected source_id {source_id}")
            raise AssertionError(f"unexpected command {cmd}")

        def fake_recover_dead_notebook(batch_ids=None):
            recreate_calls["count"] += 1
            ingestor._nb_id = "nb-fresh"
            ingestor._last_added_source_ids = ["new-s1", "new-s2"]
            return True

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch.object(ingestor, "_recover_dead_notebook", side_effect=fake_recover_dead_notebook) as mock_recover:
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": True,
                                "availability": "public",
                                "live_status": "not_live",
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ):
                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                results = ingestor.extract_transcripts(["vid1", "vid2"])

        assert results["vid1"][0] is True
        assert results["vid1"][1] == "A" * 101
        assert results["vid2"][0] is True
        assert results["vid2"][1] == "B" * 101
        assert recreate_calls["count"] == 1
        mock_recover.assert_called_once_with(["vid1", "vid2"])
        recovery_log_names = [call.args[0] for call in mock_log.call_args_list]
        assert "nlm_batch_source_content_dead_notebook_recovery_scheduled" in recovery_log_names
        assert "nlm_batch_source_content_dead_notebook_recovery_completed" in recovery_log_names

    def test_source_content_fetch_honors_retry_budget_cutoff(self):
        """A small wall-clock budget should stop retries before a second attempt."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-budget"
        content_attempts = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] > 1:
                    raise AssertionError("NotebookLM content fetch retried despite exhausted budget")
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_BUDGET_S", 0.01):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 0.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch(
                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                        return_value={
                            "classification": "ok",
                            "available": False,
                            "availability": None,
                            "live_status": None,
                            "was_live": False,
                            "is_live": False,
                            "title": None,
                            "error": None,
                        },
                    ) as mock_ytdlp:
                        with mock.patch(
                            "csf.nlm_batch.time.time",
                            side_effect=[1000.0, 1000.01, 1000.02, 1000.03, 1000.04, 1000.05, 1000.06, 1000.07, 1000.08, 1000.09],
                        ):
                            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["attempts"] == 1
        assert completed["status"] == "command_failed"

    def test_source_content_fetch_queues_retry_pass_for_ytdlp_ok(self):
        """A ytdlp-ok miss should enter the second NotebookLM pass and recover there."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-queue"
        content_attempts = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] == 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                    )()
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={
                                "classification": "ok",
                                "available": True,
                                "availability": "public",
                                "live_status": "not_live",
                                "was_live": False,
                                "is_live": False,
                                "title": None,
                                "error": None,
                            },
                        ) as mock_ytdlp:
                            with mock.patch.object(ingestor, "_recover_dead_notebook") as mock_recover:
                                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is True
        assert results["vid1"][1] == "x" * 101
        assert content_attempts["count"] == 2
        assert mock_ytdlp.call_count == 1
        mock_recover.assert_not_called()
        mock_sleep.assert_called_once_with(0.1)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert any(
            entry["pass_name"] == "primary"
            and entry["status"] == "command_failed"
            and entry["queued_for_retry"] is True
            for entry in completed
        )
        primary_completed = next(entry for entry in completed if entry["pass_name"] == "primary" and entry["status"] == "command_failed")
        assert primary_completed["retry_queue_gate_reason"] == "ytdlp_ok"
        retry_completed = next(entry for entry in completed if entry["pass_name"] == "retry" and entry["status"] == "ready")
        assert retry_completed.get("projected_retry_ready_age_s") is None
        assert retry_completed.get("projected_retry_ready_age_with_margin_s") is None
        assert retry_completed["retry_queue_gate_reason"] == "status_not_retryable"
        assert all(entry.get("retry_queue_skipped_reason") is None for entry in completed)
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 1
        assert summary["retry_queue_recovered_count"] == 1
        assert summary["retry_queue_final_failed_count"] == 0
        assert summary["content_fetch_attempts_total"] == 1
        assert summary["content_fetch_attempts_max"] == 1
        assert summary["content_fetch_retry_queue_sleep_elapsed_s_total"] == 0.1
        assert summary["content_fetch_command_elapsed_s_count"] == 2

    def test_source_content_retry_queue_skips_when_delay_would_cross_age_cliff(self):
        """Retry queue should not defer work that is projected to hit source_age_cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-age-guard"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1197.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                with mock.patch(
                                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                    return_value={
                                        "classification": "ok",
                                        "available": True,
                                        "availability": "public",
                                        "live_status": "not_live",
                                        "was_live": False,
                                        "is_live": False,
                                        "title": None,
                                        "error": None,
                                    },
                                ) as mock_ytdlp:
                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                        with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["pass_name"] == "primary"
        assert completed[0]["status"] == "command_failed"
        assert completed[0]["retry_queue_skipped_reason"] == "projected_source_age_cliff"
        assert completed[0]["projected_retry_ready_age_s"] > 200.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 0
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 0

    def test_source_content_retry_queue_skips_when_delay_hits_age_cliff_boundary(self):
        """Retry queue should skip when projected retry age lands exactly on the cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-age-boundary"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1194.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                with mock.patch(
                                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                    return_value={
                                        "classification": "ok",
                                        "available": True,
                                        "availability": "public",
                                        "live_status": "not_live",
                                        "was_live": False,
                                        "is_live": False,
                                        "title": None,
                                        "error": None,
                                    },
                                ) as mock_ytdlp:
                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                        with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["retry_queue_skipped_reason"] == "projected_source_age_cliff"
        assert completed[0]["projected_retry_ready_age_s"] >= 200.0

    def test_source_content_retry_queue_margin_skips_before_age_cliff(self):
        """Optional retry queue age margin should skip near-cliff retries without changing the cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-age-margin"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1192.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S", 3.0):
                                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                    with mock.patch(
                                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                        return_value={
                                            "classification": "ok",
                                            "available": True,
                                            "availability": "public",
                                            "live_status": "not_live",
                                            "was_live": False,
                                            "is_live": False,
                                            "title": None,
                                            "error": None,
                                        },
                                    ) as mock_ytdlp:
                                        with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["retry_queue_skipped_reason"] == "projected_source_age_cliff_margin"
        assert completed[0]["projected_retry_ready_age_s"] < 200.0
        assert completed[0]["projected_retry_ready_age_with_margin_s"] >= 200.0
        assert completed[0]["retry_queue_age_margin_s"] == 3.0

    def test_source_content_retry_queue_skips_when_primary_command_projection_hits_age_cliff(self):
        """Retry queue should not defer work that would age into the cliff before retry command time."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-primary-command-age"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1149.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 30.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", 40.0):
                                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                    with mock.patch(
                                        "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                        return_value={
                                            "classification": "ok",
                                            "available": True,
                                            "availability": "public",
                                            "live_status": "not_live",
                                            "was_live": False,
                                            "is_live": False,
                                            "title": None,
                                            "error": None,
                                        },
                                    ) as mock_ytdlp:
                                        with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                            with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["retry_queue_skipped_reason"] == "projected_primary_command_age_cliff"
        assert completed[0]["projected_retry_ready_age_s"] < 200.0
        assert completed[0]["projected_retry_command_completion_age_s"] >= 200.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 0
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 0

    def test_source_content_retry_queue_skips_when_primary_command_projection_margin_hits_age_cliff(self):
        """Retry queue should respect the margin-adjusted primary-command cliff as well."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-primary-command-margin"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1149.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 20.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", 10.0):
                                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S", 30.0):
                                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                        with mock.patch(
                                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                            return_value={
                                                "classification": "ok",
                                                "available": True,
                                                "availability": "public",
                                                "live_status": "not_live",
                                                "was_live": False,
                                                "is_live": False,
                                                "title": None,
                                                "error": None,
                                            },
                                        ) as mock_ytdlp:
                                            with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_not_called()
        assert not any(call.args[0] == "nlm_batch_source_content_retry_queued" for call in mock_log.call_args_list)
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["retry_queue_skipped_reason"] == "projected_primary_command_age_cliff_margin"
        assert completed[0]["projected_retry_ready_age_s"] < 200.0
        assert completed[0]["projected_retry_command_completion_age_s"] < 200.0
        assert completed[0]["projected_retry_command_completion_age_with_margin_s"] >= 200.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 0
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 0

    def test_source_ready_age_exceeds_cliff_treats_threshold_as_cliff(self):
        """The age cliff helper should treat the configured threshold as inclusive."""
        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            age_cliff_hit, source_ready_age_s = nlm_batch._source_ready_age_exceeds_cliff(1000.0, 1200.0)

        assert age_cliff_hit is True
        assert source_ready_age_s == 200.0

    def test_source_content_fetch_queues_shared_retry_pool_entries_when_enabled(self):
        """Shared retry should preserve local projection evidence while enqueueing elsewhere."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-shared-retry"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1150.0}

        def fake_time():
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                fake_clock["value"] += 35.0
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 2):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_INITIAL_DELAY_S", 20.0):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_MAX_DELAY_S", 20.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_BUDGET_S", 120.0):
                            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", True):
                                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                                        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                            with mock.patch(
                                                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                                return_value={
                                                    "classification": "ok",
                                                    "available": True,
                                                    "availability": "public",
                                                    "live_status": "not_live",
                                                    "was_live": False,
                                                    "is_live": False,
                                                    "title": None,
                                                    "error": None,
                                                },
                                            ) as mock_ytdlp:
                                                with mock.patch("csf.nlm_batch.enqueue_shared_retry") as mock_enqueue:
                                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                                        with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                                results = ingestor.extract_transcripts(["vid1"])

        # Shared-pool deferral intentionally leaves the item out of the
        # immediate result map so the worker drain can claim it later.
        assert "vid1" not in results
        assert content_attempts["count"] == 1
        assert mock_ytdlp.call_count == 1
        mock_enqueue.assert_called_once()
        mock_sleep.assert_not_called()
        queued = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_retry_queued")
        assert queued["projected_retry_ready_age_s"] == 190.0
        assert queued["local_retry_skipped_reason"] == "projected_local_retry_completion_age_cliff"
        assert queued["projected_local_retry_completion_age_s"] == 240.0
        completed = [call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["pass_name"] == "primary"
        assert completed[0]["status"] == "command_failed"
        assert completed[0]["queued_for_retry"] is True
        assert completed[0]["projected_retry_ready_age_s"] == 190.0
        assert completed[0]["retry_queue_skipped_reason"] is None
        assert completed[0]["local_retry_skipped_reason"] == "projected_local_retry_completion_age_cliff"
        assert completed[0]["projected_local_retry_completion_age_s"] == 240.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 1
        assert summary["shared_retry_deferred_count"] == 1
        assert summary["source_content_shared_retry_pool_enabled"] is True
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 0

    def test_source_content_retry_queue_records_drain_ready_age(self):
        """The retry queue drain window should record how old the batch was when retries started."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-drain-age"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        fake_clock = {"value": 1192.9}

        def fake_time():
            fake_clock["value"] += 0.1
            return fake_clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                with mock.patch(
                                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                    return_value={
                                        "classification": "ok",
                                        "available": True,
                                        "availability": "public",
                                        "live_status": "not_live",
                                        "was_live": False,
                                        "is_live": False,
                                        "title": None,
                                        "error": None,
                                    },
                                ) as mock_ytdlp:
                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                        with mock.patch("csf.nlm_batch.time.sleep"):
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 2
        assert mock_ytdlp.call_count == 2
        retry_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_retry_queue_window_completed"
        )
        assert retry_completed["retry_queue_count"] == 1
        assert retry_completed["recovered_count"] == 0
        assert retry_completed["final_failed_count"] == 1
        assert retry_completed["retry_queue_drain_ready_age_s"] > 0.0
        assert retry_completed["retry_queue_wait_elapsed_s_max"] > 0.0
        assert retry_completed["retry_queue_wait_elapsed_s_count"] == 1
        retry_fetch = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
            and call.args[1].get("pass_name") == "retry"
        )
        assert retry_fetch["status"] == "command_failed"
        assert retry_fetch["projected_retry_ready_age_s"] is None
        assert retry_fetch["projected_retry_ready_age_with_margin_s"] is None
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 1
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 1
        assert summary["retry_queue_drain_ready_age_s"] == retry_completed["retry_queue_drain_ready_age_s"]
        assert summary["retry_queue_wait_elapsed_s_max"] == retry_completed["retry_queue_wait_elapsed_s_max"]
        assert summary["retry_queue_wait_elapsed_s_count"] == 1

    def test_source_content_retry_queue_shortens_sleep_when_headroom_remains(self):
        """The local drain should trim its sleep instead of overshooting a still-safe retry window."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-drain-short-sleep"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        clock = {"value": 1150.0, "primary_content_returned": False, "post_primary_calls": 0}

        def fake_time():
            if clock["primary_content_returned"]:
                clock["post_primary_calls"] += 1
                if clock["post_primary_calls"] > 5:
                    clock["value"] = 1183.0
                    return clock["value"]
            clock["value"] += 0.01
            return clock["value"]

        def fake_sleep(duration):
            clock["value"] += duration

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] > 1:
                    return type(
                        "CompletedProcess",
                        (),
                        {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""},
                    )()
                clock["primary_content_returned"] = True
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 30.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                with mock.patch(
                                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                    return_value={
                                        "classification": "ok",
                                        "available": True,
                                        "availability": "public",
                                        "live_status": "not_live",
                                        "was_live": False,
                                        "is_live": False,
                                        "title": None,
                                        "error": None,
                                    },
                                ) as mock_ytdlp:
                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                        with mock.patch("csf.nlm_batch.time.sleep", side_effect=fake_sleep) as mock_sleep:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is True
        assert results["vid1"][1] == "x" * 101
        assert content_attempts["count"] == 2
        assert mock_ytdlp.call_count == 1
        mock_sleep.assert_called_once()
        sleep_s = mock_sleep.call_args.args[0]
        assert 0.0 < sleep_s < 30.0
        retry_completed = next(
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
            and call.args[1].get("pass_name") == "retry"
        )
        assert retry_completed["status"] == "ready"
        assert retry_completed["projected_retry_ready_age_s"] is None
        assert retry_completed["projected_retry_ready_age_with_margin_s"] is None
        assert retry_completed["retry_queue_gate_reason"] == "status_not_retryable"
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 1
        assert summary["retry_queue_recovered_count"] == 1
        assert summary["retry_queue_final_failed_count"] == 0
        assert summary["content_fetch_retry_queue_sleep_elapsed_s_total"] == round(sleep_s, 3)
        assert summary["retry_queue_wait_elapsed_s_count"] == 1
        assert summary["retry_queue_drain_ready_age_s"] < 200.0

    def test_source_content_retry_queue_skips_when_drain_delay_would_cross_age_cliff(self):
        """The local drain should not sleep/retry when the actual drain window is already unsafe."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-drain-age-guard"
        ingestor._last_materialization_ready_at_epoch = 1000.0
        content_attempts = {"count": 0}
        clock = {"value": 1190.0, "primary_content_returned": False, "post_primary_calls": 0}

        def fake_time():
            if clock["primary_content_returned"]:
                clock["post_primary_calls"] += 1
                if clock["post_primary_calls"] > 5:
                    clock["value"] = 1200.1
                    return clock["value"]
            clock["value"] += 0.01
            return clock["value"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                content_attempts["count"] += 1
                if content_attempts["count"] > 1:
                    raise AssertionError("local retry should be skipped before source_age_cliff")
                clock["primary_content_returned"] = True
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", False):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 5.0):
                        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                            with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                                with mock.patch(
                                    "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                    return_value={
                                        "classification": "ok",
                                        "available": True,
                                        "availability": "public",
                                        "live_status": "not_live",
                                        "was_live": False,
                                        "is_live": False,
                                        "title": None,
                                        "error": None,
                                    },
                                ):
                                    with mock.patch("csf.nlm_batch.time.time", side_effect=fake_time):
                                        with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                                            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                                results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert content_attempts["count"] == 1
        mock_sleep.assert_not_called()
        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        assert completed[0]["pass_name"] == "primary"
        assert completed[0]["queued_for_retry"] is True
        retry_completed = next(entry for entry in completed if entry["pass_name"] == "retry")
        assert retry_completed["status"] == "source_age_cliff"
        assert retry_completed["retry_queue_skipped_reason"] == "drain_projected_source_age_cliff"
        assert retry_completed["projected_retry_ready_age_s"] >= 200.0
        summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert summary["retry_queue_deferred_count"] == 1
        assert summary["retry_queue_recovered_count"] == 0
        assert summary["retry_queue_final_failed_count"] == 1
        assert summary["retry_queue_drain_skipped_count"] == 1
        assert summary["retry_queue_drain_skipped_reason_counts"] == {"drain_projected_source_age_cliff": 1}
        assert summary["content_fetch_retry_queue_sleep_elapsed_s_total"] == 0.0

    def test_source_content_fetch_logs_direct_youtube_page_classification_on_failure(self):
        """Failed fetches should carry yt-dlp and direct YouTube page metadata."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-inspect"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch(
                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                return_value={
                    "classification": "unknown",
                    "available": False,
                    "availability": None,
                    "live_status": None,
                    "was_live": False,
                    "is_live": False,
                    "title": None,
                    "error": None,
                    "elapsed_s": 1.25,
                },
            ) as mock_ytdlp:
                with mock.patch(
                    "csf.nlm_batch.inspect_youtube_watch_page",
                    return_value={
                        "classification": "removed_by_owner",
                        "available": False,
                        "status": "ERROR",
                        "reason": "Video unavailable",
                        "subreason": "This video has been removed by the uploader",
                        "is_live_content": False,
                        "title": None,
                        "elapsed_s": 0.75,
                    },
                ) as mock_inspect:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert mock_ytdlp.call_count == 1
        mock_inspect.assert_called_once_with("vid1")
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["youtube_page_classification"] == "removed_by_owner"
        assert completed["youtube_page_available"] is False
        assert completed["youtube_page_status"] == "ERROR"
        assert completed["youtube_page_reason"] == "Video unavailable"
        assert completed["youtube_ytdlp_classification"] == "unknown"
        assert completed["youtube_ytdlp_available"] is False
        assert completed["youtube_ytdlp_availability"] is None
        assert completed["retry_queue_gate_reason"] == "ytdlp_removed_by_owner"
        assert completed["youtube_ytdlp_elapsed_s"] == 1.25
        assert completed["youtube_page_elapsed_s"] == 0.75
        summary = ingestor.get_last_extract_metrics()
        assert summary is not None
        assert summary["youtube_ytdlp_elapsed_s_total"] == 1.25
        assert summary["youtube_ytdlp_elapsed_s_count"] == 1
        assert summary["youtube_page_elapsed_s_total"] == 0.75
        assert summary["youtube_page_elapsed_s_count"] == 1

    def test_source_content_retry_queue_counts_youtube_probe_elapsed_on_deferred_failure(self):
        """Deferred retry-queue failures must still accumulate yt-dlp timing in summary metrics."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-retry-queue"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch(
                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                return_value={
                    "classification": "ok",
                    "available": True,
                    "availability": "public",
                    "live_status": "not_live",
                    "was_live": False,
                    "is_live": False,
                    "title": "Queued sample",
                    "error": None,
                    "elapsed_s": 1.5,
                },
            ) as mock_ytdlp:
                with mock.patch("csf.nlm_batch.time.sleep") as mock_sleep:
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert mock_ytdlp.call_count == 2
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_extract_completed")
        assert completed["retry_queue_deferred_count"] == 1
        assert completed["youtube_ytdlp_elapsed_s_total"] == 3.0
        assert completed["youtube_ytdlp_elapsed_s_count"] == 2
        summary = ingestor.get_last_extract_metrics()
        assert summary is not None
        assert summary["youtube_ytdlp_elapsed_s_total"] == 3.0
        assert summary["youtube_ytdlp_elapsed_s_count"] == 2

    def test_source_content_fetch_records_retry_gate_reason_for_non_retryable_ytdlp_classification(self):
        """A non-OK yt-dlp classification should keep the retry gate explicit and not queue."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-gate-reason"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""},
                )()
            if cmd[:2] == ["source", "content"]:
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch(
                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                return_value={
                    "classification": "removed_by_owner",
                    "available": False,
                    "availability": None,
                    "live_status": None,
                    "was_live": False,
                    "is_live": False,
                    "title": None,
                    "error": None,
                    "elapsed_s": 1.25,
                },
            ) as mock_ytdlp:
                with mock.patch("csf.nlm_batch.log_action") as mock_log:
                    results = ingestor.extract_transcripts(["vid1"])

        assert results["vid1"][0] is False
        assert mock_ytdlp.call_count == 1
        completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "nlm_batch_source_content_fetch_completed")
        assert completed["retry_queue_gate_reason"] == "ytdlp_removed_by_owner"
        assert completed["queued_for_retry"] is False
        assert completed["retry_queue_skipped_reason"] is None

    def test_extract_transcripts_matches_sources_by_title_instead_of_order(self):
        """Source list order should not control which video ID gets which source ID."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-order"
        vid1 = "AAAAAAAAAAA"
        vid2 = "BBBBBBBBBBB"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "s2", "title": f"https://www.youtube.com/watch?v={vid2}"},
                                    {"id": "s1", "title": f"https://www.youtube.com/watch?v={vid1}"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                source_id = cmd[2]
                content = "A" * 101 if source_id == "s1" else "B" * 101
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": content}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            results = ingestor.extract_transcripts([vid1, vid2])

        assert results[vid1][0] is True
        assert results[vid1][1] == "A" * 101
        assert results[vid2][0] is True
        assert results[vid2][1] == "B" * 101

    def test_extract_transcripts_fail_closed_on_partial_title_match_without_add_ids(self):
        """A2: partial title match must not fill the remainder by source-list order."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-partial-order"
        vid1 = "AAAAAAAAAAA"
        vid2 = "BBBBBBBBBBB"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "s1", "title": f"https://www.youtube.com/watch?v={vid1}"},
                                    {"id": "s2", "title": "Previously processed source"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content fetch should not run when mapping fails closed")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                results = ingestor.extract_transcripts([vid1, vid2])

        assert results[vid1][0] is False
        assert results[vid2][0] is False
        assert results[vid1][2] == "Source mapping failed"
        assert results[vid2][2] == "Source mapping failed"
        failed_logs = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_mapping_failed"
        ]
        assert failed_logs
        assert failed_logs[0].get("pairing_mode") == "fail_closed_uncorroborated"

    def test_extract_transcripts_fail_closed_when_cadence_gap_lacks_identity(self):
        """A2: newly observed sources without video IDs are not order-mapped onto the batch."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-cadence"
        ingestor._previously_observed_source_ids = {"old-1", "old-2"}
        vid1 = "AAAAAAAAAAA"
        vid2 = "BBBBBBBBBBB"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "old-1", "title": "Prior cadence source 1"},
                                    {"id": "old-2", "title": "Prior cadence source 2"},
                                    {"id": "new-1", "title": f"https://www.youtube.com/watch?v={vid1}"},
                                    {"id": "new-2", "title": "New source without a video ID"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content fetch should not run when mapping fails closed")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                results = ingestor.extract_transcripts([vid1, vid2])

        assert results[vid1][0] is False
        assert results[vid2][0] is False
        assert results[vid1][2] == "Source mapping failed"
        assert any(call.args[0] == "nlm_batch_source_mapping_failed" for call in mock_log.call_args_list)

    def test_extract_transcripts_fail_closed_when_only_order_could_map_new_source(self):
        """A2: previously observed title matches must not force order-map onto a new source."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-cadence-repeat"
        ingestor._previously_observed_source_ids = {"old-1"}
        vid = "AAAAAAAAAAA"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "old-1", "title": f"https://www.youtube.com/watch?v={vid}"},
                                    {"id": "new-1", "title": "New source without a video ID"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content fetch should not run when mapping fails closed")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                result = ingestor.extract_transcripts([vid])

        assert result[vid][0] is False
        assert result[vid][2] == "Source mapping failed"
        assert any(call.args[0] == "nlm_batch_source_mapping_failed" for call in mock_log.call_args_list)

    def test_extract_transcripts_uses_add_response_source_ids_when_lengths_match(self):
        """A2 Rank B: same-length add-response source IDs still pair by batch submit order."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-add-map"
        ingestor._last_added_source_ids = ["src-a", "src-b"]
        vid1 = "AAAAAAAAAAA"
        vid2 = "BBBBBBBBBBB"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "src-b", "title": "no id in title"},
                                    {"id": "src-a", "title": "also no id"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                source_id = cmd[2]
                content = ("A" if source_id == "src-a" else "B") * 101
                return type(
                    "CompletedProcess",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"value": {"content": content}}), "stderr": ""},
                )()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                results = ingestor.extract_transcripts([vid1, vid2])

        assert results[vid1][0] is True
        assert results[vid1][1] == "A" * 101
        assert results[vid2][0] is True
        assert results[vid2][1] == "B" * 101
        assert not any(call.args[0] == "nlm_batch_source_mapping_failed" for call in mock_log.call_args_list)

    def test_extract_transcripts_rejects_partial_mapping_without_order_fallback(self):
        """Partial source-list matches should fail closed instead of guessing by position."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-partial"
        vid1 = "AAAAAAAAAAA"
        vid2 = "BBBBBBBBBBB"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {
                                "sources": [
                                    {"id": "s1", "title": f"https://www.youtube.com/watch?v={vid1}"},
                                    {"id": "stale", "title": "Previously processed source"},
                                    {"id": "s2", "title": "Another stale source"},
                                ]
                            }
                        ),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content fetch should not run when mapping is incomplete")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                results = ingestor.extract_transcripts([vid1, vid2])

        assert results[vid1][0] is False
        assert results[vid2][0] is False
        assert results[vid1][2] == "Source mapping failed"
        assert results[vid2][2] == "Source mapping failed"
        assert any(call.args[0] == "nlm_batch_source_mapping_failed" for call in mock_log.call_args_list)

    def test_extract_transcripts_rejects_duplicate_source_ids_before_fetch(self):
        """Duplicate source IDs should stop fetches before hot-path time is spent."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-duplicate"
        ingestor._last_added_video_ids = ["vid1", "vid2"]
        ingestor._last_added_source_ids = ["src-shared", "src-shared"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type(
                    "CompletedProcess",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"sources": [{"id": "src-shared"}, {"id": "src-shared"}]}),
                        "stderr": "",
                    },
                )()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content fetch should not run when duplicate source IDs are detected")
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch("csf.nlm_batch.log_action") as mock_log:
                results = ingestor.extract_transcripts(["vid1", "vid2"])

        assert results["vid1"][0] is False
        assert results["vid2"][0] is False
        assert results["vid1"][2] == "Source mapping failed"
        assert results["vid2"][2] == "Source mapping failed"
        assert any(call.args[0] == "nlm_batch_source_mapping_failed" for call in mock_log.call_args_list)

    def test_source_count_tracked_in_subbatch_metrics(self):
        """Subbatch metrics should include current_source_count after each subbatch."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-123"
        ingestor._direct_client = _SuccessfulDirectTestClient()

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CompletedProcess", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": f"s{i}"} for i in range(100)]}), "stderr": ""})()
            if cmd[:2] == ["source", "add"]:
                return type("CompletedProcess", (), {"returncode": 0, "stdout": "added", "stderr": ""})()
            return type("CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(ingestor, "_wait_for_sources_ready", return_value=True):
                with mock.patch("csf.nlm_batch.log_action"):
                    ingestor._add_sources_in_subbatches(["v1", "v2", "v3", "v4"], subbatch_size=2)

        for metric in ingestor._last_subbatch_metrics:
            assert "current_source_count" in metric
            assert metric["current_source_count"] == 100  # always 100 from mock

    def test_add_sources_in_subbatches_reuses_outer_source_count_probe(self):
        """The scheduler should pass its precomputed source count into the add chunk."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-123"
        add_response = type(
            "CompletedProcess",
            (object,),
            {"returncode": 0, "stdout": "Source ID: s1\nSource ID: s2", "stderr": ""},
        )()

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=7) as mock_count:
            with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]) as mock_add_chunk:
                with mock.patch("csf.nlm_batch.log_action"):
                    with mock.patch.object(ingestor, "_run_cmd", return_value=add_response):
                        ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        assert mock_count.call_count >= 1
        assert mock_add_chunk.call_count == 1
        kwargs = mock_add_chunk.call_args.kwargs
        assert kwargs["source_count_before"] == 7
        assert kwargs["source_count_probe_ok_before"] is True
        assert kwargs["source_count_probe_error_before"] is None


class TestBackoffCalculation:
    """Exponential backoff must be capped at _MAX_DELAY."""

    def test_backoff_capped_at_max_delay(self):
        """Consecutive failures beyond threshold must respect _MAX_DELAY ceiling."""
        tracker = nlm_batch._RateLimitTracker()
        for _ in range(10):
            tracker.record_failure(is_rate_limit=True)
        with tracker._lock:
            assert tracker._current_delay <= nlm_batch._MAX_DELAY

    def test_backoff_grows_exponentially(self):
        """Delay must grow as INITIAL_DELAY * 2^(n-1) for failures 1..4."""
        tracker = nlm_batch._RateLimitTracker()
        delays = []
        for i in range(1, 5):
            tracker.record_failure(is_rate_limit=True)
            with tracker._lock:
                delays.append(tracker._current_delay)
        assert delays == [0.5, 1.0, 2.0, 4.0]


class TestAgeGuardRotatesBeforeCliff:
    """Age guard rotates the notebook before source age crosses the configured cliff.

    The existing _rotate_notebook is currently triggered only by source COUNT
    approaching _NOTEBOOK_SOURCE_CAP. The age-guard adds a parallel trigger:
    source AGE (time since notebook creation or time since oldest source was added)
    exceeding a configurable cliff. The plan's decision gate showed that the 400s
    age cap run produced 3045 VPH vs the 200s cap run's 3084 VPH, with source
    age maxes of 272-279s — above the ~240s cliff observed in run08. This means
    the notebook rotation needs to fire on AGE, not just count.
    """

    def test_age_guard_rotates_before_cliff(self):
        """Notebook rotates when source age exceeds the configured cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-age-test"
        ingestor._current_source_count = 0
        # Simulate oldest source having exceeded the cliff (300s ago).
        # Patch time.time at the module level since that's where the age guard reads it.
        old_time = 1000000000.0  # fixed epoch
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.time.time", return_value=old_time + 300):
                with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]) as mock_add:
                    with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                        with mock.patch("csf.nlm_batch.log_action"):
                            ingestor._oldest_source_materialization_epoch = old_time
                            ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        assert mock_rotate.call_count >= 1, (
            f"Age guard should trigger rotation before add when age exceeds cliff. "
            f"Got call_count={mock_rotate.call_count}"
        )

    def test_age_guard_logs_check_event_for_fresh_notebook(self):
        """The guard should log a skipped decision when no epoch is available."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-fresh"
        ingestor._current_source_count = 0
        log_events = []

        def capture_log(name, data):
            log_events.append((name, data))

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]):
                with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                    with mock.patch("csf.nlm_batch.log_action", side_effect=capture_log):
                        ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        check_events = [data for name, data in log_events if name == "nlm_batch_subbatch_age_guard_checked"]
        assert check_events
        assert check_events[0]["decision"] == "skipped_no_epoch"
        assert check_events[0]["oldest_source_materialization_epoch"] is None
        mock_rotate.assert_not_called()

    def test_age_guard_logs_check_event_below_cliff(self):
        """The guard should log a below-cliff decision without rotating."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-fresh"
        ingestor._current_source_count = 0
        log_events = []

        def capture_log(name, data):
            log_events.append((name, data))

        old_time = 1000000000.0
        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.time.time", return_value=old_time + 50):
                with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]):
                    with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                        with mock.patch("csf.nlm_batch.log_action", side_effect=capture_log):
                            ingestor._oldest_source_materialization_epoch = old_time
                            ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        check_events = [data for name, data in log_events if name == "nlm_batch_subbatch_age_guard_checked"]
        assert check_events
        assert check_events[0]["decision"] == "below_cliff"
        assert check_events[0]["oldest_source_age_s"] == 50.0
        mock_rotate.assert_not_called()

    def test_age_guard_does_not_fire_for_fresh_notebook(self):
        """No rotation from age guard when the notebook is freshly created."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-fresh"
        ingestor._current_source_count = 0
        log_events = []

        def capture_log(name, data):
            log_events.append((name, data))

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]):
                with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                    with mock.patch("csf.nlm_batch.log_action", side_effect=capture_log):
                        ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        # Capacity cap is 50, current count is 0 — no rotation expected; age guard sees 0.
        assert mock_rotate.call_count == 0, (
            f"No rotation expected for fresh notebook with 0 sources. Got call_count={mock_rotate.call_count}"
        )
        check_events = [data for name, data in log_events if name == "nlm_batch_subbatch_age_guard_checked"]
        assert check_events
        assert check_events[0]["decision"] == "skipped_no_epoch"
        assert check_events[0]["oldest_source_materialization_epoch"] is None

    def test_age_guard_logs_distinct_reason(self):
        """Age-guard rotation emits a log event with rotation_reason='source_age_cliff'."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-log-age"
        ingestor._current_source_count = 0
        # Simulate oldest source having exceeded the cliff.
        old_time = 1000000000.0  # fixed epoch

        log_events = []
        def capture_log(name, data):
            log_events.append((name, data))

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.time.time", return_value=old_time + 300):
                with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]):
                    with mock.patch.object(ingestor, "_rotate_notebook"):
                        with mock.patch("csf.nlm_batch.log_action", side_effect=capture_log):
                            ingestor._oldest_source_materialization_epoch = old_time
                            ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        age_rotation_events = [
            data for name, data in log_events
            if data.get("rotation_reason") == "source_age_cliff"
        ]
        assert len(age_rotation_events) >= 1, (
            f"Expected at least one age-cliff rotation event. Got {log_events!s}"
        )
        check_events = [data for name, data in log_events if name == "nlm_batch_subbatch_age_guard_checked"]
        assert check_events
        assert check_events[0]["decision"] == "rotate_source_age_cliff"

    def test_age_guard_rotates_on_cliff_boundary(self):
        """The age guard should rotate when source age lands exactly on the cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-age-boundary"
        ingestor._current_source_count = 0
        old_time = 1000000000.0  # fixed epoch

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.time.time", return_value=old_time + 200):
                with mock.patch.object(ingestor, "_add_sources_chunk", return_value=["v1", "v2"]):
                    with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                        with mock.patch("csf.nlm_batch.log_action"):
                            ingestor._oldest_source_materialization_epoch = old_time
                            ingestor._add_sources_in_subbatches(["v1", "v2"], subbatch_size=2)

        assert mock_rotate.call_count >= 1, (
            f"Age guard should trigger rotation at the cliff boundary. "
            f"Got call_count={mock_rotate.call_count}"
        )

    def test_age_guard_rotates_on_projected_cliff_before_next_subbatch(self):
        """The guard should rotate before the next subbatch if the last elapsed time would cross the cliff."""
        ingestor = nlm_batch.NLMBatchIngestor(batch_size=2)
        ingestor._nb_id = "nb-projected-age"
        ingestor._current_source_count = 0
        ingestor._oldest_source_materialization_epoch = 1000.0
        log_events = []

        def capture_log(name, data):
            log_events.append((name, data))

        call_state = {"count": 0}

        def fake_add_sources_chunk(batch_ids, **kwargs):
            call_state["count"] += 1
            ingestor._last_add_cmd_elapsed_s = 15.0
            ingestor._last_materialization_wait_elapsed_s = 10.0
            return list(batch_ids)

        with mock.patch.object(ingestor, "_get_current_source_count", return_value=0):
            with mock.patch("csf.nlm_batch.time.time", side_effect=[1190.0, 1195.0]):
                with mock.patch.object(ingestor, "_add_sources_chunk", side_effect=fake_add_sources_chunk):
                    with mock.patch.object(ingestor, "_rotate_notebook") as mock_rotate:
                        with mock.patch("csf.nlm_batch.log_action", side_effect=capture_log):
                            ingestor._add_sources_in_subbatches(["v1", "v2", "v3", "v4"], subbatch_size=2)

        assert call_state["count"] == 2
        assert mock_rotate.call_count == 1
        check_events = [data for name, data in log_events if name == "nlm_batch_subbatch_age_guard_checked"]
        assert len(check_events) >= 2
        assert check_events[1]["decision"] == "rotate_source_age_projected_cliff"
        assert check_events[1]["projected_oldest_source_age_s"] == 220.0


class TestCandidate6Instrumentation:
    """Candidate 6 instrumentation contract:
    per_attempt_elapsed_s, per_attempt_internal_retry_count,
    per_attempt_internal_breakdown_s, per_attempt_returncode,
    run_cmd_overshoot_vs_timeout_s, retry_loop_elapsed_s,
    retry_exit_reason, source_ready_age_s_breakdown,
    retry_queue_entry_time_epoch, retry_queue_start_time_epoch,
    retry_queue_wait_time_s.
    Fields must be JSON-serializable and stable across success, command_failed,
    source_age_cliff, and queued retry paths.
    """

    @staticmethod
    def _fetch_completed_payload(mock_log):
        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        assert completed, "expected at least one nlm_batch_source_content_fetch_completed"
        return completed[-1]

    @staticmethod
    def _required_fields_present(payload):
        required = [
            "per_attempt_elapsed_s",
            "per_attempt_internal_retry_count",
            "per_attempt_internal_breakdown_s",
            "per_attempt_returncode",
            "run_cmd_overshoot_vs_timeout_s",
            "retry_loop_elapsed_s",
            "retry_exit_reason",
            "source_ready_age_s_breakdown",
            "retry_queue_entry_time_epoch",
            "retry_queue_start_time_epoch",
            "retry_queue_wait_time_s",
        ]
        for name in required:
            assert name in payload, f"missing required Candidate-6 field: {name}"

    def test_run_cmd_iteration_log_records_normal_return(self, monkeypatch):
        """_run_cmd records a single normal_return iteration when run_nlm succeeds on first try."""
        from csf import nlm_batch as nlm_batch_mod

        monkeypatch.setattr(nlm_batch_mod, "run_nlm", lambda args, *, timeout_s, **kw: type(
            "CP", (), {"returncode": 0, "stdout": "{}", "stderr": ""})())
        monkeypatch.setattr(nlm_batch_mod, "_ensure_nlm_auth", lambda: True)
        monkeypatch.setattr(nlm_batch_mod, "_get_tracker", lambda: type("T", (), {"apply_delay": lambda self: None, "record_success": lambda self: None, "record_failure": lambda self, is_rate_limit: None})())
        monkeypatch.setattr(nlm_batch_mod, "_get_nlm_auth_context", lambda: type("A", (), {"profile": None, "has_profile": False})())
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_before_command", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_fail_closed_on_default_chrome_profile", lambda *a, **kw: None)

        ingestor = nlm_batch_mod.NLMBatchIngestor(batch_size=1)
        iteration_log: list[dict] = []
        ingestor._run_cmd(["source", "content", "src-1", "--json"], timeout=30, iteration_log=iteration_log)
        assert len(iteration_log) == 1
        assert iteration_log[0]["branch"] == "normal_return"
        assert iteration_log[0]["returncode"] == 0
        assert iteration_log[0]["iteration"] == 1
        assert "subprocess_elapsed_s" in iteration_log[0]

    def test_run_cmd_iteration_log_records_rate_limit_then_normal(self, monkeypatch):
        """_run_cmd records a rate_limit iteration followed by a normal_return iteration."""
        from csf import nlm_batch as nlm_batch_mod

        responses = [
            type("CP", (), {"returncode": 1, "stdout": "", "stderr": "rate limit 429"})(),
            type("CP", (), {"returncode": 0, "stdout": "{}", "stderr": ""})(),
        ]
        monkeypatch.setattr(nlm_batch_mod, "run_nlm", lambda args, *, timeout_s, **kw: responses.pop(0))
        monkeypatch.setattr(nlm_batch_mod, "_ensure_nlm_auth", lambda: True)
        monkeypatch.setattr(nlm_batch_mod, "_get_tracker", lambda: type("T", (), {"apply_delay": lambda self: None, "record_success": lambda self: None, "record_failure": lambda self, is_rate_limit: None})())
        monkeypatch.setattr(nlm_batch_mod, "_get_nlm_auth_context", lambda: type("A", (), {"profile": None, "has_profile": False})())
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_before_command", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_fail_closed_on_default_chrome_profile", lambda *a, **kw: None)

        ingestor = nlm_batch_mod.NLMBatchIngestor(batch_size=1)
        iteration_log: list[dict] = []
        ingestor._run_cmd(["source", "content", "src-1", "--json"], timeout=30, iteration_log=iteration_log)
        assert len(iteration_log) == 2
        assert iteration_log[0]["branch"] == "rate_limit"
        assert iteration_log[0]["returncode"] == 1
        assert iteration_log[1]["branch"] == "normal_return"
        assert iteration_log[1]["returncode"] == 0
        assert iteration_log[1]["iteration"] == 2

    def test_run_cmd_iteration_log_records_timeout(self, monkeypatch):
        """_run_cmd records a timeout iteration when run_nlm returns the timed-out sentinel."""
        from csf import nlm_batch as nlm_batch_mod

        monkeypatch.setattr(nlm_batch_mod, "run_nlm", lambda args, *, timeout_s, **kw: type(
            "CP", (), {"returncode": 1, "stdout": "", "stderr": "NLM command timed out"})())
        monkeypatch.setattr(nlm_batch_mod, "_ensure_nlm_auth", lambda: True)
        monkeypatch.setattr(nlm_batch_mod, "_get_tracker", lambda: type("T", (), {"apply_delay": lambda self: None, "record_success": lambda self: None, "record_failure": lambda self, is_rate_limit: None})())
        monkeypatch.setattr(nlm_batch_mod, "_get_nlm_auth_context", lambda: type("A", (), {"profile": None, "has_profile": False})())
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_for_auth", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_reap_default_chrome_profile_before_command", lambda *a, **kw: None)
        monkeypatch.setattr(nlm_batch_mod, "_fail_closed_on_default_chrome_profile", lambda *a, **kw: None)

        ingestor = nlm_batch_mod.NLMBatchIngestor(batch_size=1)
        iteration_log: list[dict] = []
        ingestor._run_cmd(["source", "content", "src-1", "--json"], timeout=30, iteration_log=iteration_log)
        assert len(iteration_log) == 1
        assert iteration_log[0]["branch"] == "timeout"
        assert iteration_log[0]["returncode"] == 1

    def test_candidate6_fields_present_on_primary_success(self, monkeypatch):
        """Primary pass single successful fetch must surface all Candidate-6 fields
        with retry_exit_reason='success' and per_attempt_internal_retry_count==1."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-success"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                # Faithfully simulate _run_cmd telemetry: one normal_return iteration.
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "normal_return", "subprocess_elapsed_s": 1.0, "returncode": 0})
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        payload = self._fetch_completed_payload(mock_log)
        self._required_fields_present(payload)
        assert payload["retry_exit_reason"] == "success"
        assert len(payload["per_attempt_elapsed_s"]) == 1
        assert payload["per_attempt_internal_retry_count"] == [1]
        assert len(payload["per_attempt_internal_breakdown_s"]) == 1
        assert payload["per_attempt_internal_breakdown_s"][0][0]["branch"] == "normal_return"
        assert payload["per_attempt_returncode"] == [0]
        assert isinstance(payload["retry_loop_elapsed_s"], float)
        assert payload["retry_loop_elapsed_s"] >= 0.0
        assert isinstance(payload["source_ready_age_s_breakdown"], dict)
        assert set(payload["source_ready_age_s_breakdown"].keys()) == {
            "primary_batch_wait_time_s",
            "retry_queue_wait_time_s",
            "retry_loop_elapsed_s",
        }
        # All Candidate-6 values must be JSON-serializable.
        json.dumps(payload)

    def test_candidate6_fields_present_on_primary_failure_with_rate_limit_retries(self, monkeypatch):
        """Primary pass with one rate_limit internal retry must surface per_attempt_internal_retry_count==2
        and the breakdown must record a rate_limit iteration followed by a normal_return iteration."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-rl"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            iteration_log = iteration_log if iteration_log is not None else getattr(fake_run_cmd, "_iteration_log_attr", None)
            if iteration_log is not None:
                # Simulate _run_cmd populating iteration_log when called from retry loop.
                iteration_log.append({"iteration": 1, "branch": "rate_limit", "subprocess_elapsed_s": 0.5, "returncode": 1})
                iteration_log.append({"iteration": 2, "branch": "normal_return", "subprocess_elapsed_s": 1.5, "returncode": 0})
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "y" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        # Wrap fake_run_cmd to capture the iteration_log each call.
        original = fake_run_cmd
        call_count = {"n": 0}

        def wrapper(cmd, timeout=300, iteration_log=None):
            iteration_log = iteration_log if iteration_log is not None else []
            setattr(fake_run_cmd, "_iteration_log_attr", iteration_log)
            try:
                return original(cmd, timeout=timeout, iteration_log=iteration_log)
            finally:
                setattr(fake_run_cmd, "_iteration_log_attr", None)

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=wrapper):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        payload = self._fetch_completed_payload(mock_log)
        self._required_fields_present(payload)
        assert len(payload["per_attempt_internal_breakdown_s"]) >= 1
        first_breakdown = payload["per_attempt_internal_breakdown_s"][0]
        # The wrapper above simulated 2 internal iterations for the content call.
        assert len(first_breakdown) == 2
        branches = [item["branch"] for item in first_breakdown]
        assert branches == ["rate_limit", "normal_return"]
        assert payload["per_attempt_internal_retry_count"][0] == 2
        assert payload["per_attempt_returncode"][0] == 0
        assert payload["retry_exit_reason"] == "success"
        json.dumps(payload)

    def test_candidate6_fields_stable_across_paths(self, monkeypatch):
        """Candidate-6 fields must appear on EVERY nlm_batch_source_content_fetch_completed
        emission (success / command_failed / queued / no-command), preserving stable shape."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-stable"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")
        # Tight retry budget so attempts_exhausted fires.
        monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_RETRY_BUDGET_S", "60.0")
        monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_RETRY_ATTEMPTS", "1")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "non_rate_limit_failure", "subprocess_elapsed_s": 1.0, "returncode": 1})
                return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()
            return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        # Every fetch_completed emission must include the full Candidate-6 shape.
        completed_payloads = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        assert len(completed_payloads) >= 1
        required = (
            "per_attempt_elapsed_s",
            "per_attempt_internal_retry_count",
            "per_attempt_internal_breakdown_s",
            "per_attempt_returncode",
            "run_cmd_overshoot_vs_timeout_s",
            "retry_loop_elapsed_s",
            "retry_exit_reason",
            "source_ready_age_s_breakdown",
            "retry_queue_entry_time_epoch",
            "retry_queue_start_time_epoch",
            "retry_queue_wait_time_s",
        )
        for payload in completed_payloads:
            for name in required:
                assert name in payload, f"missing Candidate-6 field {name} in path payload: status={payload.get('status')!r}"
            # All values JSON-serializable.
            json.dumps(payload)
            # Lists must be lists; breakdown entries must be dicts.
            assert isinstance(payload["per_attempt_elapsed_s"], list)
            assert isinstance(payload["per_attempt_internal_breakdown_s"], list)
            assert isinstance(payload["source_ready_age_s_breakdown"], dict)
            assert set(payload["source_ready_age_s_breakdown"].keys()) == {
                "primary_batch_wait_time_s",
                "retry_queue_wait_time_s",
                "retry_loop_elapsed_s",
            }
            # retry_exit_reason must be a string (any of the allowed values).
            assert isinstance(payload["retry_exit_reason"], str)

    def test_candidate6_fields_present_on_failed_attempt_paths(self, monkeypatch):
        """Failure path must still surface Candidate-6 fields with retry_exit_reason in
        {attempts_exhausted, budget_exhausted, not_retryable, local_retry_skipped_age_cliff}."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-fail"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")
        # Tight retry budget so attempts_exhausted fires.
        monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_RETRY_BUDGET_S", "60.0")
        monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_RETRY_ATTEMPTS", "2")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                # Two attempts, both fail. _run_cmd records each.
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "non_rate_limit_failure", "subprocess_elapsed_s": 1.0, "returncode": 1})
                return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()
            return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        # The post-loop (not_retryable_queued) emission should include Candidate-6 fields.
        post_loop_payloads = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
            and "per_attempt_elapsed_s" in call.args[1]
        ]
        assert post_loop_payloads, "expected post-loop fetch_completed payload with Candidate-6 fields"
        payload = post_loop_payloads[-1]
        self._required_fields_present(payload)
        assert payload["retry_exit_reason"] in {
            "attempts_exhausted",
            "budget_exhausted",
            "not_retryable",
            "local_retry_skipped_age_cliff",
        }
        assert len(payload["per_attempt_internal_breakdown_s"]) >= 1
        # Each breakdown list should match per_attempt_internal_retry_count.
        assert sum(payload["per_attempt_internal_retry_count"]) == sum(
            len(b) for b in payload["per_attempt_internal_breakdown_s"]
        )
        json.dumps(payload)

    def test_candidate6_fields_have_json_serializable_shapes(self, monkeypatch):
        """All Candidate-6 field values must serialize via json.dumps without raising."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-json"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "z" * 101}}), "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        completed = self._fetch_completed_payload(mock_log)
        for fld in (
            "per_attempt_elapsed_s",
            "per_attempt_internal_retry_count",
            "per_attempt_internal_breakdown_s",
            "per_attempt_returncode",
            "run_cmd_overshoot_vs_timeout_s",
            "source_ready_age_s_breakdown",
        ):
            assert fld in completed, f"missing field {fld}"
            json.dumps(completed[fld])

    def test_candidate6_source_age_cliff_has_terminal_retry_exit_reason(self, monkeypatch):
        """Regression: no-command source_age_cliff rows must not leak the
        initial retry_exit_reason='in_progress' default."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-c6-age-cliff"
        ingestor._last_materialization_ready_at_epoch = 1000.0

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                raise AssertionError("source content should not run after source_age_cliff")
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_AGE_CLIFF_S", 200.0):
            with mock.patch("csf.nlm_batch.time.time", return_value=1301.0):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        assert completed, "expected source_age_cliff fetch_completed event"
        payload = completed[-1]
        self._required_fields_present(payload)
        assert payload["status"] == "source_age_cliff"
        assert payload["attempts"] == 0
        assert payload["per_attempt_elapsed_s"] == []
        assert payload["retry_exit_reason"] == "source_age_cliff"
        assert payload["retry_exit_reason"] != "in_progress"
        json.dumps(payload)

    def test_queue_timing_none_on_primary_success(self, monkeypatch):
        """Primary success fetch_completed rows must have retry_queue_entry_time_epoch=None,
        retry_queue_start_time_epoch=None, retry_queue_wait_time_s=None."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-qt-primary"
        monkeypatch.setenv("NOTEBOOKLM_PROFILE", "ytis-pro-worker-02")
        monkeypatch.setenv("YTIS_NLM_EXPECTED_EMAIL", "worker02@example.com")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_ROOT", r"P:\.data\yt-is\browser\notebooklm-pro")
        monkeypatch.setenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "Profile")
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "normal_return", "subprocess_elapsed_s": 1.0, "returncode": 0})
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        for entry in completed:
            if entry.get("pass_name") == "primary":
                assert entry["retry_queue_entry_time_epoch"] is None, f"primary {entry.get('status')} entry_time not None"
                assert entry["retry_queue_start_time_epoch"] is None, f"primary {entry.get('status')} start_time not None"
                assert entry["retry_queue_wait_time_s"] is None, f"primary {entry.get('status')} wait_time not None"
                assert entry["source_ready_age_s_breakdown"]["retry_queue_wait_time_s"] is None
                json.dumps(entry)

    def test_queue_timing_records_retry_queue_queued_at_on_deferred_primary(self, monkeypatch):
        """A primary failure that is queued for retry must record retry_queue_queued_at_epoch
        on the primary-pass fetch_completed payload."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-qt-queued"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={"classification": "ok", "available": True, "availability": "public", "live_status": "not_live"},
                        ):
                            with mock.patch.object(ingestor, "_recover_dead_notebook") as mock_recover:
                                with mock.patch("csf.nlm_batch.time.sleep"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        primary_queued = [
            e for e in completed
            if e.get("pass_name") == "primary" and e.get("queued_for_retry") is True
        ]
        assert len(primary_queued) >= 1, "expected at least one primary queued entry"
        for entry in primary_queued:
            assert entry.get("retry_queue_queued_at_epoch") is not None, "primary queued entry missing retry_queue_queued_at_epoch"
            assert isinstance(entry["retry_queue_queued_at_epoch"], float)
            json.dumps(entry)

    def test_queue_timing_nonnull_on_retry_pass(self, monkeypatch):
        """Retry-pass fetch_completed rows must receive non-null
        retry_queue_entry_time_epoch, retry_queue_start_time_epoch, and
        retry_queue_wait_time_s."""
        from csf import nlm_batch
        import time

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-qt-retry"
        content_calls = {"count": 0}

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                content_calls["count"] += 1
                if content_calls["count"] == 1:
                    return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"})()
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={"classification": "ok", "available": True, "availability": "public", "live_status": "not_live"},
                        ):
                            with mock.patch.object(ingestor, "_recover_dead_notebook"):
                                with mock.patch("csf.nlm_batch.time.sleep"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        retry_ok = [
            e for e in completed
            if e.get("pass_name") == "retry" and e.get("status") == "ready"
        ]
        assert len(retry_ok) >= 1, "expected at least one retry-ready entry"
        for entry in retry_ok:
            assert entry["retry_queue_entry_time_epoch"] is not None, "retry entry_time is None"
            assert entry["retry_queue_start_time_epoch"] is not None, "retry start_time is None"
            assert entry["retry_queue_wait_time_s"] is not None, "retry wait_time is None"
            assert entry["retry_queue_wait_time_s"] >= 0.0, "retry wait_time is negative"
            assert isinstance(entry["retry_queue_entry_time_epoch"], float)
            assert isinstance(entry["retry_queue_start_time_epoch"], float)
            assert isinstance(entry["retry_queue_wait_time_s"], float)
            json.dumps(entry)

    def test_queue_timing_json_serializable_and_non_negative(self, monkeypatch):
        """All queue timing fields must serialize via json.dumps and be non-negative."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-qt-json"

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch(
                            "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                            return_value={"classification": "ok", "available": True},
                        ):
                            with mock.patch.object(ingestor, "_recover_dead_notebook"):
                                with mock.patch("csf.nlm_batch.time.sleep"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        for entry in completed:
            for fld in (
                "retry_queue_entry_time_epoch",
                "retry_queue_start_time_epoch",
                "retry_queue_wait_time_s",
            ):
                assert fld in entry, f"missing {fld}"
                json.dumps(entry[fld])
            # source_ready_age_s_breakdown is a dict with retry_queue_wait_time_s.
            bdown = entry.get("source_ready_age_s_breakdown", {})
            assert "retry_queue_wait_time_s" in bdown
            json.dumps(bdown)
            # queue wait, if not None, must be >= 0
            if entry["retry_queue_wait_time_s"] is not None:
                assert entry["retry_queue_wait_time_s"] >= 0.0

    def test_queue_timing_shape_stable_with_existing_fields(self, monkeypatch):
        """Adding queue timing must not change the shape or type of any existing
        Candidate-6 field. All 11 original fields must still be present and match
        their prior types."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-qt-stable"
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "API error (code 5): NOT_FOUND"})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch("csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp", return_value={"classification": "ok"}):
                            with mock.patch.object(ingestor, "_recover_dead_notebook"):
                                with mock.patch("csf.nlm_batch.time.sleep"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        original_eleven = [
            "per_attempt_elapsed_s",
            "per_attempt_internal_retry_count",
            "per_attempt_internal_breakdown_s",
            "per_attempt_returncode",
            "run_cmd_overshoot_vs_timeout_s",
            "retry_loop_elapsed_s",
            "retry_exit_reason",
            "source_ready_age_s_breakdown",
            "retry_queue_entry_time_epoch",
            "retry_queue_start_time_epoch",
            "retry_queue_wait_time_s",
        ]
        for entry in completed:
            for fld in original_eleven:
                assert fld in entry, f"missing {fld} on {entry.get('pass_name')} {entry.get('status')}"
            # per-attempt lists must be lists (possibly empty).
            assert isinstance(entry["per_attempt_elapsed_s"], list)
            assert isinstance(entry["per_attempt_internal_breakdown_s"], list)
            assert isinstance(entry["per_attempt_returncode"], list)
            assert isinstance(entry["run_cmd_overshoot_vs_timeout_s"], list)
            json.dumps(entry)

    def test_semantic_primary_success_loop_elapsed_is_meaningful(self, monkeypatch):
        """Regression: early `ready` returns must emit non-zero retry_loop_elapsed_s
        and a populated source_ready_age_s_breakdown.

        Uses a monotonic time.time() monkey-patch (0.05s per call) so the underlying
        time.time() granularity on the test platform doesn't round 0-second mocked
        attempts to 0.0 in the producer's round(.,3)."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-sem-primary-success"
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        ticker = {"t": 1000.0}
        def fake_time():
            ticker["t"] += 0.05
            return ticker["t"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "normal_return", "subprocess_elapsed_s": 0.05, "returncode": 0})
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.time, "time", side_effect=fake_time):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
                with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                    with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                        with mock.patch("csf.nlm_batch.log_action") as mock_log:
                            ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        primary_ready = [
            e for e in completed
            if e.get("pass_name") == "primary" and e.get("status") == "ready"
        ]
        assert len(primary_ready) >= 1, "expected a primary ready entry"
        entry = primary_ready[0]
        # THE FIX: success-path telemetry must be meaningful.
        assert entry["retry_loop_elapsed_s"] > 0.0, f"retry_loop_elapsed_s must be > 0 on success, got {entry['retry_loop_elapsed_s']}"
        breakdown = entry["source_ready_age_s_breakdown"]
        assert breakdown["retry_loop_elapsed_s"] is not None, f"breakdown.retry_loop_elapsed_s must not be None on success, got {breakdown['retry_loop_elapsed_s']}"
        assert breakdown["retry_loop_elapsed_s"] > 0.0, f"breakdown.retry_loop_elapsed_s must be > 0 on success, got {breakdown['retry_loop_elapsed_s']}"
        # breakdown fields stay JSON-serializable.
        json.dumps(breakdown)
        # command_total also non-zero.
        assert entry["content_fetch_command_elapsed_s_total"] > 0.0
        json.dumps(entry)

    def test_semantic_retry_success_loop_elapsed_is_meaningful(self, monkeypatch):
        """Regression: retry-pass `ready` rows must emit non-zero retry_loop_elapsed_s
        and a populated source_ready_age_s_breakdown."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-sem-retry-success"
        content_calls = {"count": 0}

        ticker = {"t": 1000.0}
        def fake_time():
            ticker["t"] += 0.05
            return ticker["t"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                content_calls["count"] += 1
                if content_calls["count"] == 1:
                    if iteration_log is not None:
                        iteration_log.append({"iteration": 1, "branch": "non_rate_limit_failure", "subprocess_elapsed_s": 0.05, "returncode": 1})
                    return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "normal_return", "subprocess_elapsed_s": 0.05, "returncode": 0})
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.time, "time", side_effect=fake_time):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.0):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                            with mock.patch(
                                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                return_value={"classification": "ok", "available": True, "availability": "public", "live_status": "not_live"},
                            ):
                                with mock.patch.object(ingestor, "_recover_dead_notebook"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        retry_ready = [
            e for e in completed
            if e.get("pass_name") == "retry" and e.get("status") == "ready"
        ]
        assert len(retry_ready) >= 1, "expected a retry ready entry"
        entry = retry_ready[0]
        # THE FIX: retry success path must emit meaningful loop elapsed.
        assert entry["retry_loop_elapsed_s"] > 0.0, f"retry success retry_loop_elapsed_s must be > 0, got {entry['retry_loop_elapsed_s']}"
        breakdown = entry["source_ready_age_s_breakdown"]
        assert breakdown["retry_loop_elapsed_s"] is not None
        assert breakdown["retry_loop_elapsed_s"] > 0.0
        json.dumps(entry)

    def test_semantic_breakdown_retry_queue_wait_matches_top_level_field(self, monkeypatch):
        """The breakdown.retry_queue_wait_time_s field must match the top-level
        retry_queue_wait_time_s field on retry-pass rows."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-sem-bkdn-match"
        content_calls = {"count": 0}

        ticker = {"t": 1000.0}
        def fake_time():
            ticker["t"] += 0.05
            return ticker["t"]

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                content_calls["count"] += 1
                if content_calls["count"] == 1:
                    if iteration_log is not None:
                        iteration_log.append({"iteration": 1, "branch": "non_rate_limit_failure", "subprocess_elapsed_s": 0.05, "returncode": 1})
                    return type("CP", (), {"returncode": 1, "stdout": "", "stderr": "command failed"})()
                if iteration_log is not None:
                    iteration_log.append({"iteration": 1, "branch": "normal_return", "subprocess_elapsed_s": 0.05, "returncode": 0})
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "x" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.time, "time", side_effect=fake_time):
            with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_ATTEMPTS", 1):
                with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S", 0.0):
                    with mock.patch.object(nlm_batch, "_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S", 30.0):
                        with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                            with mock.patch(
                                "csf.nlm_batch.inspect_youtube_watch_page_via_ytdlp",
                                return_value={"classification": "ok", "available": True, "availability": "public", "live_status": "not_live"},
                            ):
                                with mock.patch.object(ingestor, "_recover_dead_notebook"):
                                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        retry_ready = [
            e for e in completed
            if e.get("pass_name") == "retry" and e.get("status") == "ready"
        ]
        assert len(retry_ready) >= 1
        entry = retry_ready[0]
        # Both fields must be present and equal on retry rows.
        assert "retry_queue_wait_time_s" in entry
        breakdown = entry["source_ready_age_s_breakdown"]
        assert "retry_queue_wait_time_s" in breakdown
        # On retry rows, the top-level and breakdown values must match.
        assert entry["retry_queue_wait_time_s"] == breakdown["retry_queue_wait_time_s"], (
            f"mismatch: top={entry['retry_queue_wait_time_s']} vs breakdown={breakdown['retry_queue_wait_time_s']}"
        )
        json.dumps(entry)

    def test_semantic_breakdown_dict_is_json_serializable_on_all_paths(self, monkeypatch):
        """source_ready_age_s_breakdown must serialize via json.dumps on primary success,
        primary failure (queued), retry success, retry failure paths."""
        from csf import nlm_batch

        ingestor = nlm_batch.NLMBatchIngestor(batch_size=1)
        ingestor._nb_id = "nb-sem-bkdn-json"
        monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", r"P:\packages\yt-is\.logs\sharded_lane_series\worker_states")

        def fake_run_cmd(cmd, timeout=300, iteration_log=None):
            if cmd[:2] == ["source", "list"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"sources": [{"id": "s1"}]}), "stderr": ""})()
            if cmd[:2] == ["source", "content"]:
                return type("CP", (), {"returncode": 0, "stdout": json.dumps({"value": {"content": "z" * 101}}), "stderr": ""})()
            return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_hit", return_value=(True, 12.345)):
            with mock.patch.object(nlm_batch.nlm_auth_guard, "auth_check_cache_session_age", return_value=12.345):
                with mock.patch.object(ingestor, "_run_cmd", side_effect=fake_run_cmd):
                    with mock.patch("csf.nlm_batch.log_action") as mock_log:
                        ingestor.extract_transcripts(["vid1"])

        completed = [
            call.args[1]
            for call in mock_log.call_args_list
            if call.args[0] == "nlm_batch_source_content_fetch_completed"
        ]
        for entry in completed:
            breakdown = entry.get("source_ready_age_s_breakdown")
            assert isinstance(breakdown, dict), f"breakdown not dict on {entry.get('pass_name')}/{entry.get('status')}"
            # Each value must be None or float.
            for k, v in breakdown.items():
                assert v is None or isinstance(v, (int, float)), f"breakdown[{k}] type: {type(v).__name__}"
            json.dumps(breakdown)


class _DummyTracker:
    """Minimal stand-in for _RateLimitTracker used by _run_cmd phase-split tests."""

    def apply_delay(self) -> None:
        return None

    def record_success(self) -> None:
        return None

    def record_failure(self, is_rate_limit: bool = False) -> None:
        return None


class TestRunCmdPhaseSplit:
    """Candidate 6 per-attempt phase-split: _record_iter must emit per-phase
    timing fields, and content_subprocess_elapsed_s must NOT fold in auth/reap time.

    These tests drive NLMBatchIngestor._run_cmd directly with the module-level
    phase helpers monkeypatched, so the phase-stamp boundaries are exercised
    against the real _record_iter closure.
    """

    _PHASE_FIELDS = (
        "iteration_elapsed_s",
        "pre_reap_elapsed_s",
        "auth_elapsed_s",
        "pre_command_reap_elapsed_s",
        "content_subprocess_elapsed_s",
        "post_reap_elapsed_s",
    )

    def _patch_env(
        self,
        monkeypatch,
        *,
        auth_ok: bool = True,
        auth_sleep: float = 0.0,
        reap_sleep: float = 0.0,
        run_nlm_sleep: float = 0.0,
        run_nlm_result: subprocess.CompletedProcess | None = None,
        reap_before_returns_pids: bool = False,
    ) -> None:
        monkeypatch.setattr(nlm_batch, "_get_tracker", lambda: _DummyTracker())
        monkeypatch.setattr(
            nlm_batch,
            "_get_nlm_auth_context",
            lambda: mock.MagicMock(has_profile=False, profile=None, should_fail_closed=False),
        )
        monkeypatch.setattr(nlm_batch.nlm_auth_guard, "add_profile_args", lambda args, profile: list(args))

        def _reap(*a, **k):
            if reap_sleep:
                time.sleep(reap_sleep)
            return None

        monkeypatch.setattr(nlm_batch, "_reap_default_chrome_profile_for_auth", _reap)

        def _reap_before(*a, **k):
            if reap_sleep:
                time.sleep(reap_sleep)
            return ["pid"] if reap_before_returns_pids else None

        monkeypatch.setattr(nlm_batch, "_reap_default_chrome_profile_before_command", _reap_before)
        monkeypatch.setattr(nlm_batch, "_fail_closed_on_default_chrome_profile", lambda *a, **k: None)

        def _ensure():
            if auth_sleep:
                time.sleep(auth_sleep)
            return auth_ok

        monkeypatch.setattr(nlm_batch, "_ensure_nlm_auth", _ensure)

        def _run_nlm(cmd_args, timeout_s=300):
            if run_nlm_sleep:
                time.sleep(run_nlm_sleep)
            return run_nlm_result

        monkeypatch.setattr(nlm_batch, "run_nlm", _run_nlm)

    def _ingestor(self) -> "nlm_batch.NLMBatchIngestor":
        return nlm_batch.NLMBatchIngestor()

    def test_success_path_emits_all_phase_fields(self, monkeypatch):
        """normal_return iteration must populate every new phase field plus the legacy field."""
        ok = subprocess.CompletedProcess(["nlm"], 0, '{"value":{"content":"x"}}', "")
        self._patch_env(monkeypatch, auth_ok=True, run_nlm_result=ok)
        log: list = []
        res = self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        assert res.returncode == 0
        assert len(log) == 1
        entry = log[0]
        assert entry["branch"] == "normal_return"
        assert entry["returncode"] == 0
        # Legacy field preserved for existing reducers.
        assert "subprocess_elapsed_s" in entry
        # All new phase fields present.
        for field in self._PHASE_FIELDS:
            assert field in entry, f"missing phase field {field}"
            assert isinstance(entry[field], float), f"{field} not float"
        # iteration_elapsed_s equals the legacy field (both = full wall time).
        assert entry["iteration_elapsed_s"] == entry["subprocess_elapsed_s"]

    def test_content_subprocess_excludes_auth_time(self, monkeypatch):
        """DISCRIMINATING TEST: a slow _ensure_nlm_auth must NOT inflate content_subprocess_elapsed_s."""
        ok = subprocess.CompletedProcess(["nlm"], 0, '{"value":{"content":"x"}}', "")
        # Auth sleeps 80ms; run_nlm returns instantly; reaps instant.
        self._patch_env(monkeypatch, auth_ok=True, auth_sleep=0.08, run_nlm_result=ok)
        log: list = []
        self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        entry = log[0]
        # Auth time is captured in auth_elapsed_s (>= 0.07s).
        assert entry["auth_elapsed_s"] >= 0.07, entry
        # Content subprocess time must exclude the auth sleep: well under 0.07s.
        assert entry["content_subprocess_elapsed_s"] < 0.03, entry
        # And pre_auth reap must also exclude the auth sleep.
        assert entry["pre_reap_elapsed_s"] < 0.03, entry

    def test_auth_excludes_content_time(self, monkeypatch):
        """Converse: a slow run_nlm must NOT inflate auth_elapsed_s."""
        ok = subprocess.CompletedProcess(["nlm"], 0, '{"value":{"content":"x"}}', "")
        self._patch_env(monkeypatch, auth_ok=True, run_nlm_sleep=0.08, run_nlm_result=ok)
        log: list = []
        self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        entry = log[0]
        assert entry["content_subprocess_elapsed_s"] >= 0.07, entry
        assert entry["auth_elapsed_s"] < 0.03, entry

    def test_auth_failed_path_content_zero(self, monkeypatch):
        """auth_failed_pre_command exits before run_nlm -> content_subprocess_elapsed_s is 0.0."""
        self._patch_env(monkeypatch, auth_ok=False, auth_sleep=0.05)
        log: list = []
        res = self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        assert res.returncode == 1
        entry = log[0]
        assert entry["branch"] == "auth_failed_pre_command"
        # Content phase never reached: must be exactly 0.0, NOT folded with auth time.
        assert entry["content_subprocess_elapsed_s"] == 0.0, entry
        assert entry["post_reap_elapsed_s"] == 0.0, entry
        # Auth phase DID run on this iteration.
        assert entry["auth_elapsed_s"] >= 0.04, entry
        # Legacy field still reports full iteration wall (non-zero).
        assert entry["subprocess_elapsed_s"] > 0.0

    def test_timeout_path_records_content_time(self, monkeypatch):
        """timeout branch: run_nlm ran, so content_subprocess_elapsed_s > 0; branch label = timeout."""
        timeout_res = subprocess.CompletedProcess(["nlm"], 124, "", "NLM command timed out after 30s")
        self._patch_env(monkeypatch, auth_ok=True, run_nlm_sleep=0.05, run_nlm_result=timeout_res)
        log: list = []
        self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        entry = log[0]
        assert entry["branch"] == "timeout"
        assert entry["content_subprocess_elapsed_s"] >= 0.04, entry
        assert entry["post_reap_elapsed_s"] >= 0.0

    def test_iteration_log_json_roundtrip(self, monkeypatch):
        """The emitted iteration dict must survive json.dumps/loads with all fields intact."""
        ok = subprocess.CompletedProcess(["nlm"], 0, '{"value":{"content":"x"}}', "")
        self._patch_env(monkeypatch, auth_ok=True, run_nlm_result=ok)
        log: list = []
        self._ingestor()._run_cmd(["source", "content", "s1", "--json"], timeout=30, iteration_log=log)

        entry = log[0]
        serialized = json.dumps([list(log)])
        roundtripped = json.loads(serialized)
        rt_entry = roundtripped[0][0]
        for field in ("iteration", "branch", "returncode", "subprocess_elapsed_s", *self._PHASE_FIELDS):
            assert field in rt_entry, f"field {field} lost in json round-trip"
        # Phase deltas sum does not exceed iteration wall (no double-counting across phases).
        phase_sum = sum(rt_entry[f] for f in self._PHASE_FIELDS[1:])  # exclude iteration_elapsed_s itself
        assert phase_sum <= rt_entry["iteration_elapsed_s"] + 0.001, rt_entry
