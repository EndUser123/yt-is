from __future__ import annotations

import json
from pathlib import Path

from csf.residual_attempt_ledger import load_residual_attempt_ledger
from scripts.build_residual_attempt_ledger import build_residual_attempt_ledger


def test_builds_ledger_from_applied_receipts_and_skips_validation_receipts(tmp_path: Path) -> None:
    root = tmp_path / "receipts"
    root.mkdir()
    (root / "requeue-validation.json").write_text(json.dumps({
        "receipt_version": 1,
        "apply": False,
        "run_id": "validation",
        "created_at": "2026-08-11T00:00:00+00:00",
        "db_path": "batch.sqlite",
        "manifest_path": "manifest.json",
        "manifest_fingerprint": "sha256:manifest",
        "video_ids": ["aaaaaaaaaaa"],
    }), encoding="utf-8")
    applied = root / "requeue-apply.json"
    applied.write_text(json.dumps({
        "receipt_version": 1,
        "apply": True,
        "status": "applied",
        "run_id": "apply-1",
        "created_at": "2026-08-11T00:00:01+00:00",
        "db_path": "batch.sqlite",
        "manifest_path": "manifest.json",
        "manifest_fingerprint": "sha256:manifest",
        "video_ids": ["aaaaaaaaaaa"],
    }), encoding="utf-8")
    output = tmp_path / "ledger.json"

    result = build_residual_attempt_ledger(roots=(root,), output=output)
    ledger = load_residual_attempt_ledger(output)

    assert result["attempt_count"] == 1
    assert ledger["attempts"][0]["attempt_id"] == "apply-1"
    assert ledger["attempts"][0]["mechanism_id"] == "legacy-receipt"
