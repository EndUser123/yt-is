import json
import logging
import queue
import threading
from pathlib import Path

from csf import csf_logging


def test_allowed_log_bases_include_workspace_level_logs_root() -> None:
    drive_logs = (Path(Path.cwd().anchor) / ".logs").resolve()

    assert drive_logs in csf_logging._allowed_log_bases()


def test_default_log_root_is_package_owned() -> None:
    assert csf_logging._default_log_root() == Path(csf_logging.__file__).resolve().parents[1] / ".logs"


def test_write_jsonl_entry_accepts_an_allowed_explicit_directory(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        csf_logging,
        "_allowed_log_bases",
        lambda: (tmp_path.resolve(),),
    )
    monkeypatch.setenv("INTELLIGENCE_STREAM_LOG_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(csf_logging, "resolve_tid", lambda: "test-trace")

    csf_logging._write_jsonl_entry("test_action", {"ok": True})

    log_file = tmp_path / "run" / "test-trace.jsonl"
    assert log_file.exists()
    assert '"action": "test_action"' in log_file.read_text(encoding="utf-8")


def test_write_jsonl_entry_adds_run_account_envelope(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(csf_logging, "_allowed_log_bases", lambda: (tmp_path.resolve(),))
    monkeypatch.setenv("INTELLIGENCE_STREAM_LOG_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("YTIS_INDUSTRIAL_RUN_ID", "run-123")
    monkeypatch.setenv("YTIS_NLM_ACCOUNT_PROFILE", "a.hominidae")
    monkeypatch.setattr(csf_logging, "resolve_tid", lambda: "envelope-trace")

    csf_logging._write_jsonl_entry("test_action", {"run_id": "explicit-run"})

    payload = json.loads(
        (tmp_path / "run" / "envelope-trace.jsonl").read_text(encoding="utf-8")
    )
    assert payload["data"] == {"run_id": "run-123", "account_profile": "a.hominidae"}


def test_write_jsonl_entry_adds_throughput_execution_nonce(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(csf_logging, "_allowed_log_bases", lambda: (tmp_path.resolve(),))
    monkeypatch.setenv("INTELLIGENCE_STREAM_LOG_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("YTIS_THROUGHPUT_PAIR_EXECUTION_NONCE", "arm-nonce")
    monkeypatch.setattr(csf_logging, "resolve_tid", lambda: "nonce-trace")

    csf_logging._write_jsonl_entry("test_action", {})

    payload = json.loads(
        (tmp_path / "run" / "nonce-trace.jsonl").read_text(encoding="utf-8")
    )
    assert payload["data"]["execution_nonce"] == "arm-nonce"


def test_write_jsonl_entry_falls_back_for_an_untrusted_directory(
    monkeypatch, tmp_path
) -> None:
    allowed = tmp_path / "allowed"
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setattr(csf_logging, "_allowed_log_bases", lambda: (allowed,))
    monkeypatch.setenv("INTELLIGENCE_STREAM_LOG_DIR", str(tmp_path / "outside"))
    monkeypatch.setattr(csf_logging, "resolve_tid", lambda: "fallback-trace")

    package_fallback = fallback / "package-logs"
    monkeypatch.setattr(csf_logging, "_default_log_root", lambda: package_fallback)
    monkeypatch.chdir(fallback)
    csf_logging._write_jsonl_entry("fallback_action", {})

    assert (package_fallback / "fallback-trace.jsonl").exists()
    assert not (tmp_path / "outside" / "fallback-trace.jsonl").exists()


def test_queue_listener_survives_a_record_write_failure(tmp_path, monkeypatch) -> None:
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    listener = csf_logging._create_queue_listener(log_queue, tmp_path / "events.jsonl")
    processed: list[str] = []
    second_record_processed = threading.Event()

    def write_record(record: logging.LogRecord) -> None:
        if record.getMessage() == "first":
            raise OSError("temporary sink failure")
        processed.append(record.getMessage())
        second_record_processed.set()

    monkeypatch.setattr(listener, "_write_record", write_record)
    listener.start()
    log_queue.put(logging.LogRecord("test", logging.INFO, __file__, 1, "first", (), None))
    log_queue.put(logging.LogRecord("test", logging.INFO, __file__, 2, "second", (), None))

    try:
        assert second_record_processed.wait(timeout=1.0)
    finally:
        listener.stop()

    assert processed == ["second"]
