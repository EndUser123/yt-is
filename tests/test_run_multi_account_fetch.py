from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.run_multi_account_fetch as mod
from csf.video_selection_manifest import load_video_selection_manifest, read_selection_receipt


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE analysis_status ("
        "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, has_captions INTEGER, "
        "published_at TEXT, title TEXT, description TEXT, channel_id TEXT, thumbnail TEXT, "
        "duration INTEGER, privacy_status TEXT, upload_status TEXT, is_live_content INTEGER, "
        "unavailable_reason TEXT, last_stage TEXT, failure_reason TEXT)"
    )
    rows = [
        ("aaaaaaaaaaa", "pending", "2026-08-08T00:00:00+00:00", "source-a", None),
        ("bbbbbbbbbbb", "pending", "2026-08-08T00:01:00+00:00", "source-b", None),
        ("ccccccccccc", "pending", "2026-08-08T00:02:00+00:00", "source-c", 0),
        ("ddddddddddd", "complete", "2026-08-08T00:03:00+00:00", "source-d", None),
    ]
    conn.executemany(
        "INSERT INTO analysis_status (video_id, status, updated_at, source, has_captions) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _make_transcript_cache(path: Path, video_ids: tuple[str, ...]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE transcript_cache ("
        "cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT, source TEXT, "
        "transcript TEXT, metadata_json TEXT, cached_at TEXT, terminal_id TEXT)"
    )
    conn.executemany(
        "INSERT INTO transcript_cache "
        "(cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id) "
        "VALUES (?, ?, 'en', 'test', 'cached transcript', '{}', 'now', 'test')",
        [(f"{video_id}:en:test:{index}", video_id) for index, video_id in enumerate(video_ids)],
    )
    conn.commit()
    conn.close()


def _write_valid_child_receipt(spec: mod.AccountRunSpec, db_path: Path) -> None:
    manifest = load_video_selection_manifest(spec.manifest_path)
    rows = {
        str(row["video_id"]): dict(row)
        for row in mod.get_entries_for_video_ids_details(list(spec.video_ids), db_path=db_path)
    }
    selection = mod.select_manifest_entries(manifest, rows)
    receipt = mod.build_selection_receipt(
        manifest,
        selection,
        manifest_path=spec.manifest_path,
        database_path=db_path,
        max_items=None,
        dry_run=False,
    )
    mod.write_selection_receipt(spec.receipt_path, receipt)


def test_read_pending_rows_defaults_to_recent_uncategorized(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    rows = mod.read_pending_rows(db_path, recent_days=None)
    assert [row.video_id for row in rows] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert [row.video_id for row in mod.read_pending_rows(db_path, recent_days=None, include_categorized=True)] == [
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
        "ccccccccccc",
    ]


def test_read_pending_rows_uncached_only_excludes_distinct_cached_ids(
    tmp_path: Path, monkeypatch
) -> None:
    batch_db = tmp_path / "batch.sqlite"
    cache_db = tmp_path / "transcripts.sqlite"
    _make_db(batch_db)
    _make_transcript_cache(cache_db, ("bbbbbbbbbbb", "bbbbbbbbbbb"))
    monkeypatch.setattr(mod, "get_transcript_db_path", lambda: cache_db)

    rows = mod.read_pending_rows(
        batch_db,
        recent_days=None,
        include_categorized=True,
        uncached_only=True,
    )

    assert [row.video_id for row in rows] == ["aaaaaaaaaaa", "ccccccccccc"]
    with sqlite3.connect(cache_db) as conn:
        cached_ids = {
            row[0]
            for row in conn.execute("SELECT DISTINCT video_id FROM transcript_cache")
        }
    assert not ({row.video_id for row in rows} & cached_ids)


def test_summary_records_explicit_transcript_cache_path(tmp_path: Path, monkeypatch) -> None:
    ambient_cache_path = tmp_path / "ambient" / "transcripts.sqlite"
    explicit_cache_path = tmp_path / "staging" / "transcripts.sqlite"
    monkeypatch.setenv("YTIS_TRANSCRIPT_CACHE_DB_PATH", str(ambient_cache_path))

    payload = mod._summary_payload(
        run_id="run-1",
        db_path=tmp_path / "batch.sqlite",
        transcript_cache_db_path=explicit_cache_path,
        lock_path=tmp_path / "lock",
        accounts=("a.hominidae",),
        selected_count=0,
        recent_days=None,
        include_categorized=True,
        limit=0,
        selection_mode="test",
        workers_per_account=3,
        batch_size=50,
        parallel_accounts=True,
        dry_run=False,
        auth_preflight={},
        account_results=[],
        selected_status_counts={},
        status="no_work",
    )

    assert payload["transcript_cache_db_path"] == str(explicit_cache_path.resolve())


def test_read_pending_rows_uncached_only_applies_after_caption_state(
    tmp_path: Path, monkeypatch
) -> None:
    batch_db = tmp_path / "batch.sqlite"
    cache_db = tmp_path / "transcripts.sqlite"
    _make_db(batch_db)
    with sqlite3.connect(batch_db) as conn:
        conn.execute(
            "INSERT INTO analysis_status (video_id, status, updated_at, source, has_captions) "
            "VALUES (?, ?, ?, ?, ?)",
            ("eeeeeeeeeee", "pending", "2026-08-08T00:04:00+00:00", "source-e", 1),
        )
        conn.commit()
    _make_transcript_cache(cache_db, ("eeeeeeeeeee", "ccccccccccc"))
    monkeypatch.setattr(mod, "get_transcript_db_path", lambda: cache_db)

    rows = mod.read_pending_rows(
        batch_db,
        recent_days=None,
        caption_state="captioned",
        uncached_only=True,
    )

    assert [row.video_id for row in rows] == []


def test_read_pending_rows_default_is_identical_to_explicit_false(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)

    implicit = mod.read_pending_rows(db_path, recent_days=None)
    explicit = mod.read_pending_rows(db_path, recent_days=None, uncached_only=False)

    assert explicit == implicit


@pytest.mark.parametrize("cache_setup", ["missing", "invalid"])
def test_read_pending_rows_uncached_only_fails_closed_for_bad_cache(
    tmp_path: Path, monkeypatch, cache_setup: str
) -> None:
    batch_db = tmp_path / "batch.sqlite"
    cache_db = tmp_path / "transcripts.sqlite"
    _make_db(batch_db)
    if cache_setup == "invalid":
        cache_db.write_text("not a sqlite database", encoding="utf-8")
    monkeypatch.setattr(mod, "get_transcript_db_path", lambda: cache_db)

    with pytest.raises(RuntimeError, match="reference transcript cache DB"):
        mod.read_pending_rows(
            batch_db,
            recent_days=None,
            uncached_only=True,
        )


def test_uncached_only_plan_receipt_identifies_selection_mode(
    tmp_path: Path, monkeypatch
) -> None:
    batch_db = tmp_path / "batch.sqlite"
    cache_db = tmp_path / "transcripts.sqlite"
    _make_db(batch_db)
    _make_transcript_cache(cache_db, ("aaaaaaaaaaa",))
    monkeypatch.setattr(mod, "get_transcript_db_path", lambda: cache_db)

    payload = mod.run_multi_account_fetch(
        db_path=batch_db,
        output_root=tmp_path / "plan",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
        include_categorized=True,
        uncached_only=True,
        plan_only=True,
    )

    assert payload["candidate_scope"]["selection_mode"] == (
        "database_pending_scope:uncached_only"
    )
    receipt_path = Path(payload["account_results"][0]["receipt_path"])
    receipt = read_selection_receipt(receipt_path)
    assert receipt["selection_criteria"]["selection_mode"] == (
        "database_pending_scope:uncached_only"
    )
    assert "aaaaaaaaaaa" not in receipt["selected_ids"]


def test_uncached_only_can_use_reference_cache_separate_from_active_cache(
    tmp_path: Path, monkeypatch
) -> None:
    batch_db = tmp_path / "batch.sqlite"
    active_cache_db = tmp_path / "active-transcripts.sqlite"
    reference_cache_db = tmp_path / "reference-transcripts.sqlite"
    _make_db(batch_db)
    _make_transcript_cache(active_cache_db, ("bbbbbbbbbbb",))
    _make_transcript_cache(reference_cache_db, ("aaaaaaaaaaa",))
    monkeypatch.setattr(mod, "get_transcript_db_path", lambda: active_cache_db)

    rows = mod.read_pending_rows(
        batch_db,
        recent_days=None,
        include_categorized=True,
        uncached_only=True,
        uncached_reference_cache_db_path=reference_cache_db,
    )

    assert [row.video_id for row in rows] == [
        "bbbbbbbbbbb",
        "ccccccccccc",
    ]


def test_read_pending_rows_supports_explicit_caption_state_cohorts(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO analysis_status (video_id, status, updated_at, source, has_captions) "
            "VALUES (?, ?, ?, ?, ?)",
            ("eeeeeeeeeee", "pending", "2026-08-08T00:04:00+00:00", "source-e", 1),
        )
        conn.commit()

    assert [row.video_id for row in mod.read_pending_rows(
        db_path, recent_days=None, caption_state="unknown"
    )] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert [row.video_id for row in mod.read_pending_rows(
        db_path, recent_days=None, caption_state="captioned"
    )] == ["eeeeeeeeeee"]
    assert [row.video_id for row in mod.read_pending_rows(
        db_path, recent_days=None, caption_state="no-caption"
    )] == ["ccccccccccc"]
    assert [row.video_id for row in mod.read_pending_rows(
        db_path, recent_days=None, caption_state="any"
    )] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "eeeeeeeeeee"]


def test_read_pending_rows_rejects_unknown_caption_state(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    with pytest.raises(ValueError, match="caption_state"):
        mod.read_pending_rows(db_path, recent_days=None, caption_state="mixed")


def test_read_exact_pending_rows_preserves_order_and_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    rows = mod.read_exact_pending_rows(db_path, ("bbbbbbbbbbb", "aaaaaaaaaaa"))
    assert [row.video_id for row in rows] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]

    try:
        mod.read_exact_pending_rows(db_path, ("ddddddddddd",))
    except RuntimeError as exc:
        assert "not_pending" in str(exc)
    else:
        raise AssertionError("non-pending exact retry selection was accepted")


def test_prepare_account_specs_round_robin_and_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    explicit_cache_path = tmp_path / "staging" / "transcripts.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE analysis_status SET status = 'pending' WHERE video_id = ?",
            ("ddddddddddd",),
        )
        conn.commit()
    rows = [
        mod.PendingRow(video_id=video_id, status="pending", source=None, updated_at="now", has_captions=None)
        for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd")
    ]
    specs = mod.prepare_account_specs(
        rows=rows,
        accounts=("a.hominidae", "troup.hominidae", "brsthomson"),
        output_root=tmp_path / "run",
        run_id="run01",
        db_path=db_path,
        transcript_cache_db_path=explicit_cache_path,
    )
    assert [spec.account_profile for spec in specs] == ["a.hominidae", "troup.hominidae", "brsthomson"]
    assert [spec.video_ids for spec in specs] == [
        ("aaaaaaaaaaa", "ddddddddddd"),
        ("bbbbbbbbbbb",),
        ("ccccccccccc",),
    ]
    assert all(load_video_selection_manifest(spec.manifest_path).items for spec in specs)
    assert all("multi-account" in spec.manifest_path.read_text(encoding="utf-8") for spec in specs)
    assert all(spec.batch_db_path == db_path for spec in specs)
    assert all(spec.transcript_cache_db_path == explicit_cache_path for spec in specs)
    assert all(spec.run_id == "run01" for spec in specs)


def test_run_account_scopes_existing_fetcher_to_identity(tmp_path: Path, monkeypatch) -> None:
    explicit_cache_path = tmp_path / "isolated" / "transcripts.sqlite"
    spec = mod.AccountRunSpec(
        account_profile="troup.hominidae",
        video_ids=("aaaaaaaaaaa",),
        batch_db_path=tmp_path / "batch.sqlite",
        manifest_path=tmp_path / "manifest.json",
        receipt_path=tmp_path / "receipt.json",
        stdout_path=tmp_path / "account" / "stdout.txt",
        stderr_path=tmp_path / "account" / "stderr.txt",
        state_root=tmp_path / "state",
        notebook_prefix="troup.hominidae-worker",
        run_id="run01",
        transcript_cache_db_path=explicit_cache_path,
    )
    spec.manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "test",
                "videos": [{"video_id": "aaaaaaaaaaa"}],
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    class FakeProcess:
        def wait(self, timeout=None):
            return 0

    def fake_popen(command, *, cwd, env, stdout, stderr, **_kwargs):
        captured.update(command=command, cwd=cwd, env=env)
        stdout.write("ok\n")
        return FakeProcess()

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("YTIS_INDUSTRIAL_ADAPTIVE_WORKERS", "1")
    fixed_result = mod._run_account(spec, workers_per_account=3)
    assert fixed_result["status"] == "completed"
    assert "YTIS_INDUSTRIAL_ADAPTIVE_WORKERS" not in captured["env"]

    result = mod._run_account(
        spec,
        workers_per_account=3,
        batch_size=25,
        adaptive_worker_options=("--adaptive-workers", "--adaptive-max-workers", "5"),
    )
    assert result["status"] == "completed"
    assert captured["env"]["YTIS_NLM_ACCOUNT_PROFILE"] == "troup.hominidae"
    assert captured["env"]["YTIS_BATCH_STATUS_DB_PATH"] == str(spec.batch_db_path)
    assert captured["env"]["YTIS_TRANSCRIPT_CACHE_DB_PATH"] == str(explicit_cache_path.resolve())
    assert captured["env"]["YTIS_INDUSTRIAL_WORKER_STATE_ROOT"] == str(spec.state_root)
    assert captured["env"]["YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX"] == spec.notebook_prefix
    assert captured["env"]["YTIS_INDUSTRIAL_RUN_ID"] == "run01"
    assert captured["env"]["YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID"] == "run01"
    assert captured["env"]["YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_PID"] == str(os.getpid())
    assert captured["env"]["YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_DB_PATH"] == str(spec.batch_db_path.resolve())
    assert captured["env"]["YTIS_NLM_BATCH_SIZE"] == "25"
    assert "--video-manifest" in captured["command"]
    assert captured["command"][captured["command"].index("--workers") + 1] == "3"
    assert captured["command"][-3:] == ["--adaptive-workers", "--adaptive-max-workers", "5"]

    route_result = mod._run_account(
        spec,
        workers_per_account=1,
        route_no_captions_to_fallback=True,
    )
    assert route_result["route_no_captions_to_fallback"] is True
    assert captured["env"]["YTIS_ROUTE_NO_CAPTIONS_TO_FALLBACK"] == "true"
    assert "YTIS_TRANSCRIPT_ROUTE_NO_CAPTIONS_TO_FALLBACK" not in captured["env"]

    fallback_result = mod._run_account(
        spec,
        workers_per_account=1,
        fallback_only=True,
        transcript_fallback_timeout_s=777.0,
    )
    assert fallback_result["fallback_only"] is True
    assert fallback_result["transcript_fallback_timeout_s"] == 777.0
    assert captured["env"]["YTIS_TRANSCRIPT_FALLBACK_TIMEOUT_S"] == "777.0"
    assert captured["env"]["YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED"] == "1"
    assert captured["env"]["YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH"] == str(
        spec.state_root / "transcript-fallback-queue.sqlite"
    )
    assert captured["command"][-1] == "--fallback-only"


def test_run_account_performs_parent_cleanup_after_timeout(tmp_path: Path, monkeypatch) -> None:
    spec = mod.AccountRunSpec(
        account_profile="a.hominidae",
        video_ids=("aaaaaaaaaaa",),
        batch_db_path=tmp_path / "batch.sqlite",
        manifest_path=tmp_path / "manifest.json",
        receipt_path=tmp_path / "receipt.json",
        stdout_path=tmp_path / "account" / "stdout.txt",
        stderr_path=tmp_path / "account" / "stderr.txt",
        state_root=tmp_path / "state",
        notebook_prefix="a.hominidae-worker",
        run_id="run-timeout",
    )
    spec.manifest_path.write_text(
        json.dumps({
            "manifest_version": 1,
            "generated_at": "now",
            "selection_name": "test",
            "videos": [{"video_id": "aaaaaaaaaaa"}],
        }),
        encoding="utf-8",
    )

    class FakeProcess:
        pid = 1234

        def __init__(self):
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("child", timeout)
            return -9

        def kill(self):
            return None

    process = FakeProcess()
    cleanup_calls = []

    def fake_popen(*_args, **_kwargs):
        return process

    def fake_run(command, **kwargs):
        cleanup_calls.append((command, kwargs))
        Path(kwargs["env"]["YTIS_NLM_CLEANUP_RECEIPT_PATH"]).write_text(
            json.dumps({"outcome": "deleted", "deleted": 2, "failed": 0}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "deleted=2", "")

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mod, "_terminate_process_tree", lambda _process: None)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    result = mod._run_account(spec, workers_per_account=1, child_timeout_s=1)
    assert result["timed_out"] is True, result
    assert result["timeout_cleanup"]["status"] == "completed"
    assert result["timeout_cleanup"]["outcome"] == "deleted"
    assert cleanup_calls[0][0][-3:] == ["--delete", "--include-active", "--only-current-state"]
    assert cleanup_calls[0][1]["env"]["YTIS_NLM_ACCOUNT_PROFILE"] == "a.hominidae"
    assert cleanup_calls[0][1]["env"]["YTIS_INDUSTRIAL_WORKER_STATE_ROOT"] == str(spec.state_root)


def test_timeout_cleanup_zero_return_without_receipt_is_unverified(tmp_path: Path, monkeypatch) -> None:
    spec = mod.AccountRunSpec(
        account_profile="a.hominidae",
        video_ids=("aaaaaaaaaaa",),
        batch_db_path=tmp_path / "batch.sqlite",
        manifest_path=tmp_path / "manifest.json",
        receipt_path=tmp_path / "receipt.json",
        stdout_path=tmp_path / "account" / "stdout.txt",
        stderr_path=tmp_path / "account" / "stderr.txt",
        state_root=tmp_path / "state",
        notebook_prefix="a.hominidae-worker",
        run_id="run-timeout-no-receipt",
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "deleted=2", "")

    spec.stderr_path.parent.mkdir(parents=True)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod._cleanup_account_after_timeout(spec)

    assert result["returncode"] == 0
    assert result["status"] == "unverified"
    assert result["outcome"] == "unverified"
    assert result["receipt_verified"] is False


def test_run_account_records_unconfirmed_child_termination(tmp_path: Path, monkeypatch) -> None:
    spec = mod.AccountRunSpec(
        account_profile="a.hominidae",
        video_ids=("aaaaaaaaaaa",),
        batch_db_path=tmp_path / "batch.sqlite",
        manifest_path=tmp_path / "manifest.json",
        receipt_path=tmp_path / "receipt.json",
        stdout_path=tmp_path / "account" / "stdout.txt",
        stderr_path=tmp_path / "account" / "stderr.txt",
        state_root=tmp_path / "state",
        notebook_prefix="a.hominidae-worker",
        run_id="run-termination-failure",
    )
    spec.manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "generated_at": "now",
                "selection_name": "test",
                "videos": [{"video_id": "aaaaaaaaaaa"}],
            }
        ),
        encoding="utf-8",
    )

    class NeverExits:
        pid = 5678

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("child", timeout)

        def kill(self):
            return None

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: NeverExits())
    monkeypatch.setattr(mod, "_terminate_process_tree", lambda _process: None)
    monkeypatch.setattr(
        mod,
        "_cleanup_account_after_timeout",
        lambda _spec: {"status": "unverified", "outcome": "unverified"},
    )

    result = mod._run_account(spec, workers_per_account=1, child_timeout_s=1)

    assert result["timed_out"] is True
    assert result["termination_status"] == "termination_failure"
    assert "termination_failure" in result["error"]
    assert result["status"] == "failed"


def test_account_settings_override_workers_batch_and_adaptive_policy(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "a.hominidae": {"workers_per_account": 2, "batch_size": 25},
                "troup.hominidae": {
                    "workers_per_account": 5,
                    "batch_size": 50,
                    "adaptive_workers": True,
                    "adaptive_max_workers": 6,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )
    captured: dict[str, dict[str, object]] = {}

    def fake_run(
        spec,
        *,
        workers_per_account,
        dry_run,
        batch_size=None,
        adaptive_worker_options=(),
        route_no_captions_to_fallback=False,
    ):
        captured[spec.account_profile] = {
            "workers_per_account": workers_per_account,
            "batch_size": batch_size,
            "adaptive_worker_options": adaptive_worker_options,
            "route_no_captions_to_fallback": route_no_captions_to_fallback,
        }
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae", "troup.hominidae"),
        limit=2,
        recent_days=None,
        workers_per_account=3,
        account_settings_path=settings_path,
    )

    assert payload["status"] == "completed"
    assert captured["a.hominidae"] == {
        "workers_per_account": 2,
        "batch_size": 25,
        "adaptive_worker_options": (),
        "route_no_captions_to_fallback": False,
    }
    assert captured["troup.hominidae"]["workers_per_account"] == 5
    assert captured["troup.hominidae"]["batch_size"] == 50
    assert "--adaptive-workers" in captured["troup.hominidae"]["adaptive_worker_options"]
    assert payload["account_settings_path"] == str(settings_path.resolve())
    assert payload["account_settings_file_fingerprint"].startswith("sha256:")
    assert payload["account_settings"]["a.hominidae"]["batch_size"] == 25
    assert payload["account_settings"]["troup.hominidae"]["workers_per_account"] == 5
    assert payload["route_no_captions_to_fallback"] is False


def test_account_settings_matrix_preserves_all_canonical_profiles(tmp_path: Path) -> None:
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "a.hominidae": {
                    "workers_per_account": 2,
                    "batch_size": 25,
                    "adaptive_workers": True,
                    "adaptive_min_workers": 1,
                    "adaptive_max_workers": 5,
                },
                "troup.hominidae": {
                    "workers_per_account": 4,
                    "batch_size": 40,
                },
                "brsthomson": {
                    "workers_per_account": 1,
                    "batch_size": 10,
                    "adaptive_workers": True,
                    "adaptive_min_workers": 1,
                    "adaptive_max_workers": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = mod._load_account_settings(
        path=settings_path,
        accounts=("a.hominidae", "troup.hominidae", "brsthomson"),
        workers_per_account=3,
        batch_size=50,
        adaptive_workers=False,
        adaptive_min_workers=1,
        adaptive_max_workers=None,
        adaptive_scale_up_backlog=2,
        adaptive_scale_down_backlog=0,
        adaptive_cooldown_s=60.0,
        adaptive_health_window=2,
    )

    assert {profile: value.workers_per_account for profile, value in loaded.items()} == {
        "a.hominidae": 2,
        "troup.hominidae": 4,
        "brsthomson": 1,
    }
    assert {profile: value.batch_size for profile, value in loaded.items()} == {
        "a.hominidae": 25,
        "troup.hominidae": 40,
        "brsthomson": 10,
    }
    assert loaded["a.hominidae"].adaptive_worker_policy["enabled"] is True
    assert loaded["brsthomson"].adaptive_worker_policy["max_workers"] == 3
    assert loaded["troup.hominidae"].adaptive_worker_policy["enabled"] is False


def test_run_multi_account_fetch_forwards_fallback_only_for_exact_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )
    captured: list[bool] = []

    def fake_run(
        spec,
        *,
        workers_per_account,
        dry_run,
        route_no_captions_to_fallback=False,
        fallback_only=False,
        transcript_fallback_timeout_s=mod.DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
    ):
        captured.append(fallback_only)
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
        video_ids=("aaaaaaaaaaa",),
        fallback_only=True,
    )

    assert payload["status"] == "completed"
    assert payload["fallback_only"] is True
    assert captured == [True]
    assert payload["account_results"][0]["execution_settings"]["fallback_only"] is True


def test_run_multi_account_fetch_forwards_source_addressability_route(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )
    captured: list[bool] = []

    def fake_run(
        spec,
        *,
        workers_per_account,
        dry_run,
        route_no_captions_to_fallback=False,
        route_source_addressability_failures_to_fallback=False,
        transcript_fallback_timeout_s=mod.DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
    ):
        captured.append(route_source_addressability_failures_to_fallback)
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
        video_ids=("aaaaaaaaaaa",),
        route_source_addressability_failures_to_fallback=True,
    )

    assert payload["status"] == "completed"
    assert payload["route_source_addressability_failures_to_fallback"] is True
    assert captured == [True]
    assert payload["account_results"][0]["execution_settings"][
        "route_source_addressability_failures_to_fallback"
    ] is True


def test_account_settings_reject_unknown_accounts_and_keys(tmp_path: Path) -> None:
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text(json.dumps({"unknown": {"workers_per_account": 2}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown account profiles"):
        mod._load_account_settings(
            path=settings_path,
            accounts=("a.hominidae",),
            workers_per_account=3,
            batch_size=None,
            adaptive_workers=False,
            adaptive_min_workers=1,
            adaptive_max_workers=None,
            adaptive_scale_up_backlog=2,
            adaptive_scale_down_backlog=0,
            adaptive_cooldown_s=60.0,
            adaptive_health_window=2,
        )


def test_account_settings_all_account_file_projects_to_selected_subset(tmp_path: Path) -> None:
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "a.hominidae": {"workers_per_account": 5},
                "troup.hominidae": {"workers_per_account": 4},
                "brsthomson": {"workers_per_account": 2},
            }
        ),
        encoding="utf-8",
    )
    loaded = mod._load_account_settings(
        path=settings_path,
        accounts=("troup.hominidae",),
        workers_per_account=3,
        batch_size=None,
        adaptive_workers=False,
        adaptive_min_workers=1,
        adaptive_max_workers=None,
        adaptive_scale_up_backlog=2,
        adaptive_scale_down_backlog=0,
        adaptive_cooldown_s=60.0,
        adaptive_health_window=2,
    )
    assert tuple(loaded) == ("troup.hominidae",)
    assert loaded["troup.hominidae"].workers_per_account == 4


def test_adaptive_worker_policy_is_bounded_and_explicit() -> None:
    policy = mod._build_adaptive_worker_policy(
        enabled=True,
        workers_per_account=3,
        min_workers=1,
        max_workers=5,
        scale_up_backlog=2,
        scale_down_backlog=0,
        cooldown_s=60.0,
        health_window=2,
    )
    assert policy["policy_version"] == "adaptive-worker-scheduler-v1"
    assert policy["initial_workers"] == 3
    options = mod._adaptive_worker_command_options(policy)
    assert options[:5] == (
        "--adaptive-workers",
        "--adaptive-min-workers",
        "1",
        "--adaptive-max-workers",
        "5",
    )
    with pytest.raises(ValueError, match="adaptive_max_workers is required"):
        mod._build_adaptive_worker_policy(
            enabled=True,
            workers_per_account=3,
            min_workers=1,
            max_workers=None,
            scale_up_backlog=2,
            scale_down_backlog=0,
            cooldown_s=60.0,
            health_window=2,
        )


def test_safe_exception_reason_redacts_credential_shaped_values() -> None:
    reason = mod._safe_exception_reason(RuntimeError("authorization: Bearer secret-token"))
    assert reason.startswith("RuntimeError:")
    assert "secret-token" not in reason
    assert "[REDACTED]" in reason
    with pytest.raises(ValueError, match="adaptive_max_workers must be >="):
        mod._build_adaptive_worker_policy(
            enabled=True,
            workers_per_account=3,
            min_workers=1,
            max_workers=2,
            scale_up_backlog=2,
            scale_down_backlog=0,
            cooldown_s=60.0,
            health_window=2,
        )


def test_run_account_does_not_persist_raw_spawn_exception_text(tmp_path: Path, monkeypatch) -> None:
    spec = mod.AccountRunSpec(
        account_profile="a.hominidae",
        video_ids=("aaaaaaaaaaa",),
        batch_db_path=tmp_path / "batch.sqlite",
        manifest_path=tmp_path / "manifest.json",
        receipt_path=tmp_path / "receipt.json",
        stdout_path=tmp_path / "account" / "stdout.txt",
        stderr_path=tmp_path / "account" / "stderr.txt",
        state_root=tmp_path / "state",
        notebook_prefix="a.hominidae-worker",
        run_id="run01",
    )
    spec.manifest_path.write_text(
        json.dumps({"manifest_version": 1, "generated_at": "now", "selection_name": "test", "videos": [{"video_id": "aaaaaaaaaaa"}]}),
        encoding="utf-8",
    )

    def fail_popen(*args, **kwargs):
        raise RuntimeError("Bearer secret-token")

    monkeypatch.setattr(mod.subprocess, "Popen", fail_popen)
    result = mod._run_account(spec, workers_per_account=1)

    assert result["status"] == "failed"
    assert result["error"] == "RuntimeError: [REDACTED]"
    assert "secret-token" not in str(result)


def test_preflight_accounts_probes_every_account_and_records_exceptions(monkeypatch) -> None:
    calls: list[str] = []

    def fake_probe(account_profile, *, worker_id, allow_bootstrap):
        assert worker_id == "multi-account-coordinator"
        assert allow_bootstrap is False
        calls.append(account_profile)
        if account_profile == "a.hominidae":
            raise RuntimeError("Bearer secret-token")
        return SimpleNamespace(
            ok=True,
            reason="token_only",
            expected_email=f"{account_profile}@example.test",
            observed_email=f"{account_profile}@example.test",
            storage_path=f"/tmp/{account_profile}",
        )

    monkeypatch.setattr(mod, "ensure_account_session", fake_probe)
    with pytest.raises(mod.AccountPreflightError) as caught:
        mod._preflight_accounts(("a.hominidae", "troup.hominidae", "brsthomson"))

    assert calls == ["a.hominidae", "troup.hominidae", "brsthomson"]
    assert caught.value.results["a.hominidae"]["ok"] is False
    assert "probe_exception:RuntimeError" in caught.value.results["a.hominidae"]["reason"]
    assert "secret-token" not in str(caught.value.results)
    assert caught.value.results["troup.hominidae"]["ok"] is True


def test_classify_outcome_does_not_call_partial_child_success_completed() -> None:
    assert mod.classify_outcome(
        selected_count=50,
        status_counts={"complete": 38, "pending": 12},
        process_failed=False,
        dry_run=False,
    ) == "partial"


def test_classify_outcome_requires_database_completion() -> None:
    assert mod.classify_outcome(
        selected_count=50,
        status_counts={"pending": 50},
        process_failed=False,
        dry_run=False,
    ) == "failed"
    assert mod.classify_outcome(
        selected_count=50,
        status_counts={"complete": 50},
        process_failed=False,
        dry_run=False,
    ) == "completed"


def test_classify_outcome_marks_dry_run_and_empty_scope() -> None:
    assert mod.classify_outcome(
        selected_count=10,
        status_counts={"pending": 10},
        process_failed=False,
        dry_run=True,
    ) == "planned"
    assert mod.classify_outcome(
        selected_count=0,
        status_counts={},
        process_failed=False,
        dry_run=False,
    ) == "no_work"


def test_plan_only_writes_revalidated_receipts_without_launching_children(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)

    def fail_run(*args, **kwargs):
        raise AssertionError("plan-only mode must not launch a child")

    monkeypatch.setattr(mod, "_run_account", fail_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "plan",
        accounts=("a.hominidae", "troup.hominidae"),
        limit=3,
        recent_days=None,
        include_categorized=True,
        plan_only=True,
    )

    assert payload["status"] == "planned"
    assert payload["plan_only"] is True
    assert payload["dry_run"] is False
    assert payload["auth_preflight"] == {}
    assert payload["selected_status_counts"] == {"pending": 3}
    assert all(row["status"] == "planned" for row in payload["account_results"])
    assert all(row["process_status"] == "not_started" for row in payload["account_results"])
    assert all(row["workers_per_account"] == 3 for row in payload["account_results"])
    assert all("adaptive_worker_options" in row for row in payload["account_results"])
    assert all(Path(row["manifest_path"]).is_file() for row in payload["account_results"])
    assert all(Path(row["receipt_path"]).is_file() for row in payload["account_results"])
    assert all(
        read_selection_receipt(Path(row["receipt_path"]))["plan_only"] is True
        for row in payload["account_results"]
    )


def test_coordinator_accepts_supervisor_runtime_marker_in_existing_root(tmp_path: Path) -> None:
    """The supervisor owns the chunk directory before launching this process."""
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    output_root = tmp_path / "supervised"
    output_root.mkdir()
    (output_root / "supervisor_runtime.json").write_text("{}\n", encoding="utf-8")

    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=output_root,
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
        include_categorized=True,
        plan_only=True,
    )

    assert payload["status"] == "planned"
    assert (output_root / "multi_account_fetch_summary.json").is_file()


def test_plan_only_and_dry_run_are_mutually_exclusive(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    with pytest.raises(ValueError, match="cannot be combined"):
        mod.run_multi_account_fetch(
            db_path=db_path,
            output_root=tmp_path / "plan",
            accounts=("a.hominidae",),
            limit=1,
            recent_days=None,
            dry_run=True,
            plan_only=True,
        )


def test_write_summary_replaces_atomically_and_leaves_no_temporary_receipt(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    output_root.mkdir()
    payload = mod._write_summary(output_root, {"status": "completed"})

    summary_path = Path(payload["summary_path"])
    assert summary_path.is_file()
    assert json.loads(summary_path.read_text(encoding="utf-8"))["status"] == "completed"
    assert not list(output_root.glob(".multi_account_fetch_summary.json.*.tmp"))


def test_read_selected_status_counts_chunks_large_selection(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analysis_status ("
        "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, has_captions INTEGER, "
        "published_at TEXT, title TEXT, description TEXT, channel_id TEXT, thumbnail TEXT, "
        "duration INTEGER, privacy_status TEXT, upload_status TEXT, is_live_content INTEGER, "
        "unavailable_reason TEXT, last_stage TEXT, failure_reason TEXT)"
    )
    rows = [(f"{index:011d}", "complete", "now", None, None) for index in range(901)]
    conn.executemany(
        "INSERT INTO analysis_status (video_id, status, updated_at, source, has_captions) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    assert mod.read_selected_status_counts(db_path, tuple(row[0] for row in rows)) == {"complete": 901}


def test_run_multi_account_fetch_reconciles_child_results_and_writes_summary(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)

    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {
            profile: {
                "ok": True,
                "reason": "token_only",
                "expected_email": f"{profile}@example.test",
                "observed_email": f"{profile}@example.test",
                "storage_path": f"/tmp/{profile}",
            }
            for profile in accounts
        },
    )

    def fake_run(spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False):
        assert workers_per_account == 3
        assert dry_run is False
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                [(video_id,) for video_id in spec.video_ids],
            )
        return {
            "account_profile": spec.account_profile,
            "video_count": len(spec.video_ids),
            "returncode": 0,
            "status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae", "troup.hominidae"),
        limit=2,
        recent_days=None,
    )

    assert payload["status"] == "completed"
    assert payload["selected_count"] == 2
    assert payload["selected_status_counts"] == {"complete": 2}
    summary_path = Path(payload["summary_path"])
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["summary_path"] == str(summary_path)
    assert summary["status"] == "completed"


def test_run_multi_account_fetch_rejects_success_without_child_selection_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )

    def fake_run(spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False):
        return {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "process_status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
    )

    assert payload["status"] == "failed"
    result = payload["account_results"][0]
    assert result["process_status"] == "artifact_validation_failed"
    assert str(result["error"]).startswith("selection_artifact_gate:")


def test_run_multi_account_fetch_rejects_success_with_mismatched_selected_ids(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )

    def fake_run(spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False):
        manifest = load_video_selection_manifest(spec.manifest_path)
        with sqlite3.connect(db_path) as conn:
            rows = {
                video_id: {"video_id": video_id, "status": status}
                for video_id, status in conn.execute(
                    "SELECT video_id, status FROM analysis_status WHERE video_id IN (?, ?)",
                    spec.video_ids,
                )
            }
        selection = mod.select_manifest_entries(manifest, rows)
        receipt = mod.build_selection_receipt(
            manifest,
            selection,
            manifest_path=spec.manifest_path,
            database_path=db_path,
            max_items=None,
            dry_run=False,
        )
        receipt["selected_ids"] = list(reversed(receipt["selected_ids"]))
        mod.write_selection_receipt(spec.receipt_path, receipt)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "error": None,
        }

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
    )

    assert payload["status"] == "partial"
    result = payload["account_results"][0]
    assert result["process_status"] == "artifact_validation_failed"
    assert "selected_ids" in str(result["error"])


def _selection_artifact_fixture(tmp_path: Path) -> tuple[Path, mod.AccountRunSpec]:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    specs = mod.prepare_account_specs(
        rows=mod.read_pending_rows(db_path, recent_days=None),
        accounts=("a.hominidae",),
        output_root=tmp_path / "run",
        run_id="run01",
        db_path=db_path,
    )
    spec = specs[0]
    _write_valid_child_receipt(spec, db_path)
    return db_path, spec


def test_selection_artifact_gate_accepts_fresh_receipt_and_persists_snapshot(tmp_path: Path) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)

    result = mod._validate_child_selection_artifacts(spec, db_path)

    assert result["ok"] is True
    receipt = read_selection_receipt(spec.receipt_path)
    assert receipt["coordinator_snapshot_version"] == 1
    assert [row["video_id"] for row in receipt["database_snapshot_rows"]] == list(spec.video_ids)


def test_selection_artifact_gate_accepts_receipt_after_post_run_status_transition(
    tmp_path: Path,
) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
            [(video_id,) for video_id in spec.video_ids],
        )
        conn.commit()

    result = mod._validate_child_selection_artifacts(spec, db_path)

    assert result["ok"] is True
    receipt = read_selection_receipt(spec.receipt_path)
    assert receipt["coordinator_snapshot_version"] == 1
    assert all(row["status"] == "pending" for row in receipt["database_snapshot_rows"])


@pytest.mark.parametrize("field", ["database_fingerprint", "selection_fingerprint"])
def test_selection_artifact_gate_rejects_forged_fingerprint(tmp_path: Path, field: str) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)
    receipt = read_selection_receipt(spec.receipt_path)
    receipt[field] = "sha256:forged"
    mod.write_selection_receipt(spec.receipt_path, receipt, overwrite=True)

    with pytest.raises(RuntimeError, match=field):
        mod._validate_child_selection_artifacts(spec, db_path)


def test_selection_artifact_gate_rejects_stale_non_pending_snapshot(tmp_path: Path) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)
    receipt = read_selection_receipt(spec.receipt_path)
    receipt["database_snapshot_rows"] = [dict(row) for row in spec.pre_run_rows]
    receipt["database_snapshot_rows"][0]["status"] = "complete"
    mod.write_selection_receipt(spec.receipt_path, receipt, overwrite=True)

    with pytest.raises(RuntimeError, match="database_snapshot_rows"):
        mod._validate_child_selection_artifacts(spec, db_path)


@pytest.mark.parametrize("mutation", ["run_id", "account_profile", "manifest_path"])
def test_selection_artifact_gate_rejects_cross_run_account_or_manifest_identity(
    tmp_path: Path, mutation: str
) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)
    receipt = read_selection_receipt(spec.receipt_path)
    receipt[mutation] = {
        "run_id": "other-run",
        "account_profile": "troup.hominidae",
        "manifest_path": str(tmp_path / "other-manifest.json"),
    }[mutation]
    mod.write_selection_receipt(spec.receipt_path, receipt, overwrite=True)

    with pytest.raises(RuntimeError, match=mutation):
        mod._validate_child_selection_artifacts(spec, db_path)


def test_selection_artifact_gate_rejects_duplicate_ids(tmp_path: Path) -> None:
    db_path, spec = _selection_artifact_fixture(tmp_path)
    receipt = read_selection_receipt(spec.receipt_path)
    receipt["selected_ids"] = [spec.video_ids[0], spec.video_ids[0]]
    mod.write_selection_receipt(spec.receipt_path, receipt, overwrite=True)

    with pytest.raises(RuntimeError, match="selected_ids"):
        mod._validate_child_selection_artifacts(spec, db_path)


def test_run_multi_account_fetch_forwards_adaptive_policy_to_each_child(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )
    captured: list[tuple[str, ...]] = []

    def fake_run(
        spec,
        *,
        workers_per_account,
        dry_run,
        adaptive_worker_options,
        route_no_captions_to_fallback=False,
    ):
        assert workers_per_account == 3
        assert dry_run is False
        captured.append(adaptive_worker_options)
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {"account_profile": spec.account_profile, "returncode": 0, "status": "completed", "error": None}

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
        adaptive_workers=True,
        adaptive_max_workers=5,
    )

    assert payload["status"] == "completed"
    assert payload["adaptive_worker_policy"] == {
        "enabled": True,
        "initial_workers": 3,
        "min_workers": 1,
        "max_workers": 5,
        "scale_up_backlog": 2,
        "scale_down_backlog": 0,
        "cooldown_s": 60.0,
        "health_window": 2,
        "policy_version": "adaptive-worker-scheduler-v1",
    }
    assert len(captured) == 1
    assert "--adaptive-workers" in captured[0]
    assert captured[0][captured[0].index("--adaptive-max-workers") + 1] == "5"


def test_run_multi_account_fetch_classifies_child_success_with_pending_rows_as_partial(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )

    def fake_run(spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False):
        _write_valid_child_receipt(spec, db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
        return {"account_profile": spec.account_profile, "returncode": 0, "status": "completed", "error": None}

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
    )

    assert payload["status"] == "partial"
    assert payload["selected_status_counts"] == {"complete": 1, "pending": 1}
    assert payload["selected_missing_video_ids"] == []
    assert payload["account_results"][0]["process_status"] == "completed"
    assert payload["account_results"][0]["status"] == "partial"


def test_run_multi_account_fetch_does_not_report_post_child_missing_row_as_success(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )

    def fake_run(spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'complete' WHERE video_id = ?",
                (spec.video_ids[0],),
            )
            conn.execute("DELETE FROM analysis_status WHERE video_id = ?", (spec.video_ids[1],))
        return {"account_profile": spec.account_profile, "returncode": 0, "status": "completed", "error": None}

    monkeypatch.setattr(mod, "_run_account", fake_run)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
    )

    assert payload["status"] == "partial"
    assert payload["selected_count"] == 2
    assert payload["selected_status_counts"] == {"complete": 1}
    assert payload["selected_missing_video_ids"] == ["bbbbbbbbbbb"]
    assert payload["account_results"][0]["selected_missing_video_ids"] == ["bbbbbbbbbbb"]
    assert payload["selected_complete_count"] < payload["selected_count"]


def test_run_multi_account_fetch_persists_reconciliation_query_failure(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: {profile: {"ok": True, "reason": "token_only"} for profile in accounts},
    )
    monkeypatch.setattr(
        mod,
        "_run_account",
        lambda spec, *, workers_per_account, dry_run, route_no_captions_to_fallback=False: {
            "account_profile": spec.account_profile,
            "returncode": 0,
            "status": "completed",
            "error": None,
        },
    )
    monkeypatch.setattr(
        mod,
        "read_selected_status_snapshot",
        lambda db_path, video_ids: (_ for _ in ()).throw(
            sqlite3.OperationalError("read-only query failed")
        ),
    )

    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
    )

    assert payload["status"] == "failed"
    assert payload["failure_stage"] == "coordinator"
    assert payload["failure_type"] == "OperationalError"
    assert "read-only query failed" in payload["failure_reason"]


def test_run_multi_account_fetch_exact_retry_drift_blocks_before_child_launch(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    missing_id = "eeeeeeeeeee"
    monkeypatch.setattr(
        mod,
        "_run_account",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("child must not launch")),
    )

    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
        video_ids=("aaaaaaaaaaa", missing_id),
    )

    assert payload["status"] == "failed"
    assert payload["failure_stage"] == "coordinator"
    assert missing_id in payload["failure_reason"]
    assert payload["account_results"] == []


def test_run_multi_account_fetch_revalidates_exact_set_after_manifest_preparation(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    real_prepare = mod.prepare_account_specs

    def prepare_and_drift(**kwargs):
        specs = real_prepare(**kwargs)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE analysis_status SET status = 'failed' WHERE video_id = ?",
                ("aaaaaaaaaaa",),
            )
            conn.commit()
        return specs

    monkeypatch.setattr(mod, "prepare_account_specs", prepare_and_drift)
    monkeypatch.setattr(
        mod,
        "_run_account",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("child must not launch")),
    )

    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=2,
        recent_days=None,
        video_ids=("aaaaaaaaaaa", "bbbbbbbbbbb"),
    )

    assert payload["status"] == "failed"
    assert payload["failure_stage"] == "coordinator"
    assert "after manifest preparation" in payload["failure_reason"]
    assert payload["account_results"] == []


def test_run_multi_account_fetch_persists_auth_preflight_block(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(
        mod,
        "_preflight_accounts",
        lambda accounts: (_ for _ in ()).throw(
            mod.AccountPreflightError(
                {
                    "a.hominidae": {"ok": False, "reason": "token_missing"},
                    "troup.hominidae": {"ok": True, "reason": "token_only"},
                }
            )
        ),
    )

    def fail_if_launched(*args, **kwargs):
        raise AssertionError("child fetch must not launch after auth preflight failure")

    monkeypatch.setattr(mod, "_run_account", fail_if_launched)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae", "troup.hominidae"),
        limit=2,
        recent_days=None,
    )

    assert payload["status"] == "blocked"
    assert payload["failure_stage"] == "auth_preflight"
    assert payload["auth_preflight"]["a.hominidae"]["reason"] == "token_missing"
    assert payload["account_results"] == []
    assert Path(payload["summary_path"]).is_file()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status = 'pending'").fetchone()[0] == 3


def test_run_multi_account_fetch_persists_lock_contention_before_selection(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    captured: dict[str, object] = {}

    class FakeLock:
        def __init__(self, path: str) -> None:
            captured["path"] = path

        def acquire(self, *, blocking: bool, timeout: float) -> bool:
            captured["acquire"] = (blocking, timeout)
            return False

        def release(self) -> None:
            raise AssertionError("a lock that was not acquired must not be released")

    monkeypatch.setattr(mod.fasteners, "InterProcessLock", FakeLock)
    monkeypatch.setattr(
        mod,
        "read_pending_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("selection must not start when the coordinator lock is unavailable")
        ),
    )
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
        lock_timeout_s=2.5,
    )

    assert payload["status"] == "blocked"
    assert payload["failure_stage"] == "coordinator_lock"
    assert payload["failure_reason"] == "lock_not_acquired"
    assert captured["acquire"] == (True, 2.5)
    assert captured["path"] == str(mod._coordinator_lock_path(db_path))
    assert Path(payload["summary_path"]).is_file()


def test_run_multi_account_fetch_persists_lock_error(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)

    class BrokenLock:
        def __init__(self, path: str) -> None:
            self.path = path

        def acquire(self, *, blocking: bool, timeout: float) -> bool:
            raise OSError("lock directory is read-only")

    monkeypatch.setattr(mod.fasteners, "InterProcessLock", BrokenLock)
    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=tmp_path / "run",
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
    )

    assert payload["status"] == "failed"
    assert payload["failure_stage"] == "coordinator_lock"
    assert payload["failure_type"] == "OSError"
    assert Path(payload["summary_path"]).is_file()


def test_run_multi_account_fetch_persists_output_root_conflict_without_overwriting_it(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _make_db(db_path)
    output_root = tmp_path / "run"
    output_root.mkdir()
    preserved_file = output_root / "existing-artifact.txt"
    preserved_file.write_text("preserve", encoding="utf-8")

    payload = mod.run_multi_account_fetch(
        db_path=db_path,
        output_root=output_root,
        accounts=("a.hominidae",),
        limit=1,
        recent_days=None,
    )

    summary_path = Path(payload["summary_path"])
    assert payload["status"] == "failed"
    assert payload["failure_stage"] == "output_root"
    assert payload["failure_type"] == "FileExistsError"
    assert payload["requested_output_root"] == str(output_root.resolve())
    assert payload["receipt_output_root"] == str(summary_path.parent)
    assert summary_path.is_file()
    assert summary_path.parent != output_root
    assert preserved_file.read_text(encoding="utf-8") == "preserve"


def test_main_rejects_unreadable_video_manifest_without_creating_output_root(
    tmp_path: Path, capsys
) -> None:
    output_root = tmp_path / "run"

    with pytest.raises(SystemExit) as caught:
        mod.main(
            [
                "--limit",
                "1",
                "--video-manifest",
                str(tmp_path / "missing.json"),
                "--output-root",
                str(output_root),
            ]
        )

    assert caught.value.code == 2
    assert "could not load --video-manifest" in capsys.readouterr().err


def test_main_rejects_large_direct_live_all_pending_scope(
    tmp_path: Path, capsys
) -> None:
    output_root = tmp_path / "run"

    with pytest.raises(SystemExit) as caught:
        mod.main(
            [
                "--limit",
                str(mod.MAX_DIRECT_LIVE_ALL_PENDING_LIMIT + 1),
                "--all-pending",
                "--output-root",
                str(output_root),
            ]
        )

    assert caught.value.code == 2
    assert "direct --all-pending execution is limited" in capsys.readouterr().err
    assert not output_root.exists()


def test_main_rejects_unbounded_direct_all_pending_without_supervisor_authorization(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    output_root = tmp_path / "run"
    monkeypatch.setattr(
        mod,
        "run_multi_account_fetch",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("coordinator must not run")),
    )

    with pytest.raises(SystemExit) as caught:
        mod.main(
            [
                "--limit",
                str(mod.MAX_DIRECT_LIVE_ALL_PENDING_LIMIT + 1),
                "--all-pending",
                "--db-path",
                str(tmp_path / "batch.sqlite"),
                "--output-root",
                str(output_root),
            ]
        )

    assert caught.value.code == 2
    assert "direct --all-pending execution is limited" in capsys.readouterr().err
    assert not output_root.exists()


def test_main_accepts_bounded_all_pending_plan_only(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(mod, "run_multi_account_fetch", fake_run)
    assert mod.main(
        [
            "--limit",
            str(mod.MAX_DIRECT_LIVE_ALL_PENDING_LIMIT),
            "--all-pending",
            "--plan-only",
            "--db-path",
            str(tmp_path / "batch.sqlite"),
            "--output-root",
            str(tmp_path / "run"),
        ]
    ) == 0
    assert captured["plan_only"] is True
    assert captured["include_categorized"] is True


def test_main_accepts_large_live_all_pending_scope_with_supervisor_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = (tmp_path / "batch.sqlite").resolve()
    output_root = (tmp_path / "supervised").resolve()
    output_root.mkdir()
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps(
            {
                "ownership": {
                    "schema_version": 1,
                    "kind": "unattended_chunk",
                    "run_id": "run-1",
                    "db_path": str(db_path),
                    "output_root": str(output_root),
                }
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(mod, "run_multi_account_fetch", fake_run)
    assert mod.main(
        [
            "--limit",
            str(mod.MAX_DIRECT_LIVE_ALL_PENDING_LIMIT + 1),
                "--all-pending",
                "--db-path",
            str(db_path),
            "--output-root",
            str(output_root),
        ]
    ) == 0
    assert captured["include_categorized"] is True
    assert captured["plan_only"] is False
    assert output_root.is_dir()


def _terminalized_partial_payload() -> dict[str, object]:
    return {
        "status": "partial",
        "selected_count": 10,
        "selected_complete_count": 8,
        "selected_status_counts": {"complete": 8, "failed": 2},
        "selected_missing_video_ids": [],
        "process_failure": False,
    }


def _run_main_with_payload(monkeypatch, payload: dict[str, object]) -> int:
    monkeypatch.setattr(mod, "run_multi_account_fetch", lambda **kwargs: dict(payload))
    return mod.main(
        [
            "--limit",
            "5",
            "--db-path",
            "unused.sqlite",
            "--output-root",
            "unused-run",
        ]
    )


def test_main_exits_zero_for_terminalized_partial(tmp_path, monkeypatch) -> None:
    # Every selected row reached a terminal DB state with no process failure:
    # exit 0 or the supervisor's continue-on-terminalized-failure gate
    # (returncode == 0 AND partial AND terminalized) can never fire.
    assert _run_main_with_payload(monkeypatch, _terminalized_partial_payload()) == 0


def test_main_exits_nonzero_for_partial_with_process_failure(tmp_path, monkeypatch) -> None:
    payload = _terminalized_partial_payload()
    payload["process_failure"] = True
    assert _run_main_with_payload(monkeypatch, payload) == 1


def test_main_exits_nonzero_for_partial_with_nonterminal_statuses(tmp_path, monkeypatch) -> None:
    payload = _terminalized_partial_payload()
    payload["selected_status_counts"] = {"complete": 8, "pending": 2}
    assert _run_main_with_payload(monkeypatch, payload) == 1


def test_main_exits_nonzero_for_partial_with_missing_ids(tmp_path, monkeypatch) -> None:
    payload = _terminalized_partial_payload()
    payload["selected_missing_video_ids"] = ["abc123"]
    assert _run_main_with_payload(monkeypatch, payload) == 1
