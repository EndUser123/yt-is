import json
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.build_full_backlog_authorization as mod


def _args(tmp_path: Path) -> object:
    db_path = tmp_path / "batch.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO analysis_status VALUES ('aaaaaaaaaaa', 'pending')")
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    evidence = tmp_path / "evidence.md"
    evidence.write_text("passed", encoding="utf-8")
    gate_evidence = []
    claims_by_gate = {
        "exact_account_auth": {
            "account_profiles": list(mod.CANONICAL_ACCOUNTS),
            "auth_mode": "token_only",
            "all_accounts_passed": True,
        },
        "scheduler_execution": {
            "task_name": "YtisUnattendedBacklog",
            "execution_mode": "s4u",
            "executed": True,
            "plan_only": False,
            "run_receipt_path": str(tmp_path / "scheduler-run-receipt.json"),
        },
        "cleanup_postcondition": {
            "all_children_cleaned": True,
            "surviving_process_count": 0,
            "staged_db_integrity": "ok",
        },
        "residual_policy": {
            "policy": "pending_only_drain_deferred_failed",
            "failed_disposition": "deferred_failed_no_automatic_retry",
            "pending_ids_fingerprint": "sha256:" + "1" * 64,
            "packet_set_fingerprint": "sha256:" + "2" * 64,
            "requires_decision_packet_count": 0,
        },
        "throughput_validation": {
            "valid": True,
            "repetition_count": 2,
            "control_vph": 3000.0,
            "candidate_vph": 3200.0,
            "account_profiles": list(mod.CANONICAL_ACCOUNTS),
            "promotion_rule": "candidate_beats_control_with_quality_and_failure_guards",
        },
    }
    for name in mod.REQUIRED_GATES:
        artifact = tmp_path / f"{name}-artifact.json"
        artifact.write_text(
            json.dumps({
                "schema_version": mod.GATE_ARTIFACT_SCHEMA_VERSION,
                "gate": name,
                "evidence_kind": mod.GATE_EVIDENCE_KINDS[name],
                "decision": "passed",
                "claims": claims_by_gate[name],
            }),
            encoding="utf-8",
        )
        evidence_sha256 = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        sidecar = tmp_path / f"{name}.json"
        sidecar.write_text(
            json.dumps(
                {
                    "schema_version": mod.GATE_EVIDENCE_SCHEMA_VERSION,
                    "gate": name,
                    "evidence_kind": mod.GATE_EVIDENCE_KINDS[name],
                    "decision": "passed",
                    "status": "passed",
                    "evidence_path": str(artifact.resolve()),
                    "evidence_sha256": evidence_sha256,
                    "verified_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        gate_evidence.append(f"{name}={sidecar}")
    return mod.argparse.Namespace(
        db_path=db_path,
        account_settings=settings,
        accounts="a.hominidae,troup.hominidae,brsthomson",
        evidence=[evidence],
        gate=[f"{name}=passed" for name in mod.REQUIRED_GATES],
        gate_evidence=gate_evidence,
        expires_at="2099-01-01T00:00:00Z",
        output=tmp_path / "authorization.json",
    )


def test_build_receipt_binds_pending_id_set(tmp_path: Path) -> None:
    args = _args(tmp_path)
    payload = mod.build_receipt(args)

    assert payload["schema_version"] == 2
    assert payload["pending_count_at_authorization"] == 1
    assert str(payload["pending_ids_fingerprint_at_authorization"]).startswith("sha256:")
    assert set(payload["gates"]) == set(mod.REQUIRED_GATES)
    assert set(payload["gate_evidence"]) == set(mod.REQUIRED_GATES)


def test_build_receipt_rejects_unbound_gate_evidence(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.gate_evidence[0] = args.gate_evidence[0].split("=", 1)[0] + "=" + str(
        tmp_path / "missing.json"
    )

    with pytest.raises(ValueError, match="missing or unreadable"):
        mod.build_receipt(args)


def test_build_receipt_rejects_empty_attested_evidence(tmp_path: Path) -> None:
    args = _args(tmp_path)
    sidecar = Path(args.gate_evidence[0].split("=", 1)[1])
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    artifact = Path(payload["evidence_path"])
    artifact.write_text("", encoding="utf-8")
    payload["evidence_sha256"] = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact is empty"):
        mod.build_receipt(args)


def test_build_receipt_rejects_forged_gate_status(tmp_path: Path) -> None:
    args = _args(tmp_path)
    sidecar = Path(args.gate_evidence[0].split("=", 1)[1])
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="status is not passed"):
        mod.build_receipt(args)


def test_build_receipt_rejects_generic_gate_artifact(tmp_path: Path) -> None:
    args = _args(tmp_path)
    sidecar = Path(args.gate_evidence[0].split("=", 1)[1])
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    artifact = Path(payload["evidence_path"])
    artifact.write_text("passed", encoding="utf-8")
    payload["evidence_sha256"] = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="structured JSON"):
        mod.build_receipt(args)


def test_build_receipt_rejects_reused_gate_artifact(tmp_path: Path) -> None:
    args = _args(tmp_path)
    first_name, first_path = args.gate_evidence[0].split("=", 1)
    first_payload = json.loads(Path(first_path).read_text(encoding="utf-8"))
    for value in args.gate_evidence[1:]:
        name, path = value.split("=", 1)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["evidence_path"] = first_payload["evidence_path"]
        payload["evidence_sha256"] = first_payload["evidence_sha256"]
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="evidence artifact.*mismatch|reuses"):
        mod.build_receipt(args)


def test_build_receipt_rejects_stale_gate_evidence(tmp_path: Path) -> None:
    args = _args(tmp_path)
    sidecar = Path(args.gate_evidence[0].split("=", 1)[1])
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="expired"):
        mod.build_receipt(args)


def test_build_receipt_requires_all_gates(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.gate = ["exact_account_auth=passed"]

    with pytest.raises(ValueError, match="missing required gates"):
        mod.build_receipt(args)


def test_build_receipt_requires_canonical_accounts(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.accounts = "a.hominidae,troup.hominidae"

    with pytest.raises(ValueError, match="canonical account order"):
        mod.build_receipt(args)


def test_main_refuses_overwrite(tmp_path: Path, capsys) -> None:
    args = _args(tmp_path)
    args.output.write_text(json.dumps({"old": True}), encoding="utf-8")

    assert mod.main([
        "--db-path", str(args.db_path),
        "--account-settings", str(args.account_settings),
        "--accounts", args.accounts,
        "--evidence", str(args.evidence[0]),
        "--expires-at", args.expires_at,
        "--output", str(args.output),
        *sum((["--gate", value] for value in args.gate), []),
        *sum((["--gate-evidence", value] for value in args.gate_evidence), []),
    ]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
