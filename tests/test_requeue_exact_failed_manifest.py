from __future__ import annotations

import json
from pathlib import Path

import pytest

from csf.batch_status import _BatchStatusStorage
from csf.video_selection_manifest import write_video_selection_manifest
import scripts.requeue_exact_failed_manifest as requeue_module
from scripts.requeue_exact_failed_manifest import requeue_exact_failed_manifest


def _manifest(path: Path, video_ids: tuple[str, ...]) -> None:
    write_video_selection_manifest(
        path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-09T00:00:00+00:00",
            "selection_name": "source-add-canary",
            "selection_criteria": {"status": "failed", "failure_reason": "Source add failed"},
            "videos": [{"video_id": video_id} for video_id in video_ids],
        },
    )


def _attempt_kwargs(tmp_path: Path, *, attempt_id: str) -> dict[str, object]:
    packet = tmp_path / f"{attempt_id}-decision.md"
    packet.write_text("# reviewed residual packet\n", encoding="utf-8")
    return {
        "attempt_ledger_path": tmp_path / "residual-attempt-ledger.json",
        "attempt_id": attempt_id,
        "mechanism_id": "test-mechanism",
        "hypothesis": "test hypothesis is materially bounded",
        "account_scope": "a.hominidae",
        "decision_packet_path": packet,
    }


def test_validation_receipt_does_not_mutate_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        storage.set_status(video_id, "failed", failure_reason="Source add failed")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "validation.json"
    _manifest(manifest_path, ("aaaaaaaaaaa", "bbbbbbbbbbb"))

    payload = requeue_exact_failed_manifest(
        db_path=db_path,
        manifest_path=manifest_path,
        receipt_path=receipt_path,
        reason="diagnostic canary",
        **_attempt_kwargs(tmp_path, attempt_id="validation-1"),
    )

    assert payload["status"] == "validated_not_applied"
    assert storage.get_status("aaaaaaaaaaa") == "failed"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["apply"] is False


def test_apply_requires_exact_reason_and_proves_pending_postcondition(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    storage.set_status("aaaaaaaaaaa", "failed", failure_reason="Source add failed")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "applied.json"
    _manifest(manifest_path, ("aaaaaaaaaaa",))

    payload = requeue_exact_failed_manifest(
        db_path=db_path,
        manifest_path=manifest_path,
        receipt_path=receipt_path,
        reason="diagnostic canary",
        **_attempt_kwargs(tmp_path, attempt_id="apply-1"),
        apply=True,
    )

    assert payload["status"] == "applied"
    assert payload["changed_ids"] == ["aaaaaaaaaaa"]
    assert storage.get_status("aaaaaaaaaaa") == "pending"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["after"][0]["failure_reason"] is None


def test_apply_rejects_wrong_failure_reason_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    storage.set_status("aaaaaaaaaaa", "failed", failure_reason="different failure")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "rejected.json"
    _manifest(manifest_path, ("aaaaaaaaaaa",))

    with pytest.raises(RuntimeError, match="guarded retry precondition"):
        requeue_exact_failed_manifest(
            db_path=db_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            reason="diagnostic canary",
            **_attempt_kwargs(tmp_path, attempt_id="wrong-reason-1"),
            apply=True,
        )
    assert storage.get_status("aaaaaaaaaaa") == "failed"
    assert not receipt_path.exists()


def test_apply_accepts_exact_failure_class_for_source_specific_reasons(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    ids = ("aaaaaaaaaaa", "bbbbbbbbbbb")
    for video_id in ids:
        storage.set_status(video_id, "failed", failure_reason="Fetch failed for source-id: command_failed")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "applied.json"
    _manifest(manifest_path, ids)

    payload = requeue_exact_failed_manifest(
        db_path=db_path,
        manifest_path=manifest_path,
        receipt_path=receipt_path,
        reason="industrial fallback class canary",
        expected_failure_class="command",
        **_attempt_kwargs(tmp_path, attempt_id="command-1"),
        apply=True,
    )

    assert payload["status"] == "applied"
    assert payload["expected_failure_reason"] is None
    assert payload["expected_failure_class"] == "command"
    assert all(storage.get_status(video_id) == "pending" for video_id in ids)


def test_apply_writes_receipt_when_ledger_finalization_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    storage.set_status("aaaaaaaaaaa", "failed", failure_reason="Source add failed")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "applied-ledger-failure.json"
    _manifest(manifest_path, ("aaaaaaaaaaa",))

    def fail_ledger_update(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated ledger outage")

    monkeypatch.setattr(requeue_module, "update_attempt_status", fail_ledger_update)
    with pytest.raises(RuntimeError, match="ledger finalization failed"):
        requeue_exact_failed_manifest(
            db_path=db_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            reason="diagnostic canary",
            **_attempt_kwargs(tmp_path, attempt_id="ledger-failure-1"),
            apply=True,
        )

    assert storage.get_status("aaaaaaaaaaa") == "pending"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "applied"
    assert receipt["ledger_finalization"] == "failed"
    assert "simulated ledger outage" in receipt["ledger_status_error"]


def test_apply_records_unverified_postcondition_when_status_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    storage.set_status("aaaaaaaaaaa", "failed", failure_reason="Source add failed")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "postcondition-read-failure.json"
    _manifest(manifest_path, ("aaaaaaaaaaa",))
    real_read_rows = requeue_module._read_rows
    calls = 0

    def fail_after_transition(*args: object, **kwargs: object) -> dict[str, dict[str, object | None]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated status read outage")
        return real_read_rows(*args, **kwargs)

    monkeypatch.setattr(requeue_module, "_read_rows", fail_after_transition)
    with pytest.raises(RuntimeError, match="postcondition could not be verified"):
        requeue_exact_failed_manifest(
            db_path=db_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            reason="diagnostic canary",
            **_attempt_kwargs(tmp_path, attempt_id="postcondition-read-failure-1"),
            apply=True,
        )

    assert storage.get_status("aaaaaaaaaaa") == "pending"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "postcondition_failed"
    assert receipt["postcondition_check"] == "error"
    assert "simulated status read outage" in receipt["error"]


def test_failure_class_guard_rejects_mixed_manifest_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path)
    storage.set_status("aaaaaaaaaaa", "failed", failure_reason="Fetch failed for source-id: command_failed")
    storage.set_status("bbbbbbbbbbb", "failed", failure_reason="unavailable: video unavailable")
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "rejected.json"
    _manifest(manifest_path, ("aaaaaaaaaaa", "bbbbbbbbbbb"))

    with pytest.raises(RuntimeError, match="guarded retry precondition"):
        requeue_exact_failed_manifest(
            db_path=db_path,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            reason="industrial fallback class canary",
            expected_failure_class="command",
            **_attempt_kwargs(tmp_path, attempt_id="mixed-1"),
            apply=True,
        )

    assert storage.get_status("aaaaaaaaaaa") == "failed"
    assert storage.get_status("bbbbbbbbbbb") == "failed"
    assert not receipt_path.exists()
