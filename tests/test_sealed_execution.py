"""Formal bound-three sealed execution tests (AMENDMENT_3, items I.1-12+22).

Fully offline synthetic fixtures: no sealed holdout, no real labels,
no provider calls. Prove that the formal execution surface binds every
scored report to exact contestant bytes and that the aggregator is
mechanical (REPEATABLE_PERFECT as code).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import eval_interest_semantic as isem  # noqa: E402
from ef import isem_d3_binding as bind  # noqa: E402
from ef import sealed_execution as seal  # noqa: E402

D3_FREEZE_COMMIT = "f7bd24fdb917aa5e35112d0b2f2eae1c2129bf59"
IMPL_MANIFEST = "3" * 64


@pytest.fixture()
def sealed_gt_factory(tmp_path, monkeypatch):
    """Write a synthetic GT and point the sealed-hash guard at it.

    Mirrors the fixture in test_eval_interest_semantic.py; each test
    writes a synthetic artifact and repoints SEALED_GT_SHA256 at that
    file's real digest, exercising identical code paths offline.
    """

    def make(doc, name="gt.json"):
        p = tmp_path / name
        p.write_text(json.dumps(doc), encoding="utf-8")
        monkeypatch.setattr(isem, "SEALED_GT_SHA256",
                            isem.sha256_file(p))
        return p

    return make


def gt_label(lid, name, scor="corpus_scorable"):
    return {"label_id": lid, "semantic_class": "Interest",
            "canonical_name": name, "aliases": [],
            "scorability": scor,
            "statement_text": f"statement about {name}",
            "probe_receipts": []}


def synthetic_gt(sealed_gt_factory):
    rows = [gt_label(f"i{k}", f"sealed topic number {k}")
            for k in range(5)]
    return isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))


def payload_for(names, cluster=900):
    return {"inferred_interests": [
        isem_result_interested(n, cluster + i)
        for i, n in enumerate(names)],
        "questions": [], "regret_candidates": []}


def isem_result_interested(name, cid):
    return {"name": name, "kind": "topic", "parent": None,
            "temporal_state": "active", "stance": "curiosity",
            "confidence": 0.9, "observed_vs_inferred": "observed",
            "goal": None, "information_need": None,
            "cluster_ids": [cid], "evidence_summary": "",
            "counterevidence": None, "related_to": []}


class YesJudge:
    live = False

    def __call__(self, *a):
        return True


def make_world(tmp_path, sealed_gt_factory, perfect=True):
    """Three synthetic contestants + binding + materialization."""
    gt = synthetic_gt(sealed_gt_factory)
    clusters = [{"cluster_id": 900 + i, "label": f"c{i}", "terms": [],
                 "entities": [], "representative": []}
                for i in range(5)]
    support = seal.build_support_artifact(gt, clusters)
    support_sha = seal.sha256_bytes(seal.canonical_bytes(support))

    names = [f"sealed topic number {k}" for k in range(5)]
    payloads = {}
    for idx, rid in enumerate(("shadow_1", "shadow_2", "shadow_3")):
        if perfect:
            payloads[rid] = payload_for(names)
        else:
            payloads[rid] = payload_for(
                names[:4] if idx == 2 else names)  # shadow_3 misses one
    mat_dir = tmp_path / "frozen-contestants"
    entries = []
    for rid, payload in payloads.items():
        blob = seal.canonical_bytes(payload)
        digest = seal.sha256_bytes(blob)
        p = mat_dir / f"{digest}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        entries.append({
            "run_id": rid, "payload_sha256": digest,
            "byte_length": len(blob), "storage_path": str(p),
            "d3_freeze_commit": D3_FREEZE_COMMIT,
            "implementation_manifest_sha256": IMPL_MANIFEST,
            "reconstruction_version": bind.RECONSTRUCTION_PROCEDURE,
            "strict_validator_status": "PASSED"})
    binding = {
        "document_kind": "ISEM_D3_PRE_UNSEAL_BINDING",
        "status": bind.BINDING_STATUS,
        "binding_identity_sha256": bind.binding_identity([
            {"run_id": rid, "expected_sha256":
                seal.sha256_bytes(seal.canonical_bytes(payloads[rid])),
             "reconstructed_sha256":
                seal.sha256_bytes(seal.canonical_bytes(payloads[rid]))}
            for rid in ("shadow_1", "shadow_2", "shadow_3")]),
        "contestants": [
            {"run_id": rid,
             "expected_sha256":
                 seal.sha256_bytes(seal.canonical_bytes(payloads[rid])),
             "reconstructed_sha256":
                 seal.sha256_bytes(seal.canonical_bytes(payloads[rid]))}
            for rid in ("shadow_1", "shadow_2", "shadow_3")],
        "inference_freeze": {"freeze_commit": D3_FREEZE_COMMIT,
                             "implementation_manifest_sha256":
                                 IMPL_MANIFEST},
    }
    return {"gt": gt, "support": support, "support_sha": support_sha,
            "binding": binding, "materialization": {
                "document_kind":
                    "ISEM_D3_CONTESTANT_MATERIALIZATION",
                "contestants": entries},
            "payloads": payloads}


def identity_for(binding, run_id, evaluator_sha="e" * 64):
    entry = next(c for c in binding["contestants"]
                 if c["run_id"] == run_id)
    return {
        "evaluator_commit": "synthetic",
        "evaluator_frozen_artifacts": [],
        "binding_manifest_identity": {
            "status": bind.BINDING_STATUS,
            "binding_identity_sha256":
                binding["binding_identity_sha256"]},
        "contestant_run_id": run_id,
        "contestant_payload_sha256": entry["expected_sha256"],
        "d3_freeze_commit": D3_FREEZE_COMMIT,
        "d3_implementation_manifest_sha256": IMPL_MANIFEST,
        "judge_prompts_sha256": {"positive": "p", "negative_interest": "n"},
        "judge_model_config": {"model": isem.JUDGE_MODEL},
        "judge_cache_sha256": None,
        "judge": evaluator_sha,
    }


def score_all(world, evaluator_sha="e" * 64):
    reports = []
    for e in world["materialization"]["contestants"]:
        payload = seal.read_materialized_payload(e)
        reports.append(seal.score_contestant(
            world["gt"], payload, YesJudge(), world["support"],
            world["support_sha"],
            identity_for(world["binding"], e["run_id"], evaluator_sha)))
    return reports


# --- item I.11/I.5: materialized bytes reproduce bound hashes and every
# report carries the exact contestant hash
def test_sealed_reports_carry_exact_contestant_hash(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    seal.verify_materialization(world["materialization"],
                                world["binding"])
    reports = score_all(world)
    by_id = {c["run_id"]: c["expected_sha256"]
             for c in world["binding"]["contestants"]}
    for rep in reports:
        st = rep["sealed_execution_identity"]
        assert st["contestant_payload_sha256"] == \
            by_id[st["contestant_run_id"]]
        assert st["holdout_sha256"] == world["gt"]["sealed_sha256"]
        assert st["support_artifact_sha256"]
        assert st["binding_manifest_identity"][
            "binding_identity_sha256"]


# --- item I.8: all three PERFECT -> YES
def test_aggregate_yes_requires_all_three_perfect(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory, perfect=True)
    agg = seal.aggregate_reports(score_all(world), world["binding"])
    assert agg["repeatable_perfect"] == "YES"
    assert agg["final_gate"] == "REPEATABLE_PERFECT"
    assert set(agg["interest_finite_set_status"].values()) == \
        {"PERFECT"}
    # minor families reported per contestant, no invented thresholds
    for rid, fam in agg["family_statuses_per_contestant"].items():
        assert set(fam) == {"Interest", "Goal", "InformationNeed",
                            "Question"}


# --- item I.9: one IMPERFECT => NO
def test_aggregate_any_imperfect_is_no(tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory, perfect=False)
    reports = score_all(world)
    statuses = [r["finite_set_conformance"]["Interest"]["status"]
                for r in reports]
    assert "IMPERFECT" in statuses
    agg = seal.aggregate_reports(reports, world["binding"])
    assert agg["repeatable_perfect"] == "NO"
    assert agg["final_gate"] == "REPEATABLE_PERFECT"


# --- item I.3/I.10: missing contestant => INCOMPLETE, no final verdict
def test_aggregate_missing_contestant_incomplete(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory, perfect=True)
    reports = score_all(world)[:-1]  # omit shadow_3
    agg = seal.aggregate_reports(reports, world["binding"])
    assert agg["repeatable_perfect"] == "INCOMPLETE"
    assert agg["final_gate"] is None
    assert any("missing report for shadow_3" in r
               for r in agg["incomplete_reasons"])


# --- item I.2: a fourth contestant is refused by the binding manifest
def test_binding_manifest_refuses_fourth_contestant(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    binding = dict(world["binding"])
    binding["contestants"] = binding["contestants"] + [
        {"run_id": "shadow_4", "expected_sha256": "x" * 64,
         "reconstructed_sha256": "x" * 64}]
    p = tmp_path / "binding4.json"
    p.write_text(json.dumps(binding), encoding="utf-8")
    with pytest.raises(seal.SealedRunError) as ei:
        seal.load_binding_manifest(p)
    assert ei.value.code == "BINDING_MANIFEST_CONTESTANT_SET"


# --- item I.6: wrong contestant identity in a report => INCOMPLETE
def test_aggregate_refuses_wrong_identity(tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    reports = score_all(world)
    rep = dict(reports[1])
    rep["sealed_execution_identity"] = dict(
        reports[1]["sealed_execution_identity"],
        contestant_payload_sha256="f" * 64)
    agg = seal.aggregate_reports(
        [reports[0], rep, reports[2]], world["binding"])
    assert agg["repeatable_perfect"] == "INCOMPLETE"
    assert agg["final_gate"] is None
    assert any("shadow_2: report payload sha != bound sha" in r
               for r in agg["incomplete_reasons"])


# --- item I.7: duplicate shadow_1 substituted for shadow_2 => refused
def test_aggregate_refuses_duplicate_substitution(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    reports = score_all(world)
    agg = seal.aggregate_reports(
        [reports[0], reports[0], reports[2]], world["binding"])
    assert agg["repeatable_perfect"] == "INCOMPLETE"
    assert agg["final_gate"] is None
    assert any("duplicate report for shadow_1" in r
               for r in agg["incomplete_reasons"])
    assert any("missing report for shadow_2" in r
               for r in agg["incomplete_reasons"])


# --- item I.4/I.12: substituted or vanished bytes cannot pass
def test_materialized_bytes_rehashed_and_substitution_refused(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    entry = world["materialization"]["contestants"][0]
    # same-schema, different payload written over the content-addressed
    # path is refused by the immediate re-hash
    p = Path(entry["storage_path"])
    saved = p.read_bytes()
    imposter = seal.canonical_bytes(payload_for(
        [f"sealed topic number {k}" for k in range(4)]))
    p.write_bytes(imposter)
    with pytest.raises(seal.SealedRunError) as ei:
        seal.read_materialized_payload(entry)
    assert ei.value.code == "CONTESTANT_BYTES_MISMATCH"
    # restore; then the SOURCE run-root analogue disappearing does not
    # matter: scoring reads only the materialized store
    p.write_bytes(saved)
    gone = tmp_path / "gone-run-roots"
    gone.mkdir()
    payload = seal.read_materialized_payload(entry)
    assert payload["inferred_interests"]


def test_materialization_identity_mismatch_refused(
        tmp_path, sealed_gt_factory):
    world = make_world(tmp_path, sealed_gt_factory)
    mat = json.loads(json.dumps(world["materialization"]))
    mat["contestants"][1]["payload_sha256"] = "a" * 64
    with pytest.raises(seal.SealedRunError) as ei:
        seal.verify_materialization(mat, world["binding"])
    assert ei.value.code == "MATERIALIZATION_IDENTITY"


# --- item I.1 + I.22: generic surfaces cannot touch the sealed holdout
def test_generic_score_refuses_sealed_digest_even_with_flag(
        tmp_path, sealed_gt_factory, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "isem_cli_sealed", REPO / "scripts" / "eval_interest_holdout.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps({"labels": []}), encoding="utf-8")
    monkeypatch.setattr(isem, "SEALED_GT_SHA256",
                        isem.sha256_file(gt))
    res = tmp_path / "arbitrary-result.json"
    res.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"frozen_artifacts": []}),
                        encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        cli.main(["score", "--gt", str(gt), "--result", str(res),
                  "--out", str(tmp_path / "o.json"),
                  "--allow-holdout",
                  "--manifest", str(manifest)])
    assert "refusing generic score" in str(ei.value)


def test_formal_runner_refuses_non_sealed_gt(
        tmp_path, sealed_gt_factory):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sealed_runner", REPO / "scripts" / "run_sealed_isem_d3.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    gt = tmp_path / "synthetic-gt.json"
    gt.write_text(json.dumps({"labels": []}), encoding="utf-8")
    with pytest.raises(seal.SealedRunError) as ei:
        runner.run_sealed(gt, tmp_path / "b.json",
                          tmp_path / "m.json", None, None,
                          tmp_path / "out")
    assert ei.value.code == "GT_NOT_SEALED_HOLDOUT"


def test_diagnostic_surfaces_have_no_gt_input():
    cli_source = (REPO / "scripts" / "eval_interest_holdout.py").read_text(
        encoding="utf-8")
    assert cli_source.count('add_argument("--gt"') == 2  # support+score
    for script in ("scripts/bind_isem_d3.py",
                   "scripts/materialize_d3_contestants.py"):
        src = (REPO / script).read_text(encoding="utf-8")
        assert '"--gt"' not in src
    runner_source = (REPO / "scripts" /
                     "run_sealed_isem_d3.py").read_text(
        encoding="utf-8")
    assert "sealed_sha256" in runner_source  # the sealed-only gate
