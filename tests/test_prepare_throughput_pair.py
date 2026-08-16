import importlib.util
import json
import importlib.util
import sqlite3
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_throughput_pair.py"
spec = importlib.util.spec_from_file_location("prepare_throughput_pair", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _db(path: Path, rows: list[tuple[str, str, str, int]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL, has_captions INTEGER, source TEXT)")
        conn.executemany("INSERT INTO analysis_status VALUES (?, ?, ?, ?, 'fixture')", rows)


def _cache(path: Path, ids: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, lang TEXT NOT NULL, source TEXT NOT NULL, transcript TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', cached_at TEXT NOT NULL, terminal_id TEXT NOT NULL)")
        conn.executemany("INSERT INTO transcript_cache VALUES (?, ?, 'en', 'notebooklm', 'existing transcript', '[]', '2026-01-01T00:00:00+00:00', 'fixture')", [(f"{item}:en:notebooklm", item) for item in ids])


def _receipt(packet: dict, pair: str, arm: str) -> dict:
    arm_packet = packet["pairs"][pair]["arms"][arm]
    ids = packet["pairs"][pair]["cohort_ids"]
    return {
        "pair_id": pair, "arm": arm, "selected_ids": ids,
        "selected_cache_absent_before_launch": True, "db_integrity": "ok", "cache_integrity": "ok",
        "staging_db": arm_packet["staging_db"],
        "staging_cache": arm_packet["staging_cache"],
        "pro_target_workers_required": True,
        "target_workers": {"a.hominidae": [3, 4] if arm == "adaptive" else [3]},
        "outcomes": [{"video_id": item, "status": "complete", "cache_non_empty": True} for item in ids],
    }


def test_prepare_is_deterministic_balanced_disjoint_and_isolated(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage", items_per_account=2)
    assert packet["cohort"]["total_ids"] == 12
    assert set(packet["pairs"]["pair-01"]["cohort_ids"]).isdisjoint(packet["pairs"]["pair-02"]["cohort_ids"])
    for pair in mod.PAIRS:
        manifests = packet["pairs"][pair]["account_manifests"]
        assert [len(manifests[name]) for name in mod.ACCOUNT_ORDER] == [2, 2, 2]
        for arm in mod.ARMS:
            arm_packet = packet["pairs"][pair]["arms"][arm]
            stage_cache = Path(arm_packet["staging_cache"])
            settings_path = Path(arm_packet["account_settings_path"])
            assert json.loads(settings_path.read_text(encoding="utf-8")) == arm_packet["effective_account_settings"]
            assert arm_packet["account_settings_fingerprint"] == mod.file_fingerprint(settings_path)
            assert all(Path(item["manifest_path"]).is_file() for item in arm_packet["manifest_templates"].values())
            with sqlite3.connect(stage_cache) as conn:
                assert conn.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_status WHERE status='pending'").fetchone()[0] == 14
    with sqlite3.connect(reference) as conn:
        assert conn.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0] == 1
    assert Path(packet["packet_path"]).is_file()


def test_prepare_allows_pending_rows_present_in_reference_cache(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(12)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, ids)

    packet = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "stage",
        items_per_account=2,
    )

    selected = packet["pairs"]["pair-01"]["cohort_ids"] + packet["pairs"]["pair-02"]["cohort_ids"]
    assert selected == ids
    assert packet["cohort"]["candidate_selection"] == (
        "authoritative_pending_rows_then_remove_selected_ids_from_staging_cache"
    )
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            stage_cache = Path(packet["pairs"][pair]["arms"][arm]["staging_cache"])
            with sqlite3.connect(stage_cache) as conn:
                placeholders = ",".join("?" for _ in packet["pairs"][pair]["cohort_ids"])
                remaining = conn.execute(
                    f"SELECT video_id FROM transcript_cache WHERE video_id IN ({placeholders})",
                    packet["pairs"][pair]["cohort_ids"],
                ).fetchall()
            assert remaining == []


def test_prepare_records_billing_plan_and_adaptive_policy_split(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(
        db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage", items_per_account=2
    )
    assert {name: data["billing_plan"] for name, data in packet["accounts"].items()} == {
        "a.hominidae": "Pro", "troup.hominidae": "Free", "brsthomson": "Free"
    }
    for pair in mod.PAIRS:
        control = packet["pairs"][pair]["arms"]["control"]["effective_account_settings"]
        adaptive = packet["pairs"][pair]["arms"]["adaptive"]["effective_account_settings"]
        assert all(settings["workers_per_account"] == 3 and settings["adaptive_workers"] is False for settings in control.values())
        assert adaptive["a.hominidae"]["adaptive_workers"] is True
        assert adaptive["troup.hominidae"]["workers_per_account"] == 3
        assert adaptive["troup.hominidae"]["adaptive_workers"] is False
        assert adaptive["brsthomson"]["workers_per_account"] == 3
        assert adaptive["brsthomson"]["adaptive_workers"] is False


def test_prepare_records_explicit_batch_size_for_adaptive_canary(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "stage",
        items_per_account=2,
        batch_size=1,
    )
    assert packet["cohort"]["batch_size"] == 1
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            settings = packet["pairs"][pair]["arms"][arm]["effective_account_settings"]
            assert {value["batch_size"] for value in settings.values()} == {1}


def test_environment_mode_uses_identical_control_settings_and_records_env(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [])
    overrides = {"control": {"YTIS_TEST_MODE": "control"}, "adaptive": {"YTIS_TEST_MODE": "candidate"}}
    packet = mod.prepare_throughput_pair(
        db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage",
        items_per_account=2, comparison_mode="environment", environment_overrides=overrides,
    )
    assert packet["comparison_mode"] == "environment"
    for pair in mod.PAIRS:
        control = packet["pairs"][pair]["arms"]["control"]
        candidate = packet["pairs"][pair]["arms"]["adaptive"]
        assert control["effective_account_settings"] == candidate["effective_account_settings"]
        assert candidate["environment_overrides"] == overrides["adaptive"]
        assert candidate["environment_overrides_fingerprint"] == mod.fingerprint(overrides["adaptive"])


def test_environment_overrides_require_ytis_keys():
    with pytest.raises(ValueError, match="invalid environment override key"):
        mod.validate_environment_overrides({"control": {"NOT_YTIS": "x"}})


def test_adaptive_workload_requirement_rejects_unobservable_scale_up(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(60)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [])

    with pytest.raises(
        ValueError,
        match=r"adaptive workload is too small.*a\.hominidae.*effective batch_size=1",
    ):
        mod.prepare_throughput_pair(
            db_path=db,
            reference_cache_path=reference,
            output_root=tmp_path / "stage",
            items_per_account=10,
            batch_size=1,
            require_adaptive_workload=True,
        )
    assert not (tmp_path / "stage").exists()


def test_adaptive_workload_requirement_records_feasible_floor(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(108)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [])
    packet = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "stage",
        items_per_account=18,
        batch_size=1,
        require_adaptive_workload=True,
    )
    requirement = packet["pairs"]["pair-01"]["arms"]["adaptive"]["settings"]["adaptive_workload"]["a.hominidae"]
    assert requirement["required_logical_batches"] == 18
    assert requirement["logical_batches"] == 18
    assert requirement["feasible"] is True


def test_prepare_supports_distinct_per_account_workers_batches_and_policies(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    overrides = {
        "a.hominidae": {"workers_per_account": 4, "batch_size": 11},
        "troup.hominidae": {
            "workers_per_account": 2,
            "batch_size": 7,
            "adaptive_workers": True,
            "adaptive_min_workers": 1,
            "adaptive_max_workers": 4,
        },
        "brsthomson": {"workers_per_account": 5, "batch_size": 3, "adaptive_workers": False},
    }
    packet = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "stage",
        items_per_account=2,
        account_settings=overrides,
    )
    control = packet["pairs"]["pair-01"]["arms"]["control"]
    adaptive = packet["pairs"]["pair-01"]["arms"]["adaptive"]
    assert {
        account: (settings["workers_per_account"], settings["batch_size"], settings["adaptive_workers"])
        for account, settings in control["effective_account_settings"].items()
    } == {
        "a.hominidae": (4, 11, False),
        "troup.hominidae": (2, 7, False),
        "brsthomson": (5, 3, False),
    }
    assert adaptive["settings"]["adaptive_target_accounts"] == ["a.hominidae", "troup.hominidae"]
    assert adaptive["effective_account_settings"]["troup.hominidae"]["adaptive_workers"] is True
    assert adaptive["effective_settings_fingerprint"] == mod.fingerprint(adaptive["effective_account_settings"])
    assert packet["account_settings_overrides"] == overrides


def test_prepare_excludes_offline_ids_and_records_contract(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(15)]
    excluded = [ids[0], ids[1]]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(
        db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage",
        items_per_account=2, exclude_video_ids=excluded,
    )
    selected = packet["pairs"]["pair-01"]["cohort_ids"] + packet["pairs"]["pair-02"]["cohort_ids"]
    assert not set(excluded).intersection(selected)
    assert packet["exclusions"] == {
        "video_ids": sorted(excluded),
        "fingerprint": mod.fingerprint(sorted(excluded)),
        "reason": "benchmark_only_offline_exclusion",
        "scope": "offline_candidate_selection_only",
    }


def test_prepare_fails_closed_for_insufficient_or_ambiguous_cohort(tmp_path):
    db, reference = tmp_path / "batch.sqlite", tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(6)]
    _db(db, [(item, "pending", "2026-01-01T00:00:00+00:00", 1) for item in ids])
    _cache(reference, [])
    with pytest.raises(ValueError, match="insufficient"):
        mod.prepare_throughput_pair(db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage", items_per_account=2)


def test_prepare_selects_explicit_unknown_and_no_caption_partitions(tmp_path):
    db = tmp_path / "batch.sqlite"
    reference = tmp_path / "reference.sqlite"
    rows = [
        (f"unknown-{index:03d}", "pending", f"2026-01-01T00:{index:02d}:00+00:00", None)
        for index in range(12)
    ] + [
        (f"no-caption-{index:03d}", "pending", f"2026-01-01T01:{index:02d}:00+00:00", 0)
        for index in range(12)
    ]
    _db(db, rows)
    _cache(reference, [])

    unknown = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "unknown",
        items_per_account=2,
        caption_state="unknown",
    )
    no_caption = mod.prepare_throughput_pair(
        db_path=db,
        reference_cache_path=reference,
        output_root=tmp_path / "no-caption",
        items_per_account=2,
        caption_state="no-caption",
    )
    assert unknown["cohort"]["caption_state"] == "unknown"
    assert all(
        item.startswith("unknown-")
        for item in unknown["pairs"]["pair-01"]["cohort_ids"]
    )
    assert no_caption["cohort"]["caption_state"] == "no-caption"
    assert all(
        item.startswith("no-caption-")
        for item in no_caption["pairs"]["pair-01"]["cohort_ids"]
    )


def test_sqlite_snapshot_accepts_wal_backed_source(tmp_path):
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        conn.execute("INSERT INTO values_table VALUES ('from-wal')")
        conn.commit()
        assert Path(str(source) + "-wal").is_file()
        target = tmp_path / "target.sqlite"
        mod._copy_sqlite(source, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM values_table").fetchone() == ("from-wal",)


def test_validator_checks_pair_cohort_pending_cache_and_adaptive_scale(tmp_path):
    db, reference = tmp_path / "batch.sqlite", tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage", items_per_account=2)
    receipts = [_receipt(packet, pair, arm) for pair in mod.PAIRS for arm in mod.ARMS]
    result = mod.validate_receipts(packet, receipts)
    assert result["status"] == "failed"
    assert "selected_ids_still_pending:('pair-01', 'control')" in result["issues"]
    receipts[1]["target_workers"] = {"a.hominidae": [3]}
    receipts[1]["outcomes"][0]["cache_non_empty"] = False
    result = mod.validate_receipts(packet, receipts)
    assert "adaptive_pro_did_not_scale:('pair-01', 'adaptive')" in result["issues"]
    assert "success_cache_empty:('pair-01', 'adaptive'):00000000000" in result["issues"]


def test_validator_rejects_forbidden_fallback_and_duplicate_receipt(tmp_path):
    db, reference = tmp_path / "batch.sqlite", tmp_path / "reference.sqlite"
    ids = [f"{index:011d}" for index in range(14)]
    _db(db, [(item, "pending", f"2026-01-01T00:{index:02d}:00+00:00", 1) for index, item in enumerate(ids)])
    _cache(reference, [ids[-1]])
    packet = mod.prepare_throughput_pair(db_path=db, reference_cache_path=reference, output_root=tmp_path / "stage", items_per_account=2)
    receipt = _receipt(packet, "pair-01", "control")
    receipt["outcomes"][0]["failure_class"] = "fallback_used"
    result = mod.validate_receipts(packet, [receipt, receipt])
    assert result["status"] == "failed"
    assert any(item.startswith("duplicate_receipt") for item in result["issues"])
    assert any(item.startswith("forbidden_fallback_or_auth_failure") for item in result["issues"])
