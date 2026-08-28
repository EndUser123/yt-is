"""Formal bound-three sealed execution runner (AMENDMENT_3/4).

The ONLY surface allowed to score the sealed v1.1 holdout.

AMENDMENT_4 one-way-door order (U13) — the holdout is never touched
before the preflight receipt exists:

    verify exact evaluator (freeze-receipt drift guard)
    -> verify binding manifest identity + contestant set
    -> verify materialization manifest + all three contestant hashes
    -> verify D3 implementation identity against the freeze document
    -> verify judge sandbox/canary (live synthetic probe)
    -> verify judge model/config against the frozen receipt
    -> verify cache identity / resume state
    -> verify durable PRIVATE output destination (unique run_id)
    -> verify support prerequisites that need NO labels (inventory)
    -> emit PRE_UNSEAL_PREFLIGHT_PASS transaction manifest (hashed)
    -> identify/verify sealed holdout digest (byte hash, no parsing)
    -> ONLY THEN parse holdout content
    -> build ONE support/scorability artifact
    -> score all three contestants
    -> mechanical aggregate (binds the preflight manifest hash)

Any preflight failure exits with HOLDOUT_CONTENT_PARSED = NO.

Modes:
  --verify       label-free verification only (evaluator + binding +
                 materialization), no judge, no output root
  --probe-judge  synthetic-canary judge isolation probe only
  --run          full formal run; requires --out-root inside the
                 canonical durable PRIVATE hierarchy (default policy:
                 P:/.data/yt-is/private/...) and --gt hashing to the
                 sealed holdout digest
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import eval_interest_semantic as isem  # noqa: E402
from ef import isem_d3_binding as bind  # noqa: E402
from ef import sealed_execution as seal  # noqa: E402

DEFAULT_BINDING = REPO / ("docs/handoffs/interest-intelligence/"
                          "isem-d3-pre-unseal-binding.json")
DEFAULT_MATERIALIZATION = REPO / (
    "docs/handoffs/interest-intelligence/"
    "isem-d3-contestant-materialization.json")
RECEIPT = REPO / ("docs/handoffs/interest-intelligence/"
                  "interest-semantic-evaluator-v1/FREEZE_RECEIPT.json")
FREEZE_DOC = REPO / ("docs/handoffs/interest-intelligence/"
                     "inference-candidate-d3-freeze.json")


def _identity(binding: dict, evaluator: dict, cache_sha,
              evaluator_commit=None, preflight_sha=None):
    return {
        "evaluator_commit": evaluator_commit,
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
        "preflight_manifest_sha256": preflight_sha,
    }


def preflight(binding_path, materialization_path, judge_cache_path,
              out_root, evaluator_commit=None, run_probe=True,
              receipt_path=None) -> dict:
    """Label-free preflight; emits the PRE-UNSEAL transaction manifest.

    The holdout path is NEVER opened here — not even hashed. Raises
    SealedRunError on any failure (HOLDOUT_CONTENT_PARSED stays NO).
    """
    receipt_path = Path(receipt_path or RECEIPT)
    evaluator = seal.verify_evaluator_candidate(receipt_path)   # 1-2
    binding = seal.load_binding_manifest(binding_path)          # 3
    materialization = seal.load_materialization_manifest(       # 4
        materialization_path)
    seal.verify_materialization(materialization, binding)
    for entry in materialization["contestants"]:                # 5
        payload = seal.read_materialized_payload(entry)
        if not isinstance(payload.get("inferred_interests"), list):
            raise seal.SealedRunError(
                "CONTESTANT_BYTES_SHAPE",
                f"{entry['run_id']}: not an inference payload")
    freeze = bind.load_freeze(FREEZE_DOC)                       # 6
    bind.verify_implementation_manifest(REPO, freeze)

    probe = isem.judge_isolation_probe() if run_probe else {    # 7
        "verdict": "PASS", "skipped_live_probe": True}
    if probe.get("verdict") != "PASS":
        raise seal.SealedRunError(
            "JUDGE_SANDBOX_BLOCKED",
            "judge isolation probe did not pass; do not unseal")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frozen_cfg = receipt["judge_model_config"]                  # 8
    live_cfg = {"model": isem.JUDGE_MODEL,
                "reasoning_effort": isem.JUDGE_REASONING_EFFORT,
                "timeout_s": isem.JUDGE_TIMEOUT_S,
                "max_attempts": isem.JUDGE_MAX_ATTEMPTS}
    if frozen_cfg != live_cfg:
        raise seal.SealedRunError(
            "JUDGE_CONFIG_DRIFT",
            f"frozen {frozen_cfg} != live {live_cfg}")
    cache_sha = None                                            # 9
    if judge_cache_path:
        p = Path(judge_cache_path)
        if p.exists():
            isem.load_judge_cache(p)  # identity-checked load
            cache_sha = isem.sha256_file(p)
    resolved_root = seal.validate_output_root(out_root)         # 10
    run_id = f"sealed-{time.strftime('%Y%m%dT%H%M%S')}-" \
             f"{uuid.uuid4().hex[:8]}"
    run_dir = resolved_root / run_id
    if run_dir.exists():
        raise seal.SealedRunError(
            "OUTPUT_ROOT_COLLISION",
            f"run directory {run_dir} already exists "
            "(non-resumable collision)")
    from ef.evidence_clusters import cached_clusters            # 11
    clusters, _coverage = cached_clusters()
    if not clusters:
        raise seal.SealedRunError(
            "SUPPORT_PRECONDITION",
            "evidence-cluster inventory unavailable or empty")

    manifest = {                                                # receipt
        "document_kind": "ISEM_PRE_UNSEAL_PREFLIGHT_PASS",
        "run_id": run_id,
        "created_utc": isem.time.strftime("%Y-%m-%dT%H:%M:%S"),
        "holdout_content_parsed": False,
        "evaluator": {
            "commit": evaluator_commit,
            "freeze_receipt_sha256":
                seal.sha256_bytes(receipt_path.read_bytes()),
            "frozen_artifacts": evaluator["frozen_artifacts"],
        },
        "binding": {
            "status": binding["status"],
            "binding_identity_sha256":
                binding["binding_identity_sha256"],
        },
        "contestant_manifest": {
            "sha256": isem.sha256_file(materialization_path),
            "entries": [
                {"run_id": e["run_id"],
                 "payload_sha256": e["payload_sha256"],
                 "byte_length": e["byte_length"],
                 "storage_path": e["storage_path"]}
                for e in materialization["contestants"]],
        },
        "d3_implementation_manifest_sha256":
            binding["inference_freeze"][
                "implementation_manifest_sha256"],
        "judge_configuration": {
            "frozen": frozen_cfg,
            "live": live_cfg,
            "sandbox": isem.JUDGE_SANDBOX_CONFIG,
            "probe_receipt": probe,
        },
        "judge_cache": {"path": str(judge_cache_path)
                        if judge_cache_path else None,
                        "sha256": cache_sha},
        "output_root": {
            "policy": seal.SEALED_OUTPUT_POLICY,
            "resolved_root": str(resolved_root),
            "run_dir": str(run_dir),
        },
        "expected_sealed_holdout_sha256": isem.SEALED_GT_SHA256,
        "support_preconditions": {
            "cluster_inventory_sha256": seal.sha256_bytes(
                seal.canonical_bytes(
                    [{k: c.get(k) for k in ("cluster_id", "label",
                                            "terms", "entities",
                                            "representative")}
                     for c in clusters])),
            "eligible_cluster_count": len(clusters),
            "labels_read": False,
        },
    }
    manifest["preflight_manifest_sha256"] = seal.sha256_bytes(
        seal.canonical_bytes(
            {k: v for k, v in manifest.items()
             if k != "preflight_manifest_sha256"}))
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "PRE_UNSEAL_PREFLIGHT_PASS.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def run_sealed(gt_path, binding_path, materialization_path,
               support_path, judge_cache_path, out_root,
               evaluator_commit=None, receipt_path=None,
               run_probe=True) -> dict:
    # ---------------- PREFLIGHT (holdout untouched) ----------------
    manifest = preflight(binding_path, materialization_path,
                         judge_cache_path, out_root,
                         evaluator_commit=evaluator_commit,
                         run_probe=run_probe, receipt_path=receipt_path)
    run_dir = Path(manifest["output_root"]["run_dir"])
    preflight_sha = manifest["preflight_manifest_sha256"]

    # ------- sealed gate: digest identification, no semantic parse ----
    if isem.sha256_file(Path(gt_path)) != \
            isem.SEALED_GT_SHA256:
        raise seal.SealedRunError(
            "GT_NOT_SEALED_HOLDOUT",
            "the formal runner scores ONLY the sealed v1.1 holdout")
    # ------------- ONLY NOW may holdout content be parsed -------------
    gt_doc = isem.load_ground_truth(gt_path)
    if gt_doc["sealed_sha256"] != isem.SEALED_GT_SHA256:
        raise seal.SealedRunError(
            "GT_NOT_SEALED_HOLDOUT", "post-parse digest mismatch")

    binding_path_p = Path(binding_path)
    binding = seal.load_binding_manifest(binding_path_p)
    materialization = seal.load_materialization_manifest(
        materialization_path)
    evaluator = seal.verify_evaluator_candidate(
        receipt_path or RECEIPT)
    cache_sha = manifest["judge_cache"]["sha256"]

    # single holdout contact: support artifact built ONCE, reused x3
    if support_path and Path(support_path).exists():
        support, support_sha = seal.load_support_artifact(
            support_path, gt_doc["sealed_sha256"])
    else:
        from ef.evidence_clusters import cached_clusters
        clusters, _coverage = cached_clusters()
        support = seal.build_support_artifact(gt_doc, clusters)
        support_sha = seal.sha256_bytes(seal.canonical_bytes(support))
        if support_path:
            Path(support_path).parent.mkdir(parents=True,
                                            exist_ok=True)
            Path(support_path).write_text(
                json.dumps(support, indent=1), encoding="utf-8")

    identity = _identity(binding, evaluator, cache_sha,
                         evaluator_commit, preflight_sha)
    judge = isem.judge_transport_factory(cache_path=judge_cache_path)

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
            receipt_doc = seal.incomplete_report(
                per_identity, exc, [], cache_sha)
            out = run_dir / f"sealed-incomplete-{entry['run_id']}.json"
            out.write_text(json.dumps(receipt_doc, indent=1),
                           encoding="utf-8")
            return {"status": seal.SEALED_EVALUATION_INCOMPLETE,
                    "run_id": manifest["run_id"],
                    "incomplete_report": str(out),
                    "preflight_manifest_sha256": preflight_sha,
                    "unresolved_prompt_hashes":
                        receipt_doc["unresolved_prompt_hashes"]}
        out = run_dir / f"sealed-report-{entry['run_id']}.json"
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        reports.append(report)

    aggregate = seal.aggregate_reports(reports, binding,
                                       preflight_manifest_sha256=
                                       preflight_sha)
    out = run_dir / "sealed-aggregate.json"
    out.write_text(json.dumps(aggregate, indent=1), encoding="utf-8")
    files_manifest = {
        "document_kind": "ISEM_SEALED_RUN_RECEIPT",
        "run_id": manifest["run_id"],
        "preflight_manifest_sha256": preflight_sha,
        "aggregate_sha256": aggregate["aggregate_sha256"],
        "files": [{"path": str(p), "sha256": isem.sha256_file(p)}
                  for p in sorted(run_dir.iterdir()) if p.is_file()],
    }
    (run_dir / "reports-manifest.json").write_text(
        json.dumps(files_manifest, indent=1) + "\n", encoding="utf-8")
    return {"status": "AGGREGATED",
            "run_id": manifest["run_id"],
            "run_dir": str(run_dir),
            "aggregate": str(out),
            "preflight_manifest_sha256": preflight_sha,
            "repeatable_perfect": aggregate["repeatable_perfect"],
            "interest_finite_set_status":
                aggregate["interest_finite_set_status"]}


def run_verify(binding_path, materialization_path,
               receipt_path=None) -> dict:
    evaluator = seal.verify_evaluator_candidate(receipt_path or RECEIPT)
    binding = seal.load_binding_manifest(binding_path)
    materialization = seal.load_materialization_manifest(
        materialization_path)
    seal.verify_materialization(materialization, binding)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--probe-judge", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--gt", default=None)
    ap.add_argument("--binding", default=str(DEFAULT_BINDING))
    ap.add_argument("--materialization",
                    default=str(DEFAULT_MATERIALIZATION))
    ap.add_argument("--receipt", default=None)
    ap.add_argument("--support-artifact", default=None)
    ap.add_argument("--judge-cache", default=None)
    ap.add_argument("--out-root", default=None,
                    help="REQUIRED for --run: durable PRIVATE "
                         "evaluation root (default policy: under "
                         "P:/.data/yt-is/private/); P:/tmp, "
                         "session-scoped and checkout paths rejected")
    ap.add_argument("--evaluator-commit", default=None)
    ap.add_argument("--no-live-probe", dest="live_probe",
                    action="store_false", default=True,
                    help="skip the live judge canary probe "
                         "(synthetic/offline contexts only)")
    a = ap.parse_args(argv)
    try:
        if a.verify:
            print(json.dumps(run_verify(a.binding, a.materialization,
                                        a.receipt), indent=1))
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
            if not a.out_root:
                print("--run requires an explicit --out-root inside "
                      "the durable PRIVATE evaluation hierarchy; "
                      "P:/tmp and session-scoped roots are rejected",
                      file=sys.stderr)
                return 2
            result = run_sealed(
                a.gt, a.binding, a.materialization, a.support_artifact,
                a.judge_cache, a.out_root, a.evaluator_commit,
                receipt_path=a.receipt, run_probe=a.live_probe)
            print(json.dumps(result, indent=1))
            return (4 if result.get("status") ==
                    seal.SEALED_EVALUATION_INCOMPLETE else 0)
        ap.error("one of --verify / --probe-judge / --run is required")
    except seal.SealedRunError as exc:
        print(f"SEALED RUN REFUSED: {exc.code}: {exc.detail}",
              file=sys.stderr)
        return 3
    except isem.JudgeCacheIdentityError as exc:
        print(f"SEALED RUN REFUSED: JUDGE_CACHE_IDENTITY: {exc}",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
