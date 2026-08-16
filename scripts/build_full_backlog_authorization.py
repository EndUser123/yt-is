#!/usr/bin/env python3
"""Build a version-2 full-backlog authorization receipt from current evidence.

This command does not run authentication, register a scheduler task, or launch
the backlog. It only materializes an explicit, fail-closed receipt after the
caller supplies all five independently verified readiness gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


REQUIRED_GATES = (
    "exact_account_auth",
    "scheduler_execution",
    "cleanup_postcondition",
    "residual_policy",
    "throughput_validation",
)
CANONICAL_ACCOUNTS = ("a.hominidae", "troup.hominidae", "brsthomson")
AUTHORIZATION_SCHEMA_VERSION = 2
GATE_EVIDENCE_SCHEMA_VERSION = 2
GATE_ARTIFACT_SCHEMA_VERSION = 1
FINGERPRINT_PREFIX = "sha256:"

GATE_EVIDENCE_KINDS = {
    "exact_account_auth": "exact-account-auth",
    "scheduler_execution": "scheduler-execution",
    "cleanup_postcondition": "cleanup-postcondition",
    "residual_policy": "residual-policy",
    "throughput_validation": "throughput-validation",
}


def _require_fingerprint(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(FINGERPRINT_PREFIX)
        or len(value) != len(FINGERPRINT_PREFIX) + 64
    ):
        raise ValueError(f"gate evidence {field} fingerprint is invalid")
    try:
        int(value[len(FINGERPRINT_PREFIX):], 16)
    except ValueError as exc:
        raise ValueError(f"gate evidence {field} fingerprint is invalid") from exc
    return value


def _pending_snapshot(db_path: Path) -> tuple[int, str]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("database integrity check did not return ok")
        rows = conn.execute(
            "SELECT video_id FROM analysis_status "
            "WHERE status = 'pending' ORDER BY video_id"
        ).fetchall()
    pending_ids = [str(row[0]) for row in rows]
    canonical = json.dumps(pending_ids, ensure_ascii=True, separators=(",", ":"))
    fingerprint = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return len(pending_ids), fingerprint


def _parse_expiry(raw: str) -> str:
    try:
        expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--expires-at must be an ISO-8601 timestamp") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError("--expires-at must be in the future and include a timezone")
    return expiry.isoformat()


def _parse_gates(values: list[str]) -> dict[str, str]:
    gates: dict[str, str] = {}
    for value in values:
        name, separator, result = value.partition("=")
        if not separator or name not in REQUIRED_GATES or result != "passed":
            raise ValueError(
                "each --gate must be one of "
                + ", ".join(f"{name}=passed" for name in REQUIRED_GATES)
            )
        if name in gates:
            raise ValueError(f"duplicate --gate: {name}")
        gates[name] = result
    if set(gates) != set(REQUIRED_GATES):
        missing = sorted(set(REQUIRED_GATES) - set(gates))
        raise ValueError("missing required gates: " + ", ".join(missing))
    return gates


def _file_fingerprint(path: Path) -> str:
    return FINGERPRINT_PREFIX + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_timestamp(raw: object, *, field: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"gate evidence {field} is missing")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"gate evidence {field} is invalid") from exc
    if value.tzinfo is None:
        raise ValueError(f"gate evidence {field} must include a timezone")
    return value


def _require_claims(payload: object, *, gate: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"gate evidence claims are missing for {gate}")
    return payload


def _require_claim_string(claims: dict[str, object], field: str, *, gate: str) -> str:
    value = claims.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"gate evidence claim {field} is missing for {gate}")
    return value


def _require_claim_bool(claims: dict[str, object], field: str, *, gate: str) -> None:
    if claims.get(field) is not True:
        raise ValueError(f"gate evidence claim {field} is not true for {gate}")


def _require_claim_nonnegative_int(claims: dict[str, object], field: str, *, gate: str) -> int:
    value = claims.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"gate evidence claim {field} is invalid for {gate}")
    return value


def _validate_gate_artifact(gate: str, evidence_path: Path) -> dict[str, object]:
    """Validate the structured, gate-specific artifact behind one sidecar."""
    try:
        artifact = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"gate evidence artifact is not structured JSON for {gate}") from exc
    if not isinstance(artifact, dict) or artifact.get("schema_version") != GATE_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"gate evidence artifact has an invalid schema for {gate}")
    if artifact.get("gate") != gate:
        raise ValueError(f"gate evidence artifact gate mismatch for {gate}")
    if artifact.get("evidence_kind") != GATE_EVIDENCE_KINDS[gate]:
        raise ValueError(f"gate evidence artifact kind mismatch for {gate}")
    if artifact.get("decision") != "passed":
        raise ValueError(f"gate evidence artifact decision is not passed for {gate}")
    claims = _require_claims(artifact.get("claims"), gate=gate)

    if gate == "exact_account_auth":
        accounts = claims.get("account_profiles")
        if accounts != list(CANONICAL_ACCOUNTS):
            raise ValueError(f"gate evidence account profiles are not canonical for {gate}")
        if _require_claim_string(claims, "auth_mode", gate=gate) != "token_only":
            raise ValueError(f"gate evidence auth mode is not token_only for {gate}")
        _require_claim_bool(claims, "all_accounts_passed", gate=gate)
    elif gate == "scheduler_execution":
        _require_claim_string(claims, "task_name", gate=gate)
        if _require_claim_string(claims, "execution_mode", gate=gate) not in {"s4u", "password"}:
            raise ValueError(f"gate evidence scheduler mode is invalid for {gate}")
        _require_claim_bool(claims, "executed", gate=gate)
        if claims.get("plan_only") is not False:
            raise ValueError(f"gate evidence scheduler was plan-only for {gate}")
        _require_claim_string(claims, "run_receipt_path", gate=gate)
    elif gate == "cleanup_postcondition":
        _require_claim_bool(claims, "all_children_cleaned", gate=gate)
        if _require_claim_nonnegative_int(claims, "surviving_process_count", gate=gate) != 0:
            raise ValueError(f"gate evidence has surviving processes for {gate}")
        if _require_claim_string(claims, "staged_db_integrity", gate=gate) != "ok":
            raise ValueError(f"gate evidence staged DB integrity is not ok for {gate}")
    elif gate == "residual_policy":
        _require_claim_string(claims, "policy", gate=gate)
        _require_claim_string(claims, "failed_disposition", gate=gate)
        _require_fingerprint(claims.get("pending_ids_fingerprint"), field=f"{gate} pending IDs")
        _require_fingerprint(claims.get("packet_set_fingerprint"), field=f"{gate} packet set")
        _require_claim_nonnegative_int(claims, "requires_decision_packet_count", gate=gate)
    elif gate == "throughput_validation":
        _require_claim_bool(claims, "valid", gate=gate)
        if _require_claim_nonnegative_int(claims, "repetition_count", gate=gate) < 2:
            raise ValueError(f"gate evidence repetition count is too small for {gate}")
        for field in ("control_vph", "candidate_vph"):
            value = claims.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"gate evidence claim {field} is invalid for {gate}")
        if claims.get("account_profiles") != list(CANONICAL_ACCOUNTS):
            raise ValueError(f"gate evidence account profiles are not canonical for {gate}")
        _require_claim_string(claims, "promotion_rule", gate=gate)
    return artifact


def _validate_gate_evidence_sidecar(
    gate: str,
    sidecar_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate one gate receipt and the artifact it attests to."""
    now = now or datetime.now(timezone.utc)
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"gate evidence is not valid JSON: {sidecar_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != GATE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError(f"gate evidence has an invalid schema: {sidecar_path}")
    if payload.get("gate") != gate or payload.get("decision") != "passed":
        raise ValueError(f"gate evidence decision mismatch for {gate}: {sidecar_path}")
    if payload.get("evidence_kind") != GATE_EVIDENCE_KINDS[gate]:
        raise ValueError(f"gate evidence kind mismatch for {gate}: {sidecar_path}")
    if payload.get("status") != "passed":
        raise ValueError(f"gate evidence status is not passed for {gate}")
    verified_at = _parse_timestamp(payload.get("verified_at"), field="verified_at")
    expires_at = _parse_timestamp(payload.get("expires_at"), field="expires_at")
    if verified_at > now:
        raise ValueError(f"gate evidence verified_at is in the future for {gate}")
    if expires_at <= verified_at:
        raise ValueError(f"gate evidence expires before verification for {gate}")
    if expires_at <= now:
        raise ValueError(f"gate evidence has expired for {gate}")
    evidence_value = payload.get("evidence_path")
    evidence_hash = payload.get("evidence_sha256")
    if not isinstance(evidence_value, str) or not evidence_value.strip():
        raise ValueError(f"gate evidence artifact path is missing for {gate}")
    evidence_path = Path(evidence_value).resolve()
    if not evidence_path.is_file():
        raise ValueError(f"gate evidence artifact is missing for {gate}: {evidence_path}")
    if not evidence_path.read_bytes().strip():
        raise ValueError(f"gate evidence artifact is empty for {gate}")
    _require_fingerprint(evidence_hash, field="artifact")
    if evidence_hash != _file_fingerprint(evidence_path):
        raise ValueError(f"gate evidence artifact fingerprint mismatch for {gate}")
    _validate_gate_artifact(gate, evidence_path)
    return {
        "path": str(sidecar_path.resolve()),
        "sha256": _file_fingerprint(sidecar_path),
        "evidence_path": str(evidence_path),
        "evidence_sha256": evidence_hash,
        "evidence_kind": GATE_EVIDENCE_KINDS[gate],
        "verified_at": verified_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def validate_gate_evidence(
    payload: dict[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, object]]:
    """Validate all per-gate receipts stored in an authorization payload."""
    raw = payload.get("gate_evidence")
    if not isinstance(raw, dict) or set(raw) != set(REQUIRED_GATES):
        raise ValueError("full-backlog authorization gate evidence is incomplete")
    validated: dict[str, dict[str, object]] = {}
    evidence_paths: set[str] = set()
    for gate in REQUIRED_GATES:
        entry = raw.get(gate)
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or entry.get("evidence_kind") != GATE_EVIDENCE_KINDS[gate]
        ):
            raise ValueError(f"full-backlog authorization gate evidence is invalid: {gate}")
        sidecar_path = Path(entry["path"]).resolve()
        if not sidecar_path.is_file():
            raise ValueError(f"full-backlog authorization gate evidence is missing: {gate}")
        _require_fingerprint(entry.get("sha256"), field=f"{gate} sidecar")
        if entry.get("sha256") != _file_fingerprint(sidecar_path):
            raise ValueError(f"full-backlog authorization gate evidence fingerprint mismatch: {gate}")
        validated[gate] = _validate_gate_evidence_sidecar(gate, sidecar_path, now=now)
        evidence_path = str(validated[gate]["evidence_path"])
        if evidence_path in evidence_paths:
            raise ValueError(f"full-backlog authorization reuses gate evidence artifact: {evidence_path}")
        evidence_paths.add(evidence_path)
    return validated


def _parse_accounts(raw: str) -> list[str]:
    accounts = [item.strip() for item in raw.split(",") if item.strip()]
    if tuple(accounts) != CANONICAL_ACCOUNTS:
        raise ValueError(
            "full-backlog authorization requires the canonical account order: "
            + ",".join(CANONICAL_ACCOUNTS)
        )
    return accounts


def build_receipt(args: argparse.Namespace) -> dict[str, object]:
    db_path = args.db_path.resolve()
    settings_path = args.account_settings.resolve()
    output_path = args.output.resolve()
    if not db_path.is_file():
        raise ValueError(f"database does not exist: {db_path}")
    if not settings_path.is_file():
        raise ValueError(f"account settings file does not exist: {settings_path}")
    evidence = [str(path.resolve()) for path in args.evidence]
    unreadable = [path for path in evidence if not Path(path).is_file()]
    if unreadable:
        raise ValueError("evidence path is missing or unreadable: " + unreadable[0])
    evidence_fingerprints = {
        path: _file_fingerprint(Path(path))
        for path in evidence
    }
    if output_path.exists():
        raise ValueError(f"refusing to overwrite existing authorization: {output_path}")
    accounts = _parse_accounts(args.accounts)
    pending_count, pending_fingerprint = _pending_snapshot(db_path)
    _parse_gates(args.gate)
    gate_evidence: dict[str, dict[str, object]] = {}
    for value in args.gate_evidence:
        name, separator, path_value = value.partition("=")
        if not separator or name not in REQUIRED_GATES or not path_value.strip():
            raise ValueError(
                "each --gate-evidence must be one of "
                + ", ".join(f"{name}=PATH" for name in REQUIRED_GATES)
            )
        if name in gate_evidence:
            raise ValueError(f"duplicate --gate-evidence: {name}")
        sidecar_path = Path(path_value).resolve()
        if not sidecar_path.is_file():
            raise ValueError(f"gate evidence path is missing or unreadable: {sidecar_path}")
        gate_evidence[name] = _validate_gate_evidence_sidecar(name, sidecar_path)
    if set(gate_evidence) != set(REQUIRED_GATES):
        missing = sorted(set(REQUIRED_GATES) - set(gate_evidence))
        raise ValueError("missing required gate evidence: " + ", ".join(missing))
    evidence_paths = [str(entry["evidence_path"]) for entry in gate_evidence.values()]
    if len(set(evidence_paths)) != len(evidence_paths):
        raise ValueError("full-backlog authorization reuses a gate evidence artifact")
    receipt = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "decision": "authorized",
        "db_path": str(db_path),
        "accounts": accounts,
        "account_settings_file_fingerprint": "sha256:" + hashlib.sha256(
            settings_path.read_bytes()
        ).hexdigest(),
        "pending_count_at_authorization": pending_count,
        "pending_ids_fingerprint_at_authorization": pending_fingerprint,
        "gates": {gate: "passed" for gate in REQUIRED_GATES},
        "evidence": evidence,
        "evidence_fingerprints": evidence_fingerprints,
        "gate_evidence": gate_evidence,
        "authorized_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": _parse_expiry(args.expires_at),
    }
    validate_gate_evidence(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--account-settings", type=Path, required=True)
    parser.add_argument("--accounts", required=True)
    parser.add_argument("--evidence", type=Path, action="append", required=True)
    parser.add_argument("--gate", action="append", default=[])
    parser.add_argument("--gate-evidence", action="append", default=[])
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = build_receipt(args)
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.resolve().write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"authorization receipt not written: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
