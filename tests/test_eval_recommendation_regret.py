"""Offline tests for RRE v1 amendment 1 (recommendation regret evaluator).

Everything runs offline on synthetic fixtures — no live corpus, no DB,
no judge transport. Covers the 16 regression tests mandated by
ARCHITECT_AMENDMENT_1_REVIEW_REPAIR plus the retained v1 behaviors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ef import eval_recommendation_regret as rre  # noqa: E402
import scripts.eval_recommendation_regret as cli  # noqa: E402

Q = "WOULD_REGRET_MISSING"
OTHER = "MORE_USEFUL_NOW"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(iid="v_alpha", claims="asserts X", why="goal link",
          refs=("ev_a",), rels=None, sim=None, title=None):
    return {
        "item_id": iid,
        "title": title or f"title-{iid}",
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
        pkg["blind_sidecar"], judgments, INV, extra=extra,
        binding_secret=pkg["binding_secret"])


def rec_for(pid, a, b, winner_arm, inv=INV, extra=None):
    """Pair record whose regret judgment prefers winner_arm."""
    pkg = rre.build_blinded_packet(pid, {"A": [a], "B": [b]})
    tok = pkg["blind_sidecar"]["reverse_map"][winner_arm]
    rec = rre.make_demo_pair_record(
        pid, a, b, pkg["blind_sidecar"],
        {Q: tok, OTHER: "TIE"}, inv, binding_secret=pkg["binding_secret"])
    if extra:
        rec.update(extra)
    return rec


def strict_win_records(n_challenger, n_baseline, counter_sim=False,
                       with_sim=False):
    """Decided records: first n_challenger prefer the challenger, the
    rest prefer the baseline. Similarity diagnostics only populated
    when with_sim (counter_sim gives the challenger LOW similarity)."""
    out = []
    total = max(n_challenger, n_baseline)
    for i in range(total):
        want_challenger = i < n_challenger
        item_b = _item(f"v_c{i}",
                       sim=((0.05 if counter_sim else 0.95)
                            if want_challenger and with_sim else
                            (0.50 if with_sim else None)))
        item_a = _item(f"v_a{i}", sim=(0.60 if with_sim else None))
        winner = "B" if want_challenger else "A"
        out.append(rec_for(f"pk_w{i}", item_a, item_b, winner))
    return out


# ---------------------------------------------------------------------------
# Mandated regression 1+2: n=5 yields NO generalization claim; the
# finite-set challenger preference stays reportable.
# ---------------------------------------------------------------------------


def test_n5_floor_met_but_no_generalization_vocabulary_emitted():
    rep = rre.evaluate(strict_win_records(5, 0),
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    blob = json.dumps(rep)
    assert "GENERALIZATION" not in blob
    assert "SUFFICIENT_EVIDENCE" not in blob
    assert "INSUFFICIENT_EVIDENCE" not in blob
    pq = rep["per_question"][Q]
    assert pq["diagnostic_coverage_status"]["status"] == \
        rre.DIAGNOSTIC_FLOOR_MET


def test_generalization_vocabulary_absent_from_module_and_cli_source():
    for src in (Path(rre.__file__).read_text(encoding="utf-8"),
                Path(cli.__file__).read_text(encoding="utf-8")):
        assert "GENERALIZATION" not in src
        assert "SUFFICIENT_EVIDENCE" not in src
        assert "INSUFFICIENT_EVIDENCE" not in src
        assert "strip_to_blinded_view" not in src


def test_finite_set_challenger_preference_reportable_at_n5():
    recs = strict_win_records(5, 0)
    agg = rre.aggregate_pair_results(recs)
    fin = rre.finite_set_outcome(agg, Q)
    assert fin["status"] == "CHALLENGER_PREFERRED"
    assert fin["exact_counts"]["B_strict_wins"] == 5
    assert fin["exact_counts"]["A_strict_wins"] == 0


# ---------------------------------------------------------------------------
# Mandated regression 3: similarity axis absent => UNTESTED, cannot pass.
# ---------------------------------------------------------------------------


def test_similarity_absent_is_untested_and_cannot_pass():
    rep = rre.evaluate(strict_win_records(5, 0),
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert rep["untested_axes"] == ["semantic_similarity_diagnostic"]
    assert rep["primary_falsifier_verdict"] == \
        "CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED"


def test_untested_similarity_flag_carried_in_falsifier_block():
    rep = rre.evaluate(strict_win_records(2, 0),
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    block = rep["per_question"][Q]["primary_falsifier"]
    assert block["similarity_axis_tested"] is False
    assert block["untested_axes"] == ["semantic_similarity_diagnostic"]
    assert block["verdict"] != "PASSED_PRIMARY_FALSIFIER"


def test_untested_axis_reported_without_diagnostics():
    recs = strict_win_records(2, 0)
    rep = rre.evaluate(recs,
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert "semantic_similarity_diagnostic" in rep["untested_axes"]


# ---------------------------------------------------------------------------
# Mandated regression 4+5: similarity diagnostics and machinery keys
# never reach a judge payload.
# ---------------------------------------------------------------------------


def test_similarity_diagnostics_never_reach_judge_payload():
    pkg = _packet(a=_item("s_a", sim=0.91), b=_item("s_b", sim=0.05))
    blob = json.dumps(pkg["blinded_packet"])
    assert "similarity_diagnostic" not in blob
    assert "0.91" not in blob and "0.05" not in blob


def test_judge_view_is_strictly_the_allowlist():
    full = _item()
    full.update({"score": 0.87, "rank_position": 3,
                 "ranking_policy": "mechanical-clusters-recency",
                 "ranking_policy_version": "7",
                 "experiment_id": "exp_1", "propensity": 0.5,
                 "policy_name": "goal-aware", "policy_version": "2",
                 "arm_id": "B", "raw_ranking_score": 0.99,
                 "surface_timestamp": "2026-08-27",
                 "evidence_inventory_available": True})
    view = rre.build_judge_item_view(full, "pk_allow")
    assert set(view.keys()) == set(rre.JUDGE_ITEM_VIEW_FIELDS)
    assert view["item_id"].startswith("RRE_J_")
    blob = json.dumps(view)
    for token in ("score", "rank_position", "ranking_policy",
                  "experiment_id", "propensity", "arm_id",
                  "policy_name", "policy_version",
                  "surface_timestamp", "evidence_inventory_available"):
        assert token not in blob, token


def test_judge_ids_are_opaque_and_arm_uncorrelated():
    a = _item("challenger_item_7", sim=0.1, title="neutral-a")
    b = _item("baseline_item_9", sim=0.9, title="neutral-b")
    pkg = rre.build_blinded_packet("pk_opaque", {"A": [a], "B": [b]})
    views = pkg["blinded_packet"]["items"]
    ids = [views["ITEM_1"]["item_id"], views["ITEM_2"]["item_id"]]
    for jid in ids:
        assert jid.startswith("RRE_J_")
        assert len(jid) == len("RRE_J_") + 16
    blob = json.dumps(pkg["blinded_packet"])
    assert "challenger_item_7" not in blob
    assert "baseline_item_9" not in blob
    # the same real item under both arms maps to the SAME judge id, so
    # duplicate-across-arms detection survives opaque-ization
    same = _item("dup_real")
    pkg2 = rre.build_blinded_packet("pk_dupj", {"A": [same],
                                                "B": [same]})
    v2 = pkg2["blinded_packet"]["items"]
    assert v2["ITEM_1"]["item_id"] == v2["ITEM_2"]["item_id"]
    assert rre.pair_kind(pkg2["blinded_packet"]) == \
        rre.PAIR_DUPLICATE_ACROSS_ARMS


def test_recent_and_relation_ids_are_opaque():
    pkg = rre.build_blinded_packet(
        "pk_recent", {"A": [_item("ra")], "B": [_item("rb")]},
        recent_surfaced_items=["challenger_seen_1"])
    blob = json.dumps(pkg["blinded_packet"])
    assert "challenger_seen_1" not in blob
    assert all(r.startswith("RRE_R_") for r in
               pkg["blinded_packet"]["recent_surfaced_items"])
    rel = _item("rr", rels=[{"kind": "goal", "id": "arm_b_goal"}])
    view = rre.build_judge_item_view(rel, "pk_recent")
    assert view["related_records"][0]["id"].startswith("RRE_REF_")
    assert "arm_b_goal" not in json.dumps(view)


def test_related_records_unknown_keys_fail_build_and_validate():
    bad = _item("rk", rels=[{"kind": "goal", "id": "g1",
                             "arm": "challenger_B"}])
    with pytest.raises(rre.PacketBindError):
        rre.build_judge_item_view(bad, "pk_rk")
    doc = rre.build_blinded_packet("pk_rk2", {"A": [_item("la")],
                                              "B": [_item("lb")]})
    doc["blinded_packet"]["items"]["ITEM_1"]["related_records"] = \
        [{"kind": "goal", "id": "g1", "treatment": "B"}]
    val = rre.validate_packet({"blinded_packet":
                               doc["blinded_packet"]}, INV)
    assert val["ok"] is False
    assert any("positive schema" in e for e in val["errors"])


def test_validator_recursively_blocks_nested_machinery_keys():
    doc = rre.build_blinded_packet("pk_leak", {"A": [_item("la")],
                                               "B": [_item("lb")]})
    # the sidecar never co-mingles with a judge-facing document; a
    # packet document that inlined one would fail validation below
    doc.pop("blind_sidecar")
    doc["blinded_packet"]["items"]["ITEM_1"]["related_records"] = \
        [{"kind": "goal", "id": "g1", "raw_score": 0.9}]
    doc["blinded_packet"]["items"]["ITEM_2"]["related_records"] = \
        [{"kind": "goal", "id": "g2", "judge_context":
          {"arm_id": "B", "propensity": 0.3}}]
    doc["wrapper_metadata"] = {"policy_name": "goal-aware-v1"}
    val = rre.validate_packet(doc, INV)
    assert val["ok"] is False
    blob = json.dumps(val["errors"])
    for token in ("raw_score", "arm_id", "propensity", "policy_name"):
        assert token in blob, token


def test_validator_flags_inlined_sidecar():
    doc = rre.build_blinded_packet("pk_comingled",
                                   {"A": [_item("la")],
                                    "B": [_item("lb")]})
    val = rre.validate_packet(doc, INV)
    assert val["ok"] is False
    assert any("blind_sidecar" in e for e in val["errors"])


@pytest.mark.parametrize("key", [
    "arm", "ARM", "Arm", "arm-id", "arm.id", "arm id",
    "ａｒｍ", "a\u200brm", "\u200barm\u200b",
    "ᴀʀᴍ", "a\ufe0frm", "ar\u180em", "ar\ufe05m",
    "treatment", "variant", "condition", "bucket",
    "policy", "score", "rank", "experiment", "propensity",
    "similarity_diagnostic", "sidecar", "arm_id", "raw_score",
])
def test_validator_blocks_normalized_machinery_key_variants(key):
    doc = rre.build_blinded_packet("pk_norm", {"A": [_item("la")],
                                               "B": [_item("lb")]})
    doc["blinded_packet"]["items"]["ITEM_1"][key] = "challenger_B"
    val = rre.validate_packet({"blinded_packet":
                               doc["blinded_packet"]}, INV)
    assert val["ok"] is False, key
    assert any("machinery key" in e for e in val["errors"]), key


def test_validator_blocks_nested_normalized_variants():
    doc = rre.build_blinded_packet("pk_norm2", {"A": [_item("la")],
                                                "B": [_item("lb")]})
    doc["blinded_packet"]["items"]["ITEM_1"]["related_records"] = \
        [{"kind": "goal", "id": "g1", "meta": {"Variant": "B"}}]
    doc["blinded_packet"]["items"]["ITEM_2"]["related_records"] = \
        [{"kind": "goal", "id": "g2", "bucket-x": 3}]
    val = rre.validate_packet({"blinded_packet":
                               doc["blinded_packet"]}, INV)
    assert val["ok"] is False
    blob = json.dumps(val["errors"])
    assert "Variant" in blob and "bucket-x" in blob


def test_validator_enforces_item_field_allowlist():
    doc = rre.build_blinded_packet("pk_allow", {"A": [_item("la")],
                                                "B": [_item("lb")]})
    doc["blinded_packet"]["items"]["ITEM_1"]["totally_new_field"] = "x"
    val = rre.validate_packet(doc["blinded_packet"], INV)
    assert val["ok"] is False
    assert any("allowlist" in e for e in val["errors"])


# ---------------------------------------------------------------------------
# Mandated regression 6: cmd_judge cannot bypass packet validation.
# ---------------------------------------------------------------------------


def _write_packet(tmp_path, doc):
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_cmd_judge_refuses_leaky_packet_before_any_transport(tmp_path,
                                                             monkeypatch):
    called = {"transport": False}

    def _forbidden_transport():
        called["transport"] = True
        return lambda prompt: '{"outcome": "ITEM_1"}'

    monkeypatch.setattr(cli, "_codex_transport", _forbidden_transport)
    doc = rre.build_blinded_packet("pk_bypass", {"A": [_item("la")],
                                                 "B": [_item("lb")]})
    doc["blinded_packet"]["items"]["ITEM_1"]["arm_id"] = "challenger_B"
    rc = cli.main(["judge", "--packet", str(_write_packet(tmp_path, doc)),
                   "--transport", "codex"])
    assert rc == 5
    assert called["transport"] is False


def test_cmd_judge_refuses_without_transport():
    doc = rre.build_blinded_packet("pk_nt", {"A": [_item("la")],
                                             "B": [_item("lb")]})
    rc = cli.main(["judge", "--packet",
                   str(Path("/nonexistent.json")), "--transport",
                   None]) if False else cli.main(
        ["judge", "--packet", "/nonexistent.json"])
    assert rc == 3


# ---------------------------------------------------------------------------
# Mandated regressions 7+8: sidecars are bound; wrong and same-layout
# sidecars both fail closed.
# ---------------------------------------------------------------------------


def _opposite_layout_pair():
    p1 = p2 = None
    for i in range(50):
        p = rre.build_blinded_packet(f"pk_m{i}", {"A": [_item("xa")],
                                                  "B": [_item("xb")]})
        m = p["blind_sidecar"]["reverse_map"]["A"]
        if m == "ITEM_1" and p1 is None:
            p1 = p
        elif m == "ITEM_2" and p2 is None:
            p2 = p
        if p1 and p2:
            return p1, p2
    raise AssertionError("no opposite-layout pair found")


def test_wrong_sidecar_rejected():
    p1, p2 = _opposite_layout_pair()
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(p1["blinded_packet"],
                          p2["blind_sidecar"],
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=p1["binding_secret"])


def test_same_layout_wrong_sidecar_rejected():
    # Different packets whose slot layouts HAPPEN to coincide must still
    # be rejected: the canonical packet hash is part of the binding.
    p1 = rre.build_blinded_packet("pk_layout_a",
                                  {"A": [_item("a1")],
                                   "B": [_item("b1")]})
    p2 = rre.build_blinded_packet("pk_layout_b",
                                  {"A": [_item("a2")],
                                   "B": [_item("b2")]})
    assert p1["blind_sidecar"]["reverse_map"] == \
        p2["blind_sidecar"]["reverse_map"]
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(p1["blinded_packet"],
                          p2["blind_sidecar"],
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=p1["binding_secret"])


def test_tampered_packet_rejected_by_binding():
    pkg = rre.build_blinded_packet("pk_tamper", {"A": [_item("a1")],
                                                 "B": [_item("b1")]})
    tampered = json.loads(json.dumps(pkg["blinded_packet"]))
    tampered["items"]["ITEM_1"]["title"] = "mutated title"
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(tampered, pkg["blind_sidecar"],
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


def test_sidecar_missing_binding_fields_rejected():
    pkg = rre.build_blinded_packet("pk_loose", {"A": [_item("a1")],
                                                "B": [_item("b1")]})
    legacy = {k: v for k, v in pkg["blind_sidecar"].items()
              if k not in ("blinded_packet_sha256", "binding_version",
                           "binding_mac")}
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], legacy,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


def test_unbind_requires_and_verifies_binding_secret():
    pkg = rre.build_blinded_packet("pk_sec", {"A": [_item("sa")],
                                              "B": [_item("sb")]})
    j = {Q: "ITEM_1", OTHER: "TIE"}
    with pytest.raises(TypeError):
        rre.unbind_result(pkg["blinded_packet"], pkg["blind_sidecar"], j)
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], pkg["blind_sidecar"],
                          j, binding_secret="cd" * 32)
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], pkg["blind_sidecar"],
                          j, binding_secret="abcd")
    # the correct secret unbinds cleanly
    rre.unbind_result(pkg["blinded_packet"], pkg["blind_sidecar"], j,
                      binding_secret=pkg["binding_secret"])


def test_non_ascii_binding_mac_is_controlled_error():
    pkg = rre.build_blinded_packet("pk_macu", {"A": [_item("ua")],
                                               "B": [_item("ub")]})
    sc = json.loads(json.dumps(pkg["blind_sidecar"]))
    sc["binding_mac"] = "méchant-mac"
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], sc,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


def test_relabel_forgery_rejected_by_binding_mac():
    """Round-3 blocker: rename arms (A,B -> Z,W), re-derive the map
    with the public salt, recompute the unkeyed digest. The HMAC still
    fails — the forger has no binding secret."""
    pkg = rre.build_blinded_packet("pk_relabel", {"A": [_item("ra")],
                                                  "B": [_item("rb")]})
    forge = json.loads(json.dumps(pkg["blind_sidecar"]))
    forge["arms"] = ["W", "Z"]
    expected = rre.canonical_binding("pk_relabel", ["W", "Z"],
                                     "rre_v1_pairwise")
    forge["slots"] = expected["slots"]
    forge["reverse_map"] = expected["reverse_map"]
    forge["blinded_packet_sha256"] = rre._binding_digest(
        pkg["blinded_packet"], expected["arms"],
        expected["reverse_map"])
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], forge,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


# ---------------------------------------------------------------------------
# Mandated regression 9: listwise unbinding is fully mechanical.
# ---------------------------------------------------------------------------


def _listwise_bundle():
    l_a = [_item(f"a{i}") for i in range(3)]
    l_b = [_item(f"b{i}") for i in range(3)]
    pkg = rre.build_blinded_list_packet("lpk1", {"A": l_a, "B": l_b},
                                        k=3)
    return pkg, l_a, l_b


def test_listwise_unbinding_mechanical_both_layouts():
    pkg, l_a, l_b = _listwise_bundle()
    rev = pkg["blind_sidecar"]["reverse_map"]
    for outcome_token, expected_arm in (
            ("LIST_1", rev and [a for a, t in rev.items()
                                if t == "LIST_1"][0]),
            ("LIST_2", [a for a, t in rev.items()
                        if t == "LIST_2"][0])):
        rec = rre.unbind_list_result(
            pkg["blinded_packet"], pkg["blind_sidecar"],
            {"outcome": outcome_token,
             "regret_marks": {"LIST_1": [], "LIST_2": []}},
            binding_secret=pkg["binding_secret"])
        assert rec["preferred_list"] == expected_arm
    rec = rre.unbind_list_result(
        pkg["blinded_packet"], pkg["blind_sidecar"],
        {"outcome": "TIE",
         "regret_marks": {"LIST_1": [], "LIST_2": []}},
        binding_secret=pkg["binding_secret"])
    assert rec["preferred_list"] == "TIE"


def test_listwise_unbind_wrong_sidecar_rejected():
    pkg, _, _ = _listwise_bundle()
    other, _, _ = _listwise_bundle()
    other_pkg = rre.build_blinded_list_packet(
        "lpk_other", {"A": [_item("x")], "B": [_item("y")]}, k=1)
    with pytest.raises(rre.PacketBindError):
        rre.unbind_list_result(pkg["blinded_packet"],
                               other_pkg["blind_sidecar"],
                               {"outcome": "LIST_1",
                                "regret_marks": {"LIST_1": [],
                                                 "LIST_2": []}},
                               binding_secret=pkg["binding_secret"])


def test_listwise_unbind_rejects_foreign_marks_and_bad_vocab():
    pkg, _, _ = _listwise_bundle()
    with pytest.raises(rre.PacketBindError):
        rre.unbind_list_result(
            pkg["blinded_packet"], pkg["blind_sidecar"],
            {"outcome": "LIST_1",
             "regret_marks": {"LIST_1": ["ghost_id"], "LIST_2": []}},
            binding_secret=pkg["binding_secret"])
    with pytest.raises(rre.PacketBindError):
        rre.unbind_list_result(
            pkg["blinded_packet"], pkg["blind_sidecar"],
            {"outcome": "LIST_7",
             "regret_marks": {"LIST_1": [], "LIST_2": []}},
            binding_secret=pkg["binding_secret"])


def test_build_and_account_listwise_via_mechanical_unbind():
    pkg, l_a, l_b = _listwise_bundle()
    rev = pkg["blind_sidecar"]["reverse_map"]
    marks = {"LIST_1": [pkg["blinded_packet"]["lists"]["LIST_1"][0]
                        ["item_id"]],
             "LIST_2": []}
    rec = rre.unbind_list_result(pkg["blinded_packet"],
                                 pkg["blind_sidecar"],
                                 {"outcome": "LIST_1",
                                  "regret_marks": marks},
                                 binding_secret=pkg["binding_secret"])
    rec.update({"list_ids_by_arm": rec["arms"],
                "challenger_arm": "B", "baseline_arm": "A",
                "prov_valid_all": True})
    agg = rre.aggregate_list_results([rec])
    winner_is_challenger = rec["preferred_list"] == "B"
    assert agg["counts"]["challenger_list_wins"] == \
        (1 if winner_is_challenger else 0)
    assert agg["regret_marked_items_challenger"] == \
        (1 if winner_is_challenger else 0)
    assert agg["regret_marked_items_baseline"] == \
        (0 if winner_is_challenger else 1)
    assert agg["mean_overlap_at_k"] == 0.0
    assert rev  # sidecar mapping existed and drove the mapping


# ---------------------------------------------------------------------------
# Mandated regressions 10-13: counting integrity.
# ---------------------------------------------------------------------------


def test_duplicate_packet_cannot_increase_distinct_count():
    recs = strict_win_records(5, 0)
    dup = json.loads(json.dumps(recs[0]))
    agg = rre.aggregate_pair_results(recs + [dup])
    q = agg[Q]
    assert q["counts"]["duplicate_records_deduplicated"] == 1
    assert q["n_decided_strict_conformance"] == 5
    assert q["n_distinct_decided_packets"] == 5
    floor = rre.diagnostic_floor_status(agg, Q)
    assert floor["status"] == rre.DIAGNOSTIC_FLOOR_MET


def test_conflicting_duplicate_packet_id_fails_closed():
    recs = strict_win_records(2, 0)
    conflicting = json.loads(json.dumps(recs[0]))
    # same packet_id, different content (baseline preferred instead of
    # the challenger) — the aggregator must refuse, not average
    conflicting["judgments"][Q]["preferred_arm"] = "A"
    conflicting["judgments"][Q]["slot_outcome"] = "ITEM_9"
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_pair_results(recs + [conflicting])


def test_provisional_packets_cannot_satisfy_coverage_floor():
    # 5 provisional pairs across 5 distinct packets contribute ZERO
    # eligible decided packets: the floor must stay NOT MET.
    prov = [rec_for(f"pk_p{i}", _item(f"pa{i}"), _item(f"pb{i}"), "A",
                    inv=None) for i in range(5)]
    agg = rre.aggregate_pair_results(prov)
    q = agg[Q]
    assert q["counts"]["provisional_excluded"] == 5
    assert q["n_decided_strict_conformance"] == 0
    assert q["n_distinct_decided_packets"] == 0
    floor = rre.diagnostic_floor_status(agg, Q)
    assert floor["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
    # 4 decided packets + any number of provisional packets: still NOT MET
    decided = strict_win_records(4, 0)
    agg2 = rre.aggregate_pair_results(decided + prov)
    floor2 = rre.diagnostic_floor_status(agg2, Q)
    assert floor2["n_distinct_decided_packets"] == 4
    assert floor2["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET


def test_floor_requires_both_thresholds():
    five = strict_win_records(5, 0)
    rep_full = rre.evaluate(
        five, judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    floor_full = rep_full["per_question"][Q]["diagnostic_coverage_status"]
    assert floor_full["status"] == rre.DIAGNOSTIC_FLOOR_MET
    # decided floor: 4 eligible decided pairs across 4 packets -> NOT MET
    four = strict_win_records(4, 0)
    agg_four = rre.aggregate_pair_results(four)
    floor_four = rre.diagnostic_floor_status(agg_four, Q)
    assert floor_four["n_decided_strict_conformance"] == 4
    assert floor_four["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
    # distinct-decided-packet floor: a duplicate-across-arms packet is
    # not an eligible decided packet and cannot pad the count
    same = _item()
    dup_pkg = _packet("pk_dupkind", a=same, b=same)
    dup_rec = _record(dup_pkg, {Q: "ITEM_1", OTHER: "TIE"},
                      item_a=same, item_b=same, pid="pk_dupkind")
    agg_pad = rre.aggregate_pair_results(five + [dup_rec])
    floor_pad = rre.diagnostic_floor_status(agg_pad, Q)
    assert floor_pad["n_decided_strict_conformance"] == 5
    assert floor_pad["n_distinct_decided_packets"] == 5
    assert floor_pad["status"] == rre.DIAGNOSTIC_FLOOR_MET


def test_falsifier_requires_floor():
    # 5-0 sweep, counter-sim win present, axis tested — but only 4
    # distinct decided packets (one id duplicated): floor NOT MET, so
    # the falsifier cannot pass.
    recs = strict_win_records(5, 0, counter_sim=True, with_sim=True)
    recs[4]["packet_id"] = recs[0]["packet_id"]
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_pair_results(recs)
    recs[4]["judgments"] = recs[4]["judgments"]  # keep identity; rebuild instead
    recs2 = strict_win_records(5, 0, counter_sim=True, with_sim=True)
    recs2[4] = json.loads(json.dumps(recs2[3]))
    agg = rre.aggregate_pair_results(recs2)
    floor = rre.diagnostic_floor_status(agg, Q)
    fin = rre.finite_set_outcome(agg, Q)
    verdict = rre.primary_falsifier_verdict(fin, floor, agg[Q])
    assert floor["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
    assert verdict["verdict"] == "CHALLENGER_WINS_FINITE_SET"


def test_repeated_regret_mark_cannot_double_count():
    pkg, _, _ = _listwise_bundle()
    target = pkg["blinded_packet"]["lists"]["LIST_1"][0]["item_id"]
    rec = rre.unbind_list_result(
        pkg["blinded_packet"], pkg["blind_sidecar"],
        {"outcome": "TIE",
         "regret_marks": {"LIST_1": [target, target, target],
                          "LIST_2": []}},
        binding_secret=pkg["binding_secret"])
    rev = pkg["blind_sidecar"]["reverse_map"]
    marked_arm = [a for a, t in rev.items() if t == "LIST_1"][0]
    assert rec["regret_marks_by_arm"][marked_arm] == [target]
    rec.update({"list_ids_by_arm": rec["arms"],
                "challenger_arm": "B", "baseline_arm": "A",
                "prov_valid_all": True})
    agg = rre.aggregate_list_results([rec])
    total = (agg["regret_marked_items_challenger"]
             + agg["regret_marked_items_baseline"])
    assert total == 1


# ---------------------------------------------------------------------------
# Landing-review round 1 repairs (run-f9f644ffc144): listwise allowlist,
# dedup double-subtraction, required evidence class, sidecar-honoring
# demo record, bare-packet judge path.
# ---------------------------------------------------------------------------


def test_listwise_judge_views_enforce_allowlist():
    pkg = rre.build_blinded_list_packet("lpk_allow",
                                        {"A": [_item("la")],
                                         "B": [_item("lb")]}, k=1)
    doc = {"blinded_packet": pkg["blinded_packet"]}
    assert rre.validate_packet(doc, INV)["ok"] is True
    leak = json.loads(json.dumps(doc))
    leak["blinded_packet"]["lists"]["LIST_1"][0]["arm"] = "challenger_B"
    val = rre.validate_packet(leak, INV)
    assert val["ok"] is False
    assert any("allowlist" in e for e in val["errors"])


def test_exact_duplicates_do_not_corrupt_finite_set_status():
    recs = strict_win_records(3, 0)
    dup_recs = [json.loads(json.dumps(r)) for r in recs]
    agg = rre.aggregate_pair_results(recs + dup_recs)
    q = agg[Q]
    assert q["counts"]["pairs_total"] == 3
    assert q["counts"]["duplicate_records_deduplicated"] == 3
    assert q["counts"]["B_strict_wins"] == 3
    fin = rre.finite_set_outcome(agg, Q)
    assert fin["status"] == "CHALLENGER_PREFERRED"


def test_evaluate_requires_evidence_class_kwarg():
    with pytest.raises(TypeError):
        rre.evaluate(strict_win_records(1, 0))


def test_make_demo_pair_record_honors_passed_sidecar():
    pkg = _packet("pk_sc")
    other = rre.build_blinded_packet("pk_other", {"A": [_item("oa")],
                                                  "B": [_item("ob")]})
    with pytest.raises(rre.PacketBindError):
        rre.make_demo_pair_record(
            "pk_sc", _item(), _item("v_beta", sim=0.9),
            other["blind_sidecar"], {Q: "ITEM_1", OTHER: "TIE"}, INV,
            binding_secret=pkg["binding_secret"])


def test_cmd_judge_accepts_bare_packet_document(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_codex_transport", lambda: (
        lambda prompt: '{"outcome": "ITEM_1"}'))
    pkg = rre.build_blinded_packet("pk_bare", {"A": [_item("la")],
                                               "B": [_item("lb")]})
    rc = cli.main(["judge", "--packet",
                   str(_write_packet(tmp_path,
                                     {"blinded_packet":
                                      pkg["blinded_packet"]})),
                   "--transport", "codex"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Mandated regression 14: evidence class travels with every result.
# ---------------------------------------------------------------------------


def test_evidence_class_present_in_emitted_report():
    rep = rre.evaluate(strict_win_records(1, 0),
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert rep["evidence_class"] == "blinded_curated_judgment"
    for q, block in rep["per_question"].items():
        assert block["evidence_class"] == "blinded_curated_judgment"
    assert rep["per_question"][Q]["primary_falsifier"]["evidence_class"] \
        == "blinded_curated_judgment"
    assert rep["amendment"] == \
        "ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING"


def test_evaluate_refuses_undeclared_or_unknown_evidence_class():
    with pytest.raises(rre.PacketBindError):
        rre.evaluate(strict_win_records(1, 0),
                     judgments_evidence_class="operator_confirmed_truth")
    with pytest.raises(rre.PacketBindError):
        rre.evaluate(strict_win_records(1, 0),
                     judgments_evidence_class="")


# ---------------------------------------------------------------------------
# Mandated regression 15+16: parser decoys; garbage judge output.
# ---------------------------------------------------------------------------


def test_parser_rejects_contradictory_and_decoy_outcomes():
    assert rre.parse_outcome('{"outcome": "ITEM_1"}') == "ITEM_1"
    assert rre.parse_outcome('{"outcome": "ITEM_1"} '
                             '{"outcome": "ITEM_1"}') == "ITEM_1"
    # contradictory tokens: no decision can be invented
    assert rre.parse_outcome('{"outcome": "ITEM_1"} '
                             '{"outcome": "ITEM_2"}') is None
    # decoy AFTER the final answer must not silently win
    assert rre.parse_outcome('{"outcome": "TIE"} '
                             'final note {"outcome": "ITEM_2"}') is None
    assert rre.parse_outcome('{"outcome": "ARM_B"}') is None
    assert rre.parse_outcome("") is None
    assert rre.parse_outcome(None) is None


def test_cmd_judge_fails_nonzero_on_garbage_output(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_codex_transport",
                        lambda: (lambda prompt: "the judge rambled "
                                "without any JSON at all"))
    pkg = rre.build_blinded_packet("pk_garbage", {"A": [_item("la")],
                                                  "B": [_item("lb")]})
    rc = cli.main(["judge", "--packet",
                   str(_write_packet(tmp_path,
                                     {"blinded_packet":
                                      pkg["blinded_packet"]})),
                   "--transport", "codex"])
    assert rc == 6


def test_cmd_evaluate_requires_evidence_class(tmp_path):
    recs = strict_win_records(1, 0)
    p = tmp_path / "recs.json"
    p.write_text(json.dumps(recs), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["evaluate", "--results", str(p)])
    rc = cli.main(["evaluate", "--results", str(p), "--evidence-class",
                   "blinded_curated_judgment"])
    assert rc == 0


def test_cmd_validate_reports_inventory_supplied(tmp_path, capsys):
    pkg = rre.build_blinded_packet("pk_val", {"A": [_item("la")],
                                              "B": [_item("lb")]})
    doc = {"blinded_packet": pkg["blinded_packet"]}
    p = _write_packet(tmp_path, doc)
    rc = cli.main(["validate", "--packet", str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["inventory_supplied"] is False
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps(INV), encoding="utf-8")
    rc2 = cli.main(["validate", "--packet", str(p),
                    "--evidence-inventory", str(inv)])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and out2["inventory_supplied"] is True
    assert out2["provenance_states"] == {
        "ITEM_1": "VALID", "ITEM_2": "VALID"}


# ---------------------------------------------------------------------------
# Amendment 2: sidecar map re-derivation (R5) + reviewer falsifier.
# ---------------------------------------------------------------------------


def _flip(sidecar):
    sc = json.loads(json.dumps(sidecar))
    swap = {"ITEM_1": "ITEM_2", "ITEM_2": "ITEM_1",
            "LIST_1": "LIST_2", "LIST_2": "LIST_1"}
    sc["reverse_map"] = {a: swap[t]
                         for a, t in sidecar["reverse_map"].items()}
    return sc


def test_flipped_reverse_map_rejected_by_rederivation():
    pkg = rre.build_blinded_packet("pk_flip", {"A": [_item("fa")],
                                               "B": [_item("fb")]})
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"],
                          _flip(pkg["blind_sidecar"]),
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


def test_same_layout_flipped_reverse_map_rejected():
    # two packets whose layouts coincide: the flipped map of one must
    # still be rejected on the other (re-derivation + digest)
    p1 = p2 = None
    for i in range(50):
        p = rre.build_blinded_packet(f"pk_sl{i}", {"A": [_item("xa")],
                                                   "B": [_item("xb")]})
        if p["blind_sidecar"]["reverse_map"]["A"] == "ITEM_1" and p1 is None:
            p1 = p
        elif p2 is None:
            p2 = p
        if p1 and p2:
            break
    for victim in (p1, p2):
        flipped = _flip(victim["blind_sidecar"])
        with pytest.raises(rre.PacketBindError):
            rre.unbind_result(victim["blinded_packet"], flipped,
                              {Q: "ITEM_1", OTHER: "TIE"},
                              binding_secret=victim["binding_secret"])


def test_fantasy_and_three_arm_maps_rejected():
    pkg = rre.build_blinded_packet("pk_fan", {"A": [_item("fa")],
                                              "B": [_item("fb")]})
    for arms in (["A", "B", "C"], ["A", "A"], ["A"], [], [["x"], "y"]):
        sc = json.loads(json.dumps(pkg["blind_sidecar"]))
        sc["arms"] = arms
        with pytest.raises(rre.PacketBindError):
            rre.unbind_result(pkg["blinded_packet"], sc,
                              {Q: "ITEM_1", OTHER: "TIE"},
                              binding_secret=pkg["binding_secret"])


def test_colliding_duplicate_slots_rejected():
    pkg = rre.build_blinded_packet("pk_col", {"A": [_item("ca")],
                                              "B": [_item("cb")]})
    sc = json.loads(json.dumps(pkg["blind_sidecar"]))
    sc["slots"] = {"0": "A", "1": "A"}
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], sc,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])
    sc2 = json.loads(json.dumps(pkg["blind_sidecar"]))
    sc2["reverse_map"] = {"A": "ITEM_1", "B": "ITEM_1"}
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], sc2,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])
    sc3 = json.loads(json.dumps(pkg["blind_sidecar"]))
    sc3["slots"] = {"0": ["A"], "1": "B"}
    with pytest.raises(rre.PacketBindError):
        rre.unbind_result(pkg["blinded_packet"], sc3,
                          {Q: "ITEM_1", OTHER: "TIE"},
                          binding_secret=pkg["binding_secret"])


def test_listwise_map_rederivation_rejects_tampering():
    pkg, _, _ = _listwise_bundle()
    sc = _flip(pkg["blind_sidecar"])
    with pytest.raises(rre.PacketBindError):
        rre.unbind_list_result(pkg["blinded_packet"], sc,
                               {"outcome": "LIST_1",
                                "regret_marks": {"LIST_1": [],
                                                 "LIST_2": []}},
                               binding_secret=pkg["binding_secret"])


def test_reviewer_falsifier_baseline_sweep_cannot_be_inverted():
    """The demonstrated round-2 attack: 5/5 BASELINE-preferred packets,
    attribution flipped by sidecar tampering. Every packet's unbind
    must fail closed, so no challenger-preferred report can exist."""
    baseline_wins = [rec_for(f"pk_inv{i}", _item(f"ia{i}"),
                             _item(f"ic{i}"), "A") for i in range(5)]
    clean = rre.evaluate(
        baseline_wins,
        judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert clean["per_question"][Q]["finite_set_outcome"]["status"] \
        == "BASELINE_PREFERRED"
    flipped = 0
    for i, rec in enumerate(baseline_wins):
        pkg = rre.build_blinded_packet(rec["packet_id"],
                                       {"A": [_item(f"ia{i}")],
                                        "B": [_item(f"ic{i}")]})
        try:
            rre.unbind_result(pkg["blinded_packet"],
                              _flip(pkg["blind_sidecar"]),
                              {Q: "ITEM_1", OTHER: "TIE"},
                              binding_secret=pkg["binding_secret"])
        except rre.PacketBindError:
            flipped += 1
    assert flipped == 5


# ---------------------------------------------------------------------------
# Amendment 2: hygiene (packet_id, malformed types, missing judgment,
# junk preferred_arm, dimensions note).
# ---------------------------------------------------------------------------


def test_packet_id_required_and_validated():
    for bad in (None, "", "   "):
        with pytest.raises(rre.PacketBindError):
            rre.build_blinded_packet(bad, {"A": [_item("pa")],
                                           "B": [_item("pb")]})
        with pytest.raises(rre.PacketBindError):
            rre.build_blinded_list_packet(bad, {"A": [_item("pa")],
                                                "B": [_item("pb")]})
    doc = rre.build_blinded_packet("pk_pid", {"A": [_item("pa")],
                                              "B": [_item("pb")]})
    doc["blinded_packet"].pop("packet_id")
    val = rre.validate_packet({"blinded_packet":
                               doc["blinded_packet"]}, INV)
    assert val["ok"] is False
    assert any("packet_id" in e for e in val["errors"])


def test_malformed_field_types_fail_with_controlled_errors():
    doc = rre.build_blinded_packet("pk_types", {"A": [_item("ta")],
                                                "B": [_item("tb")]})
    pk = doc["blinded_packet"]
    pk["items"]["ITEM_1"]["claims"] = 42
    pk["items"]["ITEM_1"]["evidence_refs"] = "ev_a"
    pk["items"]["ITEM_2"]["related_records"] = ["not-an-object"]
    val = rre.validate_packet({"blinded_packet": pk}, INV)
    assert val["ok"] is False
    blob = json.dumps(val["errors"])
    for fragment in ("must be a", "must be an", "objects with keys"):
        assert fragment in blob, fragment
    bare = rre.build_blinded_packet("pk_types2", {"A": [_item("ta")],
                                                  "B": [_item("tb")]})
    bare["blinded_packet"]["items"] = ["not", "a", "dict"]
    val2 = rre.validate_packet({"blinded_packet":
                                bare["blinded_packet"]}, INV)
    assert val2["ok"] is False
    assert any("JSON object" in e for e in val2["errors"])


def test_missing_judgment_cannot_become_neither():
    rec = rec_for("pk_mj", _item("mja"), _item("mjb"), "A")
    rec["judgments"].pop(Q)
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_pair_results([rec])


def test_junk_preferred_arm_fails_closed():
    rec = rec_for("pk_jp", _item("jpa"), _item("jpb"), "A")
    rec["judgments"][Q]["preferred_arm"] = "MOSTLY_A"
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_pair_results([rec])
    rec2 = rec_for("pk_jp2", _item("jp2a"), _item("jp2b"), "A")
    rec2["judgments"][Q]["preferred_arm"] = "C"
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_pair_results([rec2])


def test_dimensions_described_as_diagnostic_only_in_report():
    rep = rre.evaluate(strict_win_records(1, 0),
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert "DIAGNOSTIC-ONLY" in rep["judgment_dimensions"]


# ---------------------------------------------------------------------------
# Retained v1 behaviors (adapted to amendment-1 APIs).
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
    ids = [rre.assign_slots(f"pk_{i}", ["A", "B"])["reverse_map"]["A"]
           for i in range(20)]
    assert set(ids) == {"ITEM_1", "ITEM_2"}


def test_blinding_requires_exactly_two_arms():
    with pytest.raises(rre.PacketBindError):
        rre.assign_slots("pk", ["A"])
    with pytest.raises(rre.PacketBindError):
        rre.assign_slots("pk", ["A", "A"])


def test_sidecar_binds_packet_id_hash_and_version():
    pkg = _packet()
    sc = pkg["blind_sidecar"]
    assert sc["packet_id"] == "pk_test"
    assert sc["binding_version"] == rre.SIDECAR_BINDING_VERSION
    assert sc["blinded_packet_sha256"] == rre._binding_digest(
        pkg["blinded_packet"], sc["arms"], sc["reverse_map"])


def test_build_packet_two_items_only_and_duplicate_detection():
    same = _item()
    pkg = _packet(a=same, b=same)
    assert rre.pair_kind(pkg["blinded_packet"]) == \
        rre.PAIR_DUPLICATE_ACROSS_ARMS
    with pytest.raises(rre.PacketBindError):
        rre.build_blinded_packet("pk3", {"A": []})


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
    assert rre.validate_item_provenance(_item(), None) == \
        "UNRESOLVED_EVIDENCE_REF"
    assert rre.validate_item_provenance(_item(), {}) == \
        "UNRESOLVED_EVIDENCE_REF"


def test_validate_packet_detects_leaked_field():
    doc = json.loads(json.dumps(_packet()))
    doc["blinded_packet"]["items"]["ITEM_1"]["score"] = 0.5
    val = rre.validate_packet(doc, INV)
    assert val["ok"] is False
    assert any("score" in e for e in val["errors"])


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


def test_unbind_maps_preference_to_correct_arm_both_orders():
    for judgments in ({Q: "ITEM_1", OTHER: "ITEM_2"},
                      {Q: "ITEM_2", OTHER: "ITEM_1"}):
        pkg = _packet()
        rec = _record(pkg, judgments)
        slots = pkg["blind_sidecar"]["slots"]
        j = rec["judgments"][Q]
        expected_arm = slots["0"] if judgments[Q] == "ITEM_1" \
            else slots["1"]
        assert j["preferred_arm"] == expected_arm


def test_unbind_preserves_tie_neither_and_rejects_bad_vocab():
    pkg = _packet()
    rec = _record(pkg, {Q: "TIE", OTHER: "NEITHER"})
    assert rec["judgments"][Q]["preferred_arm"] == "TIE"
    assert rec["judgments"][OTHER]["preferred_arm"] == "NEITHER"
    with pytest.raises(rre.PacketBindError):
        _record(_packet(), {Q: "MOSTLY_A", OTHER: "TIE"})


def test_aggregate_counts_exact_and_dead_heat():
    def rec(pid, slot):
        return _record(_packet(pid), {Q: slot, OTHER: "TIE"},
                       pid=pid)
    agg = rre.aggregate_pair_results([rec("pk_i1", "ITEM_1"),
                                      rec("pk_i2", "ITEM_2"),
                                      rec("pk_t1", "TIE"),
                                      rec("pk_n1", "NEITHER")])
    c = agg[Q]["counts"]
    assert c["A_strict_wins"] + c["B_strict_wins"] == 2
    assert c["ties"] == 1 and c["neither"] == 1
    assert agg[Q]["n_decided_strict_conformance"] == 2
    fin = rre.finite_set_outcome(agg, Q)
    assert fin["status"] == "DEAD_HEAT"


def test_duplicate_pairs_excluded_from_decided_denominators():
    same = _item()
    pkg = _packet(a=same, b=same)
    rec = _record(pkg, {Q: "ITEM_1", OTHER: "ITEM_1"},
                  item_a=same, item_b=same)
    agg = rre.aggregate_pair_results([rec])
    q = agg[Q]
    assert q["counts"]["duplicate_across_arms"] == 1
    assert q["n_decided_strict_conformance"] == 0
    assert rre.finite_set_outcome(agg, Q)["status"] == "NOT_EVALUABLE"


def test_provisional_pairs_never_count_toward_gate():
    weak = _item()
    strong = _item("v_good")
    pkg = _packet("pk_prov", a=weak, b=strong)
    rec = rre.make_demo_pair_record(
        "pk_prov", weak, strong, pkg["blind_sidecar"],
        {Q: "ITEM_1", OTHER: "TIE"}, evidence_inventory=None,
        binding_secret=pkg["binding_secret"])
    agg = rre.aggregate_pair_results([rec])
    q = agg[Q]
    assert q["counts"]["provisional_excluded"] == 1
    assert q["n_decided_strict_conformance"] == 0
    rep = rre.evaluate([rec],
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    pq = rep["per_question"][Q]
    assert pq["finite_set_outcome"]["status"] == "NOT_EVALUABLE"
    assert rep["primary_falsifier_verdict"] == "NOT_EVALUABLE"


def test_decision_inertia_all_ties_is_real_outcome():
    rec = _record(_packet(), {Q: "TIE", OTHER: "TIE"})
    rep = rre.evaluate([rec],
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    pq = rep["per_question"][Q]
    assert pq["finite_set_outcome"]["status"] == "DECISION_INERTIA"


def test_small_n_dual_output_exact_but_floor_not_met():
    recs = strict_win_records(4, 0)
    rep = rre.evaluate(recs,
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    pq = rep["per_question"][Q]
    fin = pq["finite_set_outcome"]
    floor = pq["diagnostic_coverage_status"]
    assert fin["status"] == "CHALLENGER_PREFERRED"
    assert floor["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
    assert floor["n_decided_strict_conformance"] == 4
    assert rep["primary_falsifier_verdict"] == \
        "CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED"


def test_passed_falsifier_needs_all_conjuncts():
    five = strict_win_records(5, 0, counter_sim=True, with_sim=True)
    rep = rre.evaluate(five,
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert rep["primary_falsifier_verdict"] == "PASSED_PRIMARY_FALSIFIER"
    block = rep["per_question"][Q]["primary_falsifier"]
    assert block["scope"].startswith("finite_set_only")
    assert block["similarity_axis_tested"] is True
    assert block["counter_similarity_wins_challenger"] >= 1


def test_similarity_confounded_when_all_wins_higher_similarity():
    recs = []
    for i in range(5):
        item_a = _item(f"v_a{i}", sim=0.30)
        item_b = _item(f"v_c{i}", sim=0.90)
        recs.append(rec_for(f"pk_s{i}", item_a, item_b, "B",
                            extra={"similarity_diagnostic":
                                   {"A": 0.30, "B": 0.90}}))
    rep = rre.evaluate(recs,
                       judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    assert rep["primary_falsifier_verdict"] == \
        "SIMILARITY_CONFOUNDED_NOT_PASSING"


def test_counter_similarity_win_defeats_confounding_claim():
    a = _item("v_aX", sim=0.80)
    b = _item("v_cX", sim=0.05)
    c_rec = rec_for("pk_c0", a, b, "B",
                    extra={"similarity_diagnostic": {"A": 0.80,
                                                     "B": 0.05}})
    agg = rre.aggregate_pair_results([c_rec])
    assert agg[Q]["counter_similarity_wins_challenger"] == 1
    assert agg[Q]["similarity_axis_tested"] is True


def test_feedback_roles_unchanged():
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
    with pytest.raises(rre.OffPolicyError):
        rre.refuse_offline_reward_estimate(anything=True)


def test_offpolicy_contract_preserved():
    cls = rre.classify_feedback_event({"verdict": "clicked_lots"})
    assert cls["role"] == rre.ROLE_UNKNOWN
    ev = {"verdict": "useful", "impression_id": "imp_1",
          "annotations": [{"exclude_from_evaluation": True}]}
    cls = rre.classify_feedback_event(ev)
    assert cls["excluded"] is True and cls["role"] == rre.ROLE_EXCLUSION
    s = rre.feedback_role_summary([{"verdict": "useful",
                                   "impression_id": "imp_9"}])
    assert s["propensity"] == "UNKNOWN"
    assert s["evidence_class"] == \
        rre.EVIDENCE_CLASS_UNKNOWN_PROPENSITY
    assert "never an unbiased" in s["note"]


def test_four_quadrant_profiles_distinct():
    labels = {
        rre.classify_quadrant({"consequence": "HIGH",
                               "novelty_to_user": "HIGH",
                               "redundancy": "LOW",
                               "known_already": "LOW"}),
        rre.classify_quadrant({"consequence": "HIGH",
                               "novelty_to_user": "LOW",
                               "known_already": "HIGH",
                               "redundancy": "LOW"}),
        rre.classify_quadrant({"consequence": "LOW",
                               "actionability": "LOW",
                               "novelty_to_user": "HIGH",
                               "redundancy": "LOW",
                               "known_already": "LOW"}),
        rre.classify_quadrant({"redundancy": "HIGH"}),
    }
    assert len(labels) == 4
    assert rre.classify_quadrant({}) == rre.QUADRANT_MIXED_UNCLASSIFIED


def test_report_has_no_combined_relevance_scalar():
    report = rre.evaluate(strict_win_records(1, 0),
                          judgments_evidence_class=rre.EVIDENCE_CLASS_CURATED)
    blob = json.dumps(report)
    for banned in ("utility_score", "combined_score",
                   "total_relevance", "weighted_sum"):
        assert banned not in blob


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
    assert rre.parse_listwise_result(json.dumps({"outcome": "TIE"})) \
        is None
    bad_vocab = json.dumps({"outcome": "LIST_7",
                            "regret_marks": {"LIST_1": [],
                                             "LIST_2": []}})
    assert rre.parse_listwise_result(bad_vocab) is None


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


def test_listwise_duplicate_record_dedup_and_conflict():
    pkg, _, _ = _listwise_bundle()
    rec = rre.unbind_list_result(
        pkg["blinded_packet"], pkg["blind_sidecar"],
        {"outcome": "TIE",
         "regret_marks": {"LIST_1": [], "LIST_2": []}},
        binding_secret=pkg["binding_secret"])
    rec.update({"list_ids_by_arm": rec["arms"],
                "challenger_arm": "B", "baseline_arm": "A",
                "prov_valid_all": True})
    agg = rre.aggregate_list_results([rec, json.loads(json.dumps(rec))])
    assert agg["counts"]["duplicate_records_deduplicated"] == 1
    conflicting = json.loads(json.dumps(rec))
    conflicting["preferred_list"] = "B"
    with pytest.raises(rre.PacketBindError):
        rre.aggregate_list_results([rec, conflicting])


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


def test_builtin_fixtures_all_marked_synthetic():
    for it in rre.fixture_items():
        assert it["marker_synthetic"] is True
        assert it["item_id"].startswith("fixture_")


def test_demo_runs_offline_and_declares_synthetic(capsys):
    rc = cli.main(["demo"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["evidence_class"] == "synthetic_fixture"
    report = out["report"]
    assert report["evidence_class"] == "synthetic_fixture"
    assert "GENERALIZATION" not in json.dumps(report)
