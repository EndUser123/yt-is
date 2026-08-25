"""Synthetic-fixture tests for the v2 policy-family calibration experiment."""
import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "calib", WT / "scripts" / "calibrate_concept_discovery_v2.py")
calib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calib)


def synth_f(**kw):
    base = {"r30": 3, "ratio30": 1.0, "ch30": 1, "st30": 1, "r60": 3,
            "r90": 3, "lifetime": 5, "fs": "2020-01-01"}
    base.update(kw)
    return base


def synth_obs(n=10):
    obs = []
    for j in range(n):
        obs.append({"eu_id": f"eu_{j:04d}", "obs_date": f"2026-01-{2 + j:02d}",
                    "channel_id": f"ch{j % 3}", "source": "notebooklm",
                    "video_id": f"vid{j}", "label": "x"})
    return obs


def synth_data():
    """6 targets x (1 strong emerging, 1 weak, 1 absent) + 2 controls each."""
    targets = []
    for i in range(6):
        tid = f"v4_t{i}"
        strong = f"concept_{tid}_a"
        weak = f"concept_{tid}_b"
        cps = {}
        for cp in calib.CHECKPOINTS:
            cps[cp] = {
                "as_of": "2026-01-15",
                "all": {
                    strong: tuple(synth_f(r30=6, ratio30=2.5, ch30=3,
                                          st30=1)[k]
                                  for k in calib.AGG),
                    weak: tuple(synth_f(r30=2)[k] for k in calib.AGG),
                    "concept_other_x": tuple(synth_f(r30=1)[k]
                                             for k in calib.AGG),
                },
                "matched": [strong],
                "controls": {f"ctl_{tid}_1": synth_f(r30=2),
                             f"ctl_{tid}_2": synth_f(r30=6, ch30=1)},
                "target_obs": {strong: synth_obs()},
            }
        targets.append({"target_id": tid, "checkpoints": cps})
    return {"targets": targets}


def test_grid_is_exactly_216():
    combos = [(ck, rm, rt, ch) for ck in calib.CANDIDATES
              for rm in calib.RECENT_LEVELS
              for rt in calib.RATIO_LEVELS
              for ch in calib.CHANNEL_LEVELS]
    assert len(combos) == 216
    assert len(set(map(str, combos))) == 216


def test_source_types_disabled_in_every_v2_arm():
    f = synth_f(r30=9, ratio30=9.0, ch30=9, st30=1)
    for rm in calib.RECENT_LEVELS:
        for rt in calib.RATIO_LEVELS:
            for ch in calib.CHANNEL_LEVELS:
                assert calib.emerge_pass(f, rm, rt, ch), \
                    f"st30=1 must not fail any v2 arm {(rm, rt, ch)}"


def test_burst_policy_v1_control_preserved():
    assert not calib.v1_pass(synth_f(r30=6, ratio30=2.5, ch30=3, st30=1))
    assert calib.v1_pass(synth_f(r30=6, ratio30=2.5, ch30=3, st30=2))
    assert not calib.v1_pass(synth_f(r30=3, ratio30=2.5, ch30=3, st30=2))


def test_fold_assignment_deterministic():
    tid = "v4_02fac9aff2b1"
    expect = int(hashlib.sha256(tid.encode()).hexdigest()[:8], 16) % 5
    assert calib.fold_of(tid) == expect
    assert calib.fold_of(tid) == calib.fold_of(tid)
    assert all(0 <= calib.fold_of(f"t{i}") < 5 for i in range(50))


def test_controls_grouped_with_target():
    # controls are stored inside the target entry, so any subset by
    # target_id automatically carries its controls
    data = synth_data()
    tid = data["targets"][0]["target_id"]
    sub = calib.subset(data, {tid})
    assert len(sub["targets"]) == 1
    assert len(sub["targets"][0]["checkpoints"]["T+30"]["controls"]) == 2


def test_selection_rule_deterministic():
    data = synth_data()
    rows = calib.eval_all(data)
    s1 = calib.select_config(rows, 0.0)
    s2 = calib.select_config(rows, 0.0)
    assert s1 == s2
    assert s1["selected"] is not None
    # qualifying arm must beat pass-like axes
    sel = next(r for r in rows if calib.same_cfg(r, s1["selected"]))
    assert sel["emerging_recall"] >= 0.50
    assert sel["control_emerging_rate"] <= 0.20


def test_selection_lexicographic_order():
    data = synth_data()
    rows = calib.eval_all(data)
    s = calib.select_config(rows, 0.0)["selected"]
    sel = next(r for r in rows if calib.same_cfg(r, s))
    # lowest control rate wins first
    assert sel["control_emerging_rate"] == min(
        r["control_emerging_rate"] for r in rows
        if r["emerging_recall"] >= 0.50
        and r["perturb20_candidate_retention"] >= 0.50
        and r["candidate_recall"] >= 0.70
        and r["separation"] > 0.0)


def test_eval_config_metrics_on_fixture():
    data = synth_data()
    r = calib.eval_config(data, "C0", 4, "DISABLED", 2)
    # all 6 targets have strong entity r30=6, ch=3 -> emerging recall 1.0;
    # controls: ctl_1 r30=2 fails recent>=4; ctl_2 r30=6 but ch30=1
    # fails channels_min=2 -> control emerging rate 0.0.
    assert r["emerging_recall"] == 1.0
    assert r["control_emerging_rate"] == 0.0
    assert r["separation"] == 1.0
    assert r["candidate_recall"] == 1.0


def test_baseline_b_pass():
    f = synth_f(r30=5, fs="2026-01-01")
    assert calib.baseline_b_pass(f, date(2026, 2, 1))
    assert not calib.baseline_b_pass(f, date(2026, 6, 1))
    assert not calib.baseline_b_pass(synth_f(r30=3, fs="2026-01-01"),
                                     date(2026, 1, 15))


def test_plan_hash_guard(monkeypatch=None):
    bad = calib.PLAN_PATH.with_name("preregistered-plan-fake.json")
    real = calib.PLAN_SHA256
    try:
        calib.PLAN_SHA256 = "0" * 64
        try:
            calib.extract()
            raised = False
        except SystemExit:
            raised = True
        assert raised, "extract must refuse on plan hash mismatch"
    finally:
        calib.PLAN_SHA256 = real
    assert bad.name not in ()  # silence unused


def test_no_post_result_grid_mutation():
    before = (calib.CANDIDATES, calib.RATIO_LEVELS, calib.RECENT_LEVELS,
              calib.CHANNEL_LEVELS)
    data = synth_data()
    calib.eval_all(data)
    after = (calib.CANDIDATES, calib.RATIO_LEVELS, calib.RECENT_LEVELS,
             calib.CHANNEL_LEVELS)
    assert before == after


def test_catalog_access_is_read_only():
    src = (WT / "scripts" / "calibrate_concept_discovery_v2.py").read_text(
        encoding="utf-8")
    assert "mode=ro" in src
    assert "formal-holdout-ledger" not in src
    assert "INSERT" not in src and "UPDATE " not in src.replace(
        "update(", "").replace("UPDATE_OR", "")
    assert "claim_formal_holdout" not in src


def test_formal_baseline_A_reproduction_from_real_features():
    fp = calib.CAL / "features.json"
    if not fp.exists():
        import pytest
        pytest.skip("features.json not extracted yet")
    data = json.loads(fp.read_text(encoding="utf-8"))
    negs = json.loads((calib.ART / "negative-controls.json").read_text())
    ctl = {}
    for n in negs:
        ctl.setdefault(n["target_id"], []).append(n["control_id"])
    for t in data["targets"]:
        t["control_ids"] = ctl.get(t["target_id"], [])
    cache = calib.CAL / "formal-matched-t30-cache.json"
    if not cache.exists():
        import pytest
        pytest.skip("formal-matched cache not computed yet")
    repro = calib.reproduction_check(
        data, set(json.loads(cache.read_text(encoding="utf-8"))))
    assert repro["baseline_A_target_rate"] == 0.625
    assert repro["baseline_A_control_rate"] == 0.246
    assert repro["separation"] == 0.379
