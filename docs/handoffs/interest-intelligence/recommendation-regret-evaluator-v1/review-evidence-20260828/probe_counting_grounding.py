"""Reviewer probes: counting integrity, floor semantics, generalization
language, personal-regret grounding. Independent of the author suite."""
import sys, json
sys.path.insert(0, r"P:/tmp/rre-am2-review/tree/packages/yt-is")
from ef import eval_recommendation_regret as rre

ok = []
def check(name, cond, detail=""):
    ok.append((name, bool(cond), detail))

INV = rre.FIXTURE_INVENTORY
items = rre.fixture_items()
strong, known, novel_low, redundant = items

def rec(pid, pref, inv=None, secret=None):
    """pair record: pref in {'baseline','challenger','TIE','NEITHER'};
    arm A = strong item (baseline), arm B = weak-ish (challenger)."""
    secret = secret or rre.new_binding_secret()
    rm = rre.canonical_binding(pid, ["A", "B"], "rre_v1_pairwise")["reverse_map"]
    outcome = {"baseline": rm["A"], "challenger": rm["B"],
               "TIE": "TIE", "NEITHER": "NEITHER"}[pref]
    pkg = rre.build_blinded_packet(pid, {"A": [strong], "B": [novel_low]},
                                   recent_surfaced_items=["fixture_video_delta"],
                                   binding_secret=secret)
    return rre.make_demo_pair_record(pid, strong, novel_low,
                                     pkg["blind_sidecar"],
                                     judgments={"WOULD_REGRET_MISSING": outcome,
                                                "MORE_USEFUL_NOW": "TIE"},
                                     evidence_inventory=inv or INV,
                                     recent_surfaced_items=["fixture_video_delta"],
                                     binding_secret=secret)

rm0 = rre.canonical_binding("probe-map", ["A", "B"], "rre_v1_pairwise")["reverse_map"]
baseline_outcome = None
challenger_outcome = None  # unused now; kept for T3/T5 record shapes

# T1: duplicates cannot inflate denominator (exact dup deduped, receipted)
base = rec("dup-pkt", "baseline")
r = rre.aggregate_pair_results([base, dict(base)])
c = r["WOULD_REGRET_MISSING"]["counts"]
check("T1 exact duplicate deduped", c["duplicate_records_deduplicated"] == 1
      and c["pairs_total"] == 1, str(c))
check("T1 dup not double counted in decided", c["A_strict_wins"] == 1)

# T2: conflicting duplicate fails closed
bad = dict(base); bad["extra_marker"] = "x"
try:
    rre.aggregate_pair_results([base, bad])
    check("T2 conflicting dup raises", False, "no exception")
except rre.PacketBindError:
    check("T2 conflicting dup raises", True)

# T3: provisional packets cannot satisfy the floor (distinct packet ids)
prov = rec("prov-pkt", "challenger", inv=None)  # no inventory -> UNRESOLVED
prov["prov_item1"] = "UNRESOLVED_EVIDENCE_REF"
prov["prov_item2"] = "UNRESOLVED_EVIDENCE_REF"
recs = []
for i in range(7):
    p = dict(prov)
    p["packet_id"] = f"prov-pkt-{i}"
    recs.append(p)
rep = rre.evaluate(recs, judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
q3 = rep["per_question"]["WOULD_REGRET_MISSING"]
floor = q3["diagnostic_coverage_status"]
check("T3 provisional cannot meet floor",
      floor["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
      and q3["provisional_pairs_excluded"] == 7,
      json.dumps(floor))

# T4: 6 challenger wins meet floor; floor label non-inferential wording
recs = [rec(f"cw-{i}", "challenger") for i in range(6)]
rep = rre.evaluate(recs, judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
q = rep["per_question"]["WOULD_REGRET_MISSING"]
check("T4 floor met at 6 decided/6 distinct",
      q["diagnostic_coverage_status"]["status"] == rre.DIAGNOSTIC_FLOOR_MET)

# T4b: similarity axis ABSENT -> falsifier can never pass
def rec_plain(pid, judge_pref="challenger", secret=None):
    secret = secret or rre.new_binding_secret()
    a = dict(strong); a.pop("similarity_diagnostic")
    b = dict(novel_low); b.pop("similarity_diagnostic")
    rm = rre.canonical_binding(pid, ["A", "B"], "rre_v1_pairwise")["reverse_map"]
    slot_B = rm["B"]
    outcome = slot_B if judge_pref == "challenger" else rm["A"]
    pkg = rre.build_blinded_packet(pid, {"A": [a], "B": [b]},
                                   binding_secret=secret)
    r = rre.make_demo_pair_record(pid, a, b, pkg["blind_sidecar"],
        judgments={"WOULD_REGRET_MISSING": outcome, "MORE_USEFUL_NOW": "TIE"},
        evidence_inventory=INV, binding_secret=secret)
    return r
rep_ns = rre.evaluate([rec_plain(f"ns-{i}") for i in range(6)],
                      judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
qns = rep_ns["per_question"]["WOULD_REGRET_MISSING"]
check("T4b untested similarity axis never passes",
      qns["primary_falsifier"]["verdict"]
      == "CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED"
      and qns["primary_falsifier"]["untested_axes"]
      == ["semantic_similarity_diagnostic"],
      qns["primary_falsifier"]["verdict"])

# T5: 4 decided + 5th provisional -> floor NOT met (distinct eligible decided < 5)
recs = [rec(f"mix-{i}", "challenger") for i in range(4)]
p5 = rec("mix-4", "challenger")
p5["prov_item1"] = "MISSING_CLAIMS"
recs.append(p5)
rep = rre.evaluate(recs, judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
q = rep["per_question"]["WOULD_REGRET_MISSING"]
check("T5 floor not met at 4 eligible decided",
      q["diagnostic_coverage_status"]["status"] == rre.DIAGNOSTIC_FLOOR_NOT_MET
      and q["diagnostic_coverage_status"]["n_distinct_decided_packets"] == 4)

# T6: n=5 exact floor met but no generalization language anywhere in report
recs = [rec(f"n5-{i}", "challenger") for i in range(5)]
rep = rre.evaluate(recs, judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
blob = json.dumps(rep)
banned = ["SUFFICIENT_EVIDENCE", "generalization SUFFICIENT",
          "population-level confidence", "statistically significant",
          "production-ready", "promote"]
hits = [b for b in banned if b.lower() in blob.lower()]
check("T6 no generalization vocabulary in report at n=5", not hits, str(hits))
check("T6 scope label present",
      "no population-level" in blob.replace("population-level, ", "population-level"))

# T7: repeated marks cannot inflate listwise counts (dedup at unbind + agg)
pkg = rre.build_blinded_list_packet(
    "list-1", {"A": [strong, known, novel_low], "B": [novel_low, known, strong]},
    k=3, recent_surfaced_items=[])
blinded = pkg["blinded_packet"]
tokA = pkg["blind_sidecar"]["reverse_map"]["A"]
listed = [v["item_id"] for v in blinded["lists"][tokA]]
judgment = {"outcome": tokA,
            "regret_marks": {tokA: [listed[0], listed[0], listed[0]],
                             "LIST_2" if tokA == "LIST_1" else "LIST_1": []}}
un = rre.unbind_list_result(blinded, pkg["blind_sidecar"], judgment,
                            binding_secret=pkg["binding_secret"])
check("T7 repeated marks deduped at unbind",
      len(un["regret_marks_by_arm"]["A"]) == 1,
      str(un["regret_marks_by_arm"]))
agg = rre.aggregate_list_results([dict(un,
     challenger_arm="B", baseline_arm="A", prov_valid_all=True,
     list_ids_by_arm={"A": [i["item_id"] for i in blinded["lists"][pkg["blind_sidecar"]["reverse_map"]["A"]]],
                      "B": [i["item_id"] for i in blinded["lists"][pkg["blind_sidecar"]["reverse_map"]["B"]]]})])
# arm A (baseline) was preferred and carries 1 deduped mark; challenger 0
check("T7 listwise totals dedup",
      agg["regret_marked_items_challenger"] == 0
      and agg["regret_marked_items_baseline"] == 1
      and agg["counts"]["baseline_list_wins"] == 1,
      json.dumps(agg))

# T8: personal-regret grounding — judge instruments items for an OPERATOR,
# report says finite-set only; check report fields
check("T8 evidence class carried",
      rep["evidence_class"] == "synthetic_fixture" and
      rep["per_question"]["WOULD_REGRET_MISSING"]["evidence_class"] == "synthetic_fixture")
check("T8 off-policy stance text", "propensity=UNKNOWN" in json.dumps(rep))
check("T8 dimensions diagnostic-only note",
      "DIAGNOSTIC-ONLY" in json.dumps(rep))
# evaluate refuses missing/invalid evidence class
for bad_cls in (None, "", "operator_ground_truth", "curated"):
    try:
        rre.evaluate([base], judgments_evidence_class=bad_cls)
        check(f"T8 evidence class {bad_cls!r} refused", False)
    except rre.PacketBindError:
        check(f"T8 evidence class {bad_cls!r} refused", True)
    except TypeError:
        check(f"T8 evidence class {bad_cls!r} refused (TypeError)", True)

# T9: generalization claim function is gone (v1 companion removed)
check("T9 no SUFFICIENT_EVIDENCE constant",
      not any("SUFFICIENT" in s for s in dir(rre)))

# T10: MORE_USEFUL_NOW gates nothing (no falsifier key)
check("T10 usefulness question has no falsifier",
      "primary_falsifier" not in rep["per_question"]["MORE_USEFUL_NOW"])

fails = [x for x in ok if not x[1]]
for name, passed, detail in ok:
    print(("PASS" if passed else "FAIL"), name, detail if not passed else "")
print("SUMMARY:", len(ok) - len(fails), "/", len(ok), "passed")
