"""Tests for ef/inference_contract.py — single-source contract schemas.

Offline, pure logic. The schema layer must mechanically constrain exactly
what validate_inference constrains structurally (arrays, fields, enums,
bounds, nullability, shapes); dynamic same-payload references stay outside
its scope by design.
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

from ef.inference_contract import (OBSERVED_VS_INFERRED, KINDS,
                                   QUESTION_STATUSES, REGRET_LABELS,
                                   STANCES, TEMPORAL_STATES,
                                   conformance_errors,
                                   inference_output_schema,
                                   reconciliation_output_schema)


def valid_payload() -> dict:
    return {
        "inferred_interests": [
            {
                "name": "Distributed Databases",
                "kind": "domain",
                "parent": None,
                "temporal_state": "durable",
                "stance": "learning",
                "confidence": 0.9,
                "observed_vs_inferred": "observed",
                "goal": None,
                "information_need": None,
                "cluster_ids": [1, 2],
                "evidence_summary": "clusters 1-2",
                "counterevidence": None,
                "related_to": [],
            },
            {
                "name": "Raft Consensus",
                "kind": "subtopic",
                "parent": "Distributed Databases",
                "temporal_state": "active",
                "stance": "project",
                "confidence": 0.8,
                "observed_vs_inferred": "inferred",
                "goal": None,
                "information_need": None,
                "cluster_ids": [2],
                "evidence_summary": "cluster 2",
                "counterevidence": None,
                "related_to": ["Distributed Databases"],
            },
        ],
        "questions": [
            {"text": "Raft minority partitions?",
             "interest": "Raft Consensus", "status": "open"},
        ],
        "regret_candidates": [
            {"topic": "Astronomy", "why": "adjacent",
             "label": "inferred_adjacent", "confidence": 0.5,
             "cluster_ids": [4], "related_interests":
                 ["Distributed Databases"]},
        ],
    }


SUPPLIED = {1, 2, 4}


def mut(**overrides):
    p = json.loads(json.dumps(valid_payload()))
    p.update(overrides)
    return p


def interest_mut(i=0, **field_overrides):
    p = json.loads(json.dumps(valid_payload()))
    p["inferred_interests"][i].update(field_overrides)
    return p


def test_enums_match_validator_vocabularies():
    # single source of truth: script imports must be identical objects
    assert big.KINDS == KINDS
    assert big.TEMPORAL_STATES == TEMPORAL_STATES
    assert big.STANCES == STANCES
    assert big.OBSERVED_VS_INFERRED == OBSERVED_VS_INFERRED
    assert big.QUESTION_STATUSES == QUESTION_STATUSES
    assert big.REGRET_LABELS == REGRET_LABELS


def test_prompt_template_enum_lists_match_contract():
    template = big.PROMPT_TEMPLATE
    assert "|".join(KINDS) in template
    assert "|".join(TEMPORAL_STATES) in template
    assert "|".join(STANCES) in template
    assert "|".join(OBSERVED_VS_INFERRED) in template
    recon = big.RECONCILIATION_PROMPT_TEMPLATE
    assert "|".join(QUESTION_STATUSES) in recon


def test_valid_payload_conforms_and_still_needs_semantic_layer():
    payload = valid_payload()
    big.validate_inference(payload, SUPPLIED)
    assert conformance_errors(payload, inference_output_schema()) == []
    # related_to points at a real name -> validator-clean here...
    # ...but the SCHEMA cannot know that; a dangling target is NOT a
    # schema violation even though it violates the semantic contract.
    dangling = interest_mut(1, related_to=["Nonexistent Interest"])
    assert conformance_errors(dangling, inference_output_schema()) == []
    with pytest.raises(big.InferenceContractError):
        big.validate_inference(dangling, SUPPLIED)


def test_required_top_level_arrays_and_unknown_keys_rejected():
    schema = inference_output_schema()
    for key in ("inferred_interests", "questions", "regret_candidates"):
        p = valid_payload()
        del p[key]
        errs = conformance_errors(p, schema)
        assert any(key in e for e in errs), key
    errs = conformance_errors(mut(oops=1), schema)
    assert any("oops" in e for e in errs)


def test_empty_interest_list_is_a_violation():
    errs = conformance_errors(mut(inferred_interests=[]),
                              inference_output_schema())
    assert any("fewer than 1" in e for e in errs)


def test_field_presence_enforced():
    schema = inference_output_schema()
    required_fields = ["name", "kind", "parent", "temporal_state",
                       "stance", "confidence", "observed_vs_inferred",
                       "goal", "information_need", "cluster_ids",
                       "evidence_summary", "counterevidence",
                       "related_to"]
    for field in required_fields:
        p = json.loads(json.dumps(valid_payload()))
        del p["inferred_interests"][0][field]
        errs = conformance_errors(p, schema)
        assert any(field in e for e in errs), field


def test_enum_violations_cannot_pass_schema():
    schema = inference_output_schema()
    cases = [dict(kind="category"),
             dict(temporal_state="ongoing"),
             dict(stance="research"),
             dict(observed_vs_inferred="unknown")]
    for overrides in cases:
        field = next(iter(overrides))
        errs = conformance_errors(interest_mut(**overrides), schema)
        assert any("enum" in e.lower() or "not in enum" in e.lower()
                   for e in errs), field


def test_confidence_bounds_and_types():
    schema = inference_output_schema()
    for value in (-0.1, 1.0001, True, "0.5"):
        errs = conformance_errors(interest_mut(confidence=value), schema)
        assert errs, value
    assert conformance_errors(
        interest_mut(confidence=0.0), schema) == []
    assert conformance_errors(
        interest_mut(confidence=1.0), schema) == []


def test_nullability_of_optional_text_fields():
    schema = inference_output_schema()
    ok = interest_mut(goal=None, information_need=None,
                      counterevidence=None, parent=None)
    assert conformance_errors(ok, schema) == []
    errs = conformance_errors(interest_mut(parent=7), schema)
    assert any("parent" in e for e in errs)
    errs = conformance_errors(interest_mut(evidence_summary=None), schema)
    assert any("evidence_summary" in e for e in errs)


def test_cluster_id_array_shape():
    schema = inference_output_schema()
    # interests forbid duplicates (validator dupes=True) ...
    errs = conformance_errors(
        interest_mut(cluster_ids=[2, 2]), schema)
    assert any("unique" in e.lower() for e in errs)
    # ... while regret candidates allow them (dupes=False)
    p = valid_payload()
    p["regret_candidates"][0]["cluster_ids"] = [4, 4]
    assert conformance_errors(p, schema) == []
    # empty arrays are violations in both places
    errs = conformance_errors(interest_mut(cluster_ids=[]), schema)
    assert any("fewer than 1" in e for e in errs)


def test_question_and_regret_shapes():
    schema = inference_output_schema()
    p = valid_payload()
    p["questions"][0]["status"] = "closed"
    errs = conformance_errors(p, schema)
    assert any("status" in e for e in errs)
    p = valid_payload()
    p["regret_candidates"][0]["label"] = "urgent"
    errs = conformance_errors(p, schema)
    assert any("label" in e for e in errs)


def test_reconciliation_wrapper_schema():
    schema = reconciliation_output_schema()
    wrapper = {"final": valid_payload(),
               "fragment_dispositions": [
                   {"fragment_id": "f1", "decision": "kept",
                    "target_interest": "Raft Consensus", "reason": "r"},
                   {"fragment_id": "f2", "decision": "discarded",
                    "target_interest": None, "reason": "noise"}]}
    assert conformance_errors(wrapper, schema) == []

    bad = json.loads(json.dumps(wrapper))
    bad["fragment_dispositions"][0]["decision"] = "dropped"
    bad["final"]["inferred_interests"][0]["kind"] = "theme"
    errs = conformance_errors(bad, schema)
    assert any("decision" in e for e in errs)
    assert any("kind" in e for e in errs)

    missing = {"fragment_dispositions": wrapper["fragment_dispositions"]}
    assert any("final" in e for e in conformance_errors(missing, schema))


def test_schemas_are_serializable_strict_documents():
    for schema in (inference_output_schema(),
                   reconciliation_output_schema()):
        blob = json.dumps(schema)
        assert json.loads(blob) == schema
        text = blob
        # strict-shape sanity: every object pins additionalProperties false
        assert '"additionalProperties": false' in text
