#!/usr/bin/env python3
"""Build exact, read-only retry packets from a residual audit.

The audit is the source of truth for classification.  This tool materializes
one exact failed-video manifest and one decision packet per non-terminal
failure class.  It never changes ``analysis_status`` and does not authorize a
retry; a later reviewed operation must still use the guarded requeue command.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_batch_db_path
from csf.video_selection_manifest import load_video_selection_manifest
from scripts.build_video_selection_manifest import main as build_manifest_main


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

POLICIES: dict[str, dict[str, str]] = {
    "command": {
        "disposition": "bounded_industrial_fallback_candidate",
        "mechanism": "NotebookLM command failure; preserve the exact failure class and use the opt-in industrial fallback route.",
        "next_action": "Review the exact manifest and run a one-item fallback canary only after immediate token-only auth and a fresh packet gate.",
        "falsifier": "Any fallback canary that emits later NotebookLM source/add/content actions, leaves pending or missing IDs, or fails SQLite/cleanup reconciliation.",
        "early_abort": "Stop on auth failure, unexpected source mutation, fallback timeout, a non-selected ID, or any DB/manifest mismatch.",
        "postcondition": "Every selected ID is terminal and reconciled; successful rows have non-empty cache output; failures are explicitly classified.",
    },
    "content_threshold": {
        "disposition": "bounded_quality_retry_candidate",
        "mechanism": "NotebookLM content fell below the quality threshold; recovery must preserve quality gates and use an exact bounded packet.",
        "next_action": "Design and review a class-specific quality-recovery canary; do not blanket-requeue the class.",
        "falsifier": "A candidate retry produces empty/low-quality output, bypasses validation, duplicates attempts without improvement, or fails exact reconciliation.",
        "early_abort": "Stop on missing auth, invalid output, citation/quality-gate failure, duplicate selection, or a non-terminal DB postcondition.",
        "postcondition": "Each selected ID is complete only with validated non-empty output; otherwise it remains an explicit failed quality residual.",
    },
    "fallback_quality": {
        "disposition": "blocked_quality_policy",
        "mechanism": "Fallback produced non-empty output below the canonical promotion gate; preserve the failed state until a reviewed quality policy exists.",
        "next_action": "Review the exact output and define a quality-recovery or terminal-classification policy before any retry or promotion.",
        "falsifier": "Exact evidence shows the output meets the canonical promotion gate, or a reviewed recovery produces validated output without weakening quality checks.",
        "early_abort": "Stop on any unreviewed retry, promotion, quality-gate bypass, duplicate selection, or non-terminal DB postcondition.",
        "postcondition": "The row remains failed or is promoted only through an exact quality-gated receipt; no ambiguous pending state is allowed.",
    },
    "whisper_timeout": {
        "disposition": "bounded_quality_retry_candidate",
        "mechanism": "The prior bounded fallback retry exhausted Whisper without output; this is a negative mechanism result, not permission to raise the deadline.",
        "next_action": "Keep automatic retry disabled until a new transcription mechanism or discriminating evidence exists.",
        "falsifier": "A new mechanism demonstrates bounded completion with validated output without increasing unbounded resource use or changing unrelated rows.",
        "early_abort": "Stop on timeout, empty output, resource-budget breach, changed selection, or any NotebookLM action in a fallback-only run.",
        "postcondition": "No selected ID remains ambiguously pending; output and failure stage are reconciled exactly.",
    },
    "cookie_source": {
        "disposition": "blocked_external_cookie_state",
        "mechanism": "Audio acquisition depends on external cookie state that is not available to this offline packet builder.",
        "next_action": "Obtain a changed, operator-approved cookie-state receipt before any retry; do not use this packet to trigger login or fetch.",
        "falsifier": "A fresh exact cookie-state receipt proves the expected source can be acquired without weakening account or security boundaries.",
        "early_abort": "Stop if the cookie source is missing, stale, wrong-account, or requires an unapproved interactive login.",
        "postcondition": "The external-state receipt is bound to the exact IDs and expiry before any execution is considered.",
    },
    "source_add": {
        "disposition": "bounded_fallback_candidate",
        "mechanism": "Source-add failure; direct RPC9 replay remains prohibited without new provider evidence.",
        "next_action": "Use an exact fallback-only canary after a reviewed source-add packet; never replay RPC9 blindly.",
        "falsifier": "Direct replay is required, or fallback emits NotebookLM mutation after admission, or exact reconciliation fails.",
        "early_abort": "Stop on any direct RPC9 replay, unexpected source mutation, missing ID, or DB/cleanup failure.",
        "postcondition": "Every selected ID is terminal with an exact source-add/fallback receipt and no ambiguous pending state.",
    },
    "source_addressability": {
        "disposition": "bounded_fallback_candidate",
        "mechanism": "Content source addressability failure; source-list presence alone is not proof that content is addressable.",
        "next_action": "Use the explicit addressability fallback route only in an exact canary with marker and post-route event gates.",
        "falsifier": "A source-presence retry recovers content reliably, or the fallback route loses the marker or performs later NotebookLM content work.",
        "early_abort": "Stop on unknown presence, broadened retry admission, post-route NotebookLM actions, or non-selected IDs.",
        "postcondition": "Each selected ID is terminal and the route, cache, manifest, DB, and cleanup receipts agree.",
    },
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_text(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_audit(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("audit root must be an object")
    if payload.get("integrity_check") != "ok":
        raise ValueError("audit integrity_check must be ok")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("audit rows must be a list")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"audit rows[{index}] must be an object")
        if not row.get("requires_decision_packet"):
            continue
        video_id = row.get("video_id")
        failure_class = row.get("failure_class")
        if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
            raise ValueError(f"invalid exact video_id at audit rows[{index}]")
        if video_id in seen:
            raise ValueError(f"duplicate video_id in packet-required audit rows: {video_id}")
        if row.get("status") != "failed":
            raise ValueError(f"packet-required row is not failed: {video_id}")
        if not isinstance(failure_class, str) or not failure_class.strip():
            raise ValueError(f"missing failure_class for {video_id}")
        seen.add(video_id)
        selected.append(dict(row))
    return payload, selected


def _render_packet(
    *,
    failure_class: str,
    rows: list[dict[str, Any]],
    audit_path: Path,
    audit_sha256: str,
    manifest_path: Path,
    manifest_fingerprint: str,
    db_path: Path,
    generated_at: str,
) -> str:
    policy = POLICIES.get(failure_class, {
        "disposition": "blocked_unclassified",
        "mechanism": "Unclassified residual; no execution policy is inferred.",
        "next_action": "Create a reviewed class-specific packet before any retry.",
        "falsifier": "A reviewed artifact proves a different failure class or safe recovery mechanism.",
        "early_abort": "Stop on any mismatch, unknown state, or missing receipt.",
        "postcondition": "Every selected ID is explicitly terminal or safely retryable with exact receipts.",
    })
    ids = [str(row["video_id"]) for row in rows]
    lines = [
        f"# Residual Policy Packet: `{failure_class}`",
        "",
        f"Generated: `{generated_at}`",
        "Decision: `packet_required_not_authorized`",
        "",
        "This packet is a read-only execution boundary. It authorizes no retry,",
        "does not change the canonical database, and must be reviewed before a",
        "separate exact requeue receipt can be applied.",
        "",
        "## Exact scope",
        "",
        f"- Failure class: `{failure_class}`",
        f"- Rows: `{len(rows)}`",
        f"- Database: `{db_path}`",
        f"- Audit: `{audit_path}`",
        f"- Audit SHA-256: `{audit_sha256}`",
        f"- Manifest: `{manifest_path}`",
        f"- Manifest fingerprint: `{manifest_fingerprint}`",
        "",
        "The manifest is the complete ordered scope for this class at packet",
        "generation. A changed DB status, manifest fingerprint, or audit hash",
        "invalidates this packet.",
        "",
        "## Current disposition",
        "",
        f"**{policy['disposition']}** — {policy['mechanism']}",
        "",
        f"Recommended next action: {policy['next_action']}",
        "",
        "## Gates",
        "",
        f"- Falsifier: {policy['falsifier']}",
        f"- Early abort: {policy['early_abort']}",
        f"- Postcondition: {policy['postcondition']}",
        "- Immediate preflight: exact token-only auth for the account(s) in the",
        "  manifest; no legacy login, browser bootstrap, external metadata fetch,",
        "  or `--no-sandbox` workaround.",
        "- Execution: use a fresh isolated output/state root and the guarded",
        "  requeue command; never edit the canonical DB by hand.",
        "",
        "## IDs",
        "",
        "```text",
        *ids,
        "```",
        "",
        "## Claim ledger",
        "",
        "| Claim | Type | Evidence | Allowed action |",
        "|---|---|---|---|",
        f"| These `{len(rows)}` rows share a current audit class | `measured_metric` | `{audit_path}` | Use only this exact manifest |",
        f"| Recovery policy may improve the outcome | `hypothesis` | class-specific mechanism above | Evidence gathering only |",
        "| Full-backlog readiness follows from this packet | `unsupported` | none | Prohibited |",
        "",
    ]
    return "\n".join(lines)


def build_residual_packets(
    *,
    audit_path: Path,
    output_dir: Path,
    db_path: Path | None = None,
    selection_prefix: str = "unattended-residual-20260811",
    overwrite: bool = False,
) -> dict[str, Any]:
    audit_path = audit_path.resolve()
    output_dir = output_dir.resolve()
    db_path = (db_path or get_batch_db_path()).resolve()
    audit, rows = _load_audit(audit_path)
    recorded_db = audit.get("db_path")
    if recorded_db and Path(str(recorded_db)).resolve() != db_path:
        raise ValueError(f"audit database does not match requested database: {recorded_db}")
    if not rows:
        raise ValueError("audit contains no packet-required rows")

    generated_at = datetime.now(timezone.utc).isoformat()
    audit_sha256 = _sha256_file(audit_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["failure_class"]), []).append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    class_records: dict[str, Any] = {}
    for failure_class in sorted(grouped):
        class_rows = grouped[failure_class]
        safe_class = re.sub(r"[^A-Za-z0-9_-]+", "_", failure_class).strip("_")
        id_file = output_dir / f"{safe_class}.video_ids.txt"
        manifest_path = output_dir / f"{safe_class}.manifest.json"
        packet_path = output_dir / f"{safe_class}.decision_packet.md"
        ids = [str(row["video_id"]) for row in class_rows]
        _write_text(id_file, "\n".join(ids) + "\n", overwrite=overwrite)
        args = [
            "--output", str(manifest_path),
            "--selection-name", f"{selection_prefix}-{safe_class}",
            "--db-path", str(db_path),
            "--status", "failed",
            "--order-by", "updated_at",
            "--video-id-file", str(id_file),
        ]
        if overwrite:
            args.append("--overwrite")
        with redirect_stdout(io.StringIO()):
            build_manifest_main(args)
        manifest = load_video_selection_manifest(manifest_path)
        manifest_ids = [item.video_id for item in manifest.items]
        if manifest_ids != ids:
            raise RuntimeError(f"manifest order/content mismatch for {failure_class}")
        packet_text = _render_packet(
            failure_class=failure_class,
            rows=class_rows,
            audit_path=audit_path,
            audit_sha256=audit_sha256,
            manifest_path=manifest_path,
            manifest_fingerprint=manifest.fingerprint,
            db_path=db_path,
            generated_at=generated_at,
        )
        _write_text(packet_path, packet_text, overwrite=overwrite)
        class_records[failure_class] = {
            "count": len(class_rows),
            "video_ids": ids,
            "disposition": POLICIES.get(failure_class, {}).get("disposition", "blocked_unclassified"),
            "manifest_path": str(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "id_file": str(id_file),
            "packet_path": str(packet_path),
        }

    index = {
        "packet_set_version": 1,
        "generated_at": generated_at,
        "audit_path": str(audit_path),
        "audit_sha256": audit_sha256,
        "db_path": str(db_path),
        "integrity_check": audit.get("integrity_check"),
        "decision": "packet_required_not_authorized",
        "class_counts": {key: value["count"] for key, value in class_records.items()},
        "classes": class_records,
        "live_authorized": False,
        "database_mutated": False,
    }
    _write_text(
        output_dir / "residual_retry_packet_set.json",
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        overwrite=overwrite,
    )
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--selection-prefix", default="unattended-residual-20260811")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = build_residual_packets(
        audit_path=args.audit,
        output_dir=args.output_dir,
        db_path=args.db_path,
        selection_prefix=args.selection_prefix,
        overwrite=args.overwrite,
    )
    print(json.dumps({
        "status": "built",
        "decision": result["decision"],
        "class_counts": result["class_counts"],
        "output_dir": str(Path(args.output_dir).resolve()),
        "live_authorized": result["live_authorized"],
        "database_mutated": result["database_mutated"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
