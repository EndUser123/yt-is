"""Tests for fetch timing logs in bin/csf-source."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import types
from concurrent.futures import Future
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _canonical_account_for_source_tests(monkeypatch):
    """Unit tests exercise dispatch after a mocked canonical auth preflight."""
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")


def _load_csf_source_module(*, stub_ensure_auth: bool = False):
    """Load the extensionless bin/csf-source script as a module."""
    path = _REPO_ROOT / "bin" / "csf-source"
    loader = SourceFileLoader("csf_source_timing_test", str(path))
    spec = spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load csf-source")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    # Keep source-dispatch tests from launching a real NotebookLM worker. The
    # production boundary uses Popen so descendants can be killed on timeout;
    # the test proxy returns a realistic worker receipt while leaving this
    # test module's real subprocess module untouched.
    real_subprocess = module.subprocess

    class FakeWorkerProcess:
        pid = 12345
        returncode = 0

        def __init__(self, command):
            self.command = command

        def communicate(self, timeout=None):
            input_path = Path(self.command[self.command.index("--input") + 1])
            result_path = Path(self.command[self.command.index("--result-path") + 1])
            batches = json.loads(input_path.read_text(encoding="utf-8"))
            video_count = sum(len(batch) for batch in batches)
            runner = module.subprocess.run
            completed = runner(
                self.command,
                capture_output=True,
                text=True,
                env=None,
            )
            if not result_path.exists():
                result_path.write_text(
                    json.dumps(
                        {
                            "batch_count": len(batches),
                            "video_count": video_count,
                            "succeeded": video_count,
                            "failed": 0,
                            "elapsed_s": 0.01,
                            "content_fetch_status_counts_total": {"ready": video_count},
                        }
                    ),
                    encoding="utf-8",
                )
            return completed.stdout or "", completed.stderr or ""

        def kill(self):
            return None

    def fake_popen(command, **_kwargs):
        return FakeWorkerProcess(command)

    def default_run(command, **_kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    module.subprocess = types.SimpleNamespace(
        PIPE=real_subprocess.PIPE,
        DEVNULL=real_subprocess.DEVNULL,
        TimeoutExpired=real_subprocess.TimeoutExpired,
        CREATE_NEW_PROCESS_GROUP=getattr(real_subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        run=default_run,
        Popen=fake_popen,
    )

    def fake_ensure_account_session(account_profile, *, worker_id="coordinator", allow_bootstrap=False):
        assert allow_bootstrap is False
        identity = {
            "a.hominidae": (
                "a.hominidae@gmail.com",
                "P:/.data/yt-is/nlm-auth/storage_state.json",
            ),
            "troup.hominidae": (
                "troup.hominidae@gmail.com",
                "P:/.data/yt-is/nlm-auth/storage_state_troup_hominidae.json",
            ),
            "brsthomson": (
                "brsthomson@hotmail.com",
                "P:/.data/yt-is/nlm-auth/storage_state_brsthomson.json",
            ),
        }[account_profile]
        return types.SimpleNamespace(
            account_profile=account_profile,
            worker_id=worker_id,
            expected_email=identity[0],
            observed_email=identity[0],
            storage_path=identity[1],
            ok=True,
            reason="ok",
        )

    module.ensure_account_session = fake_ensure_account_session
    del stub_ensure_auth  # retained so older test callers remain source-compatible
    return module


def test_industrial_worker_env_uses_lane_profile_prefix(tmp_path, monkeypatch):
    """Industrial workers should get lane-specific CLI profile names."""
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-pro")
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX", "ytis-pro-worker")

    worker = mod._build_industrial_worker_launch(worker_id=3, worker_batches=[["vid001"]])

    assert worker["worker_label"] == "worker-03"
    assert worker["state_path"] == str(tmp_path / "states" / "worker-03.json")
    assert worker["notebook_title"] == "benchmark-shard-pro-03"
    assert worker["notebooklm_profile"] == "ytis-pro-worker-03"
    assert worker["env"]["YTIS_NLM_OWNER_STATE_PATH"] == worker["state_path"]
    assert worker["env"]["YTIS_NLM_OWNER_NOTEBOOK_TITLE"] == "benchmark-shard-pro-03"
    assert worker["env"]["NOTEBOOKLM_PROFILE"] == "ytis-pro-worker-03"


def test_industrial_run_id_preserves_coordinator_identity(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.delenv("YTIS_INDUSTRIAL_RUN_ID", raising=False)
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", "coordinator-run")
    assert mod._resolve_industrial_run_id() == "coordinator-run"


def test_industrial_run_id_prefers_explicit_industrial_identity(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "industrial-run")
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", "coordinator-run")
    assert mod._resolve_industrial_run_id() == "industrial-run"


def test_industrial_worker_launch_propagates_account_profile(tmp_path, monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "adaptive-free")
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX", "ytis-free-worker")
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "troup.hominidae")

    worker = mod._build_industrial_worker_launch(worker_id=2, worker_batches=[["vid001"]])

    assert worker["notebooklm_profile"] == "ytis-free-worker-02"
    assert worker["account_profile"] == "troup.hominidae"
    assert worker["env"]["YTIS_NLM_ACCOUNT_PROFILE"] == "troup.hominidae"


def test_industrial_worker_launch_contract_survives_real_subprocess(tmp_path, monkeypatch):
    """The exact worker environment must cross a real process boundary."""
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "adaptive-a-hominidae")
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX", "a.hominidae-worker")

    worker = mod._build_industrial_worker_launch(worker_id=2, worker_batches=[["aaaaaaaaaaa"]])
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
                "['YTIS_NLM_ACCOUNT_PROFILE','NOTEBOOKLM_PROFILE',"
                "'YTIS_NLM_WORKER_ID','YTIS_NLM_OWNER_STATE_PATH',"
                "'YTIS_NLM_OWNER_NOTEBOOK_TITLE']}))"
            ),
        ],
        capture_output=True,
        text=True,
        env=worker["env"],
        check=True,
    )

    assert json.loads(probe.stdout) == {
        "YTIS_NLM_ACCOUNT_PROFILE": "a.hominidae",
        "NOTEBOOKLM_PROFILE": "a.hominidae-worker-02",
        "YTIS_NLM_WORKER_ID": "worker-02",
        "YTIS_NLM_OWNER_STATE_PATH": worker["state_path"],
        "YTIS_NLM_OWNER_NOTEBOOK_TITLE": "adaptive-a-hominidae-02",
    }


def test_video_manifest_dry_run_selects_exact_ids_without_channel_scan(tmp_path, monkeypatch):
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "selection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "exact-selection",
                "videos": [
                    {"video_id": "aaaaaaaaaaa"},
                    {"video_id": "bbbbbbbbbbb"},
                    {"video_id": "ccccccccccc"},
                ],
            }
        ),
        encoding="utf-8",
    )
    events = []
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda ids: [
        {"video_id": "aaaaaaaaaaa", "status": "pending", "source": "source-a", "has_captions": True},
        {"video_id": "bbbbbbbbbbb", "status": "complete", "source": "source-b"},
    ])
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)

    mod.cmd_fetch(video_manifest=manifest_path, dry_run=True)

    selection_events = [payload for name, payload in events if name == "fetch_manifest_selection"]
    assert len(selection_events) == 1
    assert selection_events[0]["selected_count"] == 1
    assert selection_events[0]["missing_count"] == 1
    assert selection_events[0]["non_pending_count"] == 1
    completed = [payload for name, payload in events if name == "fetch_completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == "dry_run"
    assert completed[0]["selection_mode"] == "video_manifest"
    assert completed[0]["channels_active_total"] == 0
    assert completed[0]["selection_fingerprint"].startswith("sha256:")
    assert completed[0]["strategy"] == "industrial_cli_batch"
    assert completed[0]["backend"] == "notebooklm_cli_batch"


def test_account_scoped_small_manifest_uses_industrial_client_not_surgical_scraper(tmp_path, monkeypatch):
    """Small account partitions must not reach the legacy profile-based scraper."""
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "selection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "small-account-selection",
                "videos": [{"video_id": "aaaaaaaaaaa"}],
            }
        ),
        encoding="utf-8",
    )
    events = []
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(
        mod,
        "get_entries_for_video_ids_details",
        lambda ids: [
            {
                "video_id": "aaaaaaaaaaa",
                "status": "pending",
                "source": "source-a",
                "has_captions": True,
                "privacy_status": "public",
                "upload_status": "uploaded",
                "is_live_content": False,
                "unavailable_reason": None,
            }
        ],
    )
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(
        mod,
        "process_industrial_batch_reusable",
        lambda video_ids: {video_id: (True, "direct transcript", None) for video_id in video_ids},
    )
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)
    monkeypatch.setattr(mod, "set_cached_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "mark_complete", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "csf.transcript.fetch_transcript_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("account-scoped manifest reached the surgical transcript chain")
        ),
    )

    mod.cmd_fetch(video_manifest=manifest_path, dry_run=False, workers=1, max_items=1)

    strategy = next(payload for name, payload in events if name == "fetch_strategy_selected")
    completed = next(payload for name, payload in events if name == "fetch_completed")
    assert strategy["industrial_selected"] is True
    assert strategy["use_industrial"] is True
    assert completed["success_count"] == 1


def test_fallback_only_manifest_bypasses_notebooklm_and_records_route(tmp_path, monkeypatch):
    """The explicit recovery route must never enqueue an exact item for NLM."""
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "fallback-only-selection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "fallback-only-selection",
                "videos": [{"video_id": "aaaaaaaaaaa"}],
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "video_id": "aaaaaaaaaaa",
        "status": "pending",
        "source": "source-a",
        "video_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "has_captions": None,
        "privacy_status": "public",
        "upload_status": "uploaded",
        "is_live_content": False,
        "unavailable_reason": None,
    }
    events = []
    transcript_result = mock.Mock(
        transcript="fallback transcript",
        lang="en",
        source="ytdlp",
        view_count=None,
        like_count=None,
        comment_count=None,
        duration=None,
        video_title=None,
        video_description=None,
        error=None,
        failure_reason=None,
        last_stage=None,
    )
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda ids: [entry])
    monkeypatch.setattr(mod, "get_entries_for_source_details", lambda source: [entry])
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S", "0")
    queue_path = tmp_path / "fallback-queue.sqlite"
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED", "1")
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH", str(queue_path))
    monkeypatch.setattr(mod, "process_industrial_batch_reusable", mock.Mock())
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)
    monkeypatch.setattr(mod, "set_cached_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "mark_complete", lambda *args, **kwargs: None)
    with mock.patch("csf.transcript.fetch_transcript_chain", return_value=transcript_result) as mock_fetch:
        mod.cmd_fetch(
            video_manifest=manifest_path,
            fallback_only=True,
            dry_run=False,
            workers=1,
            max_items=1,
        )

    mod.process_industrial_batch_reusable.assert_not_called()
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args.kwargs["skip_notebooklm"] is True
    assert mock_fetch.call_args.kwargs["skip_oembed"] is True
    invoked = next(payload for name, payload in events if name == "fetch_invoked")
    completed = next(payload for name, payload in events if name == "fetch_completed")
    assert invoked["fallback_only"] is True
    assert completed["fallback_only"] is True
    assert completed["notebooklm_pending_count"] == 0
    assert completed["success_count"] == 1
    with sqlite3.connect(queue_path) as conn:
        assert conn.execute(
            "SELECT state FROM durable_fallback_queue WHERE video_id=?",
            ("aaaaaaaaaaa",),
        ).fetchone() == ("completed",)


def test_fallback_only_failure_persists_reason_and_stage(tmp_path, monkeypatch):
    """Surgical fallback failures must remain classifiable after the run."""
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "fallback-only-failure-selection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "fallback-only-failure-selection",
                "videos": [{"video_id": "bbbbbbbbbbb"}],
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "video_id": "bbbbbbbbbbb",
        "status": "pending",
        "source": "source-b",
        "video_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "has_captions": None,
        "privacy_status": "public",
        "upload_status": "uploaded",
        "is_live_content": False,
        "unavailable_reason": None,
    }
    transcript_result = mock.Mock(
        transcript="",
        lang="en",
        source="none",
        view_count=None,
        like_count=None,
        comment_count=None,
        duration=None,
        video_title=None,
        video_description=None,
        error="audio download failed",
        failure_reason="unknown",
        last_stage="whisper",
    )
    persisted = []
    monkeypatch.setattr(mod, "log_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda ids: [entry])
    monkeypatch.setattr(mod, "get_entries_for_source_details", lambda source: [entry])
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(mod, "set_status", lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr(mod, "process_industrial_batch_reusable", mock.Mock())
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)
    with mock.patch("csf.transcript.fetch_transcript_chain", return_value=transcript_result):
        mod.cmd_fetch(
            video_manifest=manifest_path,
            fallback_only=True,
            dry_run=False,
            workers=1,
            max_items=1,
        )

    assert len(persisted) == 1
    args, kwargs = persisted[0]
    assert args == ("bbbbbbbbbbb", "failed")
    assert kwargs["last_stage"] == "whisper"
    assert kwargs["failure_reason"] == "unknown: audio download failed"


def test_fallback_timeout_classification_reaches_db_failure_path(tmp_path, monkeypatch):
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "fallback-timeout-selection.json"
    manifest_path.write_text(
        json.dumps({
            "manifest_version": 1,
            "generated_at": "now",
            "selection_name": "fallback-timeout-selection",
            "videos": [{"video_id": "ccccccccccc"}],
        }),
        encoding="utf-8",
    )
    entry = {
        "video_id": "ccccccccccc",
        "status": "pending",
        "source": "source-c",
        "video_url": "https://www.youtube.com/watch?v=ccccccccccc",
        "has_captions": None,
        "privacy_status": "public",
        "upload_status": "uploaded",
        "is_live_content": False,
        "unavailable_reason": None,
    }
    persisted = []
    result = mock.Mock(
        transcript="", failure_reason="termination_unconfirmed",
        last_stage="transcript_fallback", error="cleanup was not confirmed",
        lang="en", source="none", view_count=None, like_count=None,
        comment_count=None, duration=None, video_title=None, video_description=None,
    )
    monkeypatch.setattr(mod, "log_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda ids: [entry])
    monkeypatch.setattr(mod, "get_entries_for_source_details", lambda source: [entry])
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(mod, "set_status", lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)
    with mock.patch("csf.transcript.fetch_transcript_chain", return_value=result):
        mod.cmd_fetch(video_manifest=manifest_path, fallback_only=True, dry_run=False, workers=1, max_items=1)

    assert persisted == [
        (("ccccccccccc", "failed"), {
            "last_stage": "transcript_fallback",
            "failure_reason": "termination_unconfirmed: cleanup was not confirmed",
        })
    ]


def test_durable_fallback_failure_preserves_admission_provenance(tmp_path, monkeypatch):
    """A recovered fallback item keeps its original source-add failure class."""
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "durable-fallback-provenance-selection.json"
    manifest_path.write_text(
        json.dumps({
            "manifest_version": 1,
            "generated_at": "now",
            "selection_name": "durable-fallback-provenance-selection",
            "videos": [{"video_id": "ddddddddddd"}],
        }),
        encoding="utf-8",
    )
    entry = {
        "video_id": "ddddddddddd",
        "status": "pending",
        "source": "source-d",
        "video_url": "https://www.youtube.com/watch?v=ddddddddddd",
        "has_captions": None,
        "privacy_status": "public",
        "upload_status": "uploaded",
        "is_live_content": False,
        "unavailable_reason": None,
    }
    queue_path = tmp_path / "durable-fallback-provenance.sqlite"
    queue_scope = "durable-fallback-provenance-test"
    original_reason = (
        "Source add failed; materialization terminal error: "
        "SourceAddError (cause=RPCError, rpc_code=9)"
    )
    queue = mod.DurableFallbackQueue(
        queue_path,
        queue_id="industrial-transcript-fallback",
        run_scope=queue_scope,
    )
    queue.enqueue(
        video_id="ddddddddddd",
        source_url=entry["video_url"],
        skip_notebooklm=True,
        failure_reason=original_reason,
        route_version="fallback-v1",
    )
    queue.close()

    transcript_result = mock.Mock(
        transcript="",
        lang="en",
        source="none",
        view_count=None,
        like_count=None,
        comment_count=None,
        duration=None,
        video_title=None,
        video_description=None,
        error="transcript fallback deadline exhausted",
        failure_reason="unknown",
        last_stage="transcript_fallback",
    )
    persisted = []
    monkeypatch.setattr(mod, "log_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda ids: [entry])
    monkeypatch.setattr(mod, "get_entries_for_source_details", lambda source: [entry])
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(mod, "set_status", lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED", "1")
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH", str(queue_path))
    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_QUEUE_SCOPE", queue_scope)
    with mock.patch("csf.transcript.fetch_transcript_chain", return_value=transcript_result):
        mod.cmd_fetch(
            video_manifest=manifest_path,
            fallback_only=True,
            dry_run=False,
            workers=1,
            max_items=1,
        )

    assert persisted[0][1]["failure_reason"].startswith(original_reason)
    with sqlite3.connect(queue_path) as conn:
        row = conn.execute(
            "SELECT state, failure_reason FROM durable_fallback_queue WHERE video_id=?",
            ("ddddddddddd",),
        ).fetchone()
    assert row[0] == "failed"
    assert row[1].startswith(original_reason)


def test_source_add_failure_recovery_admits_only_exact_rows(monkeypatch):
    mod = _load_csf_source_module()
    worker_payload = [
        mod._IndustrialQueuedBatch(
            "batch-1",
            ["source-add-1", "addressability-1", "other-1"],
            {
                "source-add-1": "https://www.youtube.com/watch?v=source-add-1",
                "addressability-1": "https://www.youtube.com/watch?v=addressability-1",
                "other-1": "https://www.youtube.com/watch?v=other-1",
            },
        )
    ]
    rows = [
        {
            "video_id": "source-add-1",
            "status": "failed",
            "failure_reason": "Source add failed: rpc_code=9",
            "source": "https://www.youtube.com/watch?v=source-add-1",
        },
        {
            "video_id": "addressability-1",
            "status": "failed",
            "failure_reason": "SourceNotFoundError: source unavailable",
            "source": "https://www.youtube.com/watch?v=addressability-1",
        },
        {
            "video_id": "other-1",
            "status": "failed",
            "failure_reason": "command_failed",
            "source": "https://www.youtube.com/watch?v=other-1",
        },
    ]
    queued = mod.deque()
    events = []
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda _ids: rows)
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))

    assert mod._queue_confirmed_worker_failures_for_fallback(
        worker_payload,
        fallback_queue=queued,
        source_urls={},
        source_add_only=True,
    ) == 1
    assert list(queued) == [
        ("source-add-1", "https://www.youtube.com/watch?v=source-add-1", True)
    ]
    queued_events = [payload for name, payload in events if name == "industrial_failure_fallback_queued"]
    assert [payload["video_id"] for payload in queued_events] == ["source-add-1"]
    assert queued_events[0]["status_preserved"] == "failed"
    assert queued_events[0]["requeue_skipped"] is True


def test_source_addressability_recovery_admits_only_exact_rows(monkeypatch):
    mod = _load_csf_source_module()
    worker_payload = [
        mod._IndustrialQueuedBatch(
            "batch-1",
            ["addressability-1", "source-add-1"],
            {},
        )
    ]
    rows = [
        {
            "video_id": "addressability-1",
            "status": "failed",
            "failure_reason": "SourceNotFoundError: source unavailable",
            "source": "https://www.youtube.com/watch?v=addressability-1",
        },
        {
            "video_id": "source-add-1",
            "status": "failed",
            "failure_reason": "Source add failed",
            "source": "https://www.youtube.com/watch?v=source-add-1",
        },
    ]
    queued = mod.deque()
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda _ids: rows)

    assert mod._queue_confirmed_worker_failures_for_fallback(
        worker_payload,
        fallback_queue=queued,
        source_urls={
            "addressability-1": "https://www.youtube.com/watch?v=addressability-1",
            "source-add-1": "https://www.youtube.com/watch?v=source-add-1",
        },
        source_addressability_only=True,
    ) == 1
    assert list(queued) == [
        ("addressability-1", "https://www.youtube.com/watch?v=addressability-1", True)
    ]


def test_cleanup_worker_notebooks_requires_delete_for_active_scope(monkeypatch, capsys):
    mod = _load_csf_source_module()
    assert mod.cmd_cleanup_worker_notebooks(delete=False, include_active=True) == 2
    assert "requires --delete" in capsys.readouterr().err


def test_cleanup_worker_notebooks_requires_active_scope_for_current_state(capsys):
    mod = _load_csf_source_module()
    assert mod.cmd_cleanup_worker_notebooks(
        delete=True, include_active=False, only_current_state=True
    ) == 2
    assert "requires --include-active" in capsys.readouterr().err


def test_cleanup_worker_notebooks_returns_cleanup_failure(monkeypatch, capsys):
    mod = _load_csf_source_module()
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **_kwargs: (2, 1))
    assert mod.cmd_cleanup_worker_notebooks(delete=True, include_active=True) == 1
    assert "deleted=2 failed=1" in capsys.readouterr().out


def test_adaptive_manifest_dispatch_has_run_id_after_auth_preflight(tmp_path, monkeypatch):
    """Adaptive dispatch must be reachable without an uninitialized run identity."""
    mod = _load_csf_source_module()
    manifest_path = tmp_path / "selection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "adaptive-preflight-order",
                "videos": [{"video_id": "aaaaaaaaaaa"}],
            }
        ),
        encoding="utf-8",
    )
    events = []
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(
        mod,
        "get_entries_for_video_ids_details",
        lambda ids: [
            {
                "video_id": "aaaaaaaaaaa",
                "status": "pending",
                "source": "source-a",
                "has_captions": True,
                "privacy_status": "public",
                "upload_status": "uploaded",
                "is_live_content": False,
                "unavailable_reason": None,
            }
        ],
    )
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)

    def fake_worker_process(command, **kwargs):
        result_path = Path(command[command.index("--result-path") + 1])
        result_path.write_text(
            json.dumps(
                {
                    "batch_count": 1,
                    "video_count": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "elapsed_s": 0.1,
                    "content_fetch_status_counts_total": {"ready": 1},
                    "failure_reason_counts": {},
                }
            ),
            encoding="utf-8",
        )
        class FailedWorkerProcess:
            pid = 12346
            returncode = 0

            def communicate(self, timeout=None):
                return "", ""

        return FailedWorkerProcess()

    monkeypatch.setattr(mod.subprocess, "Popen", fake_worker_process)

    mod.cmd_fetch(
        video_manifest=manifest_path,
        dry_run=False,
        workers=1,
        max_items=1,
        adaptive_workers=True,
        adaptive_min_workers=1,
        adaptive_max_workers=2,
        adaptive_scale_up_backlog=1,
        adaptive_scale_down_backlog=0,
        adaptive_cooldown_s=0,
        adaptive_health_window=1,
    )

    initialized = next(payload for name, payload in events if name == "adaptive_scheduler_initialized")
    started = next(payload for name, payload in events if name == "adaptive_worker_starting")
    assert initialized["run_id"]
    assert started["run_id"] == initialized["run_id"]
    auth_index = next(index for index, (name, _payload) in enumerate(events) if name == "nlm_auth_storage_probe_ok")
    worker_index = next(index for index, (name, _payload) in enumerate(events) if name == "adaptive_worker_starting")
    assert auth_index < worker_index


def test_adaptive_scheduler_exhaustion_emits_recoverable_failure_receipt(tmp_path, monkeypatch):
    """Queued work must remain attributable when every adaptive slot is quarantined."""
    mod = _load_csf_source_module()
    mod.DEFAULT_NOTEBOOKLM_BATCH_SIZE = 1
    manifest_path = tmp_path / "selection.json"
    video_ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "adaptive-exhaustion",
                "videos": [{"video_id": video_id} for video_id in video_ids],
            }
        ),
        encoding="utf-8",
    )
    events = []
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(mod, "_get_batch_status_storage", lambda: object())
    monkeypatch.setattr(
        mod,
        "get_entries_for_video_ids_details",
        lambda ids: [
            {
                "video_id": video_id,
                "status": "pending",
                "source": "source-a",
                "has_captions": True,
                "privacy_status": "public",
                "upload_status": "uploaded",
                "is_live_content": False,
                "unavailable_reason": None,
            }
            for video_id in ids
        ],
    )
    monkeypatch.setattr(mod, "has_cached_transcript", lambda video_id: False)
    monkeypatch.setattr(mod, "get_negative_cache", lambda video_id: None)
    monkeypatch.setattr(mod, "cleanup_stale_worker_notebooks", lambda **kwargs: (0, 0))
    monkeypatch.setattr(mod, "close_reusable_ingestor", lambda: None)

    # Deterministic exhaustion: the first three worker invocations write a
    # (failing) result — their videos get attributed outcomes — and every
    # later invocation writes NO result, simulating a crashed worker whose
    # queued video must remain attributable via the recoverable receipt.
    # Without the call cap this scenario races queue-drain against slot
    # quarantine and flakes under machine load.
    popen_calls = {"n": 0}

    def fake_worker_process(command, **kwargs):
        popen_calls["n"] += 1
        result_path = Path(command[command.index("--result-path") + 1])
        if popen_calls["n"] <= 3:
            result_path.write_text(
                json.dumps(
                    {
                        "batch_count": 1,
                        "video_count": 1,
                        "succeeded": 0,
                        "failed": 1,
                        "elapsed_s": 0.1,
                        "content_fetch_status_counts_total": {"source_add_failed": 1},
                        "failure_reason_counts": {"source_add_failed": 1},
                    }
                ),
                encoding="utf-8",
            )
        class FailedWorkerProcess:
            pid = 12346
            returncode = 0 if popen_calls["n"] <= 3 else 1

            def communicate(self, timeout=None):
                return "", ""

        return FailedWorkerProcess()

    monkeypatch.setattr(mod.subprocess, "Popen", fake_worker_process)

    mod.cmd_fetch(
        video_manifest=manifest_path,
        dry_run=False,
        workers=2,
        max_items=4,
        adaptive_workers=True,
        adaptive_min_workers=1,
        adaptive_max_workers=2,
        adaptive_scale_up_backlog=1,
        adaptive_scale_down_backlog=0,
        adaptive_cooldown_s=0,
        adaptive_health_window=1,
    )

    completed = next(payload for name, payload in events if name == "fetch_completed")
    assert completed["status"] == "partial"
    assert completed["processed_count"] < completed["pending_total"]
    assert completed["unprocessed_count"] == 1
    assert completed["failure_reason"] == "unprocessed_outcomes:1"
    assert any(name == "adaptive_worker_recovered" for name, _payload in events)


def test_coordinator_owned_single_worker_uses_isolated_worker_boundary(monkeypatch):
    """Coordinator runs must not use the in-process serial industrial path."""
    mod = _load_csf_source_module()
    monkeypatch.delenv("YTIS_DISABLE_INDUSTRIAL_PARALLEL", raising=False)
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", "run01")

    # Exercise the same predicate used by cmd_fetch.  The existing launch
    # contract tests prove that the isolated boundary receives account state,
    # profile, title, and worker identity.
    assert mod._industrial_parallel_enabled_for_runtime(workers=1) is True

    monkeypatch.delenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", raising=False)
    assert mod._industrial_parallel_enabled_for_runtime(workers=1) is False


def test_cli_passes_adaptive_worker_options_to_fetch_boundary(monkeypatch):
    """The live CLI must preserve adaptive options at the cmd_fetch boundary."""
    mod = _load_csf_source_module()
    calls = []
    monkeypatch.setattr(mod, "cmd_fetch", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        mod.sys,
        "argv",
        [
            "csf-source",
            "fetch",
            "--dry-run",
            "--workers",
            "2",
            "--adaptive-workers",
            "--adaptive-min-workers",
            "1",
            "--adaptive-max-workers",
            "4",
            "--adaptive-scale-up-backlog",
            "3",
            "--adaptive-scale-down-backlog",
            "0",
            "--adaptive-cooldown-s",
            "12.5",
            "--adaptive-health-window",
            "3",
            "--limit",
            "5",
        ],
    )

    mod.main()

    assert len(calls) == 1
    assert calls[0]["workers"] == 2
    assert calls[0]["adaptive_workers"] is True
    assert calls[0]["adaptive_min_workers"] == 1
    assert calls[0]["adaptive_max_workers"] == 4
    assert calls[0]["adaptive_scale_up_backlog"] == 3
    assert calls[0]["adaptive_scale_down_backlog"] == 0
    assert calls[0]["adaptive_cooldown_s"] == 12.5
    assert calls[0]["adaptive_health_window"] == 3


def test_account_preflight_fixture_preserves_named_identity_mapping():
    """Test auth fixtures must distinguish all canonical account identities."""
    mod = _load_csf_source_module()

    troup = mod.ensure_account_session("troup.hominidae", worker_id="coordinator")
    brsthomson = mod.ensure_account_session("brsthomson", worker_id="coordinator")

    assert troup.expected_email == "troup.hominidae@gmail.com"
    assert troup.storage_path.endswith("storage_state_troup_hominidae.json")
    assert brsthomson.expected_email == "brsthomson@hotmail.com"
    assert brsthomson.storage_path.endswith("storage_state_brsthomson.json")


def test_adaptive_launch_identity_pool_is_unique_and_can_scale():
    """Launch-built worker identities must feed the real scheduler policy."""
    mod = _load_csf_source_module()
    identities = []
    for worker_id in range(1, 5):
        launch = mod._build_industrial_worker_launch(
            worker_id=worker_id,
            worker_batches=[],
            create_state_root=False,
        )
        identities.append(
            mod.WorkerIdentity(
                worker_id,
                str(launch["notebooklm_profile"]),
                str(launch["notebook_title"]),
                str(launch["state_path"]),
                f"{launch['notebooklm_profile']}::{launch['notebook_title']}",
                str(launch["account_profile"]),
            )
        )

    scheduler = mod.AdaptiveWorkerScheduler(
        tuple(identities),
        mod.SchedulerConfig(2, 4, min_workers=1, cooldown_s=0, health_window=2),
    )
    decision = scheduler.choose(
        mod.SchedulerSnapshot(
            1.0,
            4,
            active_worker_ids=frozenset({1, 2}),
            health_samples=(mod.HealthSample(1, True), mod.HealthSample(1, True)),
        )
    )

    assert len({item.profile for item in identities}) == 4
    assert len({item.notebook_title for item in identities}) == 4
    assert len({item.state_path for item in identities}) == 4
    assert decision.target_workers == 3
    scheduler.apply(decision, now_s=1.0)
    assert scheduler.target_workers == 3


def test_industrial_worker_env_can_use_explicit_lane_profiles(tmp_path, monkeypatch):
    """Industrial workers can bind to exact CLI auth profiles when names are not prefix-derived."""
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "benchmark-shard-pro")
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES", "alt,ytis-pro-worker-02")

    worker = mod._build_industrial_worker_launch(worker_id=1, worker_batches=[["vid001"]])

    assert worker["worker_label"] == "worker-01"
    assert worker["notebook_title"] == "benchmark-shard-pro-01"
    assert worker["notebooklm_profile"] == "alt"
    assert worker["env"]["NOTEBOOKLM_PROFILE"] == "alt"


def test_industrial_worker_default_notebook_title_uses_profile_prefix(tmp_path, monkeypatch):
    """Default worker notebook titles should not collide across auth lanes."""
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.delenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", raising=False)
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX", "ytis-pro-worker")

    worker = mod._build_industrial_worker_launch(worker_id=1, worker_batches=[["vid001"]])

    assert worker["notebook_title"] == "ytis-pro-worker-01"
    assert worker["env"]["YTIS_NLM_OWNER_NOTEBOOK_TITLE"] == "ytis-pro-worker-01"


def test_industrial_worker_default_notebook_title_uses_explicit_profile(tmp_path, monkeypatch):
    """Explicit auth profile lists should also produce distinct visible notebook names."""
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", str(tmp_path / "states"))
    monkeypatch.delenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", raising=False)
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES", "ytis-free2-worker-01,ytis-free2-worker-02")

    worker = mod._build_industrial_worker_launch(worker_id=2, worker_batches=[["vid001"]])

    assert worker["notebook_title"] == "ytis-free2-worker-02"
    assert worker["env"]["YTIS_NLM_OWNER_NOTEBOOK_TITLE"] == "ytis-free2-worker-02"


def test_industrial_worker_timeout_is_finite_for_coordinator_owned_runs(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.delenv("YTIS_INDUSTRIAL_WORKER_TIMEOUT_S", raising=False)
    monkeypatch.setenv("YTIS_COORDINATOR_RUN_ID", "run-123")

    assert mod._industrial_worker_timeout_s() == 4 * 60 * 60

    monkeypatch.delenv("YTIS_COORDINATOR_RUN_ID", raising=False)
    monkeypatch.setenv("YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID", "run-456")
    assert mod._industrial_worker_timeout_s() == 4 * 60 * 60


def test_industrial_worker_timeout_stays_unbounded_for_standalone_runs(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.delenv("YTIS_INDUSTRIAL_WORKER_TIMEOUT_S", raising=False)
    monkeypatch.delenv("YTIS_COORDINATOR_RUN_ID", raising=False)

    assert mod._industrial_worker_timeout_s() is None


def test_industrial_worker_timeout_accepts_explicit_positive_override(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.setenv("YTIS_INDUSTRIAL_WORKER_TIMEOUT_S", "12.5")

    assert mod._industrial_worker_timeout_s() == 12.5


def test_transcript_fallback_timeout_is_finite_for_coordinator_and_validates_config(monkeypatch):
    mod = _load_csf_source_module()
    monkeypatch.delenv("YTIS_TRANSCRIPT_FALLBACK_TIMEOUT_S", raising=False)
    monkeypatch.setenv("YTIS_COORDINATOR_RUN_ID", "run-123")
    assert mod._transcript_fallback_timeout_s() == 30 * 60

    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_TIMEOUT_S", "12.5")
    assert mod._transcript_fallback_timeout_s() == 12.5

    monkeypatch.setenv("YTIS_TRANSCRIPT_FALLBACK_TIMEOUT_S", "nan")
    assert mod._transcript_fallback_timeout_s() == 30 * 60


def test_transcript_fallback_timeout_terminates_owned_process_and_classifies_timeout(monkeypatch):
    mod = _load_csf_source_module()
    calls: list[tuple[str, object]] = []

    class HangingProcess:
        pid = 9876
        returncode = None
        stdout = None
        stderr = None

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if len(calls) == 1:
                raise mod.subprocess.TimeoutExpired("worker", timeout)
            return "", ""

        def wait(self, timeout=None):
            self.returncode = -9
            return self.returncode

        def kill(self):
            calls.append(("kill", self.pid))

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: HangingProcess())
    monkeypatch.setattr(
        mod,
        "_terminate_process_tree_pid",
        lambda pid: calls.append(("terminate", pid)),
    )
    result = mod._run_transcript_fallback_subprocess(
        "dQw4w9WgXcQ", True, True, None, 3
    )

    assert result.failure_reason == "timeout"
    assert result.last_stage == "transcript_fallback"
    assert ("terminate", 9876) in calls
    assert calls[0] == ("communicate", 3)


def test_transcript_fallback_timeout_marks_termination_unconfirmed(monkeypatch):
    mod = _load_csf_source_module()
    calls: list[tuple[str, object]] = []

    class Pipe:
        def __init__(self, name):
            self.name = name

        def close(self):
            calls.append(("close", self.name))

    class UnreapedProcess:
        pid = 9878
        returncode = None
        stdout = Pipe("stdout")
        stderr = Pipe("stderr")

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            raise mod.subprocess.TimeoutExpired("worker", timeout)

        def kill(self):
            calls.append(("kill", self.pid))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            raise mod.subprocess.TimeoutExpired("worker", timeout)

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: UnreapedProcess())
    monkeypatch.setattr(mod, "_terminate_process_tree_pid", lambda pid: calls.append(("terminate", pid)))
    result = mod._run_transcript_fallback_subprocess("dQw4w9WgXcQ", True, True, None, 3)

    assert result.failure_reason == "termination_unconfirmed"
    assert "termination=termination_unconfirmed" in result.error
    assert calls == [
        ("communicate", 3),
        ("terminate", 9878),
        ("communicate", 30),
        ("kill", 9878),
        ("wait", 1),
        ("close", "stdout"),
        ("close", "stderr"),
    ]


def test_transcript_fallback_subprocess_success_preserves_result(monkeypatch):
    mod = _load_csf_source_module()

    class CompletedProcess:
        pid = 9877
        returncode = 0

        def __init__(self, command):
            self.command = command

        def communicate(self, timeout=None):
            result_path = Path(self.command[self.command.index("--result-path") + 1])
            result_path.write_text(
                json.dumps({
                    "video_id": "dQw4w9WgXcQ", "lang": "en", "raw_lang": "en",
                    "was_translated": False, "transcript": "unchanged",
                    "source": "ytdlp", "source_stage": 1, "detected_lang": "en",
                    "error": None, "last_stage": None, "failure_reason": None,
                }),
                encoding="utf-8",
            )
            return "", ""

    monkeypatch.setattr(mod.subprocess, "Popen", lambda command, **kwargs: CompletedProcess(command))
    result = mod._run_transcript_fallback_subprocess(
        "dQw4w9WgXcQ", False, False, {"duration": 12}, 3
    )

    assert result.transcript == "unchanged"
    assert result.source == "ytdlp"


def test_transcript_fallback_subprocess_passes_leading_hyphen_video_id_as_attached_option(monkeypatch):
    """YouTube IDs beginning with '-' must not be parsed as CLI options."""
    mod = _load_csf_source_module()
    captured: dict[str, list[str]] = {}

    class CompletedProcess:
        pid = 9880
        returncode = 0

        def __init__(self, command):
            self.command = command
            captured["command"] = command

        def communicate(self, timeout=None):
            result_path = Path(self.command[self.command.index("--result-path") + 1])
            result_path.write_text(
                json.dumps({
                    "video_id": "-nJIgUTc4N8", "lang": "en", "raw_lang": "en",
                    "was_translated": False, "transcript": "leading hyphen works",
                    "source": "ytdlp", "source_stage": 1, "detected_lang": "en",
                    "error": None, "last_stage": None, "failure_reason": None,
                }),
                encoding="utf-8",
            )
            return "", ""

    monkeypatch.setattr(mod.subprocess, "Popen", lambda command, **kwargs: CompletedProcess(command))
    result = mod._run_transcript_fallback_subprocess(
        "-nJIgUTc4N8", False, False, None, 3
    )

    assert result.transcript == "leading hyphen works"
    assert "--video-id=-nJIgUTc4N8" in captured["command"]
    deadline_index = captured["command"].index("--deadline-s")
    assert float(captured["command"][deadline_index + 1]) == 0.1


def test_transcript_fallback_empty_worker_result_preserves_process_diagnostics(monkeypatch):
    """A crashed child must be diagnosable even when its precreated file is empty."""
    mod = _load_csf_source_module()

    class CrashedProcess:
        pid = 9879
        returncode = 1

        def communicate(self, timeout=None):
            return "", "worker traceback"

    monkeypatch.setattr(mod.subprocess, "Popen", lambda command, **kwargs: CrashedProcess())
    result = mod._run_transcript_fallback_subprocess(
        "dQw4w9WgXcQ", True, True, None, 3
    )

    assert result.failure_reason == "unknown"
    assert "invalid transcript worker result (1)" in result.error
    assert "JSONDecodeError" in result.error
    assert "worker traceback" in result.error


def test_cmd_fetch_logs_terminal_failure_when_auth_guard_aborts():
    """cmd_fetch should emit a terminal record when canonical preflight fails."""
    mod = _load_csf_source_module()
    channel_rows = [("https://www.youtube.com/@example", "pl-1")]
    pending_entries = [
        {
            "video_id": "dQw4w9WgXcQ",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(
                            mod,
                            "ensure_account_session",
                            return_value=types.SimpleNamespace(
                                account_profile="a.hominidae",
                                worker_id="coordinator",
                                expected_email="a.hominidae@gmail.com",
                                storage_path="P:/.data/yt-is/nlm-auth/storage_state.json",
                                ok=False,
                                reason="expired_session",
                            ),
                        ):
                            with mock.patch.object(mod, "log_action") as mock_log:
                                with pytest.raises(SystemExit):
                                    mod.cmd_fetch(source_filter="https://www.youtube.com/@example", dry_run=False, workers=1)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_completed" in log_names
    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_completed")
    assert completed["status"] == "failed"
    assert completed["failure_reason"] == "canonical_auth_preflight:expired_session"


def test_dispatch_queue_reuses_stable_batch_id_after_requeue():
    """A requeued queue item keeps its logical identity across dispatch attempts."""
    mod = _load_csf_source_module()
    queued = mod._IndustrialQueuedBatch("input-batch-a", ["aaaaaaaaaaa"])
    queue = [queued]

    first_dispatch = mod._take_industrial_dispatch_groups(queue, 1, 1)
    assert first_dispatch[0][0] is queued

    queue[0:0] = first_dispatch[0]
    second_dispatch = mod._take_industrial_dispatch_groups(queue, 1, 1)
    assert second_dispatch[0][0] is queued
    assert second_dispatch[0][0].batch_id == "input-batch-a"


def test_cmd_fetch_logs_fetch_start_and_first_download_started_industrial():
    """cmd_fetch logs a run-start marker and a first-download marker for industrial backlogs."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
        for i in range(200)
    ]

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                            with mock.patch.object(mod.subprocess, "run") as mock_run:
                                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                with mock.patch.object(
                                    mod,
                                    "process_industrial_batch_reusable",
                                    return_value={entry["video_id"]: (True, "transcript", None) for entry in pending_entries},
                                ):
                                    with mock.patch.object(mod, "close_reusable_ingestor"):
                                        with mock.patch.object(mod, "set_cached_transcript"):
                                            with mock.patch.object(mod, "mark_complete"):
                                                with mock.patch.object(mod, "log_action") as mock_log:
                                                    mod.cmd_fetch(
                                                        source_filter="https://www.youtube.com/@example",
                                                        dry_run=False,
                                                        workers=1,
                                                    )

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert log_names[0] == "fetch_invoked"
    assert "fetch_strategy_selected" in log_names
    assert "fetch_scan_started" in log_names
    assert "fetch_scan_completed" in log_names
    assert "first_download_started" in log_names
    assert "fetch_completed" in log_names
    assert log_names.index("fetch_scan_started") < log_names.index("fetch_scan_completed")
    assert log_names.index("fetch_scan_completed") < log_names.index("first_download_started")
    first_payload = mock_log.call_args_list[log_names.index("first_download_started")].args[1]
    assert first_payload["kind"] == "industrial_cli_batch"
    assert first_payload["batch_index"] == 1
    assert first_payload["batch_size"] == 50
    assert first_payload["first_video_id"] == "vid000"
    assert "elapsed_s" in first_payload


def test_cmd_fetch_emits_elapsed_scan_status_heartbeat():
    """Long scans should emit a time-based scan status heartbeat, not only channel checkpoints."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [
        ("https://www.youtube.com/@chan1", "pl-1"),
        ("https://www.youtube.com/@chan2", "pl-2"),
        ("https://www.youtube.com/@chan3", "pl-3"),
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    monotonic_value = {"current": 0.0}

    def fake_monotonic():
        monotonic_value["current"] += 31.0
        return monotonic_value["current"]

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=[]):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.object(mod.subprocess, "run") as mock_run:
                        mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                        with mock.patch.object(mod.time, "monotonic", side_effect=fake_monotonic):
                            with mock.patch.object(mod, "log_action") as mock_log:
                                mod.cmd_fetch(dry_run=False, workers=1)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_scan_started" in log_names
    assert "fetch_scan_completed" in log_names
    assert "fetch_scan_progress" in log_names
    heartbeat_payloads = [
        call.args[1]
        for call in mock_log.call_args_list
        if call.args[0] == "fetch_scan_progress" and call.args[1].get("trigger") == "elapsed_interval"
    ]
    assert heartbeat_payloads, "expected a time-based scan heartbeat"
    assert heartbeat_payloads[0]["channels_active_total"] == 3


def test_cmd_check_all_emits_elapsed_scan_status_heartbeat():
    """/yt-is sync should emit a time-based scan heartbeat while checking channels."""
    mod = _load_csf_source_module()
    channel_rows = [
        ("https://www.youtube.com/@chan1", "pl-1", 0, None),
        ("https://www.youtube.com/@chan2", "pl-2", 0, None),
        ("https://www.youtube.com/@chan3", "pl-3", 0, None),
    ]

    summary_rows = [
        ("https://www.youtube.com/@chan1", 0, None, None),
        ("https://www.youtube.com/@chan2", 0, None, None),
        ("https://www.youtube.com/@chan3", 0, None, None),
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, channel_rows, summary_rows):
            self._channel_rows = channel_rows
            self._summary_rows = summary_rows

        def execute(self, query, *_args, **_kwargs):
            if "ORDER BY last_checked ASC" in query:
                return FakeCursor(self._channel_rows)
            if "ORDER BY CASE WHEN category IS NULL" in query:
                return FakeCursor(self._summary_rows)
            return FakeCursor([])

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, channel_rows, summary_rows):
            self._channel_rows = channel_rows
            self._summary_rows = summary_rows

        def _ensure_channel_metadata(self):
            return None

        def _get_conn(self):
            return FakeConn(self._channel_rows, self._summary_rows)

    monotonic_value = {"current": 0.0}

    def fake_monotonic():
        monotonic_value["current"] += 31.0
        return monotonic_value["current"]

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            fut = Future()
            fut.set_result(fn(*args, **kwargs))
            return fut

    with mock.patch("csf.batch_status._get_batch_status_storage", return_value=FakeStorage(channel_rows, summary_rows)):
        with mock.patch.object(mod, "_process_channel_check", side_effect=[(1, 10), (0, 20), (2, 30)]):
            with mock.patch.object(mod, "get_entries_for_source", return_value=[]):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch("concurrent.futures.ThreadPoolExecutor", FakeExecutor):
                        with mock.patch("concurrent.futures.as_completed", lambda futures: list(futures)):
                            with mock.patch.object(mod.time, "monotonic", side_effect=fake_monotonic):
                                with mock.patch.object(mod, "log_action") as mock_log:
                                    mod.cmd_check_all(verbose=False)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "sync_scan_progress" in log_names
    heartbeat_payloads = [
        call.args[1]
        for call in mock_log.call_args_list
        if call.args[0] == "sync_scan_progress" and call.args[1].get("trigger") == "elapsed_interval"
    ]
    assert heartbeat_payloads, "expected a time-based sync heartbeat"
    assert heartbeat_payloads[0]["channels_total"] == 3


def test_cmd_fetch_limit_caps_selected_pending_items():
    """cmd_fetch should stop after the requested pending-item limit and log it."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@example", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
        for i in range(200)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                            with mock.patch.object(mod.subprocess, "run") as mock_run:
                                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                with mock.patch.object(mod, "process_industrial_batch_reusable") as mock_process:
                                    mock_process.return_value = {
                                        f"vid{i:03d}": (True, "transcript", None) for i in range(100)
                                    }
                                    with mock.patch.object(mod, "close_reusable_ingestor"):
                                        with mock.patch.object(mod, "set_cached_transcript"):
                                            with mock.patch.object(mod, "mark_complete"):
                                                with mock.patch.object(mod, "log_action") as mock_log:
                                                    mod.cmd_fetch(dry_run=False, workers=1, max_items=100)

    invoked = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_invoked")
    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_completed")
    assert invoked["max_items"] == 100
    assert completed["max_items"] == 100
    assert mock_process.call_count == 2
    queued_ids = [call.args[0] for call in mock_process.call_args_list]
    assert [len(batch) for batch in queued_ids] == [50, 50]
    assert queued_ids[0][0] == "vid000"
    assert queued_ids[1][-1] == "vid099"


def test_cmd_fetch_limit_counts_primary_items_when_shared_retry_processes_work():
    """Shared-retry worker work should not consume the requested primary item limit."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@example", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
        for i in range(101)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def submit(self, fn, *args, **kwargs):
            fut = Future()
            fut.set_result(fn(*args, **kwargs))
            return fut

        def shutdown(self, wait=True, **_kwargs):
            return None

    worker_summaries = [
        {"batch_count": 1, "video_count": 50, "succeeded": 50, "failed": 0, "shared_retry_processed_count": 1},
        {"batch_count": 1, "video_count": 50, "succeeded": 50, "failed": 0, "shared_retry_processed_count": 0},
        {"batch_count": 1, "video_count": 1, "succeeded": 1, "failed": 0, "shared_retry_processed_count": 0},
    ]
    subprocess_calls: list[list[str]] = []
    dispatched_batches: list[list[list[str]]] = []

    def fake_run(cmd, **_kwargs):
        subprocess_calls.append(list(cmd))
        input_path = Path(cmd[cmd.index("--input") + 1])
        dispatched_batches.append(json.loads(input_path.read_text(encoding="utf-8")))
        result_path = Path(cmd[cmd.index("--result-path") + 1])
        result_path.write_text(json.dumps(worker_summaries.pop(0)), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                                with mock.patch("concurrent.futures.ThreadPoolExecutor", FakeExecutor):
                                    with mock.patch.object(mod, "set_cached_transcript"):
                                        with mock.patch.object(mod, "mark_complete"):
                                            with mock.patch.object(mod, "log_action") as mock_log:
                                                mod.cmd_fetch(dry_run=False, workers=4, max_items=100)

    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_completed")
    assert completed["pending_total"] == 101
    assert completed["worker_stage_totals"]["shared_retry_processed_count_total"] == 1
    assert len(subprocess_calls) == 3
    selected_ids = [
        video_id
        for worker_batches in dispatched_batches
        for batch in worker_batches
        for video_id in batch
    ]
    assert len(selected_ids) == 101
    assert sorted(selected_ids) == [f"vid{i:03d}" for i in range(101)]


def test_cmd_fetch_limit_waits_for_inflight_shared_retry_before_stopping_scan(monkeypatch):
    """Late worker shared-retry totals should not leave the final primary count short."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    monkeypatch.setenv("YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", "true")
    channel_rows = [("https://www.youtube.com/@example", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
        for i in range(105)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    pending_futures: list[tuple[Future, object, tuple[object, ...], dict[str, object]]] = []

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def submit(self, fn, *args, **kwargs):
            fut = Future()
            pending_futures.append((fut, fn, args, kwargs))
            return fut

        def shutdown(self, wait=True, **_kwargs):
            return None

    def fake_wait(futures, timeout=None, return_when=None):
        if timeout == 0:
            done = {future for future in futures if future.done()}
            return done, set(futures) - done
        for future, fn, args, kwargs in list(pending_futures):
            if future in futures and not future.done():
                future.set_result(fn(*args, **kwargs))
                done = {future}
                return done, set(futures) - done
        done = {future for future in futures if future.done()}
        return done, set(futures) - done

    worker_summaries = [
        {"batch_count": 1, "video_count": 50, "succeeded": 50, "failed": 0, "shared_retry_processed_count": 5},
        {"batch_count": 1, "video_count": 50, "succeeded": 50, "failed": 0, "shared_retry_processed_count": 0},
        {"batch_count": 1, "video_count": 5, "succeeded": 5, "failed": 0, "shared_retry_processed_count": 0},
    ]
    dispatched_batches: list[list[list[str]]] = []

    def fake_run(cmd, **_kwargs):
        input_path = Path(cmd[cmd.index("--input") + 1])
        dispatched_batches.append(json.loads(input_path.read_text(encoding="utf-8")))
        result_path = Path(cmd[cmd.index("--result-path") + 1])
        result_path.write_text(json.dumps(worker_summaries.pop(0)), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("concurrent.futures.wait", fake_wait)
    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                            with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
                                with mock.patch.object(mod, "set_cached_transcript"):
                                    with mock.patch.object(mod, "mark_complete"):
                                        with mock.patch.object(mod, "log_action") as mock_log:
                                            mod.cmd_fetch(dry_run=False, workers=4, max_items=100)

    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_completed")
    assert completed["pending_total"] == 105
    assert completed["worker_stage_totals"]["shared_retry_processed_count_total"] == 5
    selected_ids = [
        video_id
        for worker_batches in dispatched_batches
        for batch in worker_batches
        for video_id in batch
    ]
    assert len(selected_ids) == 105
    assert sorted(selected_ids) == [f"vid{i:03d}" for i in range(105)]


def test_cmd_fetch_logs_cached_sample_and_hit_rate():
    """cmd_fetch should expose the cached backlog sample and hit rate."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": "vid-a",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        },
        {
            "video_id": "vid-b",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        },
        {
            "video_id": "vid-c",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        },
    ]

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(
                        mod,
                        "has_cached_transcript",
                        side_effect=lambda video_id: video_id in {"vid-a", "vid-c"},
                    ):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            with mock.patch.object(mod, "log_action") as mock_log:
                                mod.cmd_fetch(
                                    source_filter="https://www.youtube.com/@example",
                                    dry_run=True,
                                    workers=1,
                                )

    summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_scan_completed")
    triage = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_triage_summary")
    assert summary["cached_total"] == 2
    assert triage["cached_total"] == 2
    assert summary["cached_hit_rate"] == 0.6667
    assert triage["cached_hit_rate"] == 0.6667
    assert summary["cached_sample"] == [
        {"video_id": "vid-a", "source": "https://www.youtube.com/@example", "channel_index": 1},
        {"video_id": "vid-c", "source": "https://www.youtube.com/@example", "channel_index": 1},
    ]
    assert triage["cached_sample"] == summary["cached_sample"]


def test_cmd_fetch_merges_worker_source_profile_totals():
    """cmd_fetch should retain worker-level source profile totals for investigation."""
    mod = _load_csf_source_module(stub_ensure_auth=True)

    totals = mod._empty_source_profile_totals()
    mod._merge_source_profile_totals(
        totals,
        {
            "total": 1,
            "matched": 1,
            "missing": 0,
            "source_class_counts": {"captioned": 1},
            "status_counts": {"pending": 1},
            "privacy_status_counts": {"public": 1},
            "upload_status_counts": {"uploaded": 1},
            "unavailable_reason_counts": {"unknown": 1},
            "failure_reason_counts": {"unknown": 1},
        },
    )
    mod._merge_source_profile_totals(
        totals,
        {
            "total": 2,
            "matched": 2,
            "missing": 0,
            "source_class_counts": {"no_captions": 2},
            "status_counts": {"pending": 2},
            "privacy_status_counts": {"public": 2},
            "upload_status_counts": {"uploaded": 2},
            "unavailable_reason_counts": {"unknown": 2},
            "failure_reason_counts": {"unknown": 2},
        },
    )

    payload = mod._build_fetch_completed_payload(
        source_filter=None,
        strategy="industrial_cli_batch",
        backend="notebooklm_cli_batch",
        backlog_threshold=50,
        batch_size=300,
        workers=4,
        channels_tracked_total=1,
        channels_blocked_total=0,
        channels_active_total=1,
        pending_total=3,
        cached_total=0,
        negative_cache_count=0,
        cached_hit_rate=0.0,
        cached_sample=[],
        negative_cache_reason_counts={},
        negative_cache_sample=[],
        industrial_batches_processed=1,
        transcript_fallback_processed_count=0,
        transcript_fallback_queued_count=0,
        terminal_count=0,
        terminal_reason_counts={},
        worker_cleanup_deleted=0,
        worker_cleanup_failed=0,
        success_count=3,
        fail_count=0,
        skip_count=0,
        processed_count=3,
        elapsed_s=1.0,
        status="completed",
        worker_stage_totals={"batch_elapsed_s_total": 1.0},
        worker_source_profile_totals=totals,
    )

    assert payload["worker_source_profile_totals"]["total"] == 3
    assert payload["worker_source_profile_totals"]["source_class_counts"]["captioned"] == 1
    assert payload["worker_source_profile_totals"]["source_class_counts"]["no_captions"] == 2
    assert payload["industrial_batches_processed"] == 1


def test_cmd_fetch_skips_active_negative_cache_before_routing():
    """cmd_fetch should skip active negative-cache videos before routing them again."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": "vid-good",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        },
        {
            "video_id": "vid-negative",
            "status": "pending",
            "has_captions": False,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        },
    ]

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "get_negative_cache") as mock_negative_cache:
                        mock_negative_cache.side_effect = lambda video_id, db_path=None: (
                            {"video_id": video_id, "reason": "no_transcript", "source": None, "last_stage": "direct_api"}
                            if video_id == "vid-negative"
                            else None
                        )
                        with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                            with mock.patch.object(mod.subprocess, "run") as mock_run:
                                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                with mock.patch.object(mod, "log_action") as mock_log:
                                    mod.cmd_fetch(
                                        source_filter="https://www.youtube.com/@example",
                                        dry_run=True,
                                        workers=1,
                                    )

    triage = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_triage_summary")
    scan = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_scan_completed")
    assert triage["negative_cache_count"] == 1
    assert scan["negative_cache_count"] == 1
    assert triage["notebooklm_pending_count"] == 1
    assert triage["transcript_fallback_processed_count"] == 0
    assert triage["transcript_fallback_queued_count"] == 0


def test_cmd_fetch_uses_transcript_fallback_env_names():
    """cmd_fetch should prefer the new transcript-fallback env names and keep aliases working."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
        for i in range(300)
    ]

    with mock.patch.dict(
        mod.os.environ,
        {
            "YTIS_TRANSCRIPT_FALLBACK_WORKERS": "3",
            "YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S": "7.5",
        },
        clear=False,
    ):
        with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
            with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
                with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                    with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                        with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                            with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                                with mock.patch.object(mod.subprocess, "run") as mock_run:
                                    mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                    with mock.patch.object(
                                        mod,
                                        "process_industrial_batch_reusable",
                                        return_value={entry["video_id"]: (True, "transcript", None) for entry in pending_entries},
                                    ):
                                        with mock.patch.object(mod, "close_reusable_ingestor"):
                                            with mock.patch.object(mod, "set_cached_transcript"):
                                                with mock.patch.object(mod, "mark_complete"):
                                                    with mock.patch.object(mod, "log_action") as mock_log:
                                                        mod.cmd_fetch(
                                                            source_filter="https://www.youtube.com/@example",
                                                            dry_run=False,
                                                            workers=4,
                                                        )

    fetch_invoked = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_invoked")
    assert fetch_invoked["transcript_fallback_workers"] == 3
    assert fetch_invoked["transcript_fallback_min_start_interval_s"] == 7.5


def test_cmd_fetch_defaults_transcript_fallback_workers_to_requested_workers():
    """cmd_fetch should default transcript fallback concurrency to the requested worker count."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": "vid000",
            "status": "pending",
            "has_captions": False,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@example",
        }
    ]

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.dict(
                            mod.os.environ,
                            {
                                "YTIS_TRANSCRIPT_FALLBACK_WORKERS": "",
                                "YTIS_AUDIO_FALLBACK_WORKERS": "",
                                "YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S": "",
                                "YTIS_AUDIO_FALLBACK_MIN_START_INTERVAL_S": "",
                            },
                            clear=False,
                        ):
                            with mock.patch.object(mod.subprocess, "run") as mock_run:
                                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                with mock.patch.object(mod, "log_action") as mock_log:
                                    mod.cmd_fetch(
                                        source_filter="https://www.youtube.com/@example",
                                        dry_run=True,
                                        workers=4,
                                    )

    invoked = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_invoked")
    assert invoked["transcript_fallback_workers"] == 4
    assert invoked["transcript_fallback_min_start_interval_s"] == 2.5


def test_cmd_fetch_logs_preflight_scan_progress_before_downloads():
    """cmd_fetch logs the preflight channel scan before the first download marker."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [(f"https://www.youtube.com/@chan{i:02d}", "pl-1") for i in range(30)]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@chan00",
        }
        for i in range(300)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            with mock.patch.object(
                                mod,
                                "process_industrial_batch_reusable",
                                return_value={entry["video_id"]: (True, "transcript", None) for entry in pending_entries},
                            ):
                                with mock.patch.object(mod, "close_reusable_ingestor"):
                                    with mock.patch.object(mod, "set_cached_transcript"):
                                        with mock.patch.object(mod, "mark_complete"):
                                            with mock.patch.object(mod, "log_action") as mock_log:
                                                mod.cmd_fetch(dry_run=False, workers=4)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert log_names[0] == "fetch_invoked"
    assert "fetch_strategy_selected" in log_names
    assert "fetch_scan_started" in log_names
    assert "fetch_scan_progress" in log_names
    assert "fetch_scan_completed" in log_names
    assert "fetch_worker_dispatch_started" in log_names
    assert "fetch_completed" in log_names
    assert log_names.index("fetch_scan_started") < log_names.index("fetch_scan_completed")
    assert log_names.index("fetch_scan_started") < log_names.index("fetch_worker_dispatch_started")
    assert log_names.index("fetch_worker_dispatch_started") < log_names.index("fetch_scan_completed")


def test_cmd_fetch_starts_industrial_batch_before_scan_completes_when_buffer_is_full():
    """Industrial fetch should begin once the first batch is full, without waiting for the scan to finish."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [
        ("https://www.youtube.com/@chan1", "pl-1"),
        ("https://www.youtube.com/@chan2", "pl-2"),
    ]
    first_channel_pending = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
        }
        for i in range(301)
    ]
    second_channel_pending: list[dict[str, object]] = []

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", side_effect=[first_channel_pending, second_channel_pending]):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                            with mock.patch.object(mod.subprocess, "run") as mock_run:
                                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                                with mock.patch.object(
                                    mod,
                                    "process_industrial_batch_reusable",
                                    return_value={vid: (True, "transcript", None) for vid in [f"vid{i:03d}" for i in range(300)]},
                                ):
                                    with mock.patch.object(mod, "close_reusable_ingestor"):
                                        with mock.patch.object(mod, "set_cached_transcript"):
                                            with mock.patch.object(mod, "mark_complete"):
                                                with mock.patch.object(mod, "log_action") as mock_log:
                                                    mod.cmd_fetch(dry_run=False, workers=4)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_worker_dispatch_state" in log_names
    assert "fetch_worker_dispatch_started" in log_names
    assert "fetch_scan_completed" in log_names
    assert "fetch_completed" in log_names
    state_payload = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_worker_dispatch_state")
    assert state_payload["workers_requested"] == 4
    assert state_payload["queued_batches"] >= 1
    assert state_payload["available_slots"] >= 1
    assert "last_worker_finished_at" in state_payload
    assert log_names.index("fetch_worker_dispatch_started") < log_names.index("fetch_scan_completed")


def test_take_industrial_dispatch_groups_uses_warm_batch_bundles():
    """A freed worker slot should receive a bounded bundle of batches."""
    mod = _load_csf_source_module()
    batch_queue = [[f"vid{i:03d}"] for i in range(1042)]

    groups = mod._take_industrial_dispatch_groups(batch_queue, 1, 4)

    assert len(groups) == 1
    assert len(groups[0]) == 4
    assert len(batch_queue) == 1038
    assert groups[0][0] == ["vid000"]


def test_bounded_dispatch_slot_count_never_exceeds_eligible_workers():
    """Quarantined slots must not cause queued batches to be removed unassigned."""
    mod = _load_csf_source_module()

    assert mod._bounded_dispatch_slot_count(4, 2) == 2
    assert mod._bounded_dispatch_slot_count(1, 0) == 0
    assert mod._bounded_dispatch_slot_count(-1, 3) == 0


def test_bounded_dispatch_preserves_unassigned_queue_batches():
    """When a slot is quarantined, unassignable batches remain queued."""
    mod = _load_csf_source_module()
    queue = [["batch-a"], ["batch-b"], ["batch-c"]]
    dispatch_slots = mod._bounded_dispatch_slot_count(4, 2)

    groups = mod._take_industrial_dispatch_groups(queue, dispatch_slots, 1)

    assert groups == [[["batch-a"]], [["batch-b"]]]
    assert queue == [["batch-c"]]


def test_load_worker_summary_falls_back_when_result_file_missing():
    """Worker summary parsing should fall back to stdout when the result file is missing."""
    mod = _load_csf_source_module()
    summary = mod._load_worker_summary(
        _REPO_ROOT / "tests" / "missing-worker-result.json",
        '{"worker_id":"worker-02","succeeded":7,"failed":2,"status":"ok"}',
    )

    assert summary["worker_id"] == "worker-02"
    assert summary["succeeded"] == 7
    assert summary["failed"] == 2
    assert summary["status"] == "ok"


@pytest.mark.parametrize("payload", ["[]", '"malformed"', "null", "17"])
def test_load_worker_summary_rejects_non_object_json(payload, tmp_path):
    """Non-object worker output must become an empty summary, not crash the coordinator."""
    mod = _load_csf_source_module()
    result_path = tmp_path / "worker-result.json"
    result_path.write_text(payload, encoding="utf-8")

    assert mod._load_worker_summary(result_path, "") == {}


def test_adaptive_health_blocks_missing_telemetry_and_disqualifying_reasons():
    mod = _load_csf_source_module()

    assert mod._classify_adaptive_worker_health({}) == (False, "worker_result")
    assert mod._classify_adaptive_worker_health({"status": "ok"}) == (
        False,
        "health_telemetry_missing",
    )
    assert mod._classify_adaptive_worker_health(
        {
            "content_fetch_status_counts_total": {"auth_failed": 1},
            "succeeded": 0,
            "failed": 1,
        }
    ) == (False, "auth_failed")
    assert mod._classify_adaptive_worker_health(
        {
            "content_fetch_status_counts_total": {"ready": 3},
            "succeeded": 3,
            "failed": 0,
        }
    ) == (True, "")


def test_adaptive_health_blocks_failure_rate_above_scheduler_limit():
    mod = _load_csf_source_module()

    assert mod._classify_adaptive_worker_health(
        {
            "content_fetch_status_counts_total": {"ready": 7, "command_failed": 3},
            "succeeded": 7,
            "failed": 3,
        },
        max_failure_rate=0.25,
    ) == (False, "failure_rate_exceeded")
    assert mod._classify_adaptive_worker_health(
        {
            "content_fetch_status_counts_total": {"ready": 9, "command_failed": 3},
            "succeeded": 9,
            "failed": 3,
        },
        max_failure_rate=0.25,
    ) == (True, "")


def test_adaptive_health_requires_result_counts_for_scale_up():
    mod = _load_csf_source_module()

    assert mod._classify_adaptive_worker_health(
        {"content_fetch_status_counts_total": {"ready": 3}}
    ) == (False, "worker_result_counts_missing")


def test_adaptive_result_requeues_only_when_completion_is_untrustworthy():
    mod = _load_csf_source_module()

    assert mod._adaptive_result_requires_requeue({}, "health_telemetry_missing") is True
    assert mod._adaptive_result_requires_requeue({}, "auth_failed") is True
    assert mod._adaptive_result_requires_requeue({}, "failure_rate_exceeded") is False
    assert mod._adaptive_result_requires_requeue({}, "source_age_cliff") is False


def test_adaptive_assignment_requeue_preserves_payload_and_ledger_identity():
    mod = _load_csf_source_module()
    ledger = mod.AssignmentLedger()
    item = mod._IndustrialQueuedBatch("batch-a", ["aaaaaaaaaaa"])
    ledger.register((item.batch_id,))
    ledger.claim("assignment-a", (item.batch_id,))
    queue = []

    mod._requeue_adaptive_assignment(queue, [item], ledger, "assignment-a")

    assert queue == [item]
    accounting = ledger.accounting()
    assert accounting.balanced
    assert accounting.requeued == 1


def test_adaptive_assignment_requeue_fails_closed_without_ownership_metadata():
    mod = _load_csf_source_module()
    item = mod._IndustrialQueuedBatch("batch-a", ["aaaaaaaaaaa"])

    with pytest.raises(RuntimeError, match="no assignment ID"):
        mod._requeue_adaptive_assignment([], [item], None, "")
    with pytest.raises(RuntimeError, match="no batch payload"):
        mod._requeue_adaptive_assignment([], [], None, "assignment-a")


def test_worker_failure_fallback_reconciles_db_and_never_replays_notebooklm(monkeypatch):
    """Only persisted failed rows with a source are queued without requeueing."""
    mod = _load_csf_source_module()
    item = mod._IndustrialQueuedBatch(
        "batch-a",
        ["failed-video", "still-pending", "no-source"],
        {"failed-video": "https://www.youtube.com/watch?v=failed-video"},
    )
    rows = [
        {
            "video_id": "failed-video",
            "status": "failed",
            "source": None,
            "failure_reason": "SourceNotFoundError",
        },
        {
            "video_id": "still-pending",
            "status": "pending",
            "source": "https://www.youtube.com/watch?v=still-pending",
            "failure_reason": None,
        },
        {
            "video_id": "no-source",
            "status": "failed",
            "source": None,
            "failure_reason": "command_failed",
        },
    ]
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda _ids: rows)
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    fallback_queue = mod.deque()

    queued = mod._queue_confirmed_worker_failures_for_fallback(
        [item],
        fallback_queue=fallback_queue,
        source_urls={},
    )

    assert queued == 1
    assert list(fallback_queue) == [
        ("failed-video", "https://www.youtube.com/watch?v=failed-video", True)
    ]
    assert any(name == "industrial_failure_fallback_queued" for name, _ in events)
    queued_event = next(payload for name, payload in events if name == "industrial_failure_fallback_queued")
    assert queued_event["status_preserved"] == "failed"
    assert queued_event["requeue_skipped"] is True
    assert not any(name == "industrial_failure_fallback_not_queued" and payload.get("video_id") == "still-pending" for name, payload in events)


def test_source_add_failure_fallback_routes_only_source_add_rows(monkeypatch):
    """The narrow recovery route must not queue unrelated worker failures."""
    mod = _load_csf_source_module()
    item = mod._IndustrialQueuedBatch(
        "batch-a",
        ["source-add-video", "command-video"],
        {
            "source-add-video": "https://www.youtube.com/watch?v=source-add-video",
            "command-video": "https://www.youtube.com/watch?v=command-video",
        },
    )
    rows = [
        {
            "video_id": "source-add-video",
            "status": "failed",
            "failure_reason": "Source add failed",
        },
        {
            "video_id": "command-video",
            "status": "failed",
            "failure_reason": "command_failed",
        },
    ]
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda _ids: rows)
    monkeypatch.setattr(mod, "log_action", lambda *_args, **_kwargs: None)
    fallback_queue = mod.deque()
    failure_reasons = {}

    queued = mod._queue_confirmed_worker_failures_for_fallback(
        [item],
        fallback_queue=fallback_queue,
        source_urls={},
        source_add_only=True,
        failure_reasons=failure_reasons,
    )

    assert queued == 1
    assert list(fallback_queue) == [
        ("source-add-video", "https://www.youtube.com/watch?v=source-add-video", True)
    ]
    assert failure_reasons == {"source-add-video": "Source add failed"}


def test_source_addressability_failure_predicate_is_narrow():
    mod = _load_csf_source_module()

    assert mod._is_source_addressability_failure_reason("SourceNotFoundError: source missing")
    assert mod._is_source_addressability_failure_reason("source_not_found")
    assert not mod._is_source_addressability_failure_reason("Source add failed")
    assert not mod._is_source_addressability_failure_reason("command_failed")


def test_source_add_failure_predicate_accepts_preserved_terminal_provenance():
    mod = _load_csf_source_module()

    assert mod._is_source_add_failure_reason(
        "Source add failed; materialization terminal error: "
        "SourceAddError (cause=RPCError, rpc_code=9)"
    )
    assert not mod._is_source_add_failure_reason(
        "Source materialization terminal error"
    )


def test_fallback_failure_reason_retains_source_add_provenance():
    mod = _load_csf_source_module()

    persisted = mod._compose_fallback_failure_reason(
        "Source add failed; materialization terminal error: "
        "SourceAddError (cause=RPCError, rpc_code=9)",
        "unknown",
        "transcript fallback deadline exhausted",
    )

    assert persisted.startswith("Source add failed; materialization terminal error:")
    assert "transcript_fallback: unknown: transcript fallback deadline exhausted" in persisted
    assert mod._is_source_add_failure_reason(persisted)


def test_source_addressability_fallback_routes_only_addressability_rows(monkeypatch):
    """The addressability route must not absorb source-add or command failures."""
    mod = _load_csf_source_module()
    item = mod._IndustrialQueuedBatch(
        "batch-a",
        ["addressability-video", "source-add-video", "command-video"],
        {
            "addressability-video": "https://www.youtube.com/watch?v=addressability-video",
            "source-add-video": "https://www.youtube.com/watch?v=source-add-video",
            "command-video": "https://www.youtube.com/watch?v=command-video",
        },
    )
    rows = [
        {
            "video_id": "addressability-video",
            "status": "failed",
            "failure_reason": "SourceNotFoundError: source content unavailable",
        },
        {
            "video_id": "source-add-video",
            "status": "failed",
            "failure_reason": "Source add failed",
        },
        {
            "video_id": "command-video",
            "status": "failed",
            "failure_reason": "command_failed",
        },
    ]
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(mod, "get_entries_for_video_ids_details", lambda _ids: rows)
    monkeypatch.setattr(mod, "log_action", lambda name, payload: events.append((name, payload)))
    fallback_queue = mod.deque()

    queued = mod._queue_confirmed_worker_failures_for_fallback(
        [item],
        fallback_queue=fallback_queue,
        source_urls={},
        source_addressability_only=True,
    )

    assert queued == 1
    assert list(fallback_queue) == [
        ("addressability-video", "https://www.youtube.com/watch?v=addressability-video", True)
    ]
    skipped = {
        payload["video_id"]: payload["reason"]
        for name, payload in events
        if name == "industrial_failure_fallback_not_queued"
    }
    assert skipped == {
        "source-add-video": "failure_reason_not_source_addressability",
        "command-video": "failure_reason_not_source_addressability",
    }


def test_take_industrial_dispatch_groups_handles_empty_queue():
    mod = _load_csf_source_module()

    queue = []
    assert mod._take_industrial_dispatch_groups(queue, 4, 4) == []
    assert queue == []


def test_build_worker_health_warning_includes_key_context():
    """Worker health warnings should carry enough context to act on quickly."""
    mod = _load_csf_source_module()
    payload = mod._build_worker_health_warning(
        reason="no_worker_completion_after_15m",
        elapsed_s=901.2,
        active_workers=4,
        queued_batches=12,
        available_slots=0,
        first_worker_finished_at=None,
        last_worker_finished_at=None,
    )

    assert payload["reason"] == "no_worker_completion_after_15m"
    assert payload["elapsed_s"] == 901.2
    assert payload["active_workers"] == 4
    assert payload["queued_batches"] == 12
    assert payload["available_slots"] == 0
    assert payload["first_worker_finished"] is False
    assert payload["last_worker_finished"] is False

    stalled = mod._build_worker_health_warning(
        reason="no_worker_completion_after_15m_since_last_completion",
        elapsed_s=1201.2,
        active_workers=2,
        queued_batches=8,
        available_slots=2,
        first_worker_finished_at=118.0,
        last_worker_finished_at=300.5,
    )

    assert stalled["reason"] == "no_worker_completion_after_15m_since_last_completion"
    assert stalled["elapsed_s"] == 1201.2
    assert stalled["first_worker_finished"] is True
    assert stalled["last_worker_finished"] is True
    assert stalled["first_worker_finished_at"] == 118.0
    assert stalled["last_worker_finished_at"] == 300.5

    oversized = mod._build_worker_health_warning(
        reason="oversized_worker_dispatch",
        elapsed_s=120.0,
        active_workers=3,
        queued_batches=1,
        available_slots=1,
        first_worker_finished_at=118.0,
        last_worker_finished_at=118.0,
        worker_id="worker-02",
        batch_count=1042,
        video_count=312599,
        batch_size=300,
    )

    assert oversized["reason"] == "oversized_worker_dispatch"
    assert oversized["first_worker_finished"] is True
    assert oversized["first_worker_finished_at"] == 118.0
    assert oversized["last_worker_finished"] is True
    assert oversized["last_worker_finished_at"] == 118.0
    assert oversized["worker_id"] == "worker-02"
    assert oversized["batch_count"] == 1042
    assert oversized["video_count"] == 312599
    assert oversized["batch_size"] == 300


def test_cmd_fetch_skips_blocked_channels_in_preflight_scan():
    """Blocked channels should be excluded before get_entries_for_source runs."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    tracked_rows = [
        ("https://www.youtube.com/@blocked", "pl-blocked"),
        ("https://www.youtube.com/@active", "pl-active"),
    ]
    pending_entries = [("vid01", "pending", None)]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    class FakeScraper:
        def __init__(self, headless: bool = True):
            self.headless = headless

        def preflight_cleanup(self):
            return (0, 0)

        def scrape_with_staging(self, batch):
            return {vid: (True, "transcript", None) for vid in batch}

        def close(self):
            return None

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(tracked_rows)):
        with mock.patch.object(mod, "is_channel_blocked", side_effect=lambda url: url.endswith("blocked")):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=[
                {
                    "video_id": "vid01",
                    "status": "pending",
                    "has_captions": True,
                    "privacy_status": "public",
                    "upload_status": "uploaded",
                    "is_live_content": False,
                    "unavailable_reason": None,
                    "source": "https://www.youtube.com/@active",
                }
            ]) as mock_entries:
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.object(mod.subprocess, "run") as mock_run:
                        mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                        fake_nlm_scraper = types.ModuleType("csf.nlm_scraper")
                        fake_nlm_scraper.NLMIndustrialScraper = FakeScraper
                        with mock.patch.dict(sys.modules, {"csf.nlm_scraper": fake_nlm_scraper}):
                            with mock.patch.object(mod, "set_cached_transcript"):
                                with mock.patch.object(mod, "mark_complete"):
                                    with mock.patch.object(mod, "log_action") as mock_log:
                                        mod.cmd_fetch(dry_run=False, workers=1)

    mock_entries.assert_called_once_with("https://www.youtube.com/@active")
    started = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_scan_started")
    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_scan_completed")
    assert started["channels_tracked_total"] == 2
    assert started["channels_blocked_total"] == 1
    assert started["channels_active_total"] == 1
    assert completed["channels_tracked_total"] == 2
    assert completed["channels_blocked_total"] == 1
    assert completed["channels_active_total"] == 1


def test_cmd_fetch_routes_non_captioned_items_to_notebooklm_first():
    """Non-captioned items should stay on the NotebookLM lane before fallback."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@active", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": False,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@active",
        }
        for i in range(200)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.dict(
                        mod.os.environ,
                        {
                            "YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S": "0",
                            "YTIS_TRANSCRIPT_FALLBACK_WORKERS": "4",
                        },
                        clear=False,
                    ):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            notebooklm_results = {
                                f"vid{i:03d}": (True, "notebooklm transcript", None)
                                for i in range(200)
                            }
                            with mock.patch.object(mod, "process_industrial_batch_reusable", return_value=notebooklm_results) as mock_process:
                                with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                                    with mock.patch.object(mod, "close_reusable_ingestor"):
                                        with mock.patch.object(mod, "set_cached_transcript"):
                                            with mock.patch.object(mod, "mark_complete"):
                                                with mock.patch.object(mod, "log_action") as mock_log:
                                                    mod.cmd_fetch(dry_run=False, workers=1)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_completed" in log_names
    assert mock_process.call_count >= 1
    assert "transcript_fallback_queued" not in log_names


def test_cmd_fetch_reconciles_cached_pending_manifest_items():
    """A cached transcript must close its pending DB row in the industrial path."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@active", "pl-1")]
    pending_entries = [
        {
            "video_id": "cache000001",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@active",
        }
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=True):
                    with mock.patch.object(mod.subprocess, "run") as mock_run:
                        mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                        with mock.patch.object(mod, "mark_complete") as mock_mark_complete:
                            with mock.patch.object(mod, "log_action") as mock_log:
                                mod.cmd_fetch(dry_run=False, workers=1)

    mock_mark_complete.assert_called_once_with("cache000001", last_stage="cache")
    assert any(
        call.args[0] == "transcript_cache_reconciled"
        and call.args[1]["video_id"] == "cache000001"
        for call in mock_log.call_args_list
    )


def test_cmd_fetch_routes_non_captioned_items_to_transcript_fallback_when_enabled():
    """The opt-in routing toggle should bypass NotebookLM for no-caption items."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@active", "pl-1")]
    pending_entries = [
        {
            "video_id": "vid000",
            "status": "pending",
            "has_captions": False,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@active",
        }
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    transcript_result = mock.Mock(
        transcript="fallback transcript",
        lang="en",
        source="selenium",
        view_count=None,
        like_count=None,
        comment_count=None,
        duration=None,
        video_title=None,
        video_description=None,
        error=None,
    )

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.dict(
                        mod.os.environ,
                        {
                            "YTIS_ROUTE_NO_CAPTIONS_TO_FALLBACK": "true",
                            "YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S": "0",
                            "YTIS_TRANSCRIPT_FALLBACK_WORKERS": "4",
                        },
                        clear=False,
                    ):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            with mock.patch("csf.transcript.fetch_transcript_chain", return_value=transcript_result) as mock_fetch:
                                with mock.patch.object(mod, "process_industrial_batch_reusable") as mock_process:
                                    with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                                        with mock.patch.object(mod, "close_reusable_ingestor"):
                                            with mock.patch.object(mod, "set_cached_transcript"):
                                                with mock.patch.object(mod, "mark_complete"):
                                                    with mock.patch.object(mod, "log_action") as mock_log:
                                                        mod.cmd_fetch(dry_run=False, workers=1)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_completed" in log_names
    assert mock_process.call_count == 0
    assert mock_fetch.call_count == 1
    assert mock_fetch.call_args.kwargs["skip_notebooklm"] is True
    assert mock_fetch.call_args.kwargs["skip_oembed"] is True
    fetch_invoked = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_invoked")
    assert fetch_invoked["route_no_captions_to_fallback"] is True


def test_cmd_fetch_routes_live_items_to_transcript_fallback_first():
    """Live items should bypass NotebookLM and go to transcript fallback."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@active", "pl-1")]
    pending_entries = [
        {
            "video_id": "vid-live",
            "status": "pending",
            "has_captions": False,
            "privacy_status": "public",
            "upload_status": "live",
            "is_live_content": True,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@active",
        }
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.dict(
                        mod.os.environ,
                        {
                            "YTIS_TRANSCRIPT_FALLBACK_MIN_START_INTERVAL_S": "0",
                            "YTIS_TRANSCRIPT_FALLBACK_WORKERS": "4",
                        },
                        clear=False,
                    ):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            transcript_result = mock.Mock(
                                transcript="live fallback transcript",
                                lang="en",
                                source="selenium",
                                view_count=None,
                                like_count=None,
                                comment_count=None,
                                duration=None,
                                video_title=None,
                                video_description=None,
                                error=None,
                            )
                            with mock.patch("csf.transcript.fetch_transcript_chain", return_value=transcript_result) as mock_fetch:
                                with mock.patch.object(mod, "process_industrial_batch_reusable") as mock_process:
                                    with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(0, 0)):
                                        with mock.patch.object(mod, "close_reusable_ingestor"):
                                            with mock.patch.object(mod, "set_cached_transcript"):
                                                with mock.patch.object(mod, "mark_complete"):
                                                    with mock.patch.object(mod, "log_action") as mock_log:
                                                        mod.cmd_fetch(dry_run=False, workers=1)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_completed" in log_names
    assert mock_process.call_count == 0
    assert mock_fetch.call_count == 1
    assert all(call.kwargs.get("skip_notebooklm") is True for call in mock_fetch.call_args_list)


def test_cmd_fetch_continues_when_preflight_worker_cleanup_fails():
    """Industrial fetch should warn on preflight cleanup failure and still run."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@chan1", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@chan1",
        }
        for i in range(200)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    cleanup_results = [(0, 1), (0, 0), (0, 0)]
    cleanup_calls: list[tuple[bool, bool]] = []
    run_calls: list[list[str]] = []

    def fake_cleanup(*, delete=False, include_active=False):
        cleanup_calls.append((delete, include_active))
        return cleanup_results.pop(0) if cleanup_results else (0, 0)

    def mock_run(cmd, **_kwargs):
        if isinstance(cmd, list):
            run_calls.append(cmd)
        if isinstance(cmd, list) and "dev.worker_pool.worker_main" in cmd:
            result_path = Path(cmd[cmd.index("--result-path") + 1])
            result_path.write_text(
                json.dumps(
                    {
                        "worker_id": "worker-01",
                        "input": "batches.json",
                        "batch_count": 1,
                        "video_count": 200,
                        "succeeded": 200,
                        "failed": 0,
                        "startup_retire_elapsed_s": 0.25,
                        "startup_notebook_check_elapsed_s": 0.5,
                        "startup_notebook_create_elapsed_s": 1.25,
                        "startup_prepare_cleanup_elapsed_s": 0.75,
                        "startup_prepare_total_elapsed_s": 2.75,
                        "setup_elapsed_s_total": 12.5,
                        "notebook_check_elapsed_s_total": 0.5,
                        "notebook_create_elapsed_s_total": 1.25,
                        "notebook_retire_elapsed_s_total": 0.25,
                        "add_sources_elapsed_s_total": 4.75,
                        "extract_elapsed_s_total": 7.0,
                        "cleanup_elapsed_s_total": 1.5,
                        "batch_elapsed_s_total": 25.75,
                        "status": "ok",
                        "returncode": 0,
                        "state_path": "P:\\\\\\.data/yt-is/industrial-worker-states/worker-01.json",
                        "notebook_title": "yt-is-worker-01",
                    }
                ),
                encoding="utf-8",
            )
            return mock.MagicMock(
                returncode=0,
                stdout='worker start\n{"worker_id":"worker-01","phase":"cleanup"}\nnot-json-final-line\n',
                stderr="",
            )
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.object(mod, "cleanup_stale_worker_notebooks", side_effect=fake_cleanup):
                        with mock.patch.object(mod.subprocess, "run", side_effect=mock_run):
                            with mock.patch.object(mod, "close_reusable_ingestor"):
                                with mock.patch.object(mod, "set_cached_transcript"):
                                    with mock.patch.object(mod, "mark_complete"):
                                        with mock.patch.object(mod, "log_action") as mock_log:
                                            mod.cmd_fetch(dry_run=False, workers=2)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_worker_prewarm_summary" in log_names
    summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_worker_prewarm_summary")
    assert summary["cleanup_deleted"] == 0
    assert summary["cleanup_failed"] == 1
    assert cleanup_calls[0] == (True, True)
    assert any("dev.worker_pool.worker_main" in cmd for cmd in run_calls)


def test_cmd_fetch_logs_worker_prewarm_summary_before_dispatch(tmp_path):
    """Industrial fetch should log the worker cleanup/prewarm summary before dispatch."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    channel_rows = [("https://www.youtube.com/@chan1", "pl-1")]
    pending_entries = [
        {
            "video_id": f"vid{i:03d}",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
            "source": "https://www.youtube.com/@chan1",
        }
        for i in range(200)
    ]

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_args, **_kwargs):
            return FakeCursor(self._rows)

        def close(self):
            return None

    class FakeStorage:
        def __init__(self, rows):
            self._rows = rows

        def _get_conn(self):
            return FakeConn(self._rows)

    def mock_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "dev.worker_pool.worker_main" in cmd:
            result_path = Path(cmd[cmd.index("--result-path") + 1])
            result_path.write_text(
                json.dumps(
                    {
                            "worker_id": "worker-01",
                            "input": "batches.json",
                            "batch_count": 1,
                            "video_count": 200,
                            "succeeded": 200,
                        "failed": 0,
                        "startup_retire_elapsed_s": 0.25,
                        "startup_notebook_check_elapsed_s": 0.5,
                        "startup_notebook_create_elapsed_s": 1.25,
                        "startup_prepare_cleanup_elapsed_s": 0.75,
                        "startup_prepare_total_elapsed_s": 2.75,
                        "setup_elapsed_s_total": 12.5,
                        "notebook_check_elapsed_s_total": 0.5,
                        "notebook_create_elapsed_s_total": 1.25,
                        "notebook_retire_elapsed_s_total": 0.25,
                        "add_sources_elapsed_s_total": 4.75,
                        "extract_elapsed_s_total": 7.0,
                        "cleanup_elapsed_s_total": 1.5,
                        "batch_elapsed_s_total": 25.75,
                        "status": "ok",
                        "returncode": 0,
                        "state_path": "P:\\\\\\.data/yt-is/industrial-worker-states/worker-01.json",
                        "notebook_title": "yt-is-worker-01",
                    }
                ),
                encoding="utf-8",
            )
            return mock.MagicMock(
                returncode=0,
                stdout='worker start\n{"worker_id":"worker-01","phase":"cleanup"}\nnot-json-final-line\n',
                stderr="",
            )
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=FakeStorage(channel_rows)):
        with mock.patch.object(mod, "is_channel_blocked", return_value=False):
            with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                    with mock.patch.object(mod, "cleanup_stale_worker_notebooks", return_value=(3, 0)):
                        with mock.patch.object(mod.subprocess, "run", side_effect=mock_run):
                            with mock.patch.object(mod, "close_reusable_ingestor"):
                                with mock.patch.object(mod, "set_cached_transcript"):
                                    with mock.patch.object(mod, "mark_complete"):
                                        with mock.patch.object(mod, "log_action") as mock_log:
                                            mod.cmd_fetch(dry_run=False, workers=2)

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert "fetch_worker_prewarm_summary" in log_names
    summary = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_worker_prewarm_summary")
    assert summary["workers_requested"] == 2
    assert summary["workers_active"] == 2
    assert summary["prewarm_expected"] == 2
    assert summary["cleanup_deleted"] == 3
    assert summary["cleanup_failed"] == 0
    worker_finished = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_worker_finished")
    assert worker_finished["summary"]["succeeded"] == 200
    assert worker_finished["summary"]["failed"] == 0
    completed = next(call.args[1] for call in mock_log.call_args_list if call.args[0] == "fetch_completed")
    assert completed["worker_cleanup_deleted"] == 3
    assert completed["worker_cleanup_failed"] == 0
    assert completed["success_count"] == 800
    assert completed["fail_count"] == 0
    assert completed["processed_count"] == 800
    assert completed["processed_per_min"] is not None
    assert completed["worker_stage_totals"]["batch_elapsed_s_total"] == 103.0
    assert completed["worker_stage_totals"]["add_sources_elapsed_s_total"] == 19.0


def test_cmd_fetch_logs_fetch_start_and_first_download_started_surgical():
    """cmd_fetch logs a run-start marker and a first-download marker for surgical runs."""
    mod = _load_csf_source_module(stub_ensure_auth=True)
    pending_entries = [
        {
            "video_id": "vid01",
            "status": "pending",
            "has_captions": True,
            "privacy_status": "public",
            "upload_status": "uploaded",
            "is_live_content": False,
            "unavailable_reason": None,
        }
    ]

    class FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

        def cancel(self):
            return True

    class FakeExecutor:
        def __init__(self, max_workers: int):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            return FakeFuture(fn(*args, **kwargs))

    def fake_as_completed(futures):
        return list(futures)

    with mock.patch.object(mod, "_get_batch_status_storage", return_value=mock.MagicMock()):
        with mock.patch.object(mod, "get_channel_metadata", return_value={"playlist_id": "pl-1"}):
            with mock.patch.object(mod, "is_channel_blocked", return_value=False):
                with mock.patch.object(mod, "get_entries_for_source_details", return_value=pending_entries):
                    with mock.patch.object(mod, "has_cached_transcript", return_value=False):
                        with mock.patch.object(mod.subprocess, "run") as mock_run:
                            mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                            with mock.patch("concurrent.futures.ThreadPoolExecutor", FakeExecutor):
                                with mock.patch("concurrent.futures.as_completed", fake_as_completed):
                                    with mock.patch("csf.transcript.fetch_transcript_chain") as mock_fetch:
                                        mock_fetch.return_value = mock.MagicMock(
                                            transcript="transcript",
                                            source="yt-dlp",
                                            lang="en",
                                            view_count=None,
                                            like_count=None,
                                            comment_count=None,
                                            duration=None,
                                            video_title=None,
                                            video_description=None,
                                            error=None,
                                        )
                                        with mock.patch.object(mod, "set_cached_transcript"):
                                                with mock.patch.object(mod, "mark_complete"):
                                                    with mock.patch.object(mod, "log_action") as mock_log:
                                                        mod.cmd_fetch(
                                                            source_filter="https://www.youtube.com/@example",
                                                            dry_run=False,
                                                            workers=1,
                                                        )

    log_names = [call.args[0] for call in mock_log.call_args_list]
    assert log_names[0] == "fetch_invoked"
    assert "fetch_scan_started" in log_names
    assert "fetch_scan_completed" in log_names
    assert "first_download_started" in log_names
    assert log_names.index("fetch_scan_started") < log_names.index("fetch_scan_completed")
    assert log_names.index("fetch_scan_completed") < log_names.index("first_download_started")
    first_payload = mock_log.call_args_list[log_names.index("first_download_started")].args[1]
    assert first_payload["kind"] == "surgical"
    assert first_payload["video_id"] == "vid01"
    assert first_payload["source_url"] == "https://www.youtube.com/@example"
    assert "elapsed_s" in first_payload
