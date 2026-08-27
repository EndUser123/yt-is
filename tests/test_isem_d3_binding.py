"""ISEM <-> D3 pre-unseal binding tests — fully offline synthetic fixtures.

Proves the binding refusal discipline WITHOUT touching the real shadow
roots, the evidence store, the holdout, or any provider:

  1. wrong contestant payload hash      -> CONTESTANT_RECONSTRUCTION_MISMATCH
  2. wrong implementation manifest      -> IMPLEMENTATION_MANIFEST_DRIFT
  3. missing contestant                 -> CONTESTANT_RUN_ROOT_MISSING
  4. fourth contestant added            -> CONTESTANT_SET_MISMATCH
  5. reordered contestant list          -> identity digest invariant
  6. provider invocation during replay  -> impossible (source seam scan)
  7. holdout access w/o authorization   -> refused (CLI-level, see
     test_eval_interest_semantic.py F1 tests; this file pins that the
     binding module itself has no ground-truth input path at all)

The real three-shadow reconstruction is binding-time evidence recorded
in the pre-unseal binding manifest; it is deliberately NOT a pytest so
the suite stays hermetic.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import isem_d3_binding as b  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_synthetic_manifest_files(tmp_path: Path) -> list[dict]:
    names = ["ef/contract_v2.py", "ef/inference_contract.py",
             "ef/evidence_clusters.py", "ef/interest_candidates.py",
             "scripts/build_interest_graph.py",
             "scripts/contract_v2_bakeoff.py"]
    manifest = []
    for i, rel in enumerate(names):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # binary write keeps the fixture LF on disk (landed form)
        p.write_bytes(f"# synthetic frozen module {i}\n".encode("utf-8"))
        manifest.append({"path": rel, "git_blob_sha": f"blob{i:04d}",
                         "content_sha256": _sha(
                             p.read_bytes())})
    return manifest


def synthetic_freeze(tmp_path: Path, n_contestants: int = 3,
                     manifest_entries: list[dict] | None = None,
                     run_roots_exist: bool = True,
                     expected_hashes: dict | None = None) -> Path:
    manifest = manifest_entries or write_synthetic_manifest_files(
        tmp_path)
    manifest_digest = b.implementation_manifest_digest(manifest)
    outputs = {}
    for i in range(n_contestants):
        root = tmp_path / f"run-shadow-{i + 1}"
        if run_roots_exist:
            root.mkdir()
        outputs[f"shadow_{i + 1}"] = {
            "run_root": str(root),
            "payload_canonical_sha256": _sha(
                f"contestant-{i + 1}".encode()),
            "code_manifest_sha256": manifest_digest,
        }
    freeze = {
        "document_kind": "INFERENCE_CANDIDATE_D3_FREEZE",
        "implementation_manifest": manifest,
        "implementation_manifest_sha256": manifest_digest,
        "runtime_configuration": {"FROZEN_PLAN_ID": "plan_SYNTHETIC",
                                  "config_identity_complete": True},
        "contestant_outputs": outputs,
    }
    p = tmp_path / "freeze.json"
    p.write_text(json.dumps(freeze), encoding="utf-8")
    return p


def fake_stats():
    return {"strict_validator": "PASSED (synthetic)",
            "objects_in": 3, "canonical_objects": 2,
            "explicit_dispositions": 4, "quarantined_edges": 0,
            "required_link_failures": 0, "run_root": "synthetic",
            "procedure": b.RECONSTRUCTION_PROCEDURE,
            "assemble_canonical_dispositions": 4,
            "relation_stage_dispositions": 0,
            "phase1_failures_consistent": True}


# 1. wrong contestant payload hash -> refuse, never substitute
def test_binding_refuses_wrong_contestant_payload_hash(
        tmp_path, monkeypatch):
    freeze = synthetic_freeze(tmp_path)
    monkeypatch.setattr(
        b, "reconstruct_contestant",
        lambda root, fz: ({"inferred_interests": [], "questions": [],
                           "regret_candidates": []}, fake_stats()))
    with pytest.raises(b.BindingRefusal) as ei:
        b.verify_binding(tmp_path, freeze)
    assert ei.value.code == "CONTESTANT_RECONSTRUCTION_MISMATCH"


def test_binding_accepts_when_reconstruction_matches(
        tmp_path, monkeypatch):
    """Control for refusal test 1: with expected hashes aligned to the
    synthetic replay output, binding verification succeeds."""
    freeze_path = synthetic_freeze(tmp_path)
    payload = {"inferred_interests": [], "questions": [],
               "regret_candidates": []}
    blob = _sha(json.dumps(payload, sort_keys=True,
                           separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    for entry in freeze["contestant_outputs"].values():
        entry["payload_canonical_sha256"] = blob
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    monkeypatch.setattr(
        b, "reconstruct_contestant",
        lambda root, fz: (payload, fake_stats()))
    rep = b.verify_binding(tmp_path, freeze_path)
    assert rep["binding_status"] == \
        "BOUND_WAITING_ON_FRESH_PRE_UNSEAL_REVIEW"
    assert all(c["byte_exact"] for c in rep["contestants"])


# 2. wrong implementation manifest -> refuse
def test_binding_refuses_wrong_implementation_manifest(tmp_path):
    manifest = write_synthetic_manifest_files(tmp_path)
    manifest[0]["content_sha256"] = _sha(b"tampered")
    freeze = synthetic_freeze(tmp_path, manifest_entries=manifest)
    with pytest.raises(b.BindingRefusal) as ei:
        b.verify_implementation_manifest(tmp_path, json.loads(
            freeze.read_text(encoding="utf-8")))
    assert ei.value.code == "IMPLEMENTATION_MANIFEST_DRIFT"


def test_binding_manifest_accepts_eol_variant_and_records_form(
        tmp_path):
    """F2: landed-LF blobs whose frozen content_sha256 was taken over
    the CRLF working-tree variant must verify and record the form."""
    manifest = write_synthetic_manifest_files(tmp_path)
    p = tmp_path / manifest[0]["path"]
    lf = p.read_bytes().replace(b"\r\n", b"\n")
    manifest[0]["content_sha256"] = _sha(
        lf.replace(b"\n", b"\r\n"))  # working-tree CRLF variant
    freeze = synthetic_freeze(tmp_path, manifest_entries=manifest)
    receipt = b.verify_implementation_manifest(
        tmp_path, json.loads(freeze.read_text(encoding="utf-8")))
    first = receipt["files_checked"][0]
    assert first["matches"] is True
    assert first["content_sha256_form_matched"] == \
        "working-crlf-variant"
    assert first["landed_lf_sha256"] == _sha(lf)


# 3. missing contestant -> refuse
def test_binding_refuses_missing_contestant(tmp_path):
    freeze = synthetic_freeze(tmp_path, n_contestants=3)
    expected = {
        f"shadow_{i}": _sha(f"x{i}".encode()) for i in (1, 2)}
    with pytest.raises(b.BindingRefusal) as ei:
        b.verify_binding(tmp_path, freeze,
                         expected_contestants=expected)
    assert ei.value.code == "CONTESTANT_SET_MISMATCH"


def test_binding_refuses_missing_run_root(tmp_path):
    freeze = synthetic_freeze(tmp_path, n_contestants=3,
                              run_roots_exist=False)
    with pytest.raises(b.BindingRefusal) as ei:
        b.verify_binding(tmp_path, freeze)
    assert ei.value.code == "CONTESTANT_RUN_ROOT_MISSING"


# 4. fourth contestant added -> refuse
def test_binding_refuses_fourth_contestant(tmp_path):
    freeze = synthetic_freeze(tmp_path, n_contestants=4)
    with pytest.raises(b.BindingRefusal) as ei:
        b.load_freeze(freeze)
    assert ei.value.code == "CONTESTANT_SET_MISMATCH"


# 5. reordered contestant list cannot change identity silently
def test_binding_identity_is_order_invariant():
    contestants = [
        {"run_id": "shadow_1", "expected_sha256": _sha(b"1"),
         "reconstructed_sha256": _sha(b"1")},
        {"run_id": "shadow_2", "expected_sha256": _sha(b"2"),
         "reconstructed_sha256": _sha(b"2")},
        {"run_id": "shadow_3", "expected_sha256": _sha(b"3"),
         "reconstructed_sha256": _sha(b"3")},
    ]
    reordered = [contestants[2], contestants[0], contestants[1]]
    assert b.binding_identity(contestants) == \
        b.binding_identity(reordered)


# 6. provider invocation during reconstruction -> impossible
def test_reconstruction_module_has_no_provider_seams():
    source = (REPO / "ef" / "isem_d3_binding.py").read_text(
        encoding="utf-8")
    forbidden = [
        "import subprocess", "subprocess.", "import socket",
        "socket.socket", "urllib", "urlopen", "requests.",
        "httpx", "shutil.which", "run_codex_capture",
        "judge_transport", "eval_interest_holdout",
        "cached_clusters", "hydrate_evidence_clusters",
        "load_workspace_env",
    ]
    hits = [tok for tok in forbidden if tok in source]
    assert hits == [], f"provider/store seams found: {hits}"


# 7. the binding module has no ground-truth input path at all
def test_binding_module_never_takes_a_gt_path():
    import inspect
    sigs = {name: str(inspect.signature(fn)) for name, fn in
            vars(b).items() if inspect.isfunction(fn)
            and not name.startswith("_")}
    for name, sig in sigs.items():
        assert "gt" not in sig.replace("digest", "").replace(
            "edge", ""), f"{name}{sig} exposes a gt-shaped parameter"
    source = (REPO / "ef" / "isem_d3_binding.py").read_text(
        encoding="utf-8")
    assert "load_ground_truth" not in source
    assert "private/interest-intelligence-holdout" not in source \
        or "EXPECTED_SEALED_GT_SHA256" in source
