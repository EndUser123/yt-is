"""Formal bound-three sealed execution runner (AMENDMENT_3).

The ONLY surface allowed to score the sealed v1.1 holdout. Pipeline:

    verify evaluator candidate -> verify binding manifest
    -> verify materialization -> [per contestant: re-hash materialized
    bytes -> score] -> mechanical aggregate -> REPEATABLE_PERFECT

Modes:
  --verify       verify evaluator + binding + materialization only
                 (no GT, no judge, no labels)
  --probe-judge  run the synthetic-canary judge isolation probe
  --run          full formal run; --gt MUST hash to the sealed holdout
                 (anything else is refused: this surface is sealed-only)

Generic `score` refuses the sealed digest; this runner refuses any
non-sealed digest. The support artifact is produced ONCE per run (or
reused after verifying its recorded GT identity) and reused for all
three contestants. A judge transport failure yields an
EVALUATION_INCOMPLETE receipt (exit 4) with no final gate; resume
re-uses the write-once judge cache (unresolved prompt hashes only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import eval_interest_semantic as isem  # noqa: E402
from ef import sealed_execution as seal  # noqa: E402

DEFAULT_BINDING = REPO / ("docs/handoffs/interest-intelligence/"
                          "isem-d3-pre-unseal-binding.json")
DEFAULT_MATERIALIZATION = REPO / (
    "docs/handoffs/interest-intelligence/"
    "isem-d3-contestant-materialization.json")
RECEIPT = REPO / ("docs/handoffs/interest-intelligence/"
                  "interest-semantic-evaluator-v1/FREEZE_RECEIPT.json")


def _identity(binding: dict, evaluator: dict, cache_sha, commit=None):
    return {
        "evaluator_commit": commit,
        "evaluator_frozen_artifacts": evaluator["frozen_artifacts"],
        "binding_manifest_identity": {
            "status": binding["status"],
            "binding_identity_sha256":
                binding["binding_identity_sha256"],
        },
        "contestant_run_id": None,      # set per contestant
        "contestant_payload_sha256": None,
        "d3_freeze_commit":
            binding["inference_freeze"]["freeze_commit"],
        "d3_implementation_manifest_sha256":
            binding["inference_freeze"][
                "implementation_manifest_sha256"],
        "judge_prompts_sha256": evaluator["judge_prompts_sha256"],
        "judge_model_config": evaluator["judge_model_config"],
        "judge_cache_sha256": cache_sha,
    }


def run_verify(binding_path, materialization_path) -> dict:
    evaluator = seal.verify_evaluator_candidate(RECEIPT)
    binding = seal.load_binding_manifest(binding_path)
    materialization = seal.load_materialization_manifest(
        materialization_path)
    seal.verify_materialization(materialization, binding)
    # every materialized payload must be present and re-hash exact
    for entry in materialization["contestants"]:
        payload = seal.read_materialized_payload(entry)
        if not isinstance(payload.get("inferred_interests"), list):
            raise seal.SealedRunError(
                "CONTESTANT_BYTES_SHAPE",
                f"{entry['run_id']}: not an inference payload")
    return {"evaluator": {"freeze_receipt_sha256":
                          evaluator["freeze_receipt_sha256"]},
            "binding_identity_sha256":
                binding["binding_identity_sha256"],
            "contestants": [
                {"run_id": e["run_id"],
                 "sha256": e["payload_sha256"],
                 "bytes": e["byte_length"]}
                for e in materialization["contestants"]],
            "verdict": "VERIFIED"}


def run_sealed(gt_path, binding_path, materialization_path,
               support_path, judge_cache_path, out_dir,
               evaluator_commit=None) -> dict:
    gt_doc = isem.load_ground_truth(gt_path)
    if gt_doc["sealed_sha256"] != isem.SEALED_GT_SHA256:
        raise seal.SealedRunError(
            "GT_NOT_SEALED_HOLDOUT",
            "the formal runner scores ONLY the sealed v1.1 holdout; "
            "got " + gt_doc["sealed_sha256"][:16])
    evaluator = seal.verify_evaluator_candidate(RECEIPT)
    binding = seal.load_binding_manifest(binding_path)
    materialization = seal.load_materialization_manifest(
        materialization_path)
    seal.verify_materialization(materialization, binding)

    # single holdout contact: build the support artifact ONCE (or reuse
    # a verified existing one) and reuse it for all three contestants
    if support_path and Path(support_path).exists():
        support, support_sha = seal.load_support_artifact(
            support_path, gt_doc["sealed_sha256"])
    else:
        from ef.evidence_clusters import cached_clusters
        clusters, _coverage = cached_clusters()
        support = seal.build_support_artifact(gt_doc, clusters)
        support_sha = seal.sha256_bytes(seal.canonical_bytes(support))
        if support_path:
            Path(support_path).parent.mkdir(parents=True, exist_ok=True)
            Path(support_path).write_text(
                json.dumps(support, indent=1), encoding="utf-8")

    cache_sha = (isem.sha256_file(judge_cache_path)
                 if judge_cache_path and Path(judge_cache_path).exists()
                 else None)
    identity = _identity(binding, evaluator, cache_sha,
                         evaluator_commit)
    judge = isem.judge_transport_factory(cache_path=judge_cache_path)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for entry in materialization["contestants"]:
        per_identity = dict(identity)
        per_identity["contestant_run_id"] = entry["run_id"]
        per_identity["contestant_payload_sha256"] = \
            entry["payload_sha256"]
        payload = seal.read_materialized_payload(entry)  # re-hash gate
        try:
            report = seal.score_contestant(
                gt_doc, payload, judge, support, support_sha,
                per_identity)
        except isem.JudgeUnavailable as exc:
            receipt = seal.incomplete_report(
                per_identity, exc, [], cache_sha)
            out = out_dir / f"sealed-incomplete-{entry['run_id']}.json"
            out.write_text(json.dumps(receipt, indent=1),
                           encoding="utf-8")
            return {"status": seal.SEALED_EVALUATION_INCOMPLETE,
                    "incomplete_report": str(out),
                    "unresolved_prompt_hashes":
                        receipt["unresolved_prompt_hashes"]}
        out = out_dir / f"sealed-report-{entry['run_id']}.json"
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        reports.append(report)

    aggregate = seal.aggregate_reports(reports, binding)
    out = out_dir / "sealed-aggregate.json"
    out.write_text(json.dumps(aggregate, indent=1), encoding="utf-8")
    return {"status": "AGGREGATED",
            "aggregate": str(out),
            "repeatable_perfect": aggregate["repeatable_perfect"],
            "interest_finite_set_status":
                aggregate["interest_finite_set_status"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--probe-judge", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--gt", default=None)
    ap.add_argument("--binding", default=str(DEFAULT_BINDING))
    ap.add_argument("--materialization",
                    default=str(DEFAULT_MATERIALIZATION))
    ap.add_argument("--support-artifact", default=None)
    ap.add_argument("--judge-cache", default=None)
    ap.add_argument("--out-dir", default="P:/tmp/isem-sealed-run")
    ap.add_argument("--evaluator-commit", default=None)
    a = ap.parse_args(argv)
    try:
        if a.verify:
            print(json.dumps(run_verify(a.binding, a.materialization),
                             indent=1))
            return 0
        if a.probe_judge:
            probe = isem.judge_isolation_probe()
            print(json.dumps(probe, indent=1))
            return 0 if probe["verdict"] == "PASS" else 5
        if a.run:
            if not a.gt:
                print("--run requires --gt (the sealed holdout path)",
                      file=sys.stderr)
                return 2
            result = run_sealed(
                a.gt, a.binding, a.materialization, a.support_artifact,
                a.judge_cache, a.out_dir, a.evaluator_commit)
            print(json.dumps(result, indent=1))
            return (4 if result.get("status") ==
                    seal.SEALED_EVALUATION_INCOMPLETE else 0)
        ap.error("one of --verify / --probe-judge / --run is required")
    except seal.SealedRunError as exc:
        print(f"SEALED RUN REFUSED: {exc.code}: {exc.detail}",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
