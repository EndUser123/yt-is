"""Offline tests for RRE v1 (recommendation regret evaluator).

Everything runs offline on synthetic fixtures — no live corpus, no DB,
no judge transport. Mirrors the test style of
tests/test_eval_interest_semantic.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ef import eval_recommendation_regret as rre


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(iid="v_alpha", claims="asserts X", why="goal link",
          refs=("ev_a",), rels=None, sim=None):
    return {
        "item_id": iid,
        "title": f"title-{iid}",
        "claims": claims,
        "why_surfaced": why,
        "evidence_refs": list(refs),
        "related_records": rels or [],
        "similarity_diagnostic": sim,
        "marker_synthetic": True,
    }


INV = {"ev_a": "supporting excerpt", "ev_b": "other excerpt"}


def _packet(pid="pk_test", a=None, b=None, recent=None):
    return rre.build_blinded_packet(
        pid, {"A": [a or _item()], "B": [b or _item("v_beta", sim=0.9)]},
        recent_surfaced_items=recent or [])


def _record(pkg, judgments, pid="pk_test", item_a=None, item_b=None,
            extra=None):
    return rre.make_demo_pair_record(
        pid, item_a or _item(), item_b or _item("v_beta", sim=0.9),
        pkg["blind_sidecar"], judgments, INV, extra=extra)


# ---------------------------------------------------------------------------
# Blinding
# ---------------------------------------------------------------------------


def test_blinding_deterministic_and_order_insensitive():
    m1 = rre.assign_slots("pk_x", ["ArmBaseline",
                                   "ArmChallenger"])["reverse_map"]
    for _ in range(5):
        m2 = rre.assign_slots("pk_x",
                              ["ArmChallenger",
                               "ArmBaseline"])["reverse_map"]
        assert m1 == m2
    assert set(m1.values()) == {"ITEM_1", "ITEM_2"}


def test_blinding_differs_across_packet_ids():
    ids = [rre.assign_slots(f"pk_{i}",
                            ["A", "B"])["reverse_map"]["A"]
           for i in range(20)]
    # fixed salt but different packet ids: both slots must occur
    assert set(ids) == {"ITEM_1", "ITEM_2"}


def test_blinding_requires_exactly_two_arms():
    with pytest.raises(rre.PacketBindError):
        rre.assign_slots("pk", ["A"])
    with pytest.raises(rre.PacketBindError):
        rre.assign_slots("pk", ["A", "A"])


def test_blinded_view_strips_machinery_fields():
    full = _item()
    full.update({"score": 0.87, "rank_position": 3,
                 "ranking_policy": "mechanical-clusters-recency",
                 "experiment_id": "exp_1", "propensity": 0.5})
    view = rre.strip_to_blinded_view(full)
    for k in ("score", "rank_position", "ranking_policy",
              "experiment_id", "propensity"):
        assert k not in view
    assert view["claims"] == "asserts X"
    # projection cannot carry machinery keys by construction
    blob = json.dumps(view)
    assert '"score"' not in blob and '"arm_id"' not in blob


def test_build_packet_two_items_only_and_duplicate_detection():
    same = _item()
    pkg = _packet(a=same, b=same)
    assert rre.pair_kind(pkg["blinded_packet"]) == \
        rre.PAIR_DUPLICATE_ACROSS_ARMS
    with pytest.raises(rre.PacketBindError):
        rre.build_blinded_packet("pk3", {"A": []})


# ---------------------------------------------------------------------------
# Packet validation / provenance
# ---------------------------------------------------------------------------


def test_validate_packet_ok_on_fixture():
    val = rre.validate_packet(_packet()["blinded_packet"], INV)
    assert val["ok"] is True
    assert val["provenance_states"] == {"ITEM_1": rre.PROV_VALID,
                                        "ITEM_2": rre.PROV_VALID}


@pytest.mark.parametrize("field,value,state", [
    ("claims", "", "MISSING_CLAIMS"),
    ("why_surfaced", "", "MISSING_WHY_SURFACED"),
    ("evidence_refs", [], "NO_EVIDENCE_REFS"),
])
def test_provenance_fail_closed(field, value, state):
    it = _item()
    it[field] = value
    assert rre.validate_item_provenance(it, INV) == state


def test_provenance_unresolved_refs_not_valid():
    assert rre.validate_item_provenance(_item(refs=("ghost",)),
                                        INV) == \
        "UNRESOLVED_EVIDENCE_REF"
    # no inventory supplied -> unverifiable is still not VALID
    assert rre.validate_item_provenance(_item(), None) == \
        "UNRESOLVED_EVIDENCE_REF"


def test_validate_packet_detects_leaked_field():
    doc = json.loads(json.dumps(_packet()))
    doc["blinded_packet"]["items"]["ITEM_1"]["score"] = 0.5
    val = rre.validate_packet(doc, INV)
    assert any("score" in e for e in val["errors"])


# ---------------------------------------------------------------------------
# Outcome / rating parsing (judge errors never invent decisions)
# ---------------------------------------------------------------------------


def test_parse_outcome_vocabulary_enforced():
    assert rre.parse_outcome('{"outcome": "ITEM_2"}') == "ITEM_2"
    assert rre.parse_outcome('{"outcome": "TIE"}') == "TIE"
    assert rre.parse_outcome('junk {"outcome": "NEITHER"} trailing') == \
        "NEITHER"
    for bad in ('{"outcome": "ARM_B"}', '{"result":"ITEM_1"}', "",
                "{broken", '{"outcome": "tie"}'):
        assert rre.parse_outcome(bad) is None


def test_parse_dimension_ratings_strict_grid():
    ratings = {d: "HIGH" for d in rre.DIMENSION_NAMES}
    raw = json.dumps({"ITEM_1": ratings, "ITEM_2": ratings})
    got = rre.parse_dimension_ratings(raw)
    assert got["ITEM_2"]["known_already"] == "HIGH"
    short = dict(list(ratings.items())[:-1])
    assert rre.parse_dimension_ratings(
        json.dumps({"ITEM_1": short, "ITEM_2": ratings})) is None
    assert rre.parse_dimension_ratings("no json") is None


# ---------------------------------------------------------------------------
# Unblinding
# ---------------------------------------------------------------------------


def test_unbind_maps_preference_to_correct_arm_both_orders():
    for judgments in ({"WOULD_REGRET_MISSING": "ITEM_1",
                       "MORE_USEFUL_NOW": "ITEM_2"},
                      {"WOULD_REGRET_MISSING": "ITEM_2",
                       "MORE_USEFUL_NOW": "ITEM_1"}):
        pkg = _packet()
        rec = _record(pkg, judgments)
        slots = pkg["blind_sidecar"]["slots"]
        j = rec["judgments"]["WOULD_REGRET_MISSING"]
        expected_arm = slots["0"] if \
            judgments["WOULD_REGRET_MISSING"] == "ITEM_1" else slots["1"]
        assert j["preferred_arm"] == expected_arm


def test_unbind_preserves_tie_neither_and_rejects_bad_vocab():
    pkg = _packet()
    rec = _record(pkg, {"WOULD_REGRET_MISSING": "TIE",
                        "MORE_USEFUL_NOW": "NEITHER"})
    assert rec["judgments"]["WOULD_REGRET_MISSING"]["preferred_arm"] == \
        "TIE"
    assert rec["judgments"]["MORE_USEFUL_NOW"]["preferred_arm"] == \
        "NEITHER"
    with pytest.raises(rre.PacketBindError):
        _record(_packet(), {"WOULD_REGRET_MISSING": "MOSTLY_A"})


# ---------------------------------------------------------------------------
# Exact accounting: exclusions are visible, ties stay real outcomes
# ---------------------------------------------------------------------------


def test_aggregate_counts_exact_and_dead_heat():
    def rec(j):
        return _record(_packet(f"pk_{j}"), {
            "WOULD_REGRET_MISSING": j, "MORE_USEFUL_NOW": "TIE"})
    agg = rre.aggregate_pair_results([rec("ITEM_1"), rec("ITEM_2"),
                                      rec("TIE"), rec("NEITHER")])
    c = agg["WOULD_REGRET_MISSING"]["counts"]
    # slot->arm mapping differs per packet_id; strict totals are stable
    assert c["A_strict_wins"] + c["B_strict_wins"] == 2
    assert c["ties"] == 1 and c["neither"] == 1
    assert agg["WOULD_REGRET_MISSING"]["n_decided_strict_conformance"] \
        == 2
    fin = rre.finite_set_outcome(agg, "WOULD_REGRET_MISSING")
    assert fin["status"] == "DEAD_HEAT"


def test_duplicate_pairs_excluded_from_decided_denominators():
    same = _item()
    pkg = _packet(a=same, b=same)
    try:
        rec = _record(pkg, {"WOULD_REGRET_MISSING": "ITEM_1",
                            "MORE_USEFUL_NOW": "ITEM_1"},
                      item_a=same, item_b=same)
    except rre.PacketBindError:
        pytest.skip("demo record requires distinct fixture items")
    agg = rre.aggregate_pair_results([rec])
    q = agg["WOULD_REGRET_MISSING"]
    assert q["counts"]["duplicate_across_arms"] == 1
    assert q["n_decided_strict_conformance"] == 0
    assert rre.finite_set_outcome(agg, "WOULD_REGRET_MISSING")[
        "status"] == "NOT_EVALUABLE"


def test_provisional_pairs_never_count_toward_gate():
    weak = _item()                      # unresolved refs under NO inv
    strong = _item("v_good")
    pkg = _packet(a=weak, b=strong)
    rec = rre.make_demo_pair_record(
        "pk_prov", weak, strong, pkg["blind_sidecar"],
        {"WOULD_REGRET_MISSING": "ITEM_1", "MORE_USEFUL_NOW": "TIE"},
        evidence_inventory=None)   # unresolved -> provisional pair
    agg = rre.aggregate_pair_results([rec])
    q = agg["WOULD_REGRET_MISSING"]
    assert q["counts"]["provisional_excluded"] == 1
    assert q["n_decided_strict_conformance"] == 0
    rep = rre.evaluate([rec])
    pq = rep["per_question"]["WOULD_REGRET_MISSING"]
    assert pq["finite_set_outcome"]["status"] == "NOT_EVALUABLE"
    assert rep["primary_falsifier_verdict"] == "NOT_EVALUABLE"


def test_decision_inertia_all_ties_is_real_outcome():
    rec = _record(_packet(), {"WOULD_REGRET_MISSING": "TIE",
                              "MORE_USEFUL_NOW": "TIE"})
    rep = rre.evaluate([rec])
    pq = rep["per_question"]["WOULD_REGRET_MISSING"]
    assert pq["finite_set_outcome"]["status"] == "DECISION_INERTIA"


# ---------------------------------------------------------------------------
# Small-n dual output (ISEM lesson)
# ---------------------------------------------------------------------------


def _strict_win_records(n_challenger, n_baseline,
                        counter_sim=False, with_sim=False):
    """Build decided records: first n_challenger judgments prefer the
    challenger arm, later ones prefer baseline. Similarity diagnostics
    only populate when with_sim (counter_sim gives the challenger LOW
    similarity so its wins are counter-similarity wins)."""
    out = []
    total = max(n_challenger, n_baseline)
    for i in range(total):
        want_challenger = i < n_challenger
        item_b = _item(f"v_c{i}",
                       sim=((0.05 if counter_sim else 0.95)
                            if want_challenger and with_sim else
                            (0.50 if with_sim else None)))
        item_a = _item(f"v_a{i}", sim=(0.60 if with_sim else None))
        pkg = _packet(f"pk_w{i}", a=item_a, b=item_b)
        rev = pkg["blind_sidecar"]["reverse_map"]
        winner_arm = "B" if want_challenger else "A"
        j = {"WOULD_REGRET_MISSING": rev[winner_arm],
             "MORE_USEFUL_NOW": "TIE"}
        rec = rre.make_demo_pair_record(
            f"pk_w{i}", item_a, item_b, pkg["blind_sidecar"], j, INV)
        out.append(rec)
    return out


def test_small_n_dual_output_exact_but_insufficient():
    recs = _strict_win_records(n_challenger=4, n_baseline=0)
    rep = rre.evaluate(recs)
    pq = rep["per_question"]["WOULD_REGRET_MISSING"]
    fin = pq["finite_set_outcome"]
    gen = pq["generalization_status"]
    assert fin["status"] == "CHALLENGER_PREFERRED"
    assert gen["status"] == "INSUFFICIENT_EVIDENCE"
    assert gen["n_decided_strict_conformance"] == 4
    assert rep["primary_falsifier_verdict"] == \
        "CHALLENGER_WINS_FINITE_SET"


def test_generalization_requires_both_thresholds():
    five = _strict_win_records(n_challenger=5, n_baseline=0)
    # 5 decided pairs but only 5 packets -> sufficient when both met
    rep_full = rre.evaluate(five)
    gen_full = rep_full["per_question"][
        "WOULD_REGRET_MISSING"]["generalization_status"]
    assert gen_full["status"] == "SUFFICIENT_EVIDENCE"
    # same 5 pairs collapsed into fewer distinct packets ->
    # distinct-packet threshold fails
    few_packets = []
    for i, rec in enumerate(five):
        rec = dict(rec)
        rec["packet_id"] = "shared_pk"
        few_packets.append(rec)
    rep_squash = rre.evaluate(few_packets)
    gen_sq = rep_squash["per_question"][
        "WOULD_REGRET_MISSING"]["generalization_status"]
    assert gen_sq["n_distinct_packets"] == 1
    assert gen_sq["status"] == "INSUFFICIENT_EVIDENCE"


def test_passed_falsifier_needs_finite_win_and_sufficiency():
    five = _strict_win_records(n_challenger=5, n_baseline=0,
                               counter_sim=True, with_sim=True)
    rep = rre.evaluate(five)
    assert rep["primary_falsifier_verdict"] == \
        "PASSED_PRIMARY_FALSIFIER"


# ---------------------------------------------------------------------------
# Primary falsifier: semantic-similarity alone does not pass
# ---------------------------------------------------------------------------


def test_similarity_confounded_when_all_wins_higher_similarity():
    recs = []
    for i in range(4):
        item_a = _item(f"v_a{i}", sim=0.30)
        item_b = _item(f"v_c{i}", sim=0.90)   # challenger only 'wins'
        pkg = _packet(f"pk_s{i}", a=item_a, b=item_b)  # by similarity
        j = {"WOULD_REGRET_MISSING":
             pkg["blind_sidecar"]["reverse_map"]["B"],
             "MORE_USEFUL_NOW": "TIE"}
        recs.append(rre.make_demo_pair_record(
            f"pk_s{i}", item_a, item_b, pkg["blind_sidecar"], j, INV,
            extra={"similarity_diagnostic":
                   {"A": 0.30, "B": 0.90}}))
    rep = rre.evaluate(recs)
    assert rep["primary_falsifier_verdict"] == \
        "SIMILARITY_CONFOUNDED_NOT_PASSING"


def test_counter_similarity_win_defeats_confounding_claim():
    recs = [
        _strict_win_records(1, 0, counter_sim=True,
                            with_sim=True)[0],
        # plus one sim-supported win: mixed pattern still passes gate
    ]
    item_a = _item("v_aX", sim=0.40)
    item_b = _item("v_cX", sim=0.60)
    pkg = _packet("pk_mix", a=item_a, b=item_b)
    recs.append(rre.make_demo_pair_record(
        "pk_mix", item_a, item_b, pkg["blind_sidecar"],
        {"WOULD_REGRET_MISSING":
         pkg["blind_sidecar"]["reverse_map"]["B"],
         "MORE_USEFUL_NOW": "TIE"}, INV,
        extra={"similarity_diagnostic": {"A": 0.40, "B": 0.60}}))
    fin = rre.finite_set_outcome(
        rre.aggregate_pair_results(recs), "WOULD_REGRET_MISSING")
    agg = rre.aggregate_pair_results(recs)["WOULD_REGRET_MISSING"]
    assert fin["counter_similarity_wins_challenger"] >= 1
    verdict = rre.primary_falsifier_verdict(
        fin, rre.generalization_status(rre.aggregate_pair_results(recs),
                                       "WOULD_REGRET_MISSING", recs),
        agg)
    assert verdict["verdict"] != "SIMILARITY_CONFOUNDED_NOT_PASSING"


def test_untested_axis_reported_without_diagnostics():
    recs = _strict_win_records(2, 0)
    rep = rre.evaluate(recs)
    assert "semantic_similarity_diagnostic" in rep["untested_axes"]


# ---------------------------------------------------------------------------
# Feedback roles (frozen contract; descriptive only)
# ---------------------------------------------------------------------------


def test_all_nine_feedback_verdicts_bound_to_roles():
    expected = {
        "useful": rre.ROLE_DIRECT_OUTCOME,
        "acted_on": rre.ROLE_DIRECT_OUTCOME,
        "not_interested": rre.ROLE_DIRECT_OUTCOME,
        "save": rre.ROLE_WEAK_PROXY,
        "investigate": rre.ROLE_WEAK_PROXY,
        "more_like": rre.ROLE_WEAK_PROXY,
        "less_like": rre.ROLE_WEAK_PROXY,
        "known_already": rre.ROLE_EXCLUSION,
        "wrong_inference": rre.ROLE_EXCLUSION,
    }
    for verdict, role in expected.items():
        cls = rre.classify_feedback_event({"verdict": verdict,
                                           "impression_id": None})
        assert cls["role"] == role, verdict


def test_unknown_feedback_verdict_fails_closed():
    cls = rre.classify_feedback_event({"verdict": "clicked_lots"})
    assert cls["role"] == rre.ROLE_UNKNOWN


def test_annotation_exclusion_overrides_role():
    ev = {"verdict": "useful", "impression_id": "imp_1",
          "annotations": [{"exclude_from_evaluation": True}]}
    cls = rre.classify_feedback_event(ev)
    assert cls["excluded"] is True
    assert cls["role"] == rre.ROLE_EXCLUSION


def test_historical_events_tagged_unknown_propensity_descriptive_only():
    s = rre.feedback_role_summary([{"verdict": "useful",
                                    "impression_id": "imp_9"}])
    assert s["propensity"] == "UNKNOWN"
    assert s["evidence_class"] == \
        rre.EVIDENCE_CLASS_UNKNOWN_PROPENSITY
    assert "never an unbiased" in s["note"]


def test_offline_reward_estimate_structurally_refused():
    with pytest.raises(rre.OffPolicyError):
        rre.refuse_offline_reward_estimate(anything=True)


# ---------------------------------------------------------------------------
# Quadrants: known/novel/redundant profiles never collapse to one score
# ---------------------------------------------------------------------------


def test_four_quadrant_profiles_distinct():
    important_novel = rre.classify_quadrant({
        "consequence": "HIGH", "novelty_to_user": "HIGH",
        "redundancy": "LOW", "known_already": "LOW"})
    useful_known = rre.classify_quadrant({
        "consequence": "HIGH", "novelty_to_user": "LOW",
        "known_already": "HIGH", "redundancy": "LOW"})
    novel_low_value = rre.classify_quadrant({
        "consequence": "LOW", "actionability": "LOW",
        "novelty_to_user": "HIGH", "redundancy": "LOW",
        "known_already": "LOW"})
    redundant = rre.classify_quadrant({"redundancy": "HIGH"})
    labels = {important_novel, useful_known, novel_low_value, redundant}
    assert len(labels) == 4
    assert rre.QUADRANT_REDUNDANT_RECENT in labels
    # precedence: redundancy beats importance+novelty
    assert rre.classify_quadrant({
        "redundancy": "HIGH", "consequence": "HIGH",
        "novelty_to_user": "HIGH"}) == rre.QUADRANT_REDUNDANT_RECENT
    assert rre.classify_quadrant({}) == \
        rre.QUADRANT_MIXED_UNCLASSIFIED


def test_report_has_no_combined_relevance_scalar():
    recs = _strict_win_records(1, 0)
    report = rre.evaluate(recs)
    blob = json.dumps(report)
    for banned in ("utility_score", "combined_score",
                   "total_relevance", "weighted_sum"):
        assert banned not in blob


# ---------------------------------------------------------------------------
# Listwise / top-k support
# ---------------------------------------------------------------------------


def test_overlap_at_k_exact_and_edge_cases():
    assert rre.overlap_at_k(["a", "b", "c"], ["b", "x", "a"]) == \
        pytest.approx(2 / 3)
    assert rre.overlap_at_k(["a"], ["a"]) == 1.0
    assert rre.overlap_at_k([], ["a"]) is None
    assert rre.overlap_at_k(["a", "b"], ["a"]) is None


def test_listwise_prompt_parse_gates():
    good = json.dumps({"outcome": "LIST_1",
                       "regret_marks": {"LIST_1": ["v1"],
                                        "LIST_2": []}})
    assert rre.parse_listwise_result(good)["outcome"] == "LIST_1"
    bad_missing_marks = json.dumps({"outcome": "TIE"})
    assert rre.parse_listwise_result(bad_missing_marks) is None
    bad_vocab = json.dumps({"outcome": "LIST_7",
                            "regret_marks": {"LIST_1": [], "LIST_2": []}})
    assert rre.parse_listwise_result(bad_vocab) is None


def test_build_and_account_listwise():
    l_a = [_item(f"a{i}") for i in range(3)]
    l_b = [_item(f"b{i}") for i in range(3)]
    pkg = rre.build_blinded_list_packet("lpk1", {"A": l_a, "B": l_b},
                                        k=3)
    assert pkg["blinded_packet"]["packet_schema"] == "rre_v1_listwise"
    rev = pkg["blind_sidecar"]["reverse_map"]
    rec = {
        "packet_id": "lpk1",
        "preferred_list": "B",      # post-unbind arm id
        "regret_marks_by_arm": {"A": ["a0"], "B": ["b1"]},
        "list_ids_by_arm": {},
        "challenger_arm": "B", "baseline_arm": "A",
        "prov_valid_all": True,
    }
    for arm in ("A", "B"):
        rec["list_ids_by_arm"][arm] = [
            r["item_id"]
            for r in pkg["blinded_packet"]["lists"][rev[arm]]]
    agg = rre.aggregate_list_results([rec])
    assert agg["counts"]["challenger_list_wins"] == 1
    assert agg["regret_marked_items_challenger"] == 1
    assert agg["regret_marked_items_baseline"] == 1
    assert agg["mean_overlap_at_k"] == 0.0


def test_listwise_provisional_lists_excluded():
    rec = {"preferred_list": "A",
           "regret_marks_by_arm": {"A": [], "B": []},
           "list_ids_by_arm": {"A": ["x"], "B": ["y"]},
           "challenger_arm": "B", "baseline_arm": "A",
           "prov_valid_all": False}
    agg = rre.aggregate_list_results([rec])
    assert agg["counts"]["provisional_excluded"] == 1
    assert agg["counts"]["lists_total"] == 1
    assert agg["strict_preference_rate_challenger"] is None


# ---------------------------------------------------------------------------
# Freeze manifest verification
# ---------------------------------------------------------------------------


def test_verify_manifest_ok_then_drift(tmp_path):
    root = tmp_path
    f = root / "artifact.txt"
    payload = b"deterministic bytes"
    f.write_bytes(payload)
    receipt = root / "FREEZE_RECEIPT.json"
    receipt.write_text(json.dumps({"frozen_artifacts": [{
        "path": "artifact.txt",
        "sha256": __import__("hashlib").sha256(payload).hexdigest(),
    }]}), encoding="utf-8")
    assert rre.verify_manifest(root, receipt) == []
    f.write_bytes(b"mutated")
    drift = rre.verify_manifest(root, receipt)
    assert len(drift) == 1 and "drift" in drift[0]


def test_verify_manifest_missing_artifact(tmp_path):
    root = tmp_path
    receipt = root / "FREEZE_RECEIPT.json"
    receipt.write_text(json.dumps({"frozen_artifacts": [{
        "path": "ghost.txt", "sha256": "00"}]}), encoding="utf-8")
    assert any("missing" in d for d in rre.verify_manifest(root,
                                                           receipt))


# ---------------------------------------------------------------------------
# Fixtures stay synthetic-marked
# ---------------------------------------------------------------------------


def test_builtin_fixtures_all_marked_synthetic():
    for it in rre.fixture_items():
        assert it["marker_synthetic"] is True
        assert it["item_id"].startswith("fixture_")
