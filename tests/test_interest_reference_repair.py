"""Tests for the bounded reference-repair machinery (candidate Arm C).

Offline; the provider is a fake invoke callable. Repair scope ceiling:
reference-only, bounded attempts, receipts for every mutation, and
fail-closed on anything outside scope.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)

from ef.inference_contract import inference_output_schema, \
    conformance_errors

SUPPLIED = {1, 2}


def payload(dangling_related=True, orphan_question=False):
    return {
        "inferred_interests": [
            {
                "name": "Distributed Databases",
                "kind": "domain", "parent": None,
                "temporal_state": "durable", "stance": "learning",
                "confidence": 0.9, "observed_vs_inferred": "observed",
                "goal": None, "information_need": None,
                "cluster_ids": [1],
                "evidence_summary": "s", "counterevidence": None,
                "related_to": ["Raft Consensus" if dangling_related
                               else "Database Sharding"],
            },
            {
                "name": "Raft Consensus",
                "kind": "subtopic", "parent": "Distributed Databases",
                "temporal_state": "active", "stance": "project",
                "confidence": 0.8, "observed_vs_inferred": "inferred",
                "goal": None, "information_need": None,
                "cluster_ids": [2],
                "evidence_summary": "s2", "counterevidence": None,
                "related_to": [],
            },
        ],
        "questions": ([{"text": "Which shard map does Raft assume?",
                        "interest": "Sharding", "status": "open"}]
                      if orphan_question else []),
        "regret_candidates": [
            {"topic": "Astronomy", "why": "adjacent",
             "label": "inferred_adjacent", "confidence": 0.5,
             "cluster_ids": [1], "related_interests":
                 ["Distributed Databases", "Cosmology"]},
        ],
    }


# ---------------------------------------------------------------------------
# deterministic hygiene
# ---------------------------------------------------------------------------

def test_hygiene_drops_only_dangling_optional_edges():
    original = payload(dangling_related=False)
    frozen = json.loads(json.dumps(original))
    cleaned, receipts = big.deterministic_reference_hygiene(original)
    assert original == frozen, "input must not be mutated"
    # BOTH optional-edge classes are repaired losslessly:
    #   interest.related_to -> ["Database Sharding"] dangles
    #   regret.related_interests -> ["Cosmology"] dangles
    types = sorted(r["repair_type"] for r in receipts)
    assert types == ["drop_dangling_regret_related_interest",
                     "drop_dangling_related_to"]
    assert cleaned["inferred_interests"][0]["related_to"] == []
    assert cleaned["regret_candidates"][0]["related_interests"] == [
        "Distributed Databases"]
    # valid targets survive untouched
    intact = payload()  # all references resolve
    intact_cleaned, intact_receipts = big.deterministic_reference_hygiene(
        intact)
    assert intact_receipts == [
        {"repair_type": "drop_dangling_regret_related_interest",
         "container": "regret_candidates[0]",
         "dropped_target": "Cosmology"}]
    assert intact_cleaned["inferred_interests"][0]["related_to"] == [
        "Raft Consensus"]
    # required-reference defects are OUTSIDE hygiene's scope: the orphan
    # question survives untouched and is left for the bounded repair round
    dirty = payload(orphan_question=True)
    dirty["regret_candidates"][0]["related_interests"] = ["Cosmology"]
    cleaned2, _ = big.deterministic_reference_hygiene(dirty)
    assert cleaned2["questions"][0]["interest"] == "Sharding"
    with pytest.raises(big.InferenceContractError) as err:
        big.validate_inference(cleaned2, SUPPLIED)
    assert "question[0]" in str(err.value)


def test_clean_payload_produces_no_receipts_and_no_changes():
    p = payload(dangling_related=False)
    p["inferred_interests"][0]["related_to"] = []
    p["regret_candidates"][0]["related_interests"] = [
        "Distributed Databases"]
    cleaned, receipts = big.deterministic_reference_hygiene(p)
    assert receipts == []
    assert cleaned == p


def test_fully_valid_payload_passes_after_scope_boundary():
    """Optional-edge drops plus semantic-layer checks compose correctly."""
    cleaned, _ = big.deterministic_reference_hygiene(
        payload(dangling_related=True))
    # simulate the bounded provider repair having fixed the one remaining
    # required-reference class this fixture does not contain (none here):
    big.validate_inference(cleaned, SUPPLIED)
    assert conformance_errors(cleaned,
                              inference_output_schema()) == []


def test_classify_error_scope():
    dangling = payload(orphan_question=True)
    try:
        big.validate_inference(dangling, SUPPLIED)
    except big.InferenceContractError as exc:
        first = exc
    assert big.classify_contract_error(first) == "reference"

    bad_enum = payload(dangling_related=False)
    bad_enum["regret_candidates"] = []
    bad_enum["questions"] = []
    bad_enum["inferred_interests"][0]["related_to"] = []
    bad_enum["inferred_interests"][0]["temporal_state"] = "ongoing"
    with pytest.raises(big.InferenceContractError) as err:
        big.validate_inference(bad_enum, SUPPLIED)
    assert "temporal_state" in str(err.value)
    assert big.classify_contract_error(err.value) == "other"


# ---------------------------------------------------------------------------
# validated_reference_repair
# ---------------------------------------------------------------------------

def fixed_invoke(repairs_questions=True):
    def invoke(prompt: str) -> dict:
        fixed = json.loads(json.dumps(payload()))
        fixed["inferred_interests"][0]["related_to"] = ["Raft Consensus"]
        fixed["regret_candidates"][0]["related_interests"] = [
            "Distributed Databases"]
        if repairs_questions:
            fixed["questions"] = [{
                "text": "Which shard map does Raft assume?",
                "interest": "Raft Consensus", "status": "open"}]
        return fixed
    return invoke


def base_payload_with_orphan_question():
    p = payload(dangling_related=False, orphan_question=True)
    _, receipts = big.deterministic_reference_hygiene(p)
    return p, receipts


def test_repair_repairs_and_validates():
    p, hyg = base_payload_with_orphan_question()
    out, receipts, attempts = big.validated_reference_repair(
        p, SUPPLIED, fixed_invoke(True))
    assert attempts == 1
    big.validate_inference(out, SUPPLIED)
    kinds = [r.get("repair_type") for r in receipts]
    assert "validation_error" in kinds or kinds == []
    applied = [r for r in receipts
               if r.get("repair_type") == "reference_repair_applied"]
    assert applied and applied[-1]["attempt"] == 1
    assert any(r["repair_type"] == "drop_dangling_regret_related_interest"
               for r in hyg)
    schema_errs = conformance_errors(out, inference_output_schema())
    assert schema_errs == []


def test_repair_fails_closed_on_out_of_scope_violation():
    def bad_invoke(prompt: str) -> dict:
        corrupted = json.loads(json.dumps(payload()))
        corrupted["inferred_interests"][0]["kind"] = "theme"
        return corrupted
    p, _ = base_payload_with_orphan_question()
    with pytest.raises(big.InferenceContractError, match="failing closed"):
        big.validated_reference_repair(p, SUPPLIED, bad_invoke)


def test_repair_exhaustion_fails_closed():
    calls = []

    def loop_invoke(prompt: str) -> dict:
        calls.append(prompt)
        # still has an unresolvable question reference every time
        return json.loads(json.dumps(base_payload_with_orphan_question()[0]))

    p, _ = base_payload_with_orphan_question()
    with pytest.raises(big.InferenceContractError,
                       match="did not converge within 2"):
        big.validated_reference_repair(p, SUPPLIED, loop_invoke)
    assert len(calls) == 2  # MAX_REFERENCE_REPAIR_ATTEMPTS


def test_repair_not_attempted_for_already_valid_payload():
    p = payload(dangling_related=False)
    p["inferred_interests"][0]["related_to"] = ["Raft Consensus"]
    p["regret_candidates"][0]["related_interests"] = [
        "Distributed Databases"]
    big.validate_inference(p, SUPPLIED)  # fixture sanity

    def explode(prompt: str) -> dict:
        raise AssertionError("provider must not be called")

    out, receipts, attempts = big.validated_reference_repair(
        p, SUPPLIED, explode)
    assert attempts == 0 and receipts == [] and out == p


def test_repair_prompt_contains_names_errors_and_payload():
    prompt = big.build_repair_prompt(payload(), ["boom at x"])
    assert "boom at x" in prompt
    assert "raft consensus" in prompt.lower() or \
        "Raft Consensus" in prompt
    assert '"inferred_interests"' in prompt


def test_repair_rejects_non_dict_invoke_output():
    def junk_invoke(prompt: str) -> dict:
        return {"not": "a contract"}
    p, _ = base_payload_with_orphan_question()
    with pytest.raises(big.InferenceContractError):
        big.validated_reference_repair(p, SUPPLIED, junk_invoke)


def test_repair_refuses_structurally_invalid_input():
    """A missing required field is an enforcement failure, not a
    reference defect: it must fail closed BEFORE any repair round."""
    bad = payload(orphan_question=True)
    del bad["inferred_interests"][0]["stance"]
    with pytest.raises(big.InferenceContractError,
                       match="structurally schema-invalid"):
        big.validated_reference_repair(
            bad, SUPPLIED,
            lambda prompt: pytest.fail("provider must not be called"))


def test_repair_fails_closed_when_inventory_changes():
    def adder_invoke(prompt: str) -> dict:
        fixed = json.loads(json.dumps(base_payload_with_orphan_question()
                                      [0]))
        # drop the dangling optional edges so they are not the complaint;
        # then try to SNEAK IN a brand-new interest during repair
        fixed["inferred_interests"][0]["related_to"] = []
        fixed["regret_candidates"][0]["related_interests"] = [
            "Distributed Databases"]
        extra = json.loads(json.dumps(fixed["inferred_interests"][0]))
        extra["name"] = "Sneaky New Interest"
        extra["cluster_ids"] = [1]
        extra["parent"] = None
        extra["related_to"] = []
        fixed["inferred_interests"].append(extra)
        return fixed
    p, _ = base_payload_with_orphan_question()
    with pytest.raises(big.InferenceContractError,
                       match="altered the interest inventory"):
        big.validated_reference_repair(p, SUPPLIED, adder_invoke)


# ---------------------------------------------------------------------------
# reconciliation-tree hook seam (default OFF)
# ---------------------------------------------------------------------------

def _dangling_final_invoke(provider, prompt, prompt_file, timeout):
    import copy
    from test_build_interest_graph import merging_invoke  # same fake logic
    wrapper, model = merging_invoke(provider, prompt, prompt_file, timeout)
    wrapper = copy.deepcopy(wrapper)
    if len(wrapper["final"]["inferred_interests"]) >= 2:
        # inject a dangling OPTIONAL edge into every intermediate/final stage
        wrapper["final"]["inferred_interests"][0]["related_to"] = [
            "No Such Interest"]
    return wrapper, model


def fragments_fixture():
    def leaf(name, cid, fid):
        return {"fragment_id": fid, "batch_id": "b001",
                "interest": {"name": name, "kind": "topic", "parent": None,
                             "temporal_state": "active",
                             "stance": "learning", "confidence": 0.7,
                             "observed_vs_inferred": "observed",
                             "goal": None, "information_need": None,
                             "cluster_ids": [cid],
                             "evidence_summary": f"evidence {name}",
                             "counterevidence": None, "related_to": []},
                "cluster_ids": [cid]}
    frags = [leaf("Interest A", 1, "frag_a"), leaf("Interest B", 2, "frag_b")]
    return {"interests": frags, "questions": [], "regret_candidates": []}


def test_reconciliation_tree_without_hook_fails_on_dangling_reference():
    # the dangling reference lives inside 'final', so it is caught by the
    # nested validate_inference and propagates as InferenceContractError;
    # either contract exception means the tree fails closed
    with pytest.raises((big.ReconciliationContractError,
                        big.InferenceContractError)):
        big.run_reconciliation_tree(fragments_fixture(), [1, 2],
                                    invoke=_dangling_final_invoke)


def test_reconciliation_tree_hook_repairs_within_scope():
    seen = []

    def hook(wrapper, group_fragments, stage, group_index):
        seen.append((stage, group_index,
                     [f["fragment_id"] for f in group_fragments]))
        cleaned, receipts = big.deterministic_reference_hygiene(
            {"inferred_interests":
                 wrapper["final"]["inferred_interests"],
             "questions": [],
             "regret_candidates": []})
        fixed = dict(wrapper)
        fixed["final"] = cleaned
        return fixed

    result = big.run_reconciliation_tree(fragments_fixture(), [1, 2],
                                         invoke=_dangling_final_invoke,
                                         repair_hook=hook)
    assert result["provider_calls"] == 1
    assert result["stages"][0]["group_sizes"] == [2]
    assert seen == [(1, 1, ["frag_a", "frag_b"])]
    assert result["final"]["inferred_interests"][0]["related_to"] == []
    # untouched audit surfaces: dispositions survive verbatim
    assert {d["decision"] for d in
            result["fragment_dispositions"]} <= {"kept", "merged",
                                                 "discarded"}


def test_reconciliation_tree_hook_none_returns_fail_closed():
    with pytest.raises(big.ReconciliationContractError,
                       match="repair_hook returned no wrapper"):
        big.run_reconciliation_tree(
            fragments_fixture(), [1, 2], invoke=_dangling_final_invoke,
            repair_hook=lambda w, g, stage, group_index: None)
