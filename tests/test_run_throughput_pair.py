import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import scripts.prepare_throughput_pair as prepare_module


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_throughput_pair.py"
spec = importlib.util.spec_from_file_location("run_throughput_pair", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

FETCH_SCRIPT = Path(__file__).parents[1] / "scripts" / "run_multi_account_fetch.py"
fetch_spec = importlib.util.spec_from_file_location("run_multi_account_fetch_for_settings", FETCH_SCRIPT)
fetch_mod = importlib.util.module_from_spec(fetch_spec)
assert fetch_spec.loader is not None
sys.modules[fetch_spec.name] = fetch_mod
fetch_spec.loader.exec_module(fetch_mod)


def _packet(tmp_path: Path) -> dict:
    accounts = tuple(mod.ACCOUNTS)
    pairs = {}
    for pair_index, pair in enumerate(mod.PAIRS):
        manifests = {
            account: [f"{pair_index}{account_index}{index:09d}" for index in range(2)]
            for account_index, account in enumerate(accounts)
        }
        ids = mod._interleaved_ids(manifests)
        pairs[pair] = {"cohort_ids": ids, "account_manifests": manifests}
    return {"packet_path": str(tmp_path / "packet.json"), "created_at": "2026-01-01T00:00:00+00:00",
            "pairs": pairs,
            "coordinator": {"execute_requires_explicit_flag": False}, "executable": False}


def _summary(tmp_path: Path, account: str, ids: list[str], arm: str, target: int = 3) -> tuple[dict, dict]:
    events = tmp_path / account / arm
    events.mkdir(parents=True)
    actions = ["fetch_invoked", "fetch_manifest_selection", "first_download_started", "fetch_worker_finished", "fetch_completed", "worker_cleanup_completed"]
    if arm == "adaptive":
        actions += ["adaptive_scale_decision"]
    with (events / "events.jsonl").open("w", encoding="utf-8") as handle:
        for action in actions:
            data = {"target_workers": target} if action == "adaptive_scale_decision" else {}
            handle.write(json.dumps({"action": action, "data": data}) + "\n")
    receipt = {"account_profile": account, "selected_ids": ids, "dry_run": False, "plan_only": False}
    summary = {"account_results": [{"account_profile": account, "returncode": 0, "error": None,
                                     "selected_complete_count": len(ids), "selected_missing_video_ids": [],
                                     "elapsed_s": 10.0, "event_log_dir": str(events)}]}
    return summary, receipt


def _arm_receipt(tmp_path: Path, packet: dict, pair: str, arm: str, *, bad_account_ids: bool = False, target: int = 4) -> dict:
    account_summaries, account_receipts = {}, {}
    for account, ids in packet["pairs"][pair]["account_manifests"].items():
        summary, receipt = _summary(tmp_path, account, ids, arm, target if account == "a.hominidae" else 3)
        if bad_account_ids and account == "a.hominidae":
            receipt["selected_ids"] = packet["pairs"][pair]["cohort_ids"]
        account_summaries[account] = summary
        account_receipts[account] = receipt
    count = sum(len(ids) for ids in packet["pairs"][pair]["account_manifests"].values())
    return {"pair_id": pair, "arm": arm, "selected_ids": packet["pairs"][pair]["cohort_ids"],
            "selected_cache_absent_before_launch": True, "db_integrity": "ok", "cache_integrity": "ok",
            "account_summaries": account_summaries, "account_receipts": account_receipts,
            "adaptive_claimed": arm == "adaptive",
            "vph_valid": True,
            "per_account_vph": {account: 2 * 3600 / 10 for account in packet["pairs"][pair]["account_manifests"]},
            "vph": count * 3600 / 10}


def test_default_plan_has_no_execute_flag_and_builds_settings(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(mod, "prepare_throughput_pair", lambda **kwargs: {
            "packet_path": str(tmp_path / "packet.json"),
            "pairs": {pair: {"cohort_ids": [f"{index:011d}" for index, account in enumerate(mod.ACCOUNTS)],
                              "account_manifests": {account: [f"{index:011d}"] for index, account in enumerate(mod.ACCOUNTS)},
                          "arms": {arm: {"staging_db": str(tmp_path / pair / arm / "db.sqlite"),
                                         "manifest_templates": {account: {"manifest_path": str(tmp_path / "m.json"), "video_ids": ["id"]} for account in mod.ACCOUNTS}}
                                   for arm in mod.ARMS}} for pair in mod.PAIRS},
    })
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            (tmp_path / pair / arm).mkdir(parents=True)
    result = mod.build_plan(db=tmp_path / "db", reference_cache=tmp_path / "cache", output_root=tmp_path, items_per_account=1)
    assert result["coordinator"]["live_launch"] is False
    assert all(Path(result["pairs"][pair]["arms"][arm]["account_settings_path"]).is_file() for pair in mod.PAIRS for arm in mod.ARMS)


def test_source_add_marker_is_exact_and_rpc9_is_source_add_scoped(tmp_path):
    root = tmp_path / "events"
    root.mkdir()
    path = root / "events.jsonl"
    path.write_text(json.dumps({"action": "some_ADD_SOURCE_error"}) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(root) is None
    path.write_text(json.dumps({"action": "source_add_failed"}) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(root) == "source_add_failed"
    path.write_text(json.dumps({"action": "nlm_batch_source_add_gate_failed"}) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(root) == "source_add_gate_failed"
    path.write_text(json.dumps({"action": "unrelated_failure", "data": {"rpc_code": 9}}) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(root) is None
    path.write_text(json.dumps({"failure_reason": "Source add failed: rpc_code=9"}) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(root) == "source_add_rpc_code_9"
    path.write_text(
        json.dumps({
            "action": "nlm_batch_source_add_retry_skipped",
            "data": {
                "reason": "rpc_code_9_failed_precondition",
                "error": "SourceAddError (cause=RPCError, rpc_code=9)",
            },
        }) + "\n",
        encoding="utf-8",
    )
    assert mod._source_add_abort_marker(root) == "source_add_rpc_code_9"


def test_source_add_marker_ignores_stale_wrong_account_and_wrong_video(tmp_path):
    root = tmp_path / "events"
    root.mkdir()
    path = root / "events.jsonl"
    marker = {
        "action": "nlm_batch_source_add_retry_skipped",
        "data": {
            "reason": "rpc_code_9_failed_precondition",
            "account_profile": "a.hominidae",
            "video_id": "video-1",
            "execution_nonce": "current",
        },
    }
    stale = dict(marker)
    stale["data"] = {**marker["data"], "execution_nonce": "stale"}
    wrong_account = dict(marker)
    wrong_account["data"] = {**marker["data"], "account_profile": "other.account"}
    wrong_video = dict(marker)
    wrong_video["data"] = {**marker["data"], "video_id": "not-selected"}
    path.write_text(
        "\n".join(json.dumps(item) for item in (stale, wrong_account, wrong_video)) + "\n",
        encoding="utf-8",
    )
    assert mod._source_add_abort_marker(
        root,
        expected_execution_nonce="current",
        expected_accounts=set(mod.ACCOUNTS),
        expected_video_ids={"video-1"},
    ) is None
    path.write_text(json.dumps(marker) + "\n", encoding="utf-8")
    assert mod._source_add_abort_marker(
        root,
        expected_execution_nonce="current",
        expected_accounts=set(mod.ACCOUNTS),
        expected_video_ids={"video-1"},
    ) == "source_add_rpc_code_9"


def test_source_add_marker_accepts_top_level_current_envelope(tmp_path):
    root = tmp_path / "events"
    root.mkdir()
    (root / "events.jsonl").write_text(
        json.dumps({
            "action": "source_add_failed",
            "execution_nonce": "current",
            "account_profile": "a.hominidae",
            "video_id": "video-1",
        }) + "\n",
        encoding="utf-8",
    )
    assert mod._source_add_abort_marker(
        root,
        expected_execution_nonce="current",
        expected_accounts=set(mod.ACCOUNTS),
        expected_video_ids={"video-1"},
    ) == "source_add_failed"


def test_launch_arm_applies_only_supplied_environment_and_aborts(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = 1
        def __init__(self):
            self.terminated = False
        def poll(self):
            return None if not self.terminated else self.returncode
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            if timeout is not None and not self.terminated:
                raise mod.subprocess.TimeoutExpired("fake", timeout)
            return self.returncode
    process = FakeProcess()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: process)
    events = tmp_path / "run"
    events.mkdir()
    (events / "events.jsonl").write_text(json.dumps({"action": "source_add_failed"}) + "\n", encoding="utf-8")
    def terminate(_process):
        process.terminated = True
        return {"termination_confirmed": True, "remaining_pids": []}

    monkeypatch.setattr(mod, "_terminate_process_tree", terminate)
    completed, reason, termination = mod._launch_arm(
        ["fake"], cwd=str(tmp_path), run_root=events,
        env={"YTIS_ARM": "candidate"}, abort_on_source_add_failure=True,
    )
    assert completed is process
    assert reason == "source_add_failed"
    assert termination["termination_confirmed"] is True
    assert process.terminated is True


def test_launch_arm_waits_between_marker_polls(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.poll_count = 0
            self.wait_timeouts = []

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count == 1 else self.returncode

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: process)
    events = tmp_path / "run"
    events.mkdir()
    completed, reason, termination = mod._launch_arm(
        ["fake"], cwd=str(tmp_path), run_root=events, env={}, abort_on_source_add_failure=True
    )
    assert completed is process
    assert reason is None
    assert termination is None
    assert process.wait_timeouts == [0.05]


def test_environment_mode_does_not_require_adaptive_claim_or_scale_up(tmp_path):
    packet = _packet(tmp_path)
    packet["comparison_mode"] = "environment"
    packet["coordinator"]["comparison_mode"] = "environment"
    packet["pairs"]["pair-01"]["arms"] = {
        arm: {"effective_account_settings": mod.effective_account_settings("control")}
        for arm in mod.ARMS
    }
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "adaptive")
    receipt["adaptive_claimed"] = False
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert "adaptive_no_target_accounts" not in result["issues"]
    assert "adaptive_claim_missing" not in result["issues"]


def test_environment_mode_rejects_different_arm_settings(tmp_path):
    packet = _packet(tmp_path)
    packet["comparison_mode"] = "environment"
    packet["coordinator"]["comparison_mode"] = "environment"
    packet["pairs"]["pair-01"]["arms"] = {
        "control": {"effective_account_settings": mod.effective_account_settings("control")},
        "adaptive": {"effective_account_settings": mod.effective_account_settings("adaptive")},
    }
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "adaptive")
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert "environment_mode_settings_not_identical" in result["issues"]


def test_aborted_receipt_cannot_claim_valid_vph(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control")
    receipt.update({
        "abort_status": "aborted",
        "abort_reason": "source_add_rpc_code_9",
        "runner_status": "completed",
    })
    result = mod.validate_arm(packet, "pair-01", "control", receipt)
    assert result["status"] == "failed"
    assert "aborted_runner_claims_completed" in result["issues"]
    assert "aborted_runner_vph_must_be_invalid" in result["issues"]


def test_failed_validation_does_not_report_numeric_vph(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "adaptive", target=3)
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert result["status"] == "failed"
    assert result["vph"] is None
    assert result["per_account_vph"] == {}


def test_missing_vph_valid_is_invalid(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control")
    receipt.pop("vph_valid")
    result = mod.validate_arm(packet, "pair-01", "control", receipt)
    assert result["status"] == "failed"
    assert "vph_valid_missing_or_invalid" in result["issues"]
    assert result["vph"] is None


def test_malformed_executable_packet_fails_closed_without_validator_crash(tmp_path):
    packet = _packet(tmp_path)
    packet.update({
        "kind": "offline_uncached_throughput_pair",
        "packet_version": 2,
        "packet_root": str(tmp_path),
        "coordinator": {"execute_requires_explicit_flag": True},
        "executable": True,
    })
    packet["pairs"]["pair-01"]["arms"] = {"control": {}}
    result = mod.validate_arm(packet, "pair-01", "control", {"pair_id": "pair-01", "arm": "control"})
    assert result["status"] == "failed"
    assert any(issue.startswith("execution_provenance:") for issue in result["issues"])


def test_termination_result_is_fail_closed_when_tree_cannot_be_enumerated(monkeypatch):
    class Process:
        pid = 12345

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(mod, "_owned_descendant_pids", lambda _pid: None)
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    result = mod._terminate_process_tree(Process())
    assert result["termination_confirmed"] is False
    assert result["remaining_pids"] is None


def test_execution_refuses_preexisting_runtime_root(tmp_path):
    packet = _packet(tmp_path)
    packet["packet_root"] = str(tmp_path)
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            stage = tmp_path / pair / arm
            stage.mkdir(parents=True, exist_ok=True)
            packet["pairs"][pair]["arms"] = packet["pairs"][pair].get("arms", {})
            packet["pairs"][pair]["arms"][arm] = {"staging_db": str(stage / "batch.sqlite")}
    (tmp_path / "pair-01" / "control" / "run").mkdir()
    with pytest.raises(ValueError, match="runtime root must be newly created"):
        mod._validate_fresh_runtime_roots(packet)


def test_plan_forwards_explicit_caption_state(tmp_path, monkeypatch):
    captured = {}
    packet = _packet(tmp_path)
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            stage = tmp_path / pair / arm
            stage.mkdir(parents=True)
            packet["pairs"][pair]["arms"] = packet["pairs"][pair].get("arms", {})
            packet["pairs"][pair].setdefault("arms", {})[arm] = {
                "staging_db": str(stage / "batch.sqlite"),
            }

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return packet

    monkeypatch.setattr(mod, "prepare_throughput_pair", fake_prepare)
    result = mod.build_plan(
        db=tmp_path / "db",
        reference_cache=tmp_path / "cache",
        output_root=tmp_path,
        items_per_account=1,
        caption_state="unknown",
        batch_size=1,
    )
    assert captured["caption_state"] == "unknown"
    assert captured["batch_size"] == 1
    assert captured["require_adaptive_workload"] is True
    assert result["coordinator"]["caption_state"] == "unknown"
    assert result["coordinator"]["batch_size"] == 1


@pytest.mark.parametrize("caption_state", ["no-caption", "any"])
def test_plan_rejects_fallback_dependent_caption_states(tmp_path, caption_state):
    with pytest.raises(ValueError, match="fallback-aware backlog runner"):
        mod.build_plan(
            db=tmp_path / "db",
            reference_cache=tmp_path / "cache",
            output_root=tmp_path / "output",
            items_per_account=1,
            caption_state=caption_state,
        )


def test_control_settings_do_not_contain_adaptive_only_fields(tmp_path):
    control = json.loads(mod._write_settings(tmp_path, "control").read_text())
    adaptive_stage = tmp_path / "adaptive"
    adaptive_stage.mkdir()
    adaptive = json.loads(mod._write_settings(adaptive_stage, "adaptive").read_text())
    assert control["a.hominidae"]["workers_per_account"] == 3
    assert control["a.hominidae"]["adaptive_workers"] is False
    assert adaptive["a.hominidae"]["adaptive_workers"] is True
    assert adaptive["a.hominidae"]["adaptive_max_workers"] == 5
    assert adaptive["troup.hominidae"]["workers_per_account"] == 3
    assert adaptive["troup.hominidae"]["adaptive_workers"] is False
    assert adaptive["brsthomson"]["workers_per_account"] == 3
    assert adaptive["brsthomson"]["adaptive_workers"] is False


def test_write_settings_records_explicit_batch_size(tmp_path):
    settings = json.loads(mod._write_settings(tmp_path, "adaptive", batch_size=1).read_text())
    assert {value["batch_size"] for value in settings.values()} == {1}


def test_adaptive_settings_make_real_loader_enable_only_pro(tmp_path):
    settings_path = mod._write_settings(tmp_path, "adaptive")
    effective = fetch_mod._load_account_settings(
        path=settings_path,
        accounts=tuple(mod.ACCOUNTS),
        workers_per_account=3,
        batch_size=None,
        adaptive_workers=True,
        adaptive_min_workers=1,
        adaptive_max_workers=5,
        adaptive_scale_up_backlog=2,
        adaptive_scale_down_backlog=0,
        adaptive_cooldown_s=60.0,
        adaptive_health_window=2,
    )
    assert effective["a.hominidae"].adaptive_worker_policy["enabled"] is True
    assert effective["troup.hominidae"].adaptive_worker_policy["enabled"] is False
    assert effective["brsthomson"].adaptive_worker_policy["enabled"] is False


def test_combined_manifest_is_interleaved_and_fingerprinted(tmp_path):
    packet = _packet(tmp_path)
    packet["canonical_fingerprints"] = {"db": "sha256:fixture"}
    pair_data = packet["pairs"]["pair-01"]
    stage = tmp_path / "stage"
    stage.mkdir()
    path, fingerprint, ids = mod._write_combined_manifest(
        stage=stage, pair="pair-01", arm="control", packet=packet, pair_data=pair_data
    )
    loaded = mod.load_video_selection_manifest(path)
    assert ids == mod._interleaved_ids(pair_data["account_manifests"])
    assert [item.video_id for item in loaded.items] == ids
    assert loaded.fingerprint == fingerprint
    pair_data["arms"] = {"control": {
        "combined_manifest_path": str(path), "combined_manifest_ids": ids,
        "account_settings_path": str(tmp_path / "settings.json"),
        "staging_db": str(tmp_path / "batch.sqlite"), "staging_cache": str(tmp_path / "cache.sqlite"),
    }}
    command = mod._command(packet, "pair-01", "control", tmp_path / "run")
    assert "--parallel-accounts" in command
    assert command[command.index("--accounts") + 1] == ",".join(mod.ACCOUNTS)
    assert command[command.index("--video-manifest") + 1] == str(path)


def test_command_forwards_per_account_settings_without_fixed_worker_restriction(tmp_path):
    packet = _packet(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    settings = mod.effective_account_settings(
        "adaptive",
        account_settings={
            "a.hominidae": {"workers_per_account": 4, "batch_size": 11},
            "troup.hominidae": {"workers_per_account": 2, "batch_size": 7, "adaptive_workers": True,
                                 "adaptive_min_workers": 1, "adaptive_max_workers": 4},
            "brsthomson": {"workers_per_account": 5, "batch_size": 3, "adaptive_workers": False},
        },
    )
    settings_path = stage / "account-settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    packet["pairs"]["pair-01"]["arms"] = {
        "adaptive": {
            "combined_manifest_path": str(stage / "manifest.json"),
            "combined_manifest_ids": packet["pairs"]["pair-01"]["cohort_ids"],
            "account_settings_path": str(settings_path),
            "staging_db": str(stage / "batch.sqlite"),
            "staging_cache": str(stage / "cache.sqlite"),
            "effective_account_settings": settings,
        }
    }
    command = mod._command(packet, "pair-01", "adaptive", tmp_path / "run")
    assert command[command.index("--workers-per-account") + 1] == "4"
    assert command[command.index("--account-settings") + 1] == str(settings_path)


def test_execute_fails_closed_on_canonical_db_provenance_mismatch(tmp_path, monkeypatch):
    packet = _packet(tmp_path)
    packet.update({"kind": "offline_uncached_throughput_pair", "packet_version": 2,
                   "packet_root": str(tmp_path), "canonical_db": str(tmp_path / "canonical.sqlite"),
                   "reference_cache": str(tmp_path / "reference.sqlite"),
                   "cohort": {"batch_size": 5},
                   "accounts": {account: {"account_profile": account, "billing_plan": ("Pro" if account == "a.hominidae" else "Free")}
                                for account in mod.ACCOUNTS}})
    packet["coordinator"]["execute_requires_explicit_flag"] = True
    for path in (tmp_path / "canonical.sqlite", tmp_path / "reference.sqlite"):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
    prep = prepare_module
    packet["canonical_fingerprints"] = {
        "db": prep.file_fingerprint(tmp_path / "canonical.sqlite"),
        "reference_cache": prep.file_fingerprint(tmp_path / "reference.sqlite"),
    }
    for pair in mod.PAIRS:
        pair_data = packet["pairs"][pair]
        pair_data["arms"] = {}
        for arm in mod.ARMS:
            stage = tmp_path / pair / arm
            stage.mkdir(parents=True)
            staging_db = stage / "batch.sqlite"
            staging_cache = stage / "cache.sqlite"
            for path in (staging_db, staging_cache):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE marker (value TEXT)")
            settings = stage / "account-settings.json"
            settings.write_text(json.dumps(mod.effective_account_settings(arm, batch_size=5), sort_keys=True), encoding="utf-8")
            combined = stage / "combined-manifest.json"
            combined.write_text("{}", encoding="utf-8")
            manifests = {}
            manifest_fingerprints = {}
            for account in mod.ACCOUNTS:
                manifest = stage / f"manifest-{account}.json"
                manifest.write_text("{}", encoding="utf-8")
                manifests[account] = {"manifest_path": str(manifest)}
                manifest_fingerprints[account] = prep.file_fingerprint(manifest)
            pair_data["arms"][arm] = {
                "staging_db": str(staging_db), "staging_cache": str(staging_cache),
                "account_settings_path": str(settings), "account_settings_fingerprint": prep.file_fingerprint(settings),
                "combined_manifest_path": str(combined), "combined_manifest_ids": pair_data["cohort_ids"],
                "effective_account_settings": mod.effective_account_settings(arm, batch_size=5),
                "effective_settings_fingerprint": mod.fingerprint(
                    mod.effective_account_settings(arm, batch_size=5)
                ),
                "manifest_templates": manifests,
                "artifact_fingerprints": {
                    "staging_db": prep.file_fingerprint(staging_db), "staging_cache": prep.file_fingerprint(staging_cache),
                    "combined_manifest": prep.file_fingerprint(combined), "manifests": manifest_fingerprints,
                },
            }
    packet_path = tmp_path / "packet.json"
    packet["packet_path"] = str(packet_path)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    mod._validate_execution_provenance(packet_path, packet)
    settings_arm = packet["pairs"]["pair-01"]["arms"]["control"]
    settings_path = Path(settings_arm["account_settings_path"])
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_payload["a.hominidae"]["workers_per_account"] = 4
    settings_path.write_text(json.dumps(settings_payload), encoding="utf-8")
    settings_arm["account_settings_fingerprint"] = prep.file_fingerprint(settings_path)
    with pytest.raises(ValueError, match="account settings contents mismatch"):
        mod._validate_execution_provenance(packet_path, packet)
    settings_path.write_text(json.dumps(mod.effective_account_settings("control", batch_size=5), sort_keys=True), encoding="utf-8")
    settings_arm["account_settings_fingerprint"] = prep.file_fingerprint(settings_path)
    original_settings = packet["pairs"]["pair-01"]["arms"]["control"]["effective_account_settings"]
    original_workers = original_settings["a.hominidae"]["workers_per_account"]
    original_settings["a.hominidae"]["workers_per_account"] = 99
    with pytest.raises(ValueError, match="effective account settings fingerprint mismatch"):
        mod._validate_execution_provenance(packet_path, packet)
    original_settings["a.hominidae"]["workers_per_account"] = original_workers
    with (tmp_path / "canonical.sqlite").open("ab") as handle:
        handle.write(b"changed")
    monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kwargs: pytest.fail("child launched before provenance gate"))
    with pytest.raises(ValueError, match="canonical db provenance mismatch"):
        mod.execute_packet(packet_path)


def test_execution_provenance_rejects_stale_in_root_arm_artifact(tmp_path):
    packet = _packet(tmp_path)
    packet.update({"kind": "offline_uncached_throughput_pair", "packet_version": 2,
                   "packet_root": str(tmp_path), "canonical_db": str(tmp_path / "canonical.sqlite"),
                   "reference_cache": str(tmp_path / "reference.sqlite"),
                   "accounts": {account: {"account_profile": account,
                                           "billing_plan": ("Pro" if account == "a.hominidae" else "Free")}
                                for account in mod.ACCOUNTS}})
    for path in (tmp_path / "canonical.sqlite", tmp_path / "reference.sqlite"):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
    packet["canonical_fingerprints"] = {
        "db": prepare_module.file_fingerprint(tmp_path / "canonical.sqlite"),
        "reference_cache": prepare_module.file_fingerprint(tmp_path / "reference.sqlite"),
    }
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            stage = tmp_path / pair / arm
            stage.mkdir(parents=True)
            staging_db = stage / "batch.sqlite"
            staging_cache = stage / "cache.sqlite"
            for path in (staging_db, staging_cache):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE marker (value TEXT)")
            settings = stage / "account-settings.json"
            settings.write_text(json.dumps(mod.effective_account_settings(arm), sort_keys=True), encoding="utf-8")
            combined = stage / "combined-manifest.json"
            combined.write_text("{}", encoding="utf-8")
            manifests = {}
            manifest_fingerprints = {}
            for account in mod.ACCOUNTS:
                manifest = stage / f"manifest-{account}.json"
                manifest.write_text("{}", encoding="utf-8")
                manifests[account] = {"manifest_path": str(manifest)}
                manifest_fingerprints[account] = prepare_module.file_fingerprint(manifest)
            effective = mod.effective_account_settings(arm)
            packet["pairs"][pair].setdefault("arms", {})[arm] = {
                "staging_db": str(staging_db), "staging_cache": str(staging_cache),
                "account_settings_path": str(settings),
                "account_settings_fingerprint": prepare_module.file_fingerprint(settings),
                "combined_manifest_path": str(combined),
                "effective_account_settings": effective,
                "effective_settings_fingerprint": mod.fingerprint(effective),
                "manifest_templates": manifests,
                "artifact_fingerprints": {
                    "staging_db": prepare_module.file_fingerprint(staging_db),
                    "staging_cache": prepare_module.file_fingerprint(staging_cache),
                    "combined_manifest": prepare_module.file_fingerprint(combined),
                    "manifests": manifest_fingerprints,
                },
            }
    packet_path = tmp_path / "packet.json"
    packet["packet_path"] = str(packet_path)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    arm_data = packet["pairs"]["pair-01"]["arms"]["control"]
    stale = tmp_path / "pair-01" / "control" / "stale.sqlite"
    stale.write_bytes(b"stale")
    arm_data["staging_db"] = str(stale)
    with pytest.raises(ValueError, match="pair-01/control staging_db provenance mismatch"):
        mod._validate_execution_provenance(packet_path, packet)


def test_post_run_provenance_allows_mutated_staging_databases(tmp_path):
    packet = _packet(tmp_path)
    packet.update({"kind": "offline_uncached_throughput_pair", "packet_version": 2,
                   "packet_root": str(tmp_path), "canonical_db": str(tmp_path / "canonical.sqlite"),
                   "reference_cache": str(tmp_path / "reference.sqlite"),
                   "accounts": {account: {"account_profile": account,
                                           "billing_plan": ("Pro" if account == "a.hominidae" else "Free")}
                                for account in mod.ACCOUNTS}})
    for path in (tmp_path / "canonical.sqlite", tmp_path / "reference.sqlite"):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
    packet["canonical_fingerprints"] = {
        "db": prepare_module.file_fingerprint(tmp_path / "canonical.sqlite"),
        "reference_cache": prepare_module.file_fingerprint(tmp_path / "reference.sqlite"),
    }
    for pair in mod.PAIRS:
        for arm in mod.ARMS:
            stage = tmp_path / pair / arm
            stage.mkdir(parents=True)
            staging_db = stage / "batch.sqlite"
            staging_cache = stage / "cache.sqlite"
            for path in (staging_db, staging_cache):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE marker (value TEXT)")
            settings = stage / "account-settings.json"
            effective = mod.effective_account_settings(arm)
            settings.write_text(json.dumps(effective, sort_keys=True), encoding="utf-8")
            combined = stage / "combined-manifest.json"
            combined.write_text("{}", encoding="utf-8")
            manifests = {}
            manifest_fingerprints = {}
            for account in mod.ACCOUNTS:
                manifest = stage / f"manifest-{account}.json"
                manifest.write_text("{}", encoding="utf-8")
                manifests[account] = {"manifest_path": str(manifest)}
                manifest_fingerprints[account] = prepare_module.file_fingerprint(manifest)
                packet["pairs"][pair].setdefault("arms", {})[arm] = {
                "staging_db": str(staging_db), "staging_cache": str(staging_cache),
                "account_settings_path": str(settings),
                "account_settings_fingerprint": prepare_module.file_fingerprint(settings),
                "combined_manifest_path": str(combined),
                "effective_account_settings": effective,
                "effective_settings_fingerprint": mod.fingerprint(effective),
                "manifest_templates": manifests,
                "artifact_fingerprints": {
                    "staging_db": prepare_module.file_fingerprint(staging_db),
                    "staging_cache": prepare_module.file_fingerprint(staging_cache),
                    "combined_manifest": prepare_module.file_fingerprint(combined),
                    "manifests": manifest_fingerprints,
                },
            }
    packet_path = tmp_path / "packet.json"
    packet["packet_path"] = str(packet_path)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    mod._validate_execution_provenance(packet_path, packet)
    with sqlite3.connect(packet["pairs"]["pair-01"]["arms"]["control"]["staging_db"]) as conn:
        conn.execute("INSERT INTO marker VALUES ('post-run mutation')")
    with pytest.raises(ValueError, match="pair-01/control staging_db provenance mismatch"):
        mod._validate_execution_provenance(packet_path, packet)
    mod._validate_execution_provenance(
        packet_path,
        packet,
        check_mutable_staging_fingerprints=False,
    )


def test_event_provenance_rejects_mismatched_run_id(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    (events / "events.jsonl").write_text(
        json.dumps({"action": "fetch_completed", "data": {"run_id": "stale-run"}}) + "\n",
        encoding="utf-8",
    )
    issues = mod._event_provenance_issues(
        events,
        account="a.hominidae",
        expected_ids=["video-1"],
        expected_identity={"run_id": "current-run", "account_profile": "a.hominidae"},
    )
    assert "a.hominidae:event_run_id_mismatch" in issues


def test_event_provenance_rejects_missing_identity_envelope(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    (events / "events.jsonl").write_text(
        json.dumps({"action": "fetch_completed", "data": {}}) + "\n",
        encoding="utf-8",
    )
    issues = mod._event_provenance_issues(
        events,
        account="a.hominidae",
        expected_ids=["video-1"],
        expected_identity={"run_id": "current-run", "account_profile": "a.hominidae"},
    )
    assert "a.hominidae:event_run_id_missing" in issues
    assert "a.hominidae:event_account_profile_missing" in issues


def test_validate_uses_per_account_ids_and_rejects_pair_wide_confusion(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control", bad_account_ids=True)
    result = mod.validate_arm(packet, "pair-01", "control", receipt)
    assert result["status"] == "failed"
    assert "a.hominidae:selected_ids_mismatch" in result["issues"]


def test_validate_reports_parallel_and_per_account_vph_separately(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control")
    result = mod.validate_arm(packet, "pair-01", "control", receipt)
    completed = len(packet["pairs"]["pair-01"]["cohort_ids"])
    assert result["vph"] == completed * 3600 / 10
    assert result["per_account_vph"]["a.hominidae"] == 2 * 3600 / 10
    assert result["elapsed_s"] == 10


def test_validate_requires_live_events_cleanup_and_adaptive_scale(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "adaptive", target=3)
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert result["status"] == "failed"
    assert "a.hominidae:adaptive_target_workers_not_gt_3" in result["issues"]
    (tmp_path / "troup.hominidae" / "adaptive" / "events.jsonl").write_text(
        json.dumps({"action": "fetch_invoked"}) + "\n", encoding="utf-8"
    )
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert any("missing_event_family:live_work" in issue for issue in result["issues"])


def test_validate_adaptive_target_is_derived_from_packet_account_settings(tmp_path):
    packet = _packet(tmp_path)
    packet["pairs"]["pair-01"]["arms"] = {
        "adaptive": {
            "effective_account_settings": mod.effective_account_settings(
                "adaptive",
                account_settings={
                    "a.hominidae": {"adaptive_workers": False},
                    "troup.hominidae": {"workers_per_account": 4, "adaptive_workers": True,
                                         "adaptive_min_workers": 2, "adaptive_max_workers": 5},
                },
            )
        }
    }
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "adaptive", target=4)
    result = mod.validate_arm(packet, "pair-01", "adaptive", receipt)
    assert "troup.hominidae:adaptive_target_workers_not_gt_4" in result["issues"]
    assert not any("a.hominidae:adaptive_target_workers_not_gt" in issue for issue in result["issues"])


def test_validate_rejects_duplicate_or_missing_arm_receipts(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control")
    path = tmp_path / "one.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = mod.validate_packet(path, [path, path])
    assert result["status"] == "failed"
    assert any(issue.startswith("duplicate_receipt") for issue in result["issues"])
    assert any(issue.startswith("missing_receipt") for issue in result["issues"])


def test_validate_preserves_partial_runner_failure_without_vph_claim(tmp_path):
    packet = _packet(tmp_path)
    receipt = _arm_receipt(tmp_path, packet, "pair-01", "control")
    receipt.update({
        "runner_status": "partial",
        "runner_returncode": 1,
        "vph_valid": False,
        "vph": None,
        "per_account_vph": {},
    })
    result = mod.validate_arm(packet, "pair-01", "control", receipt)
    assert result["status"] == "failed"
    assert "runner_not_completed:partial" in result["issues"]
    assert "vph_semantics_mismatch" not in result["issues"]
    assert result["vph"] is None
    assert result["per_account_vph"] == {}


def test_execute_stops_all_later_arms_after_failed_control_gate(tmp_path, monkeypatch):
    packet = {
        "kind": "offline_uncached_throughput_pair",
        "packet_version": 2,
        "packet_path": str(tmp_path / "packet.json"),
        "packet_root": str(tmp_path),
        "coordinator": {"execute_requires_explicit_flag": True},
        "comparison_mode": "environment",
        "pairs": {},
    }
    for pair in mod.PAIRS:
        pair_root = tmp_path / pair
        pair_root.mkdir()
        ids = {account: [f"{pair}-{account}"] for account in mod.ACCOUNTS}
        pair_data = {
            "cohort_ids": [video_id for values in ids.values() for video_id in values],
            "account_manifests": ids,
            "arms": {},
        }
        for arm in mod.ARMS:
            stage = pair_root / arm
            stage.mkdir()
            (stage / "batch.sqlite").write_bytes(b"fixture")
            (stage / "cache.sqlite").write_bytes(b"fixture")
            pair_data["arms"][arm] = {
                "staging_db": str(stage / "batch.sqlite"),
                "staging_cache": str(stage / "cache.sqlite"),
            }
        packet["pairs"][pair] = pair_data
    packet_path = Path(packet["packet_path"])
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    launches = []

    def fake_launch(command, *, run_root, **kwargs):
        launches.append(run_root.parent.parent.name + "/" + run_root.parent.name)
        run_root.mkdir(parents=True)
        (run_root / "multi_account_fetch_summary.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0), None, {"termination_confirmed": True}

    monkeypatch.setattr(mod, "_validate_execution_provenance", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_validate_fresh_runtime_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_ids_in_cache", lambda *args, **kwargs: set())
    monkeypatch.setattr(mod, "_command", lambda *args, **kwargs: ["fixture"])
    monkeypatch.setattr(mod, "_launch_arm", fake_launch)
    monkeypatch.setattr(mod, "_integrity", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(
        mod,
        "validate_arm",
        lambda packet, pair, arm, receipt, **kwargs: {"status": "failed", "issues": ["fixture_gate"]},
    )
    result = mod.execute_packet(packet_path)

    assert launches == ["pair-01/control"]
    assert result["arms"]["pair-01/control"]["status"] == "failed"
    assert result["arms"]["pair-01/adaptive"]["status"] == "failed"
    assert result["arms"]["pair-02/control"]["status"] == "skipped"
    assert result["arms"]["pair-02/adaptive"]["status"] == "skipped"
