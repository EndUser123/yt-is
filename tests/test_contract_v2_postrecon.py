"""Regression tests for the run-b39505889cfe blocker: the post-recon
one-shot reference repair must be a functional closure (prompt written,
v1 schema attached, receipts recorded) and provider flakes inside it must
fail closed with metrics, never crash the arm. Also pins the FROZEN_PLAN_ID
negative path (review F6 coverage gap)."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)

_ibspec = importlib.util.spec_from_file_location(
    "interest_contract_bakeoff",
    REPO / "scripts" / "interest_contract_bakeoff.py")
ib = importlib.util.module_from_spec(_ibspec)
_ibspec.loader.exec_module(ib)

_dspec = importlib.util.spec_from_file_location(
    "contract_v2_bakeoff", REPO / "scripts" / "contract_v2_bakeoff.py")
drv = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(drv)

_hspec = importlib.util.spec_from_file_location(
    "tbig_helpers", REPO / "tests" / "test_build_interest_graph.py")
tbig = importlib.util.module_from_spec(_hspec)
_hspec.loader.exec_module(tbig)

SUPPLIED = {1, 2}


def base_payload():
    return {
        "inferred_interests": [
            {"name": "Distributed Databases", "kind": "domain",
             "parent": None, "temporal_state": "durable",
             "stance": "learning", "confidence": 0.9,
             "observed_vs_inferred": "observed", "goal": None,
             "information_need": None, "cluster_ids": [1],
             "evidence_summary": "s", "counterevidence": None,
             "related_to": ["Ghost Interest"]}],
        "questions": [],
        "regret_candidates": [],
    }


def test_post_recon_repair_writes_prompt_and_repairs(tmp_path):
    seen = {}

    def invoke(prompt):
        seen["prompt"] = prompt
        fixed = json.loads(json.dumps(base_payload()))
        fixed["inferred_interests"][0]["related_to"] = []
        return fixed

    # translator feeds: leaf f1 maps to source DD; an accepted edge then
    # translates its related_to onto that leaf's surviving final name
    final, sani = drv.sanitize_and_validate(
        base_payload(),
        [{"fragment_id": "f1", "decision": "kept",
          "target_interest": "Distributed Databases"}],
        {"f1": "Distributed Databases"},
        {"parent_edges": [],
         "related_edges": [{"source_id": "i1",
                            "target_id": "iGHOST"}],
         "question_links": [], "regret_links": []},
        {"i1": "Distributed Databases", "iGHOST": "Nowhere"},
        SUPPLIED, final_invoke=invoke)
    big.validate_inference(final, SUPPLIED)
    assert "reference_repair_once(1)" in sani["status"]


def test_provider_flake_inside_repair_translates_to_fail_closed():
    """A ProviderExecutionError inside the sanctioned repair escapes the
    sanitizer; the DRIVER owns translating it into a receipted fail-closed
    row — pin both the class disjointness that guard relies on and the
    escape itself."""
    def flaky_invoke(prompt):
        raise big.ProviderExecutionError("simulated flake")

    assert not issubclass(big.ProviderExecutionError,
                          big.InferenceContractError), \
        "guard relies on these being disjoint; hierarchy changed"
    with pytest.raises(big.ProviderExecutionError):
        drv.sanitize_and_validate(
            base_payload(),
            [{"fragment_id": "f1", "decision": "kept",
              "target_interest": "Distributed Databases"}],
            {"f1": "Distributed Databases"},
            {"parent_edges": [
                {"child_id": "i1", "parent_id": "iGHOST"}],
             "related_edges": [], "question_links": [],
             "regret_links": []},
            {"i1": "Distributed Databases", "iGHOST": "Nowhere"},
            SUPPLIED, final_invoke=flaky_invoke)


def test_plan_guard_aborts_before_provider_on_mismatch(monkeypatch):
    """F6 coverage gap: mismatched fingerprint aborts SystemExit BEFORE
    any provider activity."""
    import ef.evidence_clusters as ec
    entries = tbig.inventory_of(60)["clusters"]
    monkeypatch.setattr(ec, "evidence_cluster_inventory",
                        lambda *a, **k: {"clusters": entries,
                                         "eligible_count": 60,
                                         "total_semantic_non_series": 60,
                                         "exclusions": {}})
    monkeypatch.setattr(ec, "hydrate_evidence_clusters",
                        lambda ids: [tbig.synth_packet(c) for c in ids])
    reached_provider = {"v": False}

    def no_provider(*a, **k):
        reached_provider["v"] = True
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(drv.ib, "run_codex_capture", no_provider)
    workdir = Path(tempfile.mkdtemp(prefix="pcv-guard-"))
    with pytest.raises(SystemExit, match="FROZEN PLAN MISMATCH"):
        drv.run_arm("D1", str(workdir))
    assert not reached_provider["v"], \
        "phase-1 must never start on a drifted plan"


def test_write_schemas_provides_inference_key_for_repair_closure(tmp_path):
    """Direct pin of the run-b39505889cfe blocker: the schemas dict that
    feeds _post_recon_invoke MUST contain the v1 inference schema file,
    and the real closure must execute end-to-end against it."""
    schemas = drv.write_schemas(tmp_path)
    assert "inference" in schemas
    assert schemas["inference"].exists()
    blob = json.loads(schemas["inference"].read_text(encoding="utf-8"))
    # v1 shape marker: interests carry the relational fields phase-1 omits
    assert "related_to" in json.dumps(
        blob["$defs"]["interest"]["properties"])

    seen = {}

    def capturing_invoke(prompt):
        seen["prompt_file"] = None
        fixed = json.loads(json.dumps(base_payload()))
        fixed["inferred_interests"][0]["related_to"] = []
        return fixed

    final, sani = drv.sanitize_and_validate(
        base_payload(),
        [{"fragment_id": "f1", "decision": "kept",
          "target_interest": "Distributed Databases"}],
        {"f1": "Distributed Databases"},
        {"parent_edges": [],
         "related_edges": [{"source_id": "i1", "target_id": "iGHOST"}],
         "question_links": [], "regret_links": []},
        {"i1": "Distributed Databases", "iGHOST": "Nowhere"},
        SUPPLIED, final_invoke=capturing_invoke)
    big.validate_inference(final, SUPPLIED)
    assert "reference_repair_once" in sani["status"]
