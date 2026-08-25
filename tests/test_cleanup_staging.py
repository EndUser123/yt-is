from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from csf.cleanup_staging import cleanup_staging, main


def _set_mtime(path: Path, epoch: float) -> None:
    path.touch(exist_ok=True)
    os.utime(path, (epoch, epoch))


def _experiment(root: Path, name: str, *, age_seconds: float, now: float) -> Path:
    experiment = root / name
    experiment.mkdir(parents=True)
    _set_mtime(experiment, now - age_seconds)
    return experiment


def test_dry_run_is_side_effect_free_and_preserves_receipts(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "run-1", age_seconds=2 * 3600, now=now)
    database = experiment / "batch_status.sqlite"
    wal = experiment / "batch_status.sqlite-wal"
    receipt = experiment / "result_receipt.md"
    database.write_bytes(b"db")
    wal.write_bytes(b"wal")
    receipt.write_text("receipt", encoding="utf-8")
    for path in (database, wal, receipt):
        _set_mtime(path, now - 2 * 3600)
    _set_mtime(experiment, now - 2 * 3600)

    report = cleanup_staging(tmp_path, dry_run=True, now=now, ledger_path=None)

    assert report["status"] == "dry_run"
    assert database.exists()
    assert wal.exists()
    assert receipt.read_text(encoding="utf-8") == "receipt"
    assert report["files_deleted"] == 2
    assert {item["path"] for item in report["actions"] if item["action"] == "delete_file"} == {
        str(database),
        str(wal),
    }


def test_cleanup_deletes_old_sqlite_and_keeps_receipts(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "run-1", age_seconds=2 * 3600, now=now)
    database = experiment / "transcripts.sqlite"
    shm = experiment / "transcripts.sqlite-shm"
    receipt_json = experiment / "summary.json"
    receipt_txt = experiment / "notes.txt"
    database.write_bytes(b"db")
    shm.write_bytes(b"shm")
    receipt_json.write_text("{}", encoding="utf-8")
    receipt_txt.write_text("notes", encoding="utf-8")
    for path in (database, shm, receipt_json, receipt_txt):
        _set_mtime(path, now - 2 * 3600)
    _set_mtime(experiment, now - 2 * 3600)

    report = cleanup_staging(tmp_path, now=now, ledger_path=None)

    assert report["status"] == "completed"
    assert not database.exists()
    assert not shm.exists()
    assert receipt_json.exists()
    assert receipt_txt.exists()


def test_recent_experiment_is_never_touched(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "active", age_seconds=30 * 60, now=now)
    database = experiment / "batch_status.sqlite"
    database.write_bytes(b"db")
    _set_mtime(database, now - 2 * 3600)

    report = cleanup_staging(tmp_path, now=now, ledger_path=None)

    assert report["experiments_skipped_active"] == 1
    assert database.exists()
    assert any(item["action"] == "skip_experiment" for item in report["actions"])


def test_recent_sqlite_is_preserved_inside_older_experiment(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "mixed", age_seconds=2 * 3600, now=now)
    old_db = experiment / "old.sqlite"
    recent_db = experiment / "recent.sqlite"
    old_db.write_bytes(b"old")
    recent_db.write_bytes(b"recent")
    _set_mtime(old_db, now - 2 * 3600)
    _set_mtime(recent_db, now - 10 * 60)
    _set_mtime(experiment, now - 2 * 3600)

    cleanup_staging(tmp_path, now=now, ledger_path=None)

    assert not old_db.exists()
    assert recent_db.exists()


def test_directories_older_than_retention_are_removed(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "old", age_seconds=8 * 86400, now=now)
    receipt = experiment / "receipt.md"
    receipt.write_text("historical", encoding="utf-8")
    _set_mtime(receipt, now - 8 * 86400)
    _set_mtime(experiment, now - 8 * 86400)

    report = cleanup_staging(tmp_path, now=now, ledger_path=None)

    assert report["directories_deleted"] == 1
    assert not experiment.exists()


def test_protected_queue_survives_and_canonical_root_is_rejected(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "queue", age_seconds=2 * 3600, now=now)
    queue = experiment / "transcript-fallback-queue.sqlite"
    queue.write_bytes(b"queue")
    _set_mtime(queue, now - 2 * 3600)
    _set_mtime(experiment, now - 2 * 3600)

    cleanup_staging(tmp_path, now=now, ledger_path=None)

    assert queue.exists()
    with pytest.raises(ValueError, match="protected path"):
        cleanup_staging(Path("P:/.data/yt-is"), dry_run=True, now=now, ledger_path=None)


def test_cli_dry_run_emits_json(tmp_path: Path, capsys) -> None:
    report = main(["--root", str(tmp_path), "--dry-run"])

    assert report == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["dry_run"] is True


def test_cli_default_scans_both_yt_is_experiment_roots(tmp_path: Path, monkeypatch, capsys) -> None:
    import importlib

    cleanup_mod = importlib.import_module("csf.cleanup_staging")
    parent_root = tmp_path / "workspace-logs" / "multi_account_fetch"
    package_root = tmp_path / "package-logs" / "multi_account_fetch"
    parent_root.mkdir(parents=True)
    package_root.mkdir(parents=True)
    monkeypatch.setattr(
        cleanup_mod,
        "DEFAULT_EXPERIMENT_ROOTS",
        (parent_root, package_root),
    )

    assert cleanup_mod.main(["--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["root_count"] == 2
    assert payload["roots"] == [str(parent_root.resolve()), str(package_root.resolve())]
    assert payload["status"] == "dry_run"


def test_multi_account_cli_runs_parent_cleanup_sweep(tmp_path: Path, monkeypatch, capsys) -> None:
    import scripts.run_multi_account_fetch as fetch_mod

    output_root = tmp_path / "experiment" / "run"
    monkeypatch.setattr(
        fetch_mod,
        "run_multi_account_fetch",
        lambda **kwargs: {"status": "completed"},
    )
    observed: dict[str, Path] = {}

    def fake_cleanup(root: Path, **kwargs):
        observed["root"] = Path(root)
        return {"status": "completed", "files_deleted": 0}

    monkeypatch.setattr(fetch_mod, "cleanup_staging", fake_cleanup)

    assert fetch_mod.main(["--limit", "1", "--output-root", str(output_root)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert observed["root"] == output_root.parent
    assert payload["staging_cleanup"]["status"] == "completed"


def test_multi_account_cli_persists_cleanup_in_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    import scripts.run_multi_account_fetch as fetch_mod

    output_root = tmp_path / "experiment" / "run"

    def fake_run(**kwargs):
        run_root = Path(kwargs["output_root"])
        run_root.mkdir(parents=True)
        summary_path = run_root / "multi_account_fetch_summary.json"
        payload = {"status": "completed", "summary_path": str(summary_path)}
        summary_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(fetch_mod, "run_multi_account_fetch", fake_run)
    monkeypatch.setattr(
        fetch_mod,
        "cleanup_staging",
        lambda root, **kwargs: {"status": "completed", "files_deleted": 2},
    )

    assert fetch_mod.main(["--limit", "1", "--output-root", str(output_root)]) == 0
    json.loads(capsys.readouterr().out)

    summary = json.loads((output_root / "multi_account_fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["staging_cleanup"] == {"status": "completed", "files_deleted": 2}


def test_unattended_cli_runs_parent_cleanup_sweep(tmp_path: Path, monkeypatch, capsys) -> None:
    import scripts.run_unattended_backlog as supervisor_mod

    output_root = tmp_path / "multi_account_fetch" / "unattended"
    monkeypatch.setattr(
        supervisor_mod,
        "run_supervisor",
        lambda config, timeout_s: {"status": "planned"},
    )
    observed: dict[str, Path] = {}

    def fake_cleanup(root: Path, **kwargs):
        observed["root"] = Path(root)
        return {"status": "completed", "files_deleted": 0}

    monkeypatch.setattr(supervisor_mod, "cleanup_staging", fake_cleanup)

    assert supervisor_mod.main([
        "--db-path", str(tmp_path / "batch.sqlite"),
        "--state-path", str(tmp_path / "state.json"),
        "--output-root", str(output_root),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert observed["root"] == output_root.parent
    assert payload["staging_cleanup"]["status"] == "completed"


def test_sweep_ledger_records_deleted_directories(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "old", age_seconds=8 * 86400, now=now)
    (experiment / "receipt.md").write_text("historical", encoding="utf-8")
    _set_mtime(experiment / "receipt.md", now - 8 * 86400)
    _set_mtime(experiment, now - 8 * 86400)
    ledger = tmp_path / "ledger.jsonl"

    report = cleanup_staging(tmp_path, now=now, ledger_path=ledger)

    assert report["directories_deleted"] == 1
    assert not experiment.exists()
    lines = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["action"] == "delete_directory"
    assert lines[0]["path"] == str(experiment)
    assert lines[0]["ts"]


def test_sweep_ledger_failure_never_fails_sweep(tmp_path: Path) -> None:
    now = time.time()
    experiment = _experiment(tmp_path, "old", age_seconds=8 * 86400, now=now)
    (experiment / "receipt.md").write_text("historical", encoding="utf-8")
    _set_mtime(experiment / "receipt.md", now - 8 * 86400)
    _set_mtime(experiment, now - 8 * 86400)
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("a file where a directory is needed", encoding="utf-8")
    bad_ledger = blocker / "sub" / "ledger.jsonl"  # mkdir under a file -> OSError

    report = cleanup_staging(tmp_path, now=now, ledger_path=bad_ledger)

    assert report["directories_deleted"] == 1  # deletion still succeeded
    assert any(":ledger:" in e for e in report["errors"])
