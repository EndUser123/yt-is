"""ISEM <-> D3 pre-unseal binding CLI (BINDING_AMENDMENT_2).

Verifies the pre-unseal binding of the frozen Interest Semantic
Evaluator (ISEM v1, landed chain 02fd3a7e -> ff9696ee -> a91bdec1) to
the frozen D3 inference contestant (freeze commit f7bd24fd) and emits
the machine-readable binding manifest.

  python scripts/bind_isem_d3.py --verify
  python scripts/bind_isem_d3.py --emit docs/handoffs/interest-intelligence/isem-d3-pre-unseal-binding.json

The verification replays all three frozen contestant payloads from
persisted provider artifacts with ZERO provider calls (see
ef/isem_d3_binding.py) and refuses on any mismatch. The holdout is
never opened; only its public expected sha256 is echoed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import isem_d3_binding as b  # noqa: E402

FREEZE_DOC = REPO / ("docs/handoffs/interest-intelligence/"
                     "inference-candidate-d3-freeze.json")
FREEZE_COMMIT = "f7bd24fdb917aa5e35112d0b2f2eae1c2129bf59"
EVALUATOR_LINEAGE = {
    "candidate_commit": "02fd3a7e",
    "integration_commit": "ff9696ee",
    "bookkeeping_commit": "a91bdec1",
    "amendment": "BINDING_AMENDMENT_2 (this candidate branch head)",
}
DEFAULT_OUT = REPO / ("docs/handoffs/interest-intelligence/"
                      "isem-d3-pre-unseal-binding.json")


def build_binding_manifest(freeze_doc: Path) -> dict:
    rep = b.verify_binding(REPO, freeze_doc)
    from ef import eval_interest_semantic as isem
    receipt_path = REPO / ("docs/handoffs/interest-intelligence/"
                           "interest-semantic-evaluator-v1/"
                           "FREEZE_RECEIPT.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    amendment_files = [
        "ef/isem_d3_binding.py",
        "scripts/bind_isem_d3.py",
        "docs/handoffs/interest-intelligence/"
        "interest-semantic-evaluator-v1/AMENDMENT_2_D3_BINDING.md",
    ]
    contestants = [{
        "run_id": c["run_id"],
        "run_root": c["run_root"],
        "expected_sha256": c["expected_sha256"],
        "reconstructed_sha256": c["reconstructed_sha256"],
        "byte_exact": c["byte_exact"],
        "strict_validator": c["strict_validator"],
        "counts": c["counts"],
    } for c in rep["contestants"]]
    manifest = {
        "document_kind": "ISEM_D3_PRE_UNSEAL_BINDING",
        "created_utc": isem.time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": b.BINDING_STATUS,
        "evaluator": {
            "identity": "isem_v1 (ARCHITECT_AMENDMENT_1 frozen core)",
            "freeze_lineage": EVALUATOR_LINEAGE,
            "canonical_artifact_hashes":
                receipt["frozen_artifacts"],
            "freeze_receipt_sha256": b.sha256_file(receipt_path),
            "judge_prompts_sha256": receipt["judge_prompts_sha256"],
            "min_n_per_type": receipt["min_n_per_type"],
        },
        "inference_freeze": {
            "architecture": "D3_DECOMPOSED",
            "freeze_commit": FREEZE_COMMIT,
            "implementation_manifest_sha256":
                rep["inference_freeze"]["implementation_manifest_sha256"],
            "runtime_configuration":
                rep["inference_freeze"]["runtime_configuration"],
        },
        "contestants": contestants,
        "binding_identity_sha256": rep["binding_identity_sha256"],
        "reconstruction": {
            "procedure": b.RECONSTRUCTION_PROCEDURE,
            "implementation_module": "ef/isem_d3_binding.py",
            "implementation_sha256": b.sha256_file(
                REPO / "ef" / "isem_d3_binding.py"),
            "serialization_recipe":
                rep["reconstruction"]["serialization_recipe"],
            "provider_calls": "ZERO",
            "holdout_opened": "NO",
            "note": "The v2 shadow driver never persisted assembled "
                    "finals; contestant identity is deterministic "
                    "replay of frozen committed code over the frozen "
                    "provider artifacts. A hash mismatch is fatal "
                    "(CONTESTANT_RECONSTRUCTION_MISMATCH): no "
                    "replacement runs, no reruns.",
        },
        "expected_holdout": {
            "path_class": "SEALED — never opened by this binding",
            "public_sha256": b.EXPECTED_SEALED_GT_SHA256,
        },
        "binding_rule": {
            "ISEM_MUST_SCORE_ALL_THREE_FROZEN_OUTPUTS": True,
            "REPEATABLE_PERFECT": "YES iff shadow_1 AND shadow_2 AND "
                                  "shadow_3 Interest finite-set "
                                  "conformance = PERFECT; any "
                                  "IMPERFECT -> NO. No majority vote; "
                                  "no best-run selection; no rerun "
                                  "after labels are opened.",
            "minor_denominator_rule": "Goal / InformationNeed / "
                                      "Question reported per run; no "
                                      "promotion threshold invented "
                                      "for their tiny denominators",
        },
        "outcome_families": {
            "A_finite_set_correctness":
                "ISEM finite-set conformance over the sealed holdout "
                "(post-unseal, single scoring run)",
            "B_generalization_evidence":
                "ISEM MIN_N_PER_TYPE=5 PASS/FAIL-gated "
                "SUFFICIENT/INSUFFICIENT evidence (orthogonal to A)",
            "C_run_to_run_semantic_stability":
                "Already-measured label-free instability stands: exact "
                "3-way Interest intersection 8 of union 500 (IoU "
                "0.016). Descriptive only; not a reason to alter D3; "
                "known-set success must not hide it.",
        },
        "amendment": {
            "name": "BINDING_AMENDMENT_2",
            "findings": {
                "F1": "support carries the same explicit holdout "
                      "authorization boundary as score "
                      "(FIXED, see tests: F1 support tests)",
                "F2": "full superseded-hash ledger recorded in "
                      "AMENDMENT_2_D3_BINDING.md; reviewed "
                      "working-tree (CRLF) hashes vs canonical landed "
                      "(LF) repo-content hashes kept distinct; no "
                      "historical receipt rewritten (COMPLETE)",
                "F3": "zero-scorable-positives + own-type matching "
                      "negative regression tests added "
                      "(TEST_ADDED)",
            },
            "amendment_file_hashes": {
                rel: b.sha256_file(REPO / rel)
                for rel in amendment_files if (REPO / rel).exists()},
            "tests_hash": {
                "tests/test_eval_interest_semantic.py":
                    b.sha256_file(REPO / "tests/"
                                  "test_eval_interest_semantic.py"),
                "tests/test_isem_d3_binding.py":
                    b.sha256_file(REPO / "tests/"
                                  "test_isem_d3_binding.py"),
            },
        },
        "review": {
            "review_performed": "NO — FRESH REVIEW REQUIRED",
            "next_step": "ARCHITECT PENDING",
        },
    }
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freeze", default=str(FREEZE_DOC))
    ap.add_argument("--emit", default=None,
                    help=f"write the binding manifest (default: "
                         f"{DEFAULT_OUT.name} under docs/handoffs/) "
                         f"when --emit is passed without a value")
    ap.add_argument("--out", dest="out_path", default=None)
    a = ap.parse_args(argv)
    try:
        manifest = build_binding_manifest(Path(a.freeze))
    except b.BindingRefusal as exc:
        print(f"BINDING REFUSED: {exc.code}: {exc.detail}",
              file=sys.stderr)
        return 2
    dst = DEFAULT_OUT
    if a.out_path:
        dst = Path(a.out_path)
    if a.emit is not None or a.out_path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(manifest, indent=1,
                                  ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(json.dumps({
            "wrote": str(dst),
            "sha256": b.sha256_file(dst),
            "status": manifest["status"],
            "binding_identity_sha256":
                manifest["binding_identity_sha256"],
            "all_byte_exact": all(c["byte_exact"]
                                  for c in manifest["contestants"]),
        }, indent=1))
    else:
        print(json.dumps({
            "status": manifest["status"],
            "binding_identity_sha256":
                manifest["binding_identity_sha256"],
            "all_byte_exact": all(c["byte_exact"]
                                  for c in manifest["contestants"]),
            "serialization_recipe":
                manifest["reconstruction"]["serialization_recipe"],
        }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
