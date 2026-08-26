"""evaluator-v3 specific tests (packet 2026-08-25)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "evaluate_concept_discovery",
    REPO / "scripts" / "evaluate_concept_discovery.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

_spec2 = importlib.util.spec_from_file_location(
    "test_evaluate_concept_discovery",
    REPO / "tests" / "test_evaluate_concept_discovery.py")
base = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(base)


@pytest.fixture(scope="module")
def receipt(tmp_path_factory):
    d = tmp_path_factory.mktemp("freeze-v3")
    return ev.build_freeze_receipt(d)


# --- versioning + policy pinning -----------------------------------------

def test_evaluator_version_is_v3():
    assert ev.EVALUATOR_VERSION == "retrospective-evaluator-v3"
    assert ev.ARTIFACT_SCHEMA_VERSION == "concept-discovery-eval-v3"
    assert ev.TARGET_POLICY_VERSION == "burst-policy-v2"


def test_freeze_receipt_pins_target_policy(receipt):
    assert receipt["target_policy_version"] == "burst-policy-v2"
    assert receipt["target_policy_param_sha256"]
    assert receipt["numerical_method"]
    assert receipt["python_version"] and receipt["numpy_version"] and \
        receipt["scipy_version"]
    assert "ef/burst_policy_v2.py" in receipt["production_file_sha256"]


def test_receipt_without_policy_pin_rejected(tmp_path):
    tampered = json.loads(json.dumps(receipt_dict(tmp_path)))
    del tampered["target_policy_version"]
    p = tmp_path / "no-pin.json"
    p.write_text(json.dumps(tampered))
    with pytest.raises(ev.FreezeError):
        ev.run_evaluation(p, base.write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


def test_drifted_v2_params_rejected(tmp_path):
    from ef import burst_policy_v2 as bp2
    tampered = json.loads(json.dumps(receipt_dict(tmp_path)))
    tampered["target_policy_param_sha256"] = "0" * 64
    p = tmp_path / "params.json"
    p.write_text(json.dumps(tampered))
    with pytest.raises(ev.FreezeError, match="parameters drifted"):
        ev.run_evaluation(p, base.write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


def test_default_production_policy_cannot_alter_formal_result(
        receipt, tmp_path, monkeypatch):
    """Formal result depends on the PINNED policy, not the default."""
    import ef.concept_discovery as cd
    calls = []
    real = cd.scan_internal

    def spy(conn, catalog_path=None, as_of=None, run_id=None,
            policy_version=None):
        calls.append(policy_version)
        return real(conn, catalog_path=catalog_path, as_of=as_of,
                    policy_version=policy_version)

    monkeypatch.setattr(cd, "scan_internal", spy)
    cat = base.build_catalog(tmp_path / "cat.sqlite")
    try:
        ev.run_evaluation(receipt_path(tmp_path), base.write_targets(
            tmp_path / "t.json", [
                {"target_id": "flux",
                 "canonical_name": "Fluxcapacitor Runtime"}]),
            tmp_path / "out", label="NON_BLIND_DIAGNOSTIC",
            catalog_path=cat, skip_perturbation=True)
    except ev.HoldoutConsumedError:
        pass
    assert calls and all(c == "burst-policy-v2" for c in calls if c)


_receipt_cache = {}


def receipt_dict(tmp_path):
    if "r" not in _receipt_cache:
        _receipt_cache["r"] = ev.build_freeze_receipt(tmp_path / "fr")
    return _receipt_cache["r"]


def receipt_path(tmp_path):
    r_path = tmp_path / "receipt.json"
    r_path.write_text(json.dumps(receipt_dict(tmp_path)))
    return r_path


# --- negative controls v3 --------------------------------------------------

def test_controls_are_entity_only(receipt, tmp_path):
    cat = base.build_catalog(tmp_path / "cat.sqlite")
    # inject a topic_cluster concept source: evidence clusters table empty
    # in the synthetic catalog, so controls can only be entity concepts;
    # verify the policy structurally
    assert ev.NEGATIVE_CONTROL_POLICY["version"] == "negctl-v2"
    assert "concept_type='entity'" in \
        ev.NEGATIVE_CONTROL_POLICY["select_from"]
    # structural: select_negative_controls receives entity-filtered rows
    rows = [{"concept_id": "c1", "evidence_count": 10,
             "concept_type": "entity"},
            {"concept_id": "c2", "evidence_count": 10,
             "concept_type": "topic_cluster"}]
    picked = ev.select_negative_controls({"evidence_count": 10}, rows, set())
    assert "c2" not in picked


def test_control_selection_uses_t30_state_only():
    # selection happens before later replays by construction in
    # _run_evaluation_body (controls chosen at cp_label == "T-30");
    # structural check: no outcome field is consulted
    rows = [{"concept_id": f"c{i}", "evidence_count": 5,
             "concept_type": "entity"} for i in range(6)]
    picked = ev.select_negative_controls({"evidence_count": 5}, rows, set())
    assert picked == ["c0", "c1", "c2"]


# --- baselines v3 ----------------------------------------------------------

def test_baselines_aligned_same_units():
    rows = [
        {"kind": "target", "A": True, "B": False, "emerging": True},
        {"kind": "target", "A": True, "B": False, "emerging": False},
        {"kind": "control", "A": False, "B": False, "emerging": False},
        {"kind": "control", "A": False, "B": False, "emerging": False},
    ]
    cmp = ev._compare_baselines(rows)
    assert cmp["policy_target_rate"] == 0.5
    assert cmp["policy_control_rate"] == 0.0
    assert cmp["policy_separation"] == 0.5
    assert cmp["baseline_A_separation"] == 1.0
    assert cmp["policy_beats_baselines"] is False  # 0.5 < 1.0
    assert cmp["baseline_B_separation"] == 0.0


def test_baseline_definitions_unchanged():
    assert ev.BASELINE_POLICIES["A"]["emerging_if"] == "recent_count >= 6"
    assert "recent_count >= 4" in ev.BASELINE_POLICIES["B"]["emerging_if"]


# --- verdict / gate / Wilson unchanged --------------------------------------

def test_verdict_thresholds_unchanged():
    assert ev.VERDICT_RULES["PASS"]["candidate_recall_scorable_min"] == 0.7
    assert ev.VERDICT_RULES["PASS"]["emerging_recall_scorable_min"] == 0.5
    assert ev.VERDICT_RULES["PASS"][
        "matched_negative_emerging_rate_max"] == 0.2
    assert ev.VERDICT_RULES["PASS"]["perturbation20_retention_min"] == 0.5
    assert ev.VERDICT_RULES["FAIL"]["or_matched_negative_emerging_rate_min"] \
        == 0.5
    assert ev.VERDICT_RULES["FAIL"]["or_perturbation20_retention_max"] == 0.3
    assert ev.VERDICT_RULES["INSUFFICIENT_EVIDENCE"][
        "min_scorable_targets"] == 20
    assert ev.VERDICT_RULES["INSUFFICIENT_EVIDENCE"][
        "min_matched_negative_controls"] == 40
    assert ev.VERDICT_RULES["INSUFFICIENT_EVIDENCE"][
        "min_negatives_per_target"] == 2.0


def test_single_use_semantics_unchanged():
    assert ev.SINGLE_USE_POLICY["version"] == "single-use-holdout-v1"
    assert ev.FORMAL_LABEL == "FORMAL"
    assert "NON_BLIND_DIAGNOSTIC" in ev.SINGLE_USE_POLICY["non_formal"]


def test_wilson_unchanged():
    w = ev.wilson_interval(0, 10)
    assert w["lo"] == 0.0 and w["hi"] < 0.5


# --- ledger guards ----------------------------------------------------------

def test_labels_not_parsed_before_formal_claim(tmp_path, monkeypatch):
    """FORMAL claims the holdout by hash BEFORE load_targets parses
    labels (structural: claim precedes load in run_evaluation)."""
    src = Path(ev.__file__).read_text(encoding="utf-8")
    i_claim = src.index("claim_formal_holdout(")
    i_load = src.index("targets = load_targets(")
    assert i_claim < i_load


def test_v2_receipt_rejected_by_v3(tmp_path):
    # a receipt claiming evaluator-v2 is not valid for evaluator-v3
    old = json.loads(json.dumps(receipt_dict(tmp_path)))
    old["evaluator_version"] = "retrospective-evaluator-v2"
    old["evaluator_file_sha256"] = "0" * 64
    p = tmp_path / "old.json"
    p.write_text(json.dumps(old))
    with pytest.raises(ev.FreezeError):
        ev.run_evaluation(p, base.write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


def test_perturbation_prefix_is_stateful():
    assert "stateful_prefix" in ev.PERTURBATION_POLICY
    assert "T-30" in ev.PERTURBATION_POLICY["stateful_prefix"]
    assert ev.PERTURBATION_POLICY["legacy_metric"].startswith(
        "candidate retained at T+30")


def test_candidate_perturbation_metric_preserved():
    # aggregate_metrics still consumes retained_20 as candidate retention
    cps = [{"target_id": "a", "checkpoints": [
        {"checkpoint": "T", "matched": [], "candidates_total": 0,
         "emerging_total": 0}]}]
    negs = [{"emerging_by_T60": False}] * 4
    pert = [{"retained_10": True, "retained_20": True}] * 2
    agg = ev.aggregate_metrics(cps, negs, pert, 1)
    assert agg["perturbation20_retention"] == 1.0
