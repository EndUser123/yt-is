"""Formal bound-three sealed execution for ISEM x D3 (AMENDMENT_3).

The ONLY execution surface permitted to score the sealed v1.1 holdout.
Generic `score` refuses the sealed digest outright; this module runs
exactly:

    verify evaluator candidate  ->  verify binding manifest
    ->  verify/materialize contestant #1  ->  score #1
    ->  ... #2 ...  ->  ... #3 ...
    ->  mechanical aggregate  ->  REPEATABLE_PERFECT

Properties pinned here (see AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_
CONSTRUCT_HARDENING):

  - every scored report carries a complete sealed_execution_identity
    block (evaluator hashes, binding identity, contestant logical id +
    payload sha256, D3 manifest sha256, holdout sha256, support
    artifact sha256, judge prompt/model/config/cache identities,
    run id); a report without contestant identity cannot exist on this
    path because the stamp is built inside score_contestant;
  - contestant bytes are re-hashed immediately before use;
  - the support/scorability artifact is produced ONCE, hashed, and
    reused for all three contestants (single holdout contact);
  - the aggregator is code, not prose: REPEATABLE_PERFECT = YES iff
    all three Interest finite-set statuses are PERFECT, NO on any
    non-PERFECT, INCOMPLETE (no final gate) on any missing report or
    identity/hash failure. No majority vote, no best-run, no averaging,
    no omitted run, no duplicate substitution.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ef import eval_interest_semantic as isem
from ef import isem_d3_binding as bind

SEALED_EVALUATION_INCOMPLETE = "EVALUATION_INCOMPLETE"
REPEATABLE_PERFECT = "REPEATABLE_PERFECT"
INTEREST_FAMILY_TRACKS = ("Interest", "Goal", "InformationNeed",
                          "Question")


class SealedRunError(Exception):
    """Fail-closed sealed-execution refusal."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Binding / materialization manifests
# ---------------------------------------------------------------------------

def load_binding_manifest(path) -> dict:
    b = json.loads(Path(path).read_text(encoding="utf-8"))
    if b.get("document_kind") != "ISEM_D3_PRE_UNSEAL_BINDING":
        raise SealedRunError(
            "BINDING_MANIFEST_UNRECOGNIZED",
            f"document_kind={b.get('document_kind')!r}")
    if b.get("status") != bind.BINDING_STATUS:
        raise SealedRunError(
            "BINDING_MANIFEST_STATUS",
            f"status={b.get('status')!r} != {bind.BINDING_STATUS}")
    contestants = b.get("contestants") or []
    ids = sorted(c.get("run_id") for c in contestants)
    if ids != sorted(bind.BOUND_CONTESTANT_IDS):
        raise SealedRunError(
            "BINDING_MANIFEST_CONTESTANT_SET",
            f"contestants {ids} != the three bound contestants")
    identity = bind.binding_identity([
        {"run_id": c["run_id"],
         "expected_sha256": c["expected_sha256"],
         "reconstructed_sha256": c["reconstructed_sha256"]}
        for c in contestants])
    if identity != b.get("binding_identity_sha256"):
        raise SealedRunError(
            "BINDING_MANIFEST_IDENTITY",
            "recomputed binding identity differs from the stored one")
    return b


def verify_evaluator_candidate(receipt_path) -> dict:
    """Verify the frozen evaluator artifacts against the freeze receipt.

    Drift here refuses: the sealed run may only execute the exact
    published evaluator candidate.
    """
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    problems = []
    for entry in receipt.get("frozen_artifacts", []):
        p = Path(entry["path"])
        if not p.exists():
            problems.append(f"missing frozen artifact {entry['path']}")
            continue
        digest = isem.sha256_file(p)
        if digest != entry["sha256"]:
            problems.append(
                f"evaluator drift {entry['path']}: {digest[:12]} != "
                f"{entry['sha256'][:12]}")
    if problems:
        raise SealedRunError("EVALUATOR_CANDIDATE_DRIFT",
                             "; ".join(problems))
    return {"freeze_receipt_sha256": isem.sha256_file(receipt_path),
            "frozen_artifacts": receipt["frozen_artifacts"],
            "judge_prompts_sha256": receipt["judge_prompts_sha256"],
            "judge_model_config": receipt["judge_model_config"]}


def load_materialization_manifest(path) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    if m.get("document_kind") != "ISEM_D3_CONTESTANT_MATERIALIZATION":
        raise SealedRunError(
            "MATERIALIZATION_MANIFEST_UNRECOGNIZED",
            f"document_kind={m.get('document_kind')!r}")
    entries = m.get("contestants") or []
    ids = sorted(e.get("run_id") for e in entries)
    if ids != sorted(bind.BOUND_CONTESTANT_IDS):
        raise SealedRunError(
            "MATERIALIZATION_CONTESTANT_SET",
            f"contestants {ids} != the three bound contestants")
    return m


def verify_materialization(materialization: dict, binding: dict) -> None:
    by_id_b = {c["run_id"]: c for c in binding["contestants"]}
    for e in materialization["contestants"]:
        b = by_id_b[e["run_id"]]
        if e["payload_sha256"] != b["expected_sha256"]:
            raise SealedRunError(
                "MATERIALIZATION_IDENTITY",
                f"{e['run_id']}: materialized sha != bound sha")
        if e["implementation_manifest_sha256"] != \
                binding["inference_freeze"][
                    "implementation_manifest_sha256"]:
            raise SealedRunError(
                "MATERIALIZATION_IDENTITY",
                f"{e['run_id']}: implementation manifest mismatch")
        if e["d3_freeze_commit"] != \
                binding["inference_freeze"]["freeze_commit"]:
            raise SealedRunError(
                "MATERIALIZATION_IDENTITY",
                f"{e['run_id']}: freeze commit mismatch")
        if e["strict_validator_status"] != "PASSED":
            raise SealedRunError(
                "MATERIALIZATION_VALIDATOR",
                f"{e['run_id']}: strict validator not PASSED")


def read_materialized_payload(entry: dict) -> dict:
    """Read contestant bytes and REHASH before any use."""
    p = Path(entry["storage_path"])
    if not p.exists():
        raise SealedRunError(
            "CONTESTANT_BYTES_MISSING",
            f"{entry['run_id']}: {p} missing")
    raw = p.read_bytes()
    digest = sha256_bytes(raw)
    if digest != entry["payload_sha256"]:
        raise SealedRunError(
            "CONTESTANT_BYTES_MISMATCH",
            f"{entry['run_id']}: {digest} != bound "
            f"{entry['payload_sha256']} — refusing substitute payload")
    if entry.get("byte_length") is not None and \
            len(raw) != entry["byte_length"]:
        raise SealedRunError(
            "CONTESTANT_BYTES_MISMATCH",
            f"{entry['run_id']}: byte length {len(raw)} != "
            f"{entry['byte_length']}")
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Support / scorability artifact (single holdout contact)
# ---------------------------------------------------------------------------

def build_support_artifact(gt_doc: dict, clusters: list[dict]) -> dict:
    """Produce the scorability support artifact ONCE from the GT."""
    cluster_texts = {}
    for c in clusters:
        reps = " ".join(r.get("title", "") or ""
                        for r in (c.get("representative") or []))
        terms = " ".join(c.get("terms") or [])
        ents = " ".join(e.get("entity", "") or ""
                        for e in (c.get("entities") or [])[:8])
        label = c.get("label") or ""
        cluster_texts[c["cluster_id"]] = f"{label} {terms} {ents} {reps}"
    support = {}
    for lab in gt_doc["labels"]:
        support[lab["label_id"]] = isem.needle_support(
            lab, cluster_texts)[:50]
    return {
        "artifact_kind": "ISEM_SUPPORT_V1",
        "holdout_sha256": gt_doc["sealed_sha256"],
        "cluster_inventory_sha256": sha256_bytes(canonical_bytes(
            [{k: c.get(k) for k in ("cluster_id", "label", "terms",
                                    "entities", "representative")}
             for c in clusters])),
        "eligible_cluster_ids": sorted(
            c["cluster_id"] for c in clusters),
        "support_by_label_id": support,
    }


def load_support_artifact(path, expected_gt_sha: str) -> tuple[dict, str]:
    """Reuse an existing support artifact; verify it belongs to this GT."""
    p = Path(path)
    artifact = json.loads(p.read_text(encoding="utf-8"))
    if artifact.get("artifact_kind") != "ISEM_SUPPORT_V1":
        raise SealedRunError(
            "SUPPORT_ARTIFACT_UNRECOGNIZED", str(p))
    if artifact.get("holdout_sha256") != expected_gt_sha:
        raise SealedRunError(
            "SUPPORT_ARTIFACT_IDENTITY",
            "support artifact was produced against a different GT")
    return artifact, isem.sha256_file(p)


# ---------------------------------------------------------------------------
# Score + identity
# ---------------------------------------------------------------------------

def score_contestant(gt_doc: dict, payload: dict, judge,
                     support_artifact: dict, support_artifact_sha: str,
                     identity: dict, stability_results=False) -> dict:
    """Score one contestant and stamp the full sealed identity block."""
    report = isem.evaluate(
        gt_doc, payload, judge,
        eligible_cluster_ids=set(support_artifact[
            "eligible_cluster_ids"]),
        support_hits_by_label=support_artifact["support_by_label_id"],
        stability_results=stability_results)
    report["sealed_execution_identity"] = {
        **identity,
        "holdout_sha256": gt_doc["sealed_sha256"],
        "support_artifact_sha256": support_artifact_sha,
        "cluster_inventory_sha256":
            support_artifact["cluster_inventory_sha256"],
        "judge_prompts_sha256": identity["judge_prompts_sha256"],
        "judge_model_config": identity["judge_model_config"],
        "run_id": identity["contestant_run_id"],
        "generated_utc": isem.time.strftime("%Y-%m-%dT%H:%M:%S"),
        "match_policy": {"ladder": isem.MATCH_LADDER,
                         "amendment": isem.MATCH_POLICY_AMENDMENT},
    }
    return report


def incomplete_report(identity: dict, judge_unavailable, judge_calls,
                      cache_sha: str | None) -> dict:
    """Fail-closed receipt when required judgments are unresolved."""
    unresolved = []
    if judge_unavailable is not None:
        unresolved.append(judge_unavailable.prompt_hash)
    unresolved += [c["pair_hash"] for c in judge_calls
                   if c.get("result") == "error"]
    return {
        "status": SEALED_EVALUATION_INCOMPLETE,
        "sealed_execution_identity": identity,
        "unresolved_prompt_hashes": sorted(set(unresolved)),
        "judge_cache_sha256": cache_sha,
        "resume_rule": "resume ONLY unresolved prompt hashes; same "
                       "evaluator, same prompt, same model/config; "
                       "completed decisions are immutable (write-once "
                       "cache); no final gate is issued with "
                       "unresolved required judgments",
    }


# ---------------------------------------------------------------------------
# Mechanical aggregate
# ---------------------------------------------------------------------------

def aggregate_reports(reports: list[dict], binding: dict) -> dict:
    """REPEATABLE_PERFECT as code.

    YES  iff Interest finite-set == PERFECT on shadow_1 AND shadow_2
         AND shadow_3.
    NO   on any non-PERFECT (IMPERFECT or NOT_EVALUABLE).
    INCOMPLETE (final_gate=None) on any missing report, duplicate
         contestant, or identity/hash failure.
    """
    expected = {c["run_id"]: c["expected_sha256"]
                for c in binding["contestants"]}
    identity = binding.get("binding_identity_sha256")
    per_contestant = {}
    reasons = []
    seen = {}
    report_hashes = {}
    for rep in reports:
        st = rep.get("sealed_execution_identity") or {}
        rid = st.get("contestant_run_id")
        if rid is None:
            reasons.append("report without contestant identity")
            continue
        if rid in seen:
            reasons.append(
                f"duplicate report for {rid} (substitution refused)")
            continue
        seen[rid] = True
        if rid not in expected:
            reasons.append(f"unknown contestant {rid}")
            continue
        if st.get("contestant_payload_sha256") != expected[rid]:
            reasons.append(
                f"{rid}: report payload sha != bound sha")
        if identity is not None:
            rep_binding_id = st.get(
                "binding_identity_sha256") or (
                st.get("binding_manifest_identity") or {}).get(
                    "binding_identity_sha256")
            if rep_binding_id != identity:
                reasons.append(
                    f"{rid}: report binding identity mismatch")
        report_hashes[rid] = sha256_bytes(canonical_bytes(rep))
        if rep.get("status") == SEALED_EVALUATION_INCOMPLETE:
            reasons.append(f"{rid}: {SEALED_EVALUATION_INCOMPLETE}")
            continue
        fs = rep.get("finite_set_conformance") or {}
        per_contestant[rid] = {
            track: (fs.get(track) or {}).get("status")
            for track in INTEREST_FAMILY_TRACKS}
    for rid in sorted(expected):
        if rid not in seen:
            reasons.append(f"missing report for {rid}")

    incomplete = bool(reasons)
    interest_statuses = {rid: per_contestant.get(rid, {}).get("Interest")
                         for rid in sorted(expected)}
    if incomplete:
        verdict = "INCOMPLETE"
        final_gate = None
    elif all(v == "PERFECT" for v in interest_statuses.values()):
        verdict = "YES"
        final_gate = "REPEATABLE_PERFECT"
    else:
        verdict = "NO"
        final_gate = "REPEATABLE_PERFECT"
    aggregate = {
        "aggregate_kind": "ISEM_D3_REPEATABLE_PERFECT",
        "binding_identity_sha256": identity,
        "rule": "REPEATABLE_PERFECT = YES iff Interest finite-set "
                "conformance is PERFECT on shadow_1 AND shadow_2 AND "
                "shadow_3; any non-PERFECT -> NO; missing/invalid "
                "identity -> INCOMPLETE with no final gate; no "
                "majority vote, no best-run choice, no averaging, no "
                "omitted run, no duplicate substitution",
        "interest_finite_set_status": interest_statuses,
        "family_statuses_per_contestant": per_contestant,
        "report_hashes_sha256": report_hashes,
        "aggregate_binds_report_hashes": sorted(report_hashes.values()),
        "repeatable_perfect": verdict,
        "final_gate": final_gate,
    }
    if incomplete:
        aggregate["incomplete_reasons"] = reasons
    aggregate["aggregate_sha256"] = sha256_bytes(canonical_bytes(
        {k: v for k, v in aggregate.items()}))
    return aggregate
