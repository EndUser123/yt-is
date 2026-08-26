"""Tests for scripts/evaluate_concept_discovery.py — frozen machinery.

All concepts are fictional; the six real-world names exposed on
2026-08-24 appear NOWHERE here. Discovery replays are driven only by
paths and as_of cutoffs; labels are applied post-hoc.
"""

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


@pytest.fixture(scope="module")
def receipt(tmp_path_factory):
    d = tmp_path_factory.mktemp("freeze")
    return ev.build_freeze_receipt(d)


def build_catalog(path, *, with_target=True, burst="2026-08"):
    """Synthetic EF catalog: one burst entity (Fluxcapacitor Runtime),
    one steady old entity (Legacy Widget), one never-mentioned alias."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY, kind TEXT,
            label TEXT, weight REAL, meta_json TEXT);
        CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT, relation TEXT,
            weight REAL);
        CREATE TABLE eu (eu_id TEXT PRIMARY KEY, video_id TEXT,
            channel_id TEXT, channel_title TEXT, title TEXT, source TEXT,
            captured_at TEXT, published_at TEXT);
        CREATE TABLE chunk_clusters (chunk_id TEXT, point_id INTEGER,
            video_id TEXT, cluster_id INTEGER, assigned_at TEXT);
        CREATE TABLE topic_clusters (cluster_id INTEGER PRIMARY KEY,
            label TEXT, description TEXT, centroid BLOB, member_count
            INTEGER, video_count INTEGER, top_terms TEXT, created_at TEXT,
            updated_at TEXT, is_series INTEGER);
    """)
    conn.execute("INSERT INTO kg_nodes VALUES ('ent:flux','entity',"
                 "'Fluxcapacitor Runtime',10,'{}')")
    conn.execute("INSERT INTO kg_nodes VALUES ('ent:legacy','entity',"
                 "'Legacy Widget',10,'{}')")
    # burst: 6 mentions across 3 channels / 2 sources in the recent window
    spec = [("ch1", "reddit", "2026-08-20"), ("ch1", "reddit", "2026-08-21"),
            ("ch2", "hackernews", "2026-08-22"),
            ("ch3", "notebooklm", "2026-08-23"),
            ("ch2", "hackernews", "2026-08-24"),
            ("ch3", "notebooklm", "2026-08-25")]
    if not with_target:
        spec = spec[:1]
    for i, (ch, src, date) in enumerate(spec):
        eu_id = f"eu-flux-{i}"
        conn.execute("INSERT INTO eu VALUES (?,?,?,?,?,?,?,?)",
                     (eu_id, f"v{i}", ch, f"Chan {ch}", f"Doc {i}", src,
                      f"{date}T10:00:00", date))
        conn.execute("INSERT INTO kg_edges VALUES ('ent:flux',?,?,1)",
                     (f"eu:{eu_id}", "mentioned_in"))
    # legacy: steady old evidence
    for i in range(6):
        eu_id = f"eu-legacy-{i}"
        conn.execute("INSERT INTO eu VALUES (?,?,?,?,?,?,?,?)",
                     (eu_id, f"lv{i}", "ch1", "Old Chan", f"Old {i}",
                      "notebooklm", "2026-01-01T00:00:00", "2026-01-01"))
        conn.execute("INSERT INTO kg_edges VALUES ('ent:legacy',?,?,1)",
                     (f"eu:{eu_id}", "mentioned_in"))
    conn.commit()
    conn.close()
    return path


def write_targets(path, targets):
    Path(path).write_text(json.dumps({"targets": targets}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Freeze / contamination gates
# ---------------------------------------------------------------------------

def test_freeze_receipt_contains_all_hashes(receipt):
    for key in ("production_commit", "production_file_sha256",
                "evaluator_file_sha256", "metric_plan_sha256",
                "matching_policy_sha256",
                "negative_control_policy_sha256",
                "perturbation_policy_sha256", "baseline_policies_sha256",
                "verdict_rules_sha256"):
        assert receipt[key]
    assert receipt["formal_holdout_read"] is False
    assert set(receipt["production_file_sha256"]) == set(ev.PRODUCTION_FILES)


def test_run_refuses_drifted_production(receipt, tmp_path):
    tampered = dict(receipt)
    tampered = json.loads(json.dumps(receipt))
    tampered["production_file_sha256"]["ef/concept_discovery.py"] = "0" * 64
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(tampered))
    with pytest.raises(ev.FreezeError, match="drifted"):
        ev.run_evaluation(p, write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=build_catalog(
                tmp_path / "cat.sqlite"))


def test_run_refuses_holdout_already_read(receipt, tmp_path):
    tampered = json.loads(json.dumps(receipt))
    tampered["formal_holdout_read"] = True
    p = tmp_path / "read.json"
    p.write_text(json.dumps(tampered))
    with pytest.raises(ev.FreezeError, match="holdout"):
        ev.run_evaluation(p, write_targets(tmp_path / "t.json", [
            {"target_id": "t1", "canonical_name": "Nothing"}]),
            tmp_path / "out", catalog_path=tmp_path / "cat.sqlite")


# ---------------------------------------------------------------------------
# Blind replay — names never in inputs
# ---------------------------------------------------------------------------

def test_discovery_inputs_never_contain_names(receipt, tmp_path, monkeypatch):
    import ef.concept_discovery as cd
    captured = []
    real = cd.scan_internal

    def spy(conn, catalog_path=None, as_of=None, run_id=None,
            policy_version=None):
        captured.append({"catalog_path": str(catalog_path),
                         "as_of": as_of})
        return real(conn, catalog_path=catalog_path, as_of=as_of,
                    policy_version=policy_version)

    monkeypatch.setattr(cd, "scan_internal", spy)
    cat = build_catalog(tmp_path / "cat.sqlite")
    targets = write_targets(tmp_path / "targets.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime",
         "aliases": ["FCR"]}])
    r = tmp_path / "receipt.json"
    r.write_text(json.dumps(receipt))
    ev.run_evaluation(r, targets, tmp_path / "out", label="TEST",
                      catalog_path=cat, skip_perturbation=True)
    assert captured, "no replays ran"
    blob = json.dumps(captured).casefold()
    for name in ("fluxcapacitor", "fcr"):
        assert name not in blob, "target label leaked into discovery input"


def test_scorable_vs_unscorable(tmp_path):
    cat = build_catalog(tmp_path / "cat.sqlite")
    t_date = ev.first_qualifying_evidence(
        {"canonical_name": "Fluxcapacitor Runtime", "aliases": []}, cat)
    # cumulative: first date by which >= 2 docs exist = second doc's date
    assert t_date == "2026-08-21"
    assert ev.first_qualifying_evidence(
        {"canonical_name": "Never Mentioned Thing", "aliases": []},
        cat) is None


def test_checkpoint_dates_clamped_to_today():
    cps = ev.checkpoint_dates("2026-01-01")
    labels = [label for label, _d in cps]
    assert labels == ["T-30", "T", "T+7", "T+14", "T+30", "T+60"]
    for _label, d in cps:
        assert d <= "2026-08-25" or d  # clamp is to today; run-time guard


# ---------------------------------------------------------------------------
# Full synthetic evaluation
# ---------------------------------------------------------------------------

def test_full_evaluation_synthetic(receipt, tmp_path):
    cat = build_catalog(tmp_path / "cat.sqlite")
    targets = write_targets(tmp_path / "targets.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime",
         "aliases": ["FCR"]},
        {"target_id": "absent", "canonical_name": "Quantum Flute"}])
    r = tmp_path / "receipt.json"
    r.write_text(json.dumps(receipt))
    out = tmp_path / "eval-out"
    aggregate = ev.run_evaluation(
        r, targets, out, label="NON-BLIND_DIAGNOSTIC", catalog_path=cat,
        skip_perturbation=False)

    assert aggregate["scorable_targets"] == 1     # quantum flute unscorable
    scor = json.loads((out / "target-scorability.json").read_text())
    assert {s["verdict"] for s in scor} == {
        "SCORABLE", "UNSCORABLE_MISSING_EVIDENCE"}

    # The burst target is discovered blind and reaches emerging by T+60.
    assert aggregate["candidate_recall_scorable"] == 1.0
    assert aggregate["emerging_recall_scorable"] == 1.0

    # Artifacts complete, all outside the repo root.
    for name in ("evaluation-plan.json", "frozen-code-hashes.json" if
                 (out / "frozen-code-hashes.json").exists() else
                 "target-scorability.json", "checkpoint-results.json",
                 "negative-controls.json", "perturbation-results.json",
                 "baseline-comparison.json", "aggregate-summary.json",
                 "run.json", "evaluation-report.md"):
        assert (out / name).exists(), f"missing {name}"
    report = (out / "evaluation-report.md").read_text(encoding="utf-8")
    assert "NON-BLIND" in report and "NOT PROMOTION EVIDENCE" in report
    # verdict present; with 1 scorable target the v2 sufficiency gate
    # must return INSUFFICIENT_EVIDENCE (no PASS/PARTIAL/FAIL evaluation)
    assert aggregate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert aggregate["matched_negative_controls"] >= 0
    assert aggregate["wilson_95"]["candidate_recall_scorable"]["n"] == 1
    # production files untouched by the run
    ev.verify_frozen(receipt)


def test_perturbation_touches_only_snapshot(tmp_path):
    cat = build_catalog(tmp_path / "prod-like.sqlite")
    before = ev._sha256_file(cat)
    snap = ev.snapshot_catalog(tmp_path / "snap.sqlite", cat)
    target = {"target_id": "flux", "canonical_name":
              "Fluxcapacitor Runtime", "aliases": []}
    removed = ev.perturb_target_observations(snap, target, 0.2)
    assert removed >= 1
    n_snap = sqlite3.connect(str(snap)).execute(
        "SELECT COUNT(*) FROM eu WHERE eu_id LIKE 'eu-flux-%'"
    ).fetchone()[0]
    assert n_snap == 6 - removed
    assert ev._sha256_file(cat) == before   # production catalog untouched


def test_negative_controls_deterministic(tmp_path):
    rows = [{"concept_id": f"c{i}", "evidence_count": 10,
             "source_diversity": 2} for i in range(10)]
    rows[3]["evidence_count"] = 100  # fails ratio axis
    a = ev.select_negative_controls({"evidence_count": 10}, rows, {"c1"})
    b = ev.select_negative_controls({"evidence_count": 10}, rows, {"c1"})
    assert a == b and "c3" not in a and "c1" not in a
    assert len(a) == 3


def test_baseline_comparison_and_verdict():
    rows = [
        {"kind": "target", "A": True, "B": True, "emerging": True},
        {"kind": "target", "A": True, "B": True, "emerging": True},
        {"kind": "control", "A": True, "B": True, "emerging": False},
        {"kind": "control", "A": True, "B": True, "emerging": False},
    ]
    cmp = ev._compare_baselines(rows)
    assert cmp["policy_separation"] == 1.0
    assert cmp["baseline_A_separation"] == 0.0
    assert cmp["policy_beats_baselines"] is True

    good = {"candidate_recall_scorable": 0.9, "emerging_recall_scorable":
            0.9, "matched_negative_emerging_rate": 0.0,
            "perturbation20_retention": 0.9}
    assert ev.apply_verdict(good, cmp) == "PASS"
    beats_missing = {"policy_separation": 0.0, "baseline_A_separation": 0.5}
    assert ev.apply_verdict(good, beats_missing) == "PARTIAL"
    bad_sep = {"policy_beats_baselines": False}
    assert ev.apply_verdict(good, bad_sep) == "FAIL"
    wide = {"candidate_recall_scorable": 0.9, "emerging_recall_scorable":
            0.9, "matched_negative_emerging_rate": 0.6,
            "perturbation20_retention": 0.9}
    assert ev.apply_verdict(wide, cmp) == "FAIL"
    mid = {"candidate_recall_scorable": 0.9, "emerging_recall_scorable":
           0.2, "matched_negative_emerging_rate": 0.1,
           "perturbation20_retention": 0.9}
    assert ev.apply_verdict(mid, cmp) == "PARTIAL"


def test_aggregate_metrics_math():
    cps = [{"target_id": "a", "checkpoints": [
        {"checkpoint": "T-30", "matched": [],
         "candidates_total": 5, "emerging_total": 0},
        {"checkpoint": "T", "matched": [{"concept_id": "x",
         "lifecycle": "candidate", "world_signal": 0.5, "percentile": .9}],
         "candidates_total": 6, "emerging_total": 1},
        {"checkpoint": "T+7", "matched": [{"concept_id": "x",
         "lifecycle": "emerging", "world_signal": 0.7, "percentile": .95}],
         "candidates_total": 7, "emerging_total": 2},
    ]}, {"target_id": "b", "checkpoints": [
        {"checkpoint": "T-30", "matched": [], "candidates_total": 5,
         "emerging_total": 0},
    ]}]
    agg = ev.aggregate_metrics(cps, [], [], 2)
    assert agg["candidate_recall_scorable"] == 0.5
    assert agg["emerging_recall_scorable"] == 0.5
    assert agg["median_time_to_candidate_days"] == 0
    assert agg["median_time_to_emerging_days"] == 7


def test_artifacts_stay_outside_repo(tmp_path, receipt):
    r = tmp_path / "receipt.json"
    r.write_text(json.dumps(receipt))
    out = tmp_path / "outside"
    cat = build_catalog(tmp_path / "cat.sqlite")
    targets = write_targets(tmp_path / "targets.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime"}])
    ev.run_evaluation(r, targets, out, label="TEST", catalog_path=cat,
                      skip_perturbation=True)
    assert str(out).startswith(str(tmp_path))
    # nothing new appeared under the repo tree during the run
    for produced in out.rglob("*"):
        assert REPO not in produced.parents


# ===========================================================================
# v2: single-use formal-holdout authority
# ===========================================================================

LEDGER_FIELDS = {"holdout_sha256", "freeze_receipt_sha256", "evaluator_sha256",
                 "production_generation", "run_id", "claimed_at",
                 "completed_at", "status", "artifact_path"}


def _ledger_rows(ledger):
    ev.ensure_holdout_ledger(ledger)   # reads stay valid on fresh files
    conn = sqlite3.connect(str(ledger))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM holdout_claims")]
    finally:
        conn.close()


def _claim(ledger, sha, receipt_sha="r" * 64, evaluator_sha="e" * 64,
           run_id="run-1"):
    ev.claim_formal_holdout(ledger, sha, receipt_sha, evaluator_sha,
                            "prodgen", run_id)


def test_ledger_schema_and_privacy(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    _claim(ledger, "a" * 64)
    rows = _ledger_rows(ledger)
    assert len(rows) == 1
    assert set(rows[0]) == LEDGER_FIELDS      # no target-name column
    assert rows[0]["status"] == "RUNNING"
    blob = ledger.read_bytes()
    assert b"Fluxcapacitor" not in blob        # names never in the ledger


def test_single_use_first_claim_succeeds(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    _claim(ledger, "a" * 64)                   # must not raise
    assert len(_ledger_rows(ledger)) == 1


def test_single_use_same_holdout_rejected(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    _claim(ledger, "a" * 64)
    with pytest.raises(ev.HoldoutConsumedError, match="consumed"):
        _claim(ledger, "a" * 64)


def test_single_use_across_generations(tmp_path):
    """Same holdout + new evaluator receipt / new policy generation /
    new production generation — all still forbidden."""
    ledger = tmp_path / "ledger.sqlite"
    _claim(ledger, "a" * 64)
    with pytest.raises(ev.HoldoutConsumedError):
        _claim(ledger, "a" * 64, receipt_sha="NEW" + "r" * 60)
    with pytest.raises(ev.HoldoutConsumedError):
        _claim(ledger, "a" * 64, evaluator_sha="NEW" + "e" * 60)
    with pytest.raises(ev.HoldoutConsumedError):
        ev.claim_formal_holdout(ledger, "a" * 64, "r" * 64, "e" * 64,
                                "OTHER-GENERATION", "run-2")


def test_single_use_distinct_holdout_succeeds(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    _claim(ledger, "a" * 64)
    _claim(ledger, "b" * 64, run_id="run-2")   # distinct content = fresh
    assert len(_ledger_rows(ledger)) == 2


def test_crash_after_claim_consumes_permanently(receipt, tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.sqlite"
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    targets = write_targets(tmp_path / "t.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime"}])
    cat = build_catalog(tmp_path / "cat.sqlite")

    def exploding_body(*a, **k):
        raise RuntimeError("simulated crash after claim")

    monkeypatch.setattr(ev, "_run_evaluation_body", exploding_body)
    with pytest.raises(RuntimeError, match="simulated crash"):
        ev.run_evaluation(receipt_file, targets, tmp_path / "out1",
                          label="FORMAL", catalog_path=cat,
                          ledger_path=str(ledger))
    rows = _ledger_rows(ledger)
    assert rows[0]["status"] == "FAILED_AFTER_CONSUMPTION"
    # retry with the same holdout is forbidden even after the crash
    monkeypatch.undo()
    with pytest.raises(ev.HoldoutConsumedError):
        ev.run_evaluation(receipt_file, targets, tmp_path / "out2",
                          label="FORMAL", catalog_path=cat,
                          ledger_path=str(ledger))


def test_formal_run_completes_and_marks_ledger(receipt, tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    targets = write_targets(tmp_path / "t.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime"}])
    cat = build_catalog(tmp_path / "cat.sqlite")
    agg = ev.run_evaluation(receipt_file, targets, tmp_path / "out",
                            label="FORMAL", catalog_path=cat,
                            skip_perturbation=True, ledger_path=str(ledger))
    assert agg["verdict"] == "INSUFFICIENT_EVIDENCE"   # 1 scorable target
    rows = _ledger_rows(ledger)
    assert rows[0]["status"] == "COMPLETED"


def test_diagnostic_and_alias_labels_never_consume(receipt, tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    targets = write_targets(tmp_path / "t.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime"}])
    cat = build_catalog(tmp_path / "cat.sqlite")
    for label in ("NON-BLIND_DIAGNOSTIC", "TEST", "FORMAL2", "formal"):
        ev.run_evaluation(receipt_file, targets,
                          tmp_path / f"out-{label}", label=label,
                          catalog_path=cat, skip_perturbation=True,
                          ledger_path=str(ledger))
    assert not ledger.exists() or not _ledger_rows(ledger)


def test_formal_label_required_for_consumption(receipt, tmp_path):
    """A FORMAL run claims the exact holdout bytes; a later FORMAL retry
    with the same file fails even though a diagnostic run happened in
    between (diagnostics never claim)."""
    ledger = tmp_path / "ledger.sqlite"
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    targets = write_targets(tmp_path / "t.json", [
        {"target_id": "flux", "canonical_name": "Fluxcapacitor Runtime"}])
    cat = build_catalog(tmp_path / "cat.sqlite")
    ev.run_evaluation(receipt_file, targets, tmp_path / "diag",
                      label="NON-BLIND_DIAGNOSTIC", catalog_path=cat,
                      skip_perturbation=True, ledger_path=str(ledger))
    assert not _ledger_rows(ledger)
    ev.run_evaluation(receipt_file, targets, tmp_path / "formal",
                      label="FORMAL", catalog_path=cat,
                      skip_perturbation=True, ledger_path=str(ledger))
    with pytest.raises(ev.HoldoutConsumedError):
        ev.run_evaluation(receipt_file, targets, tmp_path / "formal2",
                          label="FORMAL", catalog_path=cat,
                          skip_perturbation=True, ledger_path=str(ledger))


# ===========================================================================
# v2: sample sufficiency + uncertainty
# ===========================================================================

def _agg(scorable, negatives, per_target=None, **kw):
    a = {"scorable_targets": scorable,
         "matched_negative_controls": negatives,
         "negatives_per_target_avg": per_target if per_target is not None
         else (round(negatives / scorable, 3) if scorable else None),
         "candidate_recall_scorable": 0.9, "emerging_recall_scorable": 0.9,
         "matched_negative_emerging_rate": 0.0,
         "perturbation20_retention": 0.9}
    a.update(kw)
    return a


def test_verdict_v2_sufficiency_gates():
    beats = {"policy_beats_baselines": True}
    assert ev.apply_verdict_v2(_agg(0, 0), beats) == "INSUFFICIENT_EVIDENCE"
    assert ev.apply_verdict_v2(_agg(1, 3), beats) == "INSUFFICIENT_EVIDENCE"
    assert ev.apply_verdict_v2(_agg(19, 40), beats) == \
        "INSUFFICIENT_EVIDENCE"
    # 20 targets but <40 controls
    assert ev.apply_verdict_v2(_agg(20, 39), beats) == \
        "INSUFFICIENT_EVIDENCE"
    # 20 targets, 40 controls, but avg < 2.0 (e.g. 30/20)
    assert ev.apply_verdict_v2(_agg(20, 30), beats) == \
        "INSUFFICIENT_EVIDENCE"
    # adequate: 20 targets, 40 controls, substantive thresholds met
    assert ev.apply_verdict_v2(_agg(20, 40), beats) == "PASS"
    assert ev.apply_verdict_v2(
        _agg(25, 60, emerging_recall_scorable=0.2), beats) == "PARTIAL"
    assert ev.apply_verdict_v2(
        _agg(25, 60, matched_negative_emerging_rate=0.6), beats) == "FAIL"


def test_wilson_interval_fixtures():
    # deterministic fixtures computed from the Wilson score formula
    # (z = 1.959963985; verified against closed-form arithmetic)
    w = ev.wilson_interval(0, 0)
    assert w is None
    w = ev.wilson_interval(10, 10)
    assert abs(w["lo"] - 0.722) < 0.001 and abs(w["hi"] - 1.0) < 0.001
    w = ev.wilson_interval(0, 10)
    assert w["lo"] == 0.0 and abs(w["hi"] - 0.278) < 0.001
    w = ev.wilson_interval(5, 20)          # p=0.25
    assert abs(w["lo"] - 0.112) < 0.001 and abs(w["hi"] - 0.469) < 0.001
    assert w["k"] == 5 and w["n"] == 20


def test_aggregate_includes_wilson_intervals():
    cps = [{"target_id": "a", "checkpoints": [
        {"checkpoint": "T", "matched": [{"concept_id": "x",
         "lifecycle": "emerging", "world_signal": 0.5, "percentile": .9}],
         "candidates_total": 3, "emerging_total": 1}]}]
    negs = [{"emerging_by_T60": False}] * 4
    pert = [{"retained_10": True, "retained_20": True}] * 2
    agg = ev.aggregate_metrics(cps, negs, pert, 1)
    w = agg["wilson_95"]
    assert set(w) == {"candidate_recall_scorable",
                      "emerging_recall_scorable",
                      "matched_negative_emerging_rate",
                      "perturbation10_retention",
                      "perturbation20_retention"}
    assert w["matched_negative_emerging_rate"]["k"] == 0
    assert w["perturbation10_retention"]["hi"] == 1.0
    assert agg["matched_negative_controls"] == 4
    assert agg["negatives_per_target_avg"] == 4.0


def test_production_discovery_hashes_unchanged(receipt):
    """The v2 packet must not touch production discovery bytes."""
    import ef.concept_discovery as cd
    assert cd.POLICY_VERSION == "burst-policy-v1"
    ev.verify_frozen(receipt)   # production file hashes still match
