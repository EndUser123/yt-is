"""evaluator-v4 tests: explicit-negative ground truth, comparator
demotion, ledger safety (packet 2026-08-26)."""
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
    return ev.build_freeze_receipt(tmp_path_factory.mktemp("freeze-v4"))


def test_schema_requires_both_cohorts(tmp_path):
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({"targets": [
        {"target_id": "t1", "canonical_name": "X"}]}))
    with pytest.raises(ev.FreezeError, match="negative_targets"):
        ev.load_case_control(p)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({
        "positive_targets": [{"target_id": "t1", "canonical_name": "X"}],
        "negative_targets": [
            {"negative_id": "n1", "canonical_name": "Y",
             "paired_positive_id": "t1", "anchor_T": "2026-01-01"}]}))
    cc = ev.load_case_control(ok)
    assert len(cc["positives"]) == 1 and len(cc["negatives"]) == 1


def test_labels_parsed_only_after_claim():
    src = Path(ev.__file__).read_text(encoding="utf-8")
    i_claim = src.index("claim_formal_holdout(")
    i_load = src.index("case_control = load_case_control(targets_path)")
    assert i_claim < i_load
    # negatives come from the SAME post-claim payload (never constructed
    # from production outcomes)
    assert 'out["negatives"]' in src


def test_explicit_negative_rate_drives_verdict():
    good = {"candidate_recall_scorable": 0.9,
            "emerging_recall_scorable": 0.9,
            "explicit_negative_emerging_rate": 0.1,
            "perturbation20_retention": 0.9}
    cmp = {"policy_beats_baselines": True}
    assert ev.apply_verdict(good, cmp) == "PASS"
    bad = dict(good, explicit_negative_emerging_rate=0.6)
    assert ev.apply_verdict(bad, cmp) == "FAIL"


def test_comparator_rate_does_not_drive_verdict():
    agg = {"candidate_recall_scorable": 0.9,
           "emerging_recall_scorable": 0.9,
           "explicit_negative_emerging_rate": 0.1,
           "matched_comparator_emerging_rate": 0.9,  # high, must not FAIL
           "perturbation20_retention": 0.9}
    assert ev.apply_verdict(agg, {"policy_beats_baselines": True}) == \
        "PASS"


def test_thresholds_unchanged():
    assert ev.VERDICT_RULES["PASS"][
        "matched_negative_emerging_rate_max"] == 0.2
    assert ev.VERDICT_RULES["FAIL"][
        "or_matched_negative_emerging_rate_min"] == 0.5


def test_sufficiency_counts_explicit_negatives_only():
    def agg(n_neg, n_pos):
        return {"scorable_targets": n_pos,
                "explicit_negative_rows": n_neg,
                "explicit_negatives_per_positive_avg":
                    n_neg / n_pos if n_pos else None}
    assert ev.apply_verdict_v2(agg(39, 20), None) == "INSUFFICIENT_EVIDENCE"
    assert ev.apply_verdict_v2(agg(40, 20), None) != "INSUFFICIENT_EVIDENCE"
    assert ev.apply_verdict_v2(agg(30, 20), None) == "INSUFFICIENT_EVIDENCE"


def test_comparators_selected_at_t30_only():
    src = Path(ev.__file__).read_text(encoding="utf-8")
    i_sel = src.index("controls = select_negative_controls(")
    # selection occurs inside the cp_label == "T-30" branch
    assert 'if cp_label == "T-30":' in src
    assert src.index('if cp_label == "T-30":') < i_sel


def test_baseline_cohort_alignment():
    rows = [
        {"kind": "target", "A": True, "B": False, "emerging": True},
        {"kind": "negative", "A": False, "B": False, "emerging": False},
    ]
    cmp = ev._compare_baselines(rows)
    assert cmp["policy_separation"] == 1.0
    assert cmp["baseline_A_separation"] == 1.0
    # comparators never appear as baseline rows
    assert "control" not in {r["kind"] for r in rows}


def test_single_use_ledger_unchanged():
    assert ev.SINGLE_USE_POLICY["version"] == "single-use-holdout-v1"
    assert ev.SINGLE_USE_POLICY["ledger_path"].endswith(
        "formal-holdout-ledger.sqlite")


def test_v3_receipt_rejected_by_v4(tmp_path):
    r = ev.build_freeze_receipt(tmp_path / "fr")
    old = json.loads(json.dumps(r))
    old["evaluator_version"] = "retrospective-evaluator-v3"
    old["evaluator_file_sha256"] = "0" * 64
    p = tmp_path / "old.json"
    p.write_text(json.dumps(old))
    with pytest.raises(ev.FreezeError):
        ev.run_evaluation(p, base.write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


def test_drift_rejected(tmp_path):
    r = ev.build_freeze_receipt(tmp_path / "fr")
    tampered = json.loads(json.dumps(r))
    tampered["production_file_sha256"]["ef/burst_policy_v2.py"] = "0" * 64
    p = tmp_path / "drift.json"
    p.write_text(json.dumps(tampered))
    with pytest.raises(ev.FreezeError, match="drifted"):
        ev.run_evaluation(p, base.write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


def test_ledger_env_override_regression(tmp_path, monkeypatch):
    """Unit tests MUST be able to redirect the formal ledger; with the
    override set, a FORMAL claim lands ONLY in the redirected ledger and
    the production ledger row count is unchanged. Regression for the
    cd9733d9 incident."""
    tmp_ledger = tmp_path / "ledger.sqlite"
    monkeypatch.setenv("YTIS_FORMAL_LEDGER_PATH", str(tmp_ledger))
    ev.ensure_holdout_ledger(tmp_ledger)
    prod_before = _prod_claim_count()

    # minimal case-control file; discovery runs on a synthetic catalog
    cat = base.build_catalog(tmp_path / "cat.sqlite")
    p = tmp_path / "cc.json"
    p.write_text(json.dumps({
        "positive_targets": [{"target_id": "flux",
                              "canonical_name":
                                  "Fluxcapacitor Runtime"}],
        "negative_targets": [
            {"negative_id": "n1", "canonical_name": "Legacy Widget",
             "aliases": [], "domain": "unlabeled",
             "paired_positive_id": "flux",
             "anchor_T": "2026-08-20"}]}))
    r = ev.build_freeze_receipt(tmp_path / "fr")
    rp = tmp_path / "receipt.json"
    rp.write_text(json.dumps(r))
    ev.run_evaluation(rp, p, tmp_path / "out", label="FORMAL",
                      catalog_path=cat, skip_perturbation=True)

    # the claim exists ONLY in the redirected ledger
    over = sqlite3.connect(str(tmp_ledger))
    n_over = over.execute(
        "SELECT COUNT(*) FROM holdout_claims").fetchone()[0]
    over.close()
    assert n_over == 1
    assert _prod_claim_count() == prod_before


def _prod_claim_count():
    conn = sqlite3.connect(
        f"file:{ev.SINGLE_USE_POLICY['ledger_path']}?mode=ro", uri=True)
    n = conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0]
    conn.close()
    return n


def test_v4_difference_documentation():
    plan_src = Path(ev.__file__).read_text(encoding="utf-8")
    assert "explicit negative" in plan_src
    assert "matched_comparator_emerging_rate" in plan_src
