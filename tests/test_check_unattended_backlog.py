from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import scripts.check_unattended_backlog as mod
from csf.video_selection_manifest import (
    build_selection_receipt,
    load_video_selection_manifest,
    select_manifest_entries,
    write_selection_receipt,
    write_video_selection_manifest,
)


def _db(path: Path, statuses: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)")
        conn.executemany(
            "INSERT INTO analysis_status VALUES (?, ?)",
            [(f"{index:011d}", status) for index, status in enumerate(statuses)],
        )


def _state(path: Path, db_path: Path, status: str, *, summary_path: Path | None = None) -> None:
    payload = {
        "schema_version": 1,
        "config": {
            "db_path": str(db_path.resolve()),
            "accounts": ["a.hominidae"],
            "chunk_size": 1,
            "workers_per_account": 1,
            "execute": status != "planned",
            "parallel_accounts": True,
            "max_chunks": 1,
            "until_empty": False,
        },
        "status": status,
        "chunks": [],
    }
    if summary_path is not None:
        payload["chunks"] = [{"summary_path": str(summary_path)}]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _summary(path: Path, status: str, *, selected_count: int, complete_count: int, status_counts: dict[str, int]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": status,
                "selected_count": selected_count,
                "selected_complete_count": complete_count,
                "selected_status_counts": status_counts,
            }
        ),
        encoding="utf-8",
    )


def _write_completed_receipt_fixture(tmp_path: Path, *, forged_fingerprint: bool = False) -> tuple[Path, Path]:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    video_id = "00000000000"
    _db(db_path, ["pending"])
    write_video_selection_manifest(
        manifest_path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-10T00:00:00+00:00",
            "selection_name": "multi-account-run-a-a-hominidae",
            "selection_criteria": {
                "status": "pending",
                "account_profile": "a.hominidae",
                "run_id": "run-a",
            },
            "videos": [{"video_id": video_id, "source_note": "analysis_status:pending"}],
        },
    )
    manifest = load_video_selection_manifest(manifest_path)
    snapshot = {video_id: {"video_id": video_id, "status": "pending"}}
    selection = select_manifest_entries(manifest, snapshot)
    receipt = build_selection_receipt(
        manifest,
        selection,
        manifest_path=manifest_path.resolve(),
        database_path=db_path.resolve(),
        max_items=1,
        dry_run=False,
    )
    receipt.update(
        {
            "coordinator_snapshot_version": 1,
            "database_snapshot_rows": [snapshot[video_id]],
            "run_id": "run-a",
            "account_profile": "a.hominidae",
        }
    )
    if forged_fingerprint:
        receipt["database_fingerprint"] = "sha256:forged"
    write_selection_receipt(receipt_path, receipt)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE analysis_status SET status='complete' WHERE video_id=?", (video_id,))
        conn.commit()
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "run_id": "run-a",
                "selected_count": 1,
                "selected_complete_count": 1,
                "selected_status_counts": {"complete": 1},
                "account_results": [
                    {
                        "account_profile": "a.hominidae",
                        "status": "completed",
                        "manifest_path": str(manifest_path.resolve()),
                        "receipt_path": str(receipt_path.resolve()),
                        "selected_status_counts": {"complete": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _state(state_path, db_path, "completed", summary_path=summary_path)
    return db_path, state_path


def test_health_reconciles_exact_completed_account_receipt_after_status_changes(tmp_path: Path) -> None:
    db_path, state_path = _write_completed_receipt_fixture(tmp_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "healthy"
    assert payload["issues"] == []


def test_health_rejects_forged_completed_account_receipt_fingerprint(tmp_path: Path) -> None:
    db_path, state_path = _write_completed_receipt_fixture(tmp_path, forged_fingerprint=True)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "account_receipt_0_fingerprint_mismatch" in payload["issues"]


@pytest.mark.parametrize("reported_counts", [{"complete": "1"}, {"complete": -1}, {"complete": True}])
def test_health_reports_malformed_completed_account_counts(
    tmp_path: Path, reported_counts: dict[str, object]
) -> None:
    db_path, state_path = _write_completed_receipt_fixture(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    summary_path = Path(state["chunks"][0]["summary_path"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["account_results"][0]["selected_status_counts"] = reported_counts
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "account_receipt_0_reported_counts_invalid" in payload["issues"]


def test_health_reports_healthy_completed_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    _db(db_path, ["complete"])
    summary_path = tmp_path / "summary.json"
    _summary(summary_path, "no_work", selected_count=0, complete_count=0, status_counts={})
    _state(state_path, db_path, "completed", summary_path=summary_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "healthy"
    assert payload["pending_count"] == 0
    assert payload["issues"] == []


def test_health_detects_completed_state_with_pending_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    _db(db_path, ["pending"])
    _state(state_path, db_path, "completed")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "completed_with_pending_rows" in payload["issues"]


def test_health_marks_plan_without_live_work(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    video_id = "00000000000"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, source TEXT, "
            "updated_at TEXT, has_captions INTEGER)"
        )
        conn.execute(
            "INSERT INTO analysis_status VALUES (?, 'pending', NULL, NULL, NULL)",
            (video_id,),
        )
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    write_video_selection_manifest(
        manifest_path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-10T00:00:00+00:00",
            "selection_name": "multi-account-plan-a-a-hominidae",
            "selection_criteria": {
                "account_profile": "a.hominidae",
                "run_id": "plan-a",
                "status": "pending",
            },
            "videos": [{"video_id": video_id, "source_note": "analysis_status:pending"}],
        },
    )
    manifest = load_video_selection_manifest(manifest_path)
    snapshot = {
        video_id: {
            "video_id": video_id,
            "status": "pending",
            "source": None,
            "updated_at": None,
            "has_captions": None,
        }
    }
    selection = select_manifest_entries(manifest, snapshot)
    receipt = build_selection_receipt(
        manifest,
        selection,
        manifest_path=manifest_path.resolve(),
        database_path=db_path.resolve(),
        max_items=None,
        dry_run=True,
    )
    receipt.update({"plan_only": True, "operation_mode": "plan_only"})
    write_selection_receipt(receipt_path, receipt)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "status": "planned",
                "run_id": "plan-a",
                "selected_count": 1,
                "selected_complete_count": 0,
                "selected_status_counts": {"pending": 1},
                "account_results": [
                    {
                        "account_profile": "a.hominidae",
                        "manifest_path": str(manifest_path.resolve()),
                        "receipt_path": str(receipt_path.resolve()),
                        "selected_status_counts": {"pending": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _state(state_path, db_path, "planned", summary_path=summary_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["chunks"][0]["assignment_ownership"] = [{
        "account_profile": "a.hominidae",
        "manifest_path": str(manifest_path.resolve()),
        "receipt_path": str(receipt_path.resolve()),
        "video_ids": [video_id],
    }]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "planned"
    assert payload["pending_count"] == 1
    assert payload["readiness"] == {
        "planned": True,
        "live_bounded": False,
        "scheduler_unverified": True,
        "residuals": True,
        "full_authorization": False,
        "full_authorization_status": "not_verified",
    }


def test_health_rejects_corrupted_planned_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    video_id = "00000000000"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, source TEXT, "
            "updated_at TEXT, has_captions INTEGER)"
        )
        conn.execute(
            "INSERT INTO analysis_status VALUES (?, 'pending', NULL, NULL, NULL)",
            (video_id,),
        )
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    write_video_selection_manifest(
        manifest_path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-10T00:00:00+00:00",
            "selection_name": "plan-corrupt-a-hominidae",
            "selection_criteria": {"account_profile": "a.hominidae", "run_id": "plan-corrupt", "status": "pending"},
            "videos": [{"video_id": video_id}],
        },
    )
    manifest = load_video_selection_manifest(manifest_path)
    selection = select_manifest_entries(
        manifest,
        {video_id: {"video_id": video_id, "status": "pending", "source": None, "updated_at": None, "has_captions": None}},
    )
    receipt = build_selection_receipt(
        manifest, selection, manifest_path=manifest_path.resolve(), database_path=db_path.resolve(), max_items=None, dry_run=True
    )
    receipt.update({"plan_only": True, "operation_mode": "plan_only", "selection_fingerprint": "sha256:forged"})
    write_selection_receipt(receipt_path, receipt)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({
        "status": "planned", "run_id": "plan-corrupt", "selected_count": 1,
        "selected_complete_count": 0, "selected_status_counts": {"pending": 1},
        "account_results": [{"account_profile": "a.hominidae", "manifest_path": str(manifest_path.resolve()),
                             "receipt_path": str(receipt_path.resolve()), "selected_status_counts": {"pending": 1}}],
    }), encoding="utf-8")
    _state(state_path, db_path, "planned", summary_path=summary_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["config"]["accounts"] = ["a.hominidae"]
    state["chunks"][0]["assignment_ownership"] = [{
        "account_profile": "a.hominidae", "manifest_path": str(manifest_path.resolve()),
        "receipt_path": str(receipt_path.resolve()), "video_ids": [video_id],
    }]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "planned_account_receipt_0_fingerprint_mismatch" in payload["issues"]


def test_health_rejects_empty_state_object(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, ["pending"])
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "state_schema_invalid" in payload["issues"]
    assert "state_config_missing" in payload["issues"]
    assert "state_status_invalid" in payload["issues"]


def test_health_rejects_drifted_account_settings_fingerprint(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    settings_path = tmp_path / "account-settings.json"
    _db(db_path, ["pending"])
    settings_path.write_text("{}", encoding="utf-8")
    _state(state_path, db_path, "planned")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["config"].update(
        {
            "account_settings_path": str(settings_path.resolve()),
            "account_settings_file_fingerprint": "sha256:" + "0" * 64,
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "state_account_settings_file_fingerprint_mismatch" in payload["issues"]


def test_health_detects_paused_state_with_failed_latest_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    summary_path = tmp_path / "summary.json"
    _db(db_path, ["pending"])
    _summary(summary_path, "failed", selected_count=1, complete_count=0, status_counts={"failed": 1})
    _state(state_path, db_path, "paused", summary_path=summary_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "paused_summary_mismatch" in payload["issues"]


def test_health_reports_missing_state(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [])

    payload = mod.inspect_backlog(db_path=db_path, state_path=tmp_path / "missing.json")

    assert payload["health_status"] == "needs_attention"
    assert "state_missing" in payload["issues"]


def test_health_detects_active_runtime_receipt(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    output_root = tmp_path / "chunk-0001"
    output_root.mkdir()
    _db(db_path, ["pending"])
    _state(state_path, db_path, "paused")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["chunks"] = [{"output_root": str(output_root), "summary_path": str(output_root / "missing.json")}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (output_root / "supervisor_runtime.json").write_text(json.dumps({
        "status": "running",
        "pid": 1234,
        "lease_until_epoch": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(mod, "_runtime_process_matches", lambda pid, output_root: True)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "active_runtime" in payload["issues"]


def test_health_rejects_live_unrelated_pid_as_active_runtime(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    output_root = tmp_path / "chunk-0001"
    output_root.mkdir()
    _db(db_path, ["pending"])
    _state(state_path, db_path, "paused")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["chunks"] = [{"output_root": str(output_root), "summary_path": str(output_root / "missing.json")}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (output_root / "supervisor_runtime.json").write_text(json.dumps({
        "status": "running",
        "pid": 1234,
        "lease_until_epoch": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(mod, "_runtime_process_matches", lambda pid, output_root: False)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "runtime_process_mismatch" in payload["issues"]
    assert "active_runtime" not in payload["issues"]


def test_runtime_process_match_requires_entrypoint_and_output_root(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / "chunk-0001"

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return [
                r"C:\Python314\python.exe",
                r"P:\packages\yt-is\scripts\run_multi_account_fetch.py",
                "--output-root",
                str(output_root),
            ]

    monkeypatch.setattr(mod.psutil, "Process", FakeProcess)

    assert mod._runtime_process_matches(1234, output_root) is True
    assert mod._runtime_process_matches(1234, tmp_path / "other") is False


def test_health_detects_expired_orphan_runtime(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    output_root = tmp_path / "chunk-0001"
    output_root.mkdir()
    _db(db_path, ["pending"])
    _state(state_path, db_path, "paused")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["chunks"] = [{"output_root": str(output_root), "summary_path": str(output_root / "missing.json")}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (output_root / "supervisor_runtime.json").write_text(json.dumps({
        "status": "running",
        "pid": 1234,
        "lease_until_epoch": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "needs_attention"
    assert "orphaned_runtime" in payload["issues"]


def test_pid_is_alive_false_for_exited_pid_and_invalid_inputs() -> None:
    # Regression: the old os.kill(pid, 0) probe returned True for dead PIDs
    # on Windows (signal 0 is CTRL_C_EVENT there), making the orphaned/
    # lease-expired health branches unreachable.
    import os as _os
    import subprocess as _subprocess
    import sys as _sys

    assert mod._pid_is_alive(_os.getpid()) is True
    for invalid in (True, 0, -1, "123", 3.5, None):
        assert mod._pid_is_alive(invalid) is False

    child = _subprocess.Popen(
        [_sys.executable, "-c", "pass"],
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
    )
    assert child.wait(timeout=30) == 0
    assert mod._pid_is_alive(child.pid) is False


# ---------------------------------------------------------------------------
# Supervisor-vocabulary contract fixes (2026-08-17 monitor work)
#
# Reproduced incident: the checker rejected the supervisor's legitimate
# budget-pause state (state=paused + last chunk partial, after terminalized
# failures) with `paused_summary_mismatch`, and treated
# planning/recovering/completed_with_failures as invalid statuses. The
# producer contract is run_unattended_backlog.py: the paused/partial
# continuation rule and the completed_with_failures terminal status.
# ---------------------------------------------------------------------------


def _state_with_summary(path: Path, db_path: Path, status: str, summary_path: Path) -> None:
    _state(path, db_path, status, summary_path=summary_path)


def test_paused_with_partial_summary_is_healthy(tmp_path):
    """The reproduced paused_summary_mismatch case must now pass.

    Mirrors the live 2026-08-17 state: supervisor paused after exhausting
    the invocation budget with the last chunk partial (3 complete + 1
    failed of 4 selected, all terminal in the DB)."""
    db_path = tmp_path / "batch.sqlite"
    state_path = tmp_path / "state.json"
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    video_ids = [f"{index:011d}" for index in range(4)]
    # Background backlog outside the chunk scope: the supervisor pauses on
    # invocation budget with pending work remaining (live-shape state).
    _db(db_path, ["pending"] * 4 + ["pending"] * 20)
    write_video_selection_manifest(
        manifest_path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-17T00:00:00+00:00",
            "selection_name": "multi-account-run-partial-a-hominidae",
            "selection_criteria": {
                "status": "pending",
                "account_profile": "a.hominidae",
                "run_id": "run-partial",
            },
            "videos": [
                {"video_id": vid, "source_note": "analysis_status:pending"}
                for vid in video_ids
            ],
        },
    )
    manifest = load_video_selection_manifest(manifest_path)
    snapshot = {
        vid: {"video_id": vid, "status": "pending"} for vid in video_ids
    }
    selection = select_manifest_entries(manifest, snapshot)
    receipt = build_selection_receipt(
        manifest,
        selection,
        manifest_path=manifest_path.resolve(),
        database_path=db_path.resolve(),
        max_items=4,
        dry_run=False,
    )
    receipt.update(
        {
            "coordinator_snapshot_version": 1,
            "database_snapshot_rows": [snapshot[vid] for vid in video_ids],
            "run_id": "run-partial",
            "account_profile": "a.hominidae",
        }
    )
    write_selection_receipt(receipt_path, receipt)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE analysis_status SET status='complete' WHERE video_id IN (?, ?, ?)",
            video_ids[:3],
        )
        conn.execute(
            "UPDATE analysis_status SET status='failed' WHERE video_id=?",
            (video_ids[3],),
        )
        conn.commit()
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "status": "partial",
                "run_id": "run-partial",
                "selected_count": 4,
                "selected_complete_count": 3,
                "selected_status_counts": {"complete": 3, "failed": 1},
                "account_results": [
                    {
                        "account_profile": "a.hominidae",
                        "status": "partial",
                        "manifest_path": str(manifest_path.resolve()),
                        "receipt_path": str(receipt_path.resolve()),
                        "selected_status_counts": {"complete": 3, "failed": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _state_with_summary(state_path, db_path, "paused", summary_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert payload["health_status"] == "healthy"
    assert "paused_summary_mismatch" not in payload["issues"]
    assert payload["issues"] == []


@pytest.mark.parametrize(
    "status", ["planning", "recovering", "completed_with_failures", "planned", "paused"]
)
def test_extended_supervisor_statuses_are_valid(tmp_path, status):
    """planning/recovering/completed_with_failures must not be
    state_status_invalid (verified producer vocabulary)."""
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, ["pending"] * 3)
    state_path = tmp_path / "state.json"
    _state(state_path, db_path, status)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert "state_status_invalid" not in payload["issues"]


def test_completed_with_failures_with_no_work_summary(tmp_path):
    """completed_with_failures terminates after a no_work summary with
    terminalized failures: counts must reconcile and stay healthy."""
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [])
    summary_path = tmp_path / "summary.json"
    _summary(
        summary_path,
        "no_work",
        selected_count=0,
        complete_count=0,
        status_counts={},
    )
    state_path = tmp_path / "state.json"
    _state_with_summary(state_path, db_path, "completed_with_failures", summary_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert "completed_with_failures_summary_mismatch" not in payload["issues"]
    assert payload["health_status"] in {"healthy", "planned"}


def test_paused_still_rejects_failed_summary(tmp_path):
    """Vocabulary fix must not weaken validation: paused + failed summary
    is still a mismatch requiring attention."""
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, ["pending"] * 5)
    summary_path = tmp_path / "summary.json"
    _summary(
        summary_path,
        "failed",
        selected_count=4,
        complete_count=1,
        status_counts={"complete": 1, "failed": 3},
    )
    state_path = tmp_path / "state.json"
    _state_with_summary(state_path, db_path, "paused", summary_path)

    payload = mod.inspect_backlog(db_path=db_path, state_path=state_path)

    assert "paused_summary_mismatch" in payload["issues"]
    assert payload["health_status"] == "needs_attention"
