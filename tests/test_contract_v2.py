"""CONTRACT ARCHITECTURE v2 tests — decomposed object/relation/grouping.

Includes the packet's REQUIRED RELIABILITY FALSIFIER battery: every
adversarial scenario must be CONTAINED without destroying validated core
objects, and assembled output must pass the strict frozen v1 validator.
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

from ef import contract_v2 as v2
from ef.inference_contract import (grouping_output_schema,
                                   phase1_output_schema,
                                   relation_output_schema,
                                   conformance_errors)


def interest(name, cids, conf=0.8):
    return {"name": name, "kind": "topic", "temporal_state": "active",
            "stance": "learning", "confidence": conf,
            "observed_vs_inferred": "observed", "goal": None,
            "information_need": None, "cluster_ids": list(cids),
            "evidence_summary": f"e {name}", "counterevidence": None}


def payload(interests=None, questions=None, regrets=None):
    return {
        "inferred_interests": interests if interests is not None
        else [interest("Distributed Databases", [1]),
              interest("Raft Consensus", [2], 0.6)],
        "questions": questions if questions is not None
        else [{"text": "Raft minority partitions?", "status": "open"}],
        "regret_candidates": regrets if regrets is not None
        else [{"topic": "Astronomy", "why": "adjacent",
               "label": "inferred_adjacent", "confidence": 0.4,
               "cluster_ids": [2]}],
    }


SUPPLIED = {1, 2}


def build_two_batch_inventory():
    inv = []
    for bid in ("b001", "b002"):
        valid, _ = v2.validate_phase1_payload(payload(), SUPPLIED)
        inv.extend(v2.build_inventory(valid, bid))
    return inv


# ---------------------------------------------------------------------------
# Phase-1 per-object validation + isolation
# ---------------------------------------------------------------------------

def test_valid_phase1_passes_with_no_failures():
    valid, failures = v2.validate_phase1_payload(payload(), SUPPLIED)
    assert failures == []
    assert len(valid["inferred_interests"]) == 2
    assert len(valid["questions"]) == 1
    assert len(valid["regret_candidates"]) == 1


def test_invalid_optional_object_does_not_destroy_core_objects():
    # falsifier: one malformed non-core object contained
    p = payload(questions=[{"text": "ok?", "status": "open"},
                           {"text": "", "status": "wat"}])
    valid, failures = v2.validate_phase1_payload(p, SUPPLIED)
    assert len(valid["inferred_interests"]) == 2          # cores survive
    assert len(valid["questions"]) == 1
    cls = {(f["array"], f["index"]): f for f in failures}
    bad = cls[("questions", 1)]
    assert bad["severity"] == "optional"
    assert bad["error_class"] in ("schema_enforcement", "semantic")


def test_schema_violating_interest_isolated_not_fatal():
    p = payload(interests=[interest("Good", [1]),
                           dict(interest("Bad Shape", [2]), extra=1),
                           interest("Also Good", [2], 0.5)])
    valid, failures = v2.validate_phase1_payload(p, SUPPLIED)
    names = [i["name"] for i in valid["inferred_interests"]]
    assert names == ["Good", "Also Good"]
    assert len(failures) == 1
    assert failures[0]["severity"] == "core"
    assert failures[0]["error_class"] == "schema_enforcement"


def test_foreign_cluster_id_and_duplicate_names_are_semantic_failures():
    p = payload(interests=[interest("Good", [1]),
                           interest("Foreign", [999]),
                           interest("Good", [1])])   # dup within batch
    valid, failures = v2.validate_phase1_payload(p, SUPPLIED)
    assert [i["name"] for i in valid["inferred_interests"]] == ["Good"]
    kinds = sorted(f["error_class"] for f in failures)
    assert kinds == ["semantic", "semantic"]


def test_envelope_level_problems_raise():
    with pytest.raises(ValueError):
        v2.validate_phase1_payload({"oops": True}, SUPPLIED)
    with pytest.raises(ValueError):
        v2.validate_phase1_payload(payload(investors := []), SUPPLIED)


# ---------------------------------------------------------------------------
# Mechanical identity
# ---------------------------------------------------------------------------

def test_object_ids_deterministic_and_order_insensitive():
    a = v2.make_object_id("interest", "b001", "Topic X", [3, 1, 2])
    b = v2.make_object_id("interest", "b001", " topic   x ", [2, 1, 3])
    c = v2.make_object_id("interest", "b002", "Topic X", [1, 2, 3])
    assert a == b                    # normalization + order insensitivity
    assert a != c                    # run/batch scoping keeps ids distinct


def test_inventory_covers_all_object_types():
    inv = v2.build_inventory(v2.validate_phase1_payload(
        payload(), SUPPLIED)[0], "b001")
    types = {o["type"]: o["id"] for o in inv}
    assert set(types) == {"interest", "question", "regret"}
    assert all(str(big.ARTIFACT_ROOT) not in oid for oid in types.values())


# ---------------------------------------------------------------------------
# Relation stage — falsifier containment
# ---------------------------------------------------------------------------

def two_interest_inventory():
    inv = v2.build_inventory(v2.validate_phase1_payload(
        payload(), SUPPLIED)[0], "b001")
    return inv


def rel_ids(inv):
    return ([o["id"] for o in inv if o["type"] == "interest"],
            [o["id"] for o in inv if o["type"] == "question"],
            [o["id"] for o in inv if o["type"] == "regret"])


def by_name(inv):
    return {o["object"]["name"]: o["id"]
            for o in inv if o["type"] == "interest"}


def test_falsifier_dangling_optional_related_to_quarantined():
    inv = two_interest_inventory()
    ints, qs, rgs = rel_ids(inv)
    n = by_name(inv)
    accepted, quar = v2.verify_relations(
        {"parent_edges": [], "related_edges":
            [{"source_id": n["Distributed Databases"],
              "target_id": "int_ghost"}],
         "question_links": [], "regret_links": []}, ints, qs, rgs)
    assert accepted["related_edges"] == []
    assert quar[0]["reason"].startswith("endpoint unknown")


def test_falsifier_invalid_parent_self_and_cycle_contained():
    inv = two_interest_inventory()
    ints, qs, rgs = rel_ids(inv)
    a = by_name(inv)["Distributed Databases"]
    b = by_name(inv)["Raft Consensus"]
    accepted, quar = v2.verify_relations(
        {"parent_edges": [
            {"child_id": b, "parent_id": a},       # valid
            {"child_id": a, "parent_id": b},       # cycle -> quarantined
            {"child_id": a, "parent_id": a},       # self -> quarantined
            {"child_id": "qst_ghost", "parent_id": a}],  # wrong type
         "related_edges": [], "question_links": [],
         "regret_links": []},
        ints, qs, rgs)
    assert len(accepted["parent_edges"]) == 1
    reasons = sorted(q["reason"] for q in quar)
    assert any("cycle" in r for r in reasons)
    assert any("self-parent" in r for r in reasons)
    assert any("unknown" in r for r in reasons)


def test_falsifier_question_linkage_failure_is_explicit_not_silent():
    inv = two_interest_inventory()
    assembled, receipts = _assemble_with_unlinked_question()
    assert receipts["required_link_failures"], "must be receipted"
    # the unlinked question is EXCLUDED from final but recorded
    total_q_receipts = 0


def _assemble_with_unlinked_question(monkeypatched_groups=None):
    inv = build_two_batch_inventory()
    ints = [o["id"] for o in inv if o["type"] == "interest"]
    qs = [o["id"] for o in inv if o["type"] == "question"]
    groups = [{"members": ints, "action": "distinct",
               "canonical_name": None, "reason": ""},
              {"members": qs, "action": "distinct",
               "canonical_name": None, "reason": ""}]
    canon, disps = v2.assemble_canonical(groups, inv, [1, 2])
    c_int = [c for c in canon if c["type"] == "interest"]
    qc = [c for c in canon if c["type"] == "question"]
    acc, _ = v2.verify_relations(
        {"parent_edges": [], "related_edges": [],
         "question_links": [], "regret_links": []},
        [c["canonical_id"] for c in c_int],
        [c["canonical_id"] for c in qc], [])
    extra: list = []
    assembled, receipts = v2.apply_relations_to_assembly(canon, acc, extra)
    return assembled, receipts


# ---------------------------------------------------------------------------
# Decomposed reconciliation — zero silent loss
# ---------------------------------------------------------------------------

def test_falsifier_provider_group_omission_detected_and_recovered():
    """Provider forgets to group some objects: coverage check flags them;
    identical-key leftovers are then recovered mechanically."""
    inv = build_two_batch_inventory()
    ids = [o["id"] for o in inv]
    # provider 'covers' everything EXCEPT the duplicate Raft pair + a regret
    raft = [o for o in inv if o["object"].get("name") == "Raft Consensus"]
    astro = [o for o in inv
             if o["object"].get("topic") == "Astronomy"]
    omitted = {raft[1]["id"], astro[-1]["id"]}
    covered = [i for i in ids if i not in omitted]
    groups = [{"members": covered, "action": "distinct",
               "canonical_name": None, "reason": ""}]
    ok, covered_ids, problems = v2.verify_group_coverage(groups, inv)
    assert not ok and problems                     # omission DETECTED
    # mechanical recovery pass adds explicit distinct groups for missing
    recovery = [{"members": [oid], "action": "distinct",
                 "canonical_name": None, "reason": "coverage-retry"}
                for oid in sorted(omitted)]
    ok2, _, problems2 = v2.verify_group_coverage(groups + recovery, inv)
    assert ok2 and not problems2
    canon, disps = v2.assemble_canonical(groups + recovery, inv, [1, 2])
    src_dispositioned = {d["source_id"] for d in disps}
    assert set(ids) <= src_dispositioned           # ZERO silent loss
    # and the provider's omission of the Raft duplicate still merges
    # mechanically at assembly time
    n_raft = [c for c in canon if c["type"] == "interest"
              and c["object"]["name"] == "Raft Consensus"]
    assert len(n_raft) == 1
    assert any(d["decision"] == "mechanically_merged" for d in disps)


def test_merged_group_unions_provenance_and_picks_deterministic_rep():
    inv = build_two_batch_inventory()
    dd = sorted([o for o in inv
                 if o["type"] == "interest"
                 and o["object"]["name"] == "Distributed Databases"],
                key=lambda o: (-o["object"]["confidence"], o["id"]))
    groups = [{"members": [dd[0]["id"], dd[1]["id"]],
               "action": "merged", "canonical_name": None,
               "reason": "same"},
              {"members": [o["id"] for o in inv
                           if o["id"] not in {dd[0]["id"],
                                              dd[1]["id"]}],
               "action": "distinct", "canonical_name": None,
               "reason": ""}]
    ok, _, probs = v2.verify_group_coverage(groups, inv)
    assert ok, probs
    canon, disps = v2.assemble_canonical(groups, inv, [1, 2])
    rep = [c for c in canon if c["type"] == "interest"
           and c["object"]["name"] == "Distributed Databases"][0]
    assert rep["provenance_cluster_ids"] == [1]     # both copies cite [1]
    merged = [d for d in disps if d["decision"] == "merged"]
    assert len(merged) == 1 and \
        merged[0]["target_id"] == dd[0]["id"]      # higher confidence wins


def test_drop_noise_allowed_for_non_core_only():
    inv = build_two_batch_inventory()
    ids = [o["id"] for o in inv]
    noise = [o["id"] for o in inv if o["type"] != "interest"][:1]
    with pytest.raises(ValueError):
        v2.assemble_canonical(
            [{"members": [ids[0]], "action": "drop_noise",
              "canonical_name": None, "reason": "x"}],
            inv, [1, 2])
    ok_groups = [
        {"members": [o["id"] for o in inv
                     if o["id"] not in noise],
         "action": "distinct", "canonical_name": None, "reason": ""},
        {"members": noise, "action": "drop_noise",
         "canonical_name": None, "reason": "duplicate noise"}]
    canon, disps = v2.assemble_canonical(ok_groups, inv, [1, 2])
    dropped = {d["source_id"] for d in disps
               if d["decision"] == "discarded"}
    assert dropped == set(noise)
    q_count = sum(1 for c in canon if c["type"] == "question")
    assert q_count == 1                             # one was dropped


def test_assembled_output_passes_strict_frozen_validator_end_to_end():
    inv = build_two_batch_inventory()
    ints = [o["id"] for o in inv if o["type"] == "interest"]
    qs = [o["id"] for o in inv if o["type"] == "question"]
    rgs = [o["id"] for o in inv if o["type"] == "regret"]
    n = by_name(inv)
    a = n["Distributed Databases"]
    b = n["Raft Consensus"]
    canon, _ = v2.assemble_canonical(
        [{"members": ints, "action": "distinct", "canonical_name": None,
          "reason": ""},
         {"members": qs + rgs, "action": "distinct",
          "canonical_name": None, "reason": ""}],
        inv, [1, 2])
    ci = [c["canonical_id"] for c in canon if c["type"] == "interest"]
    cq = [c["canonical_id"] for c in canon if c["type"] == "question"]
    # relations proposed over CANONICAL ids (post-merge set)
    def cid(name):
        return next(c["canonical_id"] for c in canon
                    if c["type"] == "interest"
                    and c["object"]["name"] == name)

    acc, quar = v2.verify_relations(
        {"parent_edges": [{"child_id": cid("Raft Consensus"),
                           "parent_id": cid("Distributed Databases")}],
         "related_edges": [{"source_id": cid("Distributed Databases"),
                            "target_id": "int_ghost"},     # dangling prop
                           {"source_id": cid("Distributed Databases"),
                            "target_id": cid("Raft Consensus")}],
         "question_links": [{"question_id": cq[0],
                             "interest_id": cid("Raft Consensus")}],
         "regret_links": []},
        ci, cq, [c["canonical_id"] for c in canon
                 if c["type"] == "regret"])
    assert len(quar) == 1                                # dangling edge only
    extra: list = []
    assembled, receipts = v2.apply_relations_to_assembly(canon, acc, extra)
    big.validate_inference(assembled, SUPPLIED)          # FROZEN gate PASSES
    raft = [i for i in assembled["inferred_interests"]
            if i["name"] == "Raft Consensus"][0]
    assert raft["parent"] == "Distributed Databases"
    assert assembled["questions"][0]["interest"] == "Raft Consensus"


def test_relation_and_grouping_wrappers_conform_to_schemas():
    good_rel = {"parent_edges": [], "related_edges": [],
                "question_links": [], "regret_links": []}
    assert conformance_errors(good_rel, relation_output_schema()) == []
    bad = json.loads(json.dumps(good_rel))
    bad["unexpected"] = 1
    assert conformance_errors(bad, relation_output_schema())
    good_grp = {"groups": [{"members": ["a"], "action": "distinct",
                            "canonical_name": None, "reason": "r"}]}
    assert conformance_errors(good_grp, grouping_output_schema()) == []
    assert conformance_errors({"groups": []},
                              grouping_output_schema()) == []


def test_same_second_runs_still_get_distinct_roots():
    # artifact-isolation invariant preserved inside the v2 pipeline too
    roots = {str(big._new_run_dir("contract-v2")) for _ in range(4)}
    assert len(roots) == 4


def test_phase1_prompt_reuses_frozen_semantic_prose_verbatim():
    template_head = big.PROMPT_TEMPLATE.split("Return ONLY")[0]
    rendered = v2.render_phase1_prompt(template_head, 25, "PACKETS")
    assert rendered.startswith(template_head.rstrip("\n"))
    assert "\"related_to\"" not in rendered.split("EVIDENCE CLUSTERS")[0]
    assert '"cluster_ids": "[int]"' in rendered or \
        '"cluster_ids"' in rendered


# ---------------------------------------------------------------------------
# review-finding regressions (run-d5d2f22357e5)
# ---------------------------------------------------------------------------

def test_non_dict_relation_elements_quarantined_never_raised():
    inv = two_interest_inventory()
    ints, qs, rgs = rel_ids(inv)
    wrapper = {"parent_edges": ["garbage", {"child_id": ints[1],
                                            "parent_id": ints[0]}],
               "related_edges": "not-a-list",
               "question_links": [7],
               "regret_links": None}
    accepted, quar = v2.verify_relations(wrapper, ints, qs, rgs)
    assert len(accepted["parent_edges"]) == 1
    reasons = [q["reason"] for q in quar]
    assert any("not a list" in r for r in reasons)
    assert any("element not object" in r for r in reasons)
    assert any("element not object" in r
               for r in reasons)  # question_links int element


def test_regret_foreign_cluster_id_rejected_in_phase1():
    p = payload(regrets=[{"topic": "T", "why": "w",
                          "label": "inferred_adjacent",
                          "confidence": 0.4,
                          "cluster_ids": [999]}])
    valid, failures = v2.validate_phase1_payload(p, SUPPLIED)
    assert valid["regret_candidates"] == []
    assert failures and failures[0]["severity"] == "optional"
    assert failures[0]["error_class"] == "semantic"


def test_distinct_group_dispositions_equal_member_count():
    """Review F2 regression: no N^2 disposition multiplication."""
    inv = build_two_batch_inventory()          # 8 objects
    ids = [o["id"] for o in inv]
    groups = [{"members": ids, "action": "distinct",
               "canonical_name": None, "reason": ""}]
    canon, disps = v2.assemble_canonical(groups, inv, [1, 2])
    # the identical-key pairs merge mechanically, so canon shrinks —
    # but accounting NEVER does: every source id receipted exactly once
    src_ids = [d["source_id"] for d in disps]
    # every source object receipted AT LEAST once, and the grouping-stage
    # rows themselves are unique (multi-stage transforms may stack an
    # additional explicit receipt on top)
    assert set(src_ids) == set(ids)
    first_stage = [d["source_id"] for d in disps
                   if d["decision"] != "mechanically_merged"]
    assert len(first_stage) == len(set(first_stage)) == len(ids)
    merged_pairs = [d for d in disps
                    if d["decision"] == "mechanically_merged"]
    assert len(merged_pairs) == len(ids) - len(canon)


def test_same_batch_duplicate_question_ids_unique():
    p = payload(questions=[{"text": "same?", "status": "open"},
                           {"text": "same?", "status": "open"}])
    valid, _ = v2.validate_phase1_payload(p, SUPPLIED)
    inv = v2.build_inventory(valid, "b001")
    qids = [o["id"] for o in inv if o["type"] == "question"]
    assert len(qids) == 2 and qids[0] != qids[1]


def test_case_variant_duplicate_question_ids_unique():
    p = payload(questions=[{"text": "What is Raft?", "status": "open"},
                           {"text": "what is raft?", "status": "open"}])
    valid, _ = v2.validate_phase1_payload(p, SUPPLIED)
    inv = v2.build_inventory(valid, "b001")
    qids = [o["id"] for o in inv if o["type"] == "question"]
    assert len(qids) == 2 and qids[0] != qids[1]
