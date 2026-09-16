"""Fail-closed readiness check for the yt-is intelligence workflow.

This is a structural gate only. It never calls a provider, opens private
holdouts, or claims semantic quality. A READY result means the executable
contract is present; the semantic-recall and recommendation gates remain
separate.
"""

from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path


REQUIRED_DRIVER_SYMBOLS = (
    "validate_inference",
    "run_inference",
    "run_batch_inference",
    "run_reconciliation_tree",
    "run_bootstrap",
    "load_grounded_source_manifest",
)
REQUIRED_ARTIFACT_SYMBOLS = (
    "GroundedSourceArtifact",
    "GroundedSpan",
    "validate_artifact",
    "to_inference_context",
    "from_media_reference",
    "with_inspected_representations",
)
REQUIRED_DASHBOARD_MARKERS = (
    "def _interest_detail(",
    "def _render_interest_page(",
    'parsed.path.startswith("/interest/")',
)
DISTILL_SOURCE_SKILL = Path("P:/.agents/skills/distill-source/SKILL.md")
REQUIRED_DISTILL_SOURCE_MARKERS = (
    "# /distill-source (fleet)",
    "## 1. Source-only",
    "## 2. High coverage, not a summary",
    "## 4. Provenance",
    "## 7. Artifact fidelity",
    "# Grounded Reference",
    "## Source Status",
)
EVALUATOR_RECEIPT = (
    "docs/handoffs/interest-intelligence/"
    "interest-semantic-evaluator-v1/FREEZE_RECEIPT.json"
)
IMPLEMENTATION_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluator_freeze_status(repo: Path) -> dict:
    """Report evaluator integrity and implementation binding only.

    This is deliberately a design-time diagnostic: it reads the public
    evaluator receipt and frozen source artifacts, never the private holdout
    and never a result artifact.  A structurally valid evaluator with no
    selected inference SHA is an honest waiting state, not a structural
    failure.
    """
    receipt_path = repo / EVALUATOR_RECEIPT
    if not receipt_path.exists():
        return {"status": "UNAVAILABLE", "reason": "freeze receipt missing"}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "INVALID", "reason": f"receipt unreadable: {exc}"}

    drift = []
    for entry in receipt.get("frozen_artifacts", []):
        if not isinstance(entry, dict):
            drift.append("malformed frozen artifact entry")
            continue
        rel = entry.get("path")
        expected = entry.get("sha256")
        path = repo / rel if isinstance(rel, str) else None
        if path is None or not path.exists():
            drift.append(f"missing frozen artifact: {rel!r}")
            continue
        if not isinstance(expected, str) or _sha256(path) != expected:
            drift.append(f"hash drift: {rel}")

    candidate = receipt.get("candidate_inference_implementation")
    if drift:
        status = "INVALID"
        binding = "UNUSABLE"
    elif (isinstance(candidate, str)
          and IMPLEMENTATION_SHA.fullmatch(candidate)):
        status = "BOUND"
        binding = "BOUND"
    else:
        status = "WAITING_ON_IMPLEMENTATION_FREEZE"
        binding = "NOT_BOUND"
    return {
        "status": status,
        "implementation_binding": binding,
        "candidate_inference_implementation": candidate,
        "artifact_drift": drift,
        "receipt_status": receipt.get("status"),
    }


def distill_source_contract_status(skill_path: Path | None = None) -> dict:
    """Verify the canonical source-faithful authoring contract is present.

    This is intentionally a marker check, not an attempt to reinterpret the
    Markdown output as graph state.  The typed ``GroundedSourceArtifact`` is
    still the machine-facing integration boundary.
    """
    path = Path(skill_path or DISTILL_SOURCE_SKILL)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {"status": "UNAVAILABLE", "path": str(path),
                "reason": f"skill unreadable: {exc}", "missing": []}
    missing = [marker for marker in REQUIRED_DISTILL_SOURCE_MARKERS
               if marker not in text]
    return {
        "status": "READY" if not missing else "INVALID",
        "path": str(path),
        "missing": missing,
    }


def check(repo: Path | None = None) -> dict:
    repo = repo or Path(__file__).resolve().parents[1]
    reasons: list[str] = []
    distill_source = distill_source_contract_status()
    if distill_source["status"] != "READY":
        reasons.append(
            "canonical distill-source contract unavailable or incomplete: "
            f"{distill_source.get('reason') or distill_source.get('missing')}"
        )
    driver = None
    try:
        driver = _load(repo / "scripts" / "build_interest_graph.py",
                       "ytis_readiness_build_interest_graph")
    except Exception as exc:
        reasons.append(f"inference driver failed to load: {type(exc).__name__}: {exc}")

    missing_driver = [name for name in REQUIRED_DRIVER_SYMBOLS
                      if driver is None or not hasattr(driver, name)]
    if missing_driver:
        reasons.append("missing driver symbols: " + ", ".join(missing_driver))
    if driver is not None and hasattr(driver, "build_packets"):
        params = inspect.signature(driver.build_packets).parameters
        if "grounded_sources_by_cluster" not in params:
            reasons.append("build_packets has no grounded-source input")
    else:
        reasons.append("build_packets is unavailable")

    try:
        artifact = _load(repo / "ef" / "grounded_source.py",
                         "ytis_readiness_grounded_source")
    except Exception as exc:
        artifact = None
        reasons.append(f"grounded-source contract failed to load: {type(exc).__name__}: {exc}")
    missing_artifact = [name for name in REQUIRED_ARTIFACT_SYMBOLS
                        if artifact is None or not hasattr(artifact, name)]
    if missing_artifact:
        reasons.append("missing grounded-source symbols: " + ", ".join(missing_artifact))

    try:
        manifest_adapter = _load(
            repo / "scripts" / "make_grounded_source_manifest.py",
            "ytis_readiness_grounded_source_manifest",
        )
        if not hasattr(manifest_adapter, "build_manifest"):
            reasons.append("grounded-source manifest adapter has no build_manifest")
    except Exception as exc:
        reasons.append(
            "grounded-source manifest adapter failed to load: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        graph = _load(repo / "ef" / "personal_graph.py",
                      "ytis_readiness_personal_graph")
        if "grounded_sources" not in inspect.signature(
                graph.store_validated_inference).parameters:
            reasons.append("typed persistence has no grounded_sources input")
    except Exception as exc:
        reasons.append(f"typed persistence failed to load: {type(exc).__name__}: {exc}")

    try:
        dashboard_source = (repo / "ef" / "warm_query_service.py").read_text(
            encoding="utf-8")
        missing_dashboard = [marker for marker in REQUIRED_DASHBOARD_MARKERS
                             if marker not in dashboard_source]
        if missing_dashboard:
            reasons.append(
                "missing typed-interest dashboard markers: "
                + ", ".join(missing_dashboard))
    except (OSError, UnicodeError) as exc:
        reasons.append(f"typed-interest dashboard source unavailable: {exc}")

    evaluator = evaluator_freeze_status(repo)
    return {
        "status": "READY" if not reasons else "NOT_READY",
        "structural_only": True,
        "workflow_status": "INCOMPLETE",
        "semantic_recall_gate": "outstanding",
        "semantic_recall_gate_detail": evaluator["status"],
        "recommendation_gate": "blocked_on_semantic_recall_and_provenance",
        "distill_source_contract": distill_source,
        "evaluator_freeze": evaluator,
        "reasons": reasons,
    }


def main() -> int:
    result = check()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
