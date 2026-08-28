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
    "amendment_2_commit": "0a5d7b73 (REJECTED by fresh pre-unseal "
                          "review; rejection ACCEPTED; history kept "
                          "immutable)",
    "amendment": "AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_CONSTRUCT_"
                 "HARDENING (this candidate branch head)",
}
MATERIALIZATION_MIRROR = REPO / (
    "docs/handoffs/interest-intelligence/"
    "isem-d3-contestant-materialization.json")
AMENDMENT_3_DOC = REPO / (
    "docs/handoffs/interest-intelligence/"
    "interest-semantic-evaluator-v1/"
    "AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_CONSTRUCT_HARDENING.md")
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
        "ef/sealed_execution.py",
        "scripts/bind_isem_d3.py",
        "scripts/run_sealed_isem_d3.py",
        "scripts/materialize_d3_contestants.py",
        "docs/handoffs/interest-intelligence/"
        "isem-d3-contestant-materialization.json",
        "docs/handoffs/interest-intelligence/"
        "interest-semantic-evaluator-v1/"
        "AMENDMENT_2_D3_BINDING.md",
        "docs/handoffs/interest-intelligence/"
        "interest-semantic-evaluator-v1/"
        "AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_CONSTRUCT_HARDENING.md",
    ]
    # live judge-sandbox isolation probe at emit time (synthetic
    # canaries only, no labels); a failing probe refuses the emit
    probe = isem.judge_isolation_probe()
    if probe["verdict"] != "PASS":
        raise b.BindingRefusal(
            "JUDGE_SANDBOX_BLOCKED",
            "judge isolation probe did not pass; do not unseal")
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
                "tests/test_sealed_execution.py":
                    b.sha256_file(REPO / "tests/"
                                  "test_sealed_execution.py"),
            },
        },
        "amendment_3": {
            "name": "AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_CONSTRUCT_"
                    "HARDENING",
            "review_blocker_F_R1": {
                "finding": "generic score accepted arbitrary --result "
                           "without binding a scored report to the "
                           "three contestants",
                "fix": "generic score REFUSES the sealed holdout "
                       "digest outright; the ONLY sealed surface is "
                       "scripts/run_sealed_isem_d3.py (verify -> "
                       "materialize -> score x3 -> mechanical "
                       "aggregate); every report carries a full "
                       "sealed_execution_identity block",
                "status": "FIXED",
            },
            "match_policy_amendment": {
                "ladder": isem.MATCH_LADDER,
                "amendment": isem.MATCH_POLICY_AMENDMENT,
                "removed": ["substring auto-match",
                            "significant-token-subset auto-match",
                            "context-produced auto-match"],
                "recorded": "pre-unseal construct-validity amendment, "
                            "labels never inspected",
            },
            "judge_sandbox": {
                "configuration": isem.JUDGE_SANDBOX_CONFIG,
                "isolation_probe": {
                    "method": "synthetic canaries: outside nonce file "
                              "must be unreadable; prompt-embedded "
                              "nonce must be processed",
                    "result": {"verdict": probe["verdict"],
                               "outside_canary_leaked":
                                   probe["outside_canary_leaked"],
                               "judge_can_read_outside_sandbox":
                                   probe["judge_can_read_outside_"
                                         "sandbox"],
                               "prompt_canary_processed":
                                   probe["prompt_canary_processed"]},
                    "labels_touched": "NONE",
                },
                "blocked_rule": "if the probe ever fails: "
                                "JUDGE_SANDBOX_BLOCKED, do not unseal",
            },
            "transport_failure_semantics": {
                "rule": "judge transport failure raises "
                        "JudgeUnavailable -> EVALUATION_INCOMPLETE; "
                        "never a semantic no_match",
                "resume": "unresolved prompt hashes ONLY; same "
                          "evaluator, same prompt, same model/config",
                "cache": "write-once, keyed by exact rendered prompt, "
                         "header pins model+effort, refuses resume "
                         "under other identity",
                "final_gate": "never issued with unresolved required "
                              "judgments",
            },
            "contestant_materialization": {
                "store_root":
                    "P:/.data/yt-is/ef/interest-inference/"
                    "frozen-contestants/isem-d3-v1",
                "manifest_mirror":
                    "docs/handoffs/interest-intelligence/"
                    "isem-d3-contestant-materialization.json",
                "manifest_sha256": b.sha256_file(MATERIALIZATION_MIRROR)
                if MATERIALIZATION_MIRROR.exists() else None,
                "policy": "materialized pre-unseal; scoring re-hashes "
                          "bytes immediately before use; contestants "
                          "never regenerated after unseal",
            },
            "formal_execution": {
                "runner": "scripts/run_sealed_isem_d3.py",
                "runner_sha256": b.sha256_file(
                    REPO / "scripts" / "run_sealed_isem_d3.py"),
                "aggregate_implementation": "ef/sealed_execution.py "
                                            "(aggregate_reports)",
                "aggregate_sha256": b.sha256_file(
                    REPO / "ef" / "sealed_execution.py"),
                "aggregate_rule": "REPEATABLE_PERFECT = YES iff all "
                                  "three Interest finite-set == "
                                  "PERFECT; non-PERFECT -> NO; "
                                  "missing/invalid identity -> "
                                  "INCOMPLETE with no final gate",
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
