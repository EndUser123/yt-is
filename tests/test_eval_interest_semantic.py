"""ISEM v1 evaluator tests — fully offline synthetic fixtures.

No private holdout content appears here; fictional topics only
(espresso/gardening/etc.). These tests pin the frozen metric-plan
behavior: type separation, scorability independence, matching-path
ladder, provenance gating, INSUFFICIENT_EVIDENCE rules, and stability
determinism.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import eval_interest_semantic as isem  # noqa: E402

def gt_label(lid, cls, name, aliases=None, scor="corpus_scorable"):
    return {
        "label_id": lid,
        # deliberately different key name to exercise synonym binding
        "type": cls,
        "canonical_name": name,
        "aliases": aliases or [],
        "scorability": scor,
        "statement_text": f"operator statement about {name}",
        "probe_receipts": [],
    }


def full_gt():
    rows = [
        gt_label("i_espresso", "Interest", "Home espresso dialing-in"),
        gt_label("i_roast", "Interest", "Coffee roaster sourcing"),
        gt_label("i_garden", "Interest", "Urban balcony gardening"),
        gt_label("i_keeb", "Interest", "Mechanical keyboard design"),
        gt_label("i_film", "Interest", "Analog film development"),
        gt_label("i_solar", "Interest",
                 "Solar microinverter monitoring"),
        gt_label("n_drip", "InterestNegative",
                 "drip coffee machine reviews",
                 aliases=["drip machines"]),
        gt_label("g_repro", "Goal",
                 "brew reproducible espresso shots"),
        gt_label("q_backflush", "Question",
                 "How often should I backflush the group head?"),
        gt_label("s_paywall", "SourceNegative",
                 "paywalled blog reposts"),
        gt_label("wp_async", "WorkPreference", "async-first workflows"),
    ]
    return {"labels": rows}


def interest_obj(name, cluster=100, ovi="observed", goal=None,
                 info_need=None):
    return {"name": name, "kind": "topic", "parent": None,
            "temporal_state": "active", "stance": "curiosity",
            "confidence": 0.5, "observed_vs_inferred": ovi, "goal":
            goal, "information_need": info_need,
            "cluster_ids": [cluster], "evidence_summary": "",
            "counterevidence": None, "related_to": []}


RESULT_GOOD = {
    "inferred_interests": [
        interest_obj("Home espresso dialing-in", 101,
                     goal="brew reproducible espresso shots",
                     info_need="grinder burr alignment tolerance"),
        interest_obj("espresso workflow", 102),
        interest_obj("urban balcony gardening", 103,
                     goal="grow salad greens year round"),
        interest_obj("mechanical keyboard layouts", 104,
                     info_need="keycap profile acoustic differences"),
        interest_obj("analog film development", 105, "inferred"),
        interest_obj("hybrid inverter telemetry dashboards", 106),
    ],
    "questions": [
        {"text": "How often should I backflush the group head?",
         "interest": "Home espresso dialing-in", "status": "open"},
        {"text": "Which filtration media suit balcony planters?",
         "interest": "urban balcony gardening", "status": "watching"},
        {"text": "Does panel orientation dominate harvest variance?",
         "interest": "nonexistent-interest", "status": "open"},
    ],
    "regret_candidates": [],
}


class AlwaysYesJudge:
    live = False

    def __call__(self, prompt, surface, target):
        self.log = getattr(self, "log", [])
        self.log.append((surface["sid"], target["label_id"]))
        return True


class AlwaysNoJudge:
    live = False

    def __call__(self, *a):
        return False


def eligible_ids():
    return set(range(100, 110))


def write_gt(tmp_path, doc) -> Path:
    p = tmp_path / "gt.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


@pytest.fixture()
def sealed_gt_factory(tmp_path, monkeypatch):
    """Write a synthetic GT and point the sealed-hash guard at it.

    A production run loads the real v1.1 artifact whose digest must
    equal the frozen SEALED_GT_SHA256; here each test writes a
    synthetic artifact and repoints the constant at that file's real
    digest, exercising identical code paths.
    """

    def make(doc, name="gt.json"):
        p = tmp_path / name
        p.write_text(json.dumps(doc), encoding="utf-8")
        monkeypatch.setattr(isem, "SEALED_GT_SHA256",
                            isem.sha256_file(p))
        return p

    return make


# --------------------------------------------------------------- tests

def test_synonym_binding(sealed_gt_factory):
    gt = isem.load_ground_truth(sealed_gt_factory(full_gt()))
    classes = {l["semantic_class"] for l in gt["labels"]}
    assert classes == {"Interest", "InterestNegative", "Goal",
                       "Question", "SourceNegative",
                       "WorkPreference"}


def test_fail_closed_on_unknown_class(tmp_path):
    rows = [{"label_id": "z1", "semantic_class": "MysteryClass",
             "canonical_name": "whatever"}]
    with pytest.raises(isem.SchemaBindError):
        isem.load_ground_truth(write_gt(
            tmp_path, {"labels": rows}))


def test_verify_sealed_refuses_wrong_hash(tmp_path):
    gt = isem.load_ground_truth(write_gt(tmp_path, full_gt()))
    with pytest.raises(isem.SchemaBindError):
        isem.verify_sealed(gt)


def test_matching_paths_exact_alias_judge(sealed_gt_factory):
    gt = isem.load_ground_truth(sealed_gt_factory(full_gt()))
    view = isem.ResultView(json.loads(json.dumps(RESULT_GOOD)),
                           eligible_cluster_ids=eligible_ids())
    targets = [l for l in gt["labels"]
               if l["semantic_class"] == "Interest"]
    yes = AlwaysYesJudge()
    tr = isem.run_track("Interest", targets,
                        view.surfaces_for("Interest"), yes)
    by_target = {o["target_id"]: o for o in tr["outcomes"]}
    assert by_target["i_espresso"]["matching_path"] == \
        isem.MATCH_EXACT
    assert by_target["i_roast"]["matching_path"] in (
        isem.MATCH_JUDGE, isem.MATCH_NONE)
    assert len(yes.log) >= 1  # judge tier actually engaged


def test_judge_ladder_only_after_alias_miss():
    """exact and alias tiers must resolve without judge calls."""
    target = {"label_id": "t1", "semantic_class": "Interest",
              "canonical_name": "Home espresso dialing-in",
              "aliases": ["dial-in workflow"]}
    surface = {"sid": "I0", "text": "Home espresso dialing-in",
               "context": ""}
    path, _ = isem.match_one(target, surface, False, AlwaysNoJudge())
    assert path == isem.MATCH_EXACT
    surface2 = {"sid": "I1", "text": "espresso dial-in workflow v2",
                "context": ""}
    path2, _ = isem.match_one(target, surface2, False,
                              AlwaysNoJudge())
    assert path2 == isem.MATCH_ALIAS


def test_type_separation_negatives_never_enter_interest_track(
        sealed_gt_factory):
    gt = isem.load_ground_truth(sealed_gt_factory(full_gt()))
    empty = {"inferred_interests": [], "questions": [],
             "regret_candidates": []}
    rep = isem.evaluate(gt, empty, AlwaysNoJudge())
    # Only the contract types + the Interest scorer open tracks.
    assert set(rep["tracks"]) == {
        "Interest", "InterestNegative", "Goal", "Question"}
    listed = {x["semantic_class"] for x in
              rep["non_interest_negatives_not_scoring_interest"]}
    assert "SourceNegative" in listed
    assert rep["retyped_outside_contract_counts"]["WorkPreference"] == 1


def test_question_unresolvable_parent_is_unsupported_hit(
        sealed_gt_factory):
    rows = [gt_label("q1", "Question",
                     "Some open calibration question?")]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {
        "inferred_interests": [],
        "questions": [{"text": "Some open calibration question?",
                       "interest": "missing-owner",
                       "status": "open"}],
        "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    qm = rep["tracks"]["Question"]
    item = qm["per_item"][0]
    assert item["matched"] is True
    assert item["matching_path"] == isem.MATCH_EXACT
    assert item["provenance"] == "missing_parent_interest"
    assert qm["recall_gross"] == 1.0
    assert qm["recall_provenance_ok"] == 0.0
    assert qm["unsupported_matched_hits"] == 1
    assert qm["verdict"] == "INSUFFICIENT_EVIDENCE"  # n=1 < 5


def test_scorability_states_and_denominators(sealed_gt_factory):
    rows = [gt_label("i1", "Interest", "alpha pursuit",
                     scor="corpus_scorable"),
            gt_label("i2", "Interest", "beta pursuit",
                     scor="corpus_unscorable"),
            gt_label("i3", "Interest", "gamma pursuit",
                     scor="unknown")]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [], "questions": [],
               "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        support_hits_by_label={"i3": [7]})
    tm = rep["tracks"]["Interest"]
    states = {r["label_id"]: r["scorability"]
              for r in tm["per_item"]}
    assert states == {"i1": "SCORABLE",
                      "i2": "UNSCORABLE_MISSING_EVIDENCE",
                      "i3": "SCORABLE"}  # probe resolved unknown
    assert tm["n_scorable_positives"] == 2
    assert tm["recall_gross"] == 0.0
    assert tm["excluded_unscorable"] == 1
    assert tm["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_insufficient_evidence_gate_small_n(sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest", f"topic number {k}")
            for k in range(4)]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [
        interest_obj(f"topic number {k}", cluster=10 + k)
        for k in range(4)],
        "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        stability_results=True)
    tm = rep["tracks"]["Interest"]
    assert tm["recall_provenance_ok"] == 1.0
    assert tm["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert rep["overall_verdict"] == \
        "DIAGNOSTIC_ONLY_INSUFFICIENT_EVIDENCE"


def test_full_pass_requires_fp_zero_and_stability(sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest",
                     f"distilled water topic {k}") for k in range(5)]
    rows.append(gt_label("neg1", "InterestNegative",
                         "sparkling soda apparatus"))
    gt = isem.load_ground_truth(sealed_gt_factory({"labels": rows}))

    def payload_with(extra_name=None):
        ints = [interest_obj(f"distilled water topic {k}",
                             cluster=200 + k) for k in range(5)]
        if extra_name:
            ints.append(interest_obj(extra_name, 299))
        return {"inferred_interests": ints, "questions": [],
                "regret_candidates": []}

    rep_clean = isem.evaluate(gt, payload_with(), AlwaysNoJudge(),
                              stability_results=True)
    assert rep_clean["tracks"]["Interest"]["verdict"] == "PASS"
    neg_tm = rep_clean["tracks"]["InterestNegative"]
    assert neg_tm["interest_negative_fp_hits"] == 0

    rep_fp = isem.evaluate(
        gt, payload_with("sparkling soda apparatus review"),
        AlwaysNoJudge(), stability_results=True)
    assert rep_fp["tracks"]["Interest"][
        "interest_negative_fp_hits"] == 1
    assert rep_fp["tracks"]["Interest"]["verdict"] == "FAIL"

    # an unmatched scorable positive fails even with no FPs
    rep_miss = isem.evaluate(gt, payload_with(), AlwaysNoJudge(),
                             stability_results=True)
    del rep_miss
    subset = dict(gt)
    subset["labels"] = gt["labels"][:6]  # drop the negative
    rep_sub = isem.evaluate(subset, payload_with(), AlwaysNoJudge(),
                            stability_results=False)
    assert rep_sub["tracks"]["Interest"]["verdict"] == \
        "INCOMPLETE_PERTURBATION_PENDING"


def test_adjacent_excluded_and_dedup_works(sealed_gt_factory):
    rows = [gt_label("g1", "Goal", "one shared latent goal"),
            gt_label("q1", "Question", "Duplicate question text?")]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))

    a = interest_obj("a surface", 300,
                     goal="One Shared Latent Goal")
    b = interest_obj("b adjacent", 301,
                     goal="one shared latent goal",
                     ovi="inferred_adjacent")
    payload = {
        "inferred_interests": [a, b],
        "questions": [
            {"text": "Duplicate question text?", "interest":
             "a surface", "status": "open"},
            {"text": "duplicate question TEXT?", "interest":
             "a surface", "status": "watching"},
        ],
        "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    d = rep["diagnostics"]
    assert d["n_goal_strings"] == 1          # normalized dedup
    assert d["question_duplicate_collapses"] == 1
    assert d["adjacent_excluded_from_core_denominators"] is True
    gm = rep["tracks"]["Goal"]
    assert gm["per_item"][0]["matching_path"] == isem.MATCH_EXACT


def test_blindness_constants_frozen_shape():
    prompt_pos = isem.FROZEN_JUDGE_PROMPT_POSITIVE
    prompt_neg = isem.FROZEN_JUDGE_PROMPT_NEGATIVE_INTEREST
    assert "blinded" in prompt_pos and "ONE candidate" in prompt_pos
    assert "constraint record" in prompt_neg
    for banned in ("threshold", "arm ", "aggregate", "recall"):
        assert banned not in prompt_pos.lower()
    assert isem.MIN_N_PER_TYPE == 5


def test_stability_variants_deterministic():
    inv = {"clusters": [
        {"cluster_id": k, "channels": k % 7, "member_count": 50 - k,
         "representative": [{"title": f"t{k}{j}"}
                            for j in range(4)]}
        for k in range(60)]}
    b1 = isem.stability_variants(inv)
    b2 = isem.stability_variants(inv)
    m1 = {m["scheme"]: m.get("removed_cluster_ids_sha256")
          for m in b1["manifests"]}
    m2 = {m["scheme"]: m.get("removed_cluster_ids_sha256")
          for m in b2["manifests"]}
    assert m1 == m2
    assert m1["S1_RANDOM_DROP_5PCT"] != \
        m1["S2_TOP_BREADTH_DROP_10"]
    s3 = b1["variants"]["S3_REPS_TRIM"]["clusters"][0]
    assert len(s3["representative"]) == 2
    s4 = b1["variants"]["S4_ORDER_SHUFFLE"]["clusters"]
    assert sorted(c["cluster_id"] for c in s4) == list(range(60))


def test_compare_matched_sets_flags_drift():
    def report(tids_by_track):
        return {"tracks": {cls: {"per_item": [
            {"label_id": tid, "matched": True} for tid in tids]}
            for cls, tids in tids_by_track.items()}}

    base = report({"Interest": ["a", "b"], "Goal": ["g1"]})
    shifted = report({"Interest": ["a", "c"], "Goal": ["g1"]})
    cmp_ = isem.compare_matched_sets(base, {"S1": shifted})
    assert cmp_["S1"]["stable"] is False
    assert cmp_["S1"]["diffs"][0]["lost"] == ["b"]
    assert cmp_["S1"]["diffs"][0]["gained"] == ["c"]
    stable = isem.compare_matched_sets(base, {"S2": base})
    assert stable["S2"]["stable"] is True


def test_cli_freeze_receipt_and_manifest_guard(tmp_path):
    import subprocess
    receipt_path = tmp_path / "receipt.json"
    r = subprocess.run(
        [sys.executable,
         str(REPO / "scripts/eval_interest_holdout.py"),
         "freeze-receipt", "--out", str(receipt_path)],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == \
        "EVALUATOR_READY_WAITING_ON_INFERENCE_FREEZE"
    assert receipt["candidate_inference_implementation"] == \
        "NOT_YET_FROZEN"

    victim = tmp_path / "artifact.py"
    victim.write_text("x = 1\n", encoding="utf-8")
    digest = isem.sha256_file(victim)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"frozen_artifacts": [
        {"path": str(victim), "sha256": digest}]}),
        encoding="utf-8")

    def guarded_run():
        return subprocess.run(
            [sys.executable,
             str(REPO / "scripts/eval_interest_holdout.py"),
             "score", "--gt", "X", "--result", "X", "--out",
             str(tmp_path / "r.json"), "--allow-holdout",
             "--manifest", str(manifest)],
            capture_output=True, text=True, cwd=str(REPO))

    r_ok = guarded_run()   # hashes match: proceeds into score inputs
    assert "refusing to open holdout" not in (r_ok.stdout + r_ok.stderr)
    victim.write_text("x = 2\n", encoding="utf-8")
    r_drift = guarded_run()
    assert r_drift.returncode == 3
    assert "drifted" in r_drift.stderr


def test_score_refuses_without_explicit_holdout_flag(tmp_path):
    import subprocess
    p = write_gt(tmp_path, {})
    rp = tmp_path / "res.json"
    rp.write_text("{}", encoding="utf-8")
    r = subprocess.run(
        [sys.executable,
         str(REPO / "scripts/eval_interest_holdout.py"),
         "score", "--gt", str(p), "--result", str(rp),
         "--out", str(tmp_path / "o.json")],
        capture_output=True, text=True, cwd=str(REPO))
    assert "refusing" in (r.stderr + r.stdout)


def test_result_view_wrapper_form_bindings():
    wrapped = {"final": RESULT_GOOD,
               "fragment_dispositions": [{"fragment_id": "f1",
                                          "decision": "kept"}]}
    view = isem.ResultView.from_payload(wrapped,
                                        eligible_cluster_ids=(
                                            eligible_ids()))
    assert len(view.interest_core) == 6
    assert view.dispositions is not None


# ------------------------------------------- ARCHITECT_AMENDMENT_1 tests

def fs_rows(rep):
    # report stores per-type map and aggregate at distinct top-level keys
    return {"per_type": rep["finite_set_conformance"],
            "aggregate": rep["overall_finite_set_conformance"]}


def test_amendment_n4_insufficient_but_perfect(sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest", f"topic number {k}")
            for k in range(4)]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [
        interest_obj(f"topic number {k}", cluster=10 + k)
        for k in range(4)],
        "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        stability_results=True)
    assert rep["tracks"]["Interest"]["verdict"] == \
        "INSUFFICIENT_EVIDENCE"
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "PERFECT"
    assert fi["provenance_valid_recovered"] == 4
    assert all(it["provenance_valid_match"]
               for it in fi["items"])
    assert fi["explicit_negative_hits"] == []


def test_amendment_one_miss_is_imperfect_with_gen_insufficient(
        sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest", f"topic number {k}")
            for k in range(4)]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    # surface list omits topic number 3 -> one scorable miss; the last
    # surface duplicates topic 2 with dangling refs -> provenance hit
    ints = [interest_obj(f"topic number {k}", cluster=10 + k)
            for k in range(3)]
    bad = interest_obj("topic number 2 alt", cluster=99999)
    ints.append(bad)
    payload = {"inferred_interests": ints,
               "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        eligible_cluster_ids=eligible_ids(),
                        stability_results=True)
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "IMPERFECT"
    assert rep["tracks"]["Interest"]["verdict"] == \
        "INSUFFICIENT_EVIDENCE"
    missed = [it for it in fi["items"] if it["missed"]]
    assert len(missed) >= 1


def test_amendment_matching_negative_is_imperfect(sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest", f"topic number {k}")
            for k in range(4)]
    rows.append(gt_label("neg1", "InterestNegative",
                         "drip coffee machine reviews"))
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [
        interest_obj(f"topic number {k}", cluster=20 + k)
        for k in range(4)] + [
        interest_obj("drip coffee machine reviews", cluster=29)],
        "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        stability_results=True)
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "IMPERFECT"
    assert fi["explicit_negative_hits"] == ["neg1"]
    neg_item = next(it for it in fi["items"]
                    if it["role"] == "negative")
    assert neg_item["semantically_inferred"] is True


def test_amendment_wrong_class_negative_never_touches_interest(
        sealed_gt_factory):
    rows = [gt_label(f"i{k}", "Interest", f"topic number {k}")
            for k in range(4)]
    rows.append(gt_label("s_payw", "SourceNegative",
                         "paywalled blog reposts"))
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [
        interest_obj(f"topic number {k}", cluster=30 + k)
        for k in range(4)] + [
        interest_obj("paywalled blog reposts aggregator", 39)],
        "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        stability_results=True)
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "PERFECT"
    assert "SourceNegative" not in fs_rows(rep)["per_type"]
    # no InterestNegative labels exist -> its track simply absent
    assert "InterestNegative" not in rep["tracks"]
    assert any(x["semantic_class"] == "SourceNegative" for x in
               rep["non_interest_negatives_not_scoring_interest"])


def test_amendment_zero_scorable_is_not_evaluable(sealed_gt_factory):
    rows = [gt_label("i1", "Interest", "alpha pursuit",
                     scor="corpus_unscorable"),
            gt_label("i2", "Interest", "beta pursuit",
                     scor="unknown")]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {"inferred_interests": [], "questions": [],
               "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "NOT_EVALUABLE"


def test_amendment_semantic_vs_provenance_fields_reported(
        sealed_gt_factory):
    rows = [gt_label("q1", "Question",
                     "Some open calibration question?")]
    gt = isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))
    payload = {
        "inferred_interests": [],
        "questions": [{"text": "Some open calibration question?",
                       "interest": "missing-owner",
                       "status": "open"}],
        "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    qm = rep["tracks"]["Question"]
    item = qm["per_item"][0]
    assert item["semantic_match"] is True
    assert item["provenance_valid_match"] is False
    fi = fs_rows(rep)["per_type"]["Question"]
    assert fi["status"] == "IMPERFECT"
    assert fs_rows(rep)["aggregate"]["imperfect"] == ["Question"]


# ------------------------------------------- BINDING_AMENDMENT_2 tests
# F3: the frozen finite-set semantics must stay EVALUABLE when a type
# has zero corpus-scorable positives but carries its own negative
# class — only the negative side decides (eval_interest_semantic.py
# zero-scorable branch), never NOT_EVALUABLE while a negative exists.

def f3_gt(sealed_gt_factory, scor):
    rows = [gt_label("i1", "Interest", "alpha pursuit", scor=scor),
            gt_label("neg1", "InterestNegative",
                     "drip coffee machine reviews")]
    return isem.load_ground_truth(
        sealed_gt_factory({"labels": rows}))


def test_amendment_f3_zero_scorable_matching_negative_imperfect(
        sealed_gt_factory):
    gt = f3_gt(sealed_gt_factory, "corpus_unscorable")
    payload = {"inferred_interests": [
        interest_obj("drip coffee machine reviews", 41)],
        "questions": [], "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "IMPERFECT"  # evaluable: negative decides
    assert fi["n_scorable_positives"] == 0
    assert fi["explicit_negative_hits"] == ["neg1"]
    neg_item = next(it for it in fi["items"]
                    if it["role"] == "negative")
    assert neg_item["semantically_inferred"] is True
    assert "Interest" in fs_rows(rep)["aggregate"]["imperfect"]


def test_amendment_f3_zero_scorable_clean_negative_perfect(
        sealed_gt_factory):
    gt = f3_gt(sealed_gt_factory, "corpus_unscorable")
    payload = {"inferred_interests": [], "questions": [],
               "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge())
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["status"] == "PERFECT"  # evaluable: clean negative side
    assert fi["n_scorable_positives"] == 0
    assert fi["explicit_negative_hits"] == []
    assert "Interest" in fs_rows(rep)["aggregate"]["perfect"]


def test_amendment_f3_unknown_scorability_probe_resolves_to_scorable(
        sealed_gt_factory):
    # same shape as the unscorable pair but the unknown-scorability
    # positive is probe-resolved SCORABLE: the branch boundary between
    # "zero scorable positives" and "one scorable positive" is pinned.
    gt = f3_gt(sealed_gt_factory, "unknown")
    payload = {"inferred_interests": [
        interest_obj("alpha pursuit", 42)], "questions": [],
        "regret_candidates": []}
    rep = isem.evaluate(gt, payload, AlwaysNoJudge(),
                        support_hits_by_label={"i1": [7]})
    fi = fs_rows(rep)["per_type"]["Interest"]
    assert fi["n_scorable_positives"] == 1
    assert fi["status"] == "PERFECT"


# --- F1: support carries the same holdout authorization boundary ----

def _cli_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "isem_cli_under_test",
        REPO / "scripts" / "eval_interest_holdout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f1_support_refuses_private_path_without_flag(
        tmp_path, monkeypatch):
    cli = _cli_module()
    private = tmp_path / "private"
    private.mkdir()
    gt = private / "holdout.json"
    gt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "PRIVATE_GT_DIR", private)
    with pytest.raises(SystemExit) as ei:
        cli.main(["support", "--gt", str(gt),
                  "--out", str(tmp_path / "s.json")])
    assert "refusing to open holdout" in str(ei.value)


def test_f1_support_refuses_sealed_digest_copy_without_flag(
        tmp_path, monkeypatch):
    cli = _cli_module()
    gt = tmp_path / "elsewhere.json"
    gt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "PRIVATE_GT_DIR",
                        tmp_path / "private")  # path check will miss
    monkeypatch.setattr(isem, "SEALED_GT_SHA256",
                        isem.sha256_file(gt))
    with pytest.raises(SystemExit) as ei:
        cli.main(["support", "--gt", str(gt),
                  "--out", str(tmp_path / "s.json")])
    assert "refusing to open holdout" in str(ei.value)


def test_f1_support_private_path_allowed_with_flag(
        tmp_path, monkeypatch):
    import ef.evidence_clusters as clusters_mod
    cli = _cli_module()
    private = tmp_path / "private"
    private.mkdir()
    gt = private / "holdout.json"
    gt.write_text(json.dumps(full_gt()), encoding="utf-8")
    monkeypatch.setattr(cli, "PRIVATE_GT_DIR", private)
    monkeypatch.setattr(clusters_mod, "cached_clusters",
                        lambda: ([{"cluster_id": 7, "label": "x",
                                   "terms": [], "entities": [],
                                   "representative": []}], {}))
    rc = cli.main(["support", "--gt", str(gt),
                   "--out", str(tmp_path / "s.json"),
                   "--allow-holdout"])
    assert rc == 0
    out = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "support_by_label_id" in out


def test_f1_support_synthetic_gt_still_runs_without_flag(
        tmp_path, monkeypatch):
    import ef.evidence_clusters as clusters_mod
    cli = _cli_module()
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(full_gt()), encoding="utf-8")
    monkeypatch.setattr(cli, "PRIVATE_GT_DIR",
                        tmp_path / "private")  # does not exist
    monkeypatch.setattr(clusters_mod, "cached_clusters",
                        lambda: ([], {}))
    rc = cli.main(["support", "--gt", str(gt),
                   "--out", str(tmp_path / "s.json")])
    assert rc == 0
