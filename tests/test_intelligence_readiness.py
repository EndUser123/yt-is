from __future__ import annotations

import json

from scripts.check_intelligence_readiness import (
    distill_source_contract_status,
    evaluator_freeze_status,
)


def _receipt(tmp_path, *, candidate="NOT_YET_FROZEN"):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("frozen", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "status": "EVALUATOR_READY_WAITING_ON_INFERENCE_FREEZE",
        "candidate_inference_implementation": candidate,
        "frozen_artifacts": [{"path": "artifact.txt", "sha256": digest}],
    }), encoding="utf-8")
    return receipt


def test_evaluator_freeze_reports_waiting_without_opening_holdout(tmp_path,
                                                                  monkeypatch):
    receipt = _receipt(tmp_path)
    monkeypatch.setattr(
        "scripts.check_intelligence_readiness.EVALUATOR_RECEIPT",
        "receipt.json",
    )
    result = evaluator_freeze_status(tmp_path)
    assert result["status"] == "WAITING_ON_IMPLEMENTATION_FREEZE"
    assert result["implementation_binding"] == "NOT_BOUND"
    assert result["artifact_drift"] == []


def test_evaluator_freeze_detects_artifact_drift(tmp_path, monkeypatch):
    _receipt(tmp_path, candidate="9931396")
    monkeypatch.setattr(
        "scripts.check_intelligence_readiness.EVALUATOR_RECEIPT",
        "receipt.json",
    )
    (tmp_path / "artifact.txt").write_text("changed", encoding="utf-8")
    result = evaluator_freeze_status(tmp_path)
    assert result["status"] == "INVALID"
    assert result["implementation_binding"] == "UNUSABLE"
    assert result["artifact_drift"] == ["hash drift: artifact.txt"]


def test_evaluator_freeze_accepts_bound_sha(tmp_path, monkeypatch):
    _receipt(tmp_path, candidate="993139679488b9c8db345cfb9b1667db54ca2e4b")
    monkeypatch.setattr(
        "scripts.check_intelligence_readiness.EVALUATOR_RECEIPT",
        "receipt.json",
    )
    result = evaluator_freeze_status(tmp_path)
    assert result["status"] == "BOUND"
    assert result["implementation_binding"] == "BOUND"


def test_distill_source_contract_requires_canonical_markers(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "# /distill-source (fleet)\n## 1. Source-only\n"
        "## 2. High coverage, not a summary\n## 4. Provenance\n"
        "## 7. Artifact fidelity\n# Grounded Reference\n## Source Status\n",
        encoding="utf-8",
    )
    result = distill_source_contract_status(skill)
    assert result["status"] == "READY"
    assert result["missing"] == []


def test_distill_source_contract_reports_drift(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# /distill-source (fleet)\n", encoding="utf-8")
    result = distill_source_contract_status(skill)
    assert result["status"] == "INVALID"
    assert "## 4. Provenance" in result["missing"]
