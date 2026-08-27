#!/usr/bin/env python3
"""Post-seal mechanical comparator for the corrected-time replay.

WRITTEN BLIND before the replay ran and before any historical result
artifact was opened; frozen in the replay-manifest commit. Reads ONLY
JSON artifacts (never narrative documents). Emits:

  1. subject-by-subject transitions historical -> corrected, per family
     (positives TP->TP/TP->FN/FN->TP/FN->FN; negatives FP->TN/FP->FP/
     TN->FP/TN->TN, TP/FP := family confirmed flag) plus candidate-state
     (armA candidate_ever / a_emerging_ever) and confirmation-state
     (per-family delay deltas) transitions;
  2. decomposition historical -> shadow-prerepair -> corrected (isolates
     catalog drift from the time-semantics dimension);
  3. causal classification per the mechanical rule frozen in
     REPLAY_MANIFEST.json (thresholds fixed a priori: 0.10 absolute);
  4. per-family corrected-time bar tables under the ORIGINAL frozen
     decision mapping and the mechanical enum candidates.

Usage: replay_compare.py --historical-dir DIR --corrected-dir DIR
                         --shadow-dir DIR --out PATH
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

FAMILIES = ["armB_EU1-W30", "armB_EU2-W60", "armB_BUCKETS-W120",
            "armB_POSTERIOR-EXCL-W30", "armB_CHANNELNEW-W30",
            "armC", "armD"]
MATERIAL_DELTA = 0.10  # frozen a priori in REPLAY_MANIFEST.json


def load_rows(path: Path) -> dict:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["sid"]: r for r in rows}


def transitions(old: dict, new: dict, fam: str) -> dict:
    out = {"pos": {}, "neg": {}, "unpaired_sids": []}
    for sid, o in old.items():
        n = new.get(sid)
        if n is None:
            out["unpaired_sids"].append(sid)
            continue
        ok = bool(o.get(fam + "_confirmed"))
        nk = bool(n.get(fam + "_confirmed"))
        kind = o["kind"]
        if kind != n["kind"]:
            out["unpaired_sids"].append(sid + ":kind-flip")
            continue
        if kind == "positive":
            key = ("TP" if ok else "FN") + "->" + ("TP" if nk else "FN")
        else:
            key = ("FP" if ok else "TN") + "->" + ("FP" if nk else "TN")
        out[kind].setdefault(key, []).append(sid)
    out["pos"] = {k: sorted(v) for k, v in sorted(out["pos"].items())}
    out["neg"] = {k: sorted(v) for k, v in sorted(out["neg"].items())}
    return out


def state_transitions(old: dict, new: dict) -> dict:
    out = {}
    for field in ("candidate_ever", "a_emerging_ever"):
        cells: dict[str, list] = {}
        for sid, o in old.items():
            n = new.get(sid)
            if n is None:
                continue
            key = f"{bool(o.get(field))}->{bool(n.get(field))}"
            cells.setdefault(key, []).append(sid)
        out[field] = {k: {"n": len(v), "sids": sorted(v)}
                      for k, v in sorted(cells.items())}
    return out


def delay_deltas(old: dict, new: dict, fam: str) -> dict:
    pairs = []
    for sid, o in old.items():
        n = new.get(sid)
        if n is None or o["kind"] != "positive":
            continue
        od, nd = o.get(fam + "_delay"), n.get(fam + "_delay")
        if od is not None and nd is not None:
            pairs.append({"sid": sid, "old": od, "new": nd,
                          "delta": nd - od})
    out = {"n_both_confirmed": len(pairs), "pairs": pairs}
    if pairs:
        out["median_old"] = statistics.median(p["old"] for p in pairs)
        out["median_new"] = statistics.median(p["new"] for p in pairs)
    return out


def bar_pass_count(entry: dict, d4: bool, d7: bool) -> int:
    bars = entry.get("bars", {})
    return sum(1 for b in (bars.get("D1_material_neg_drop"),
                           bars.get("D2_pos_useful"),
                           bars.get("D3_separation_beats_A"),
                           d4, d7) if b)


def family_bars(hist_dir: Path, fam: str) -> dict:
    """Mechanical D1/D2/D3 from the frozen decision-bars.json plus D4/D5/D7
    from the run's own frozen artifacts (prereg bars made numeric)."""
    dec = json.loads((hist_dir / "decision-bars.json").read_text(
        encoding="utf-8"))
    agg = json.loads((hist_dir / "aggregate.json").read_text(
        encoding="utf-8"))
    cf = json.loads((hist_dir / "counterfactuals.json").read_text(
        encoding="utf-8"))
    entry = dec.get(fam, {})
    bars = entry.get("bars", {})
    d4 = None
    for frac in ("perturbation20", "perturbation10"):
        axis = agg.get("perturbation", {}).get(frac, {}).get(
            fam + "_confirmed_retained")
        if frac == "perturbation20":
            d4 = bool(axis and axis.get("ratio") is not None
                      and axis["ratio"] >= 0.50)
    d5 = bool(agg.get("determinism", {}).get("identical")) and all(
        r.get("checks", {}).get("inject_future", {}).get("identical")
        for r in cf if r.get("checks", {}).get("inject_future"))
    stab = agg.get("stability_leave_one_out", {}).get(fam, {})
    d7 = True
    if {"lo_negative_rate_range", "n_neg"} <= set(stab.keys()) and \
            stab["n_neg"] > 1:
        anchor = 72 / 124
        lo, hi = stab["lo_negative_rate_range"]
        v_lo = (lo <= 0.35 and lo <= 0.50 * anchor)
        v_hi = (hi <= 0.35 and hi <= 0.50 * anchor)
        if v_lo != v_hi:
            d7 = False
    supported = bool(bars.get("D1_material_neg_drop")
                     and bars.get("D2_pos_useful")
                     and bars.get("D3_separation_beats_A")
                     and d4 and d5 and d7)
    return {"bars": {"D1": bars.get("D1_material_neg_drop"),
                     "D2": bars.get("D2_pos_useful"),
                     "D3": bars.get("D3_separation_beats_A"),
                     "D4_perturb20_retention": d4,
                     "D5_determinism_noleak": d5,
                     "D7_loo_stable": d7},
            "family_supported": supported,
            "bar_pass_count": bar_pass_count(
                {"bars": bars}, bool(d4), bool(d7))}


def rates(agg: dict, fam: str) -> dict:
    if fam == "armA":
        return {"pos_rate": agg["armA"]["positive_emerging_recall"]["rate"],
                "neg_rate": agg["armA"]["negative_emerging_rate"]["rate"],
                "separation": agg["armA"]["separation"]}
    e = agg[fam]
    return {"pos_rate": e["positive_rate"]["rate"],
            "neg_rate": e["negative_rate"]["rate"],
            "separation": e["separation"]}


def classify(q_old: set, q_new: set, agg_old: dict, agg_new: dict,
             bars_old: dict, bars_new: dict, cohorts_new: dict) -> dict:
    out = {"qualified_old": sorted(q_old),
           "qualified_new": sorted(q_new),
           "cohort_guard": {
               "positives_scorable":
                   cohorts_new.get("positives_scorable"),
               "negatives_paired_scorable":
                   cohorts_new.get("negatives_paired_scorable"),
               "insufficient":
                   (cohorts_new.get("negatives_paired_scorable", 0) < 40
                    or cohorts_new.get("positives_scorable", 0) < 40)}}
    if out["cohort_guard"]["insufficient"]:
        out["class"] = "COHORT_INSUFFICIENT_FOR_CAUSAL_CLASSIFICATION"
        return out
    if q_new != q_old:
        out["class"] = "TIME_CORRECTION_REVERSES_PRIOR_FRONTIER"
        out["reason"] = ("family-supported set changed: "
                         f"{sorted(q_old)} -> {sorted(q_new)}")
        return out
    improving = []
    for fam in FAMILIES:
        ro, rn = rates(agg_old, fam), rates(agg_new, fam)
        neg_drop = ro["neg_rate"] - rn["neg_rate"]
        sep_gain = (rn["separation"] - ro["separation"]) \
            if (rn["separation"] is not None
                and ro["separation"] is not None) else 0.0
        bpc_old = bars_old[fam]["bar_pass_count"]
        bpc_new = bars_new[fam]["bar_pass_count"]
        if (neg_drop >= MATERIAL_DELTA or sep_gain >= MATERIAL_DELTA) \
                and bpc_new >= bpc_old:
            improving.append({"family": fam,
                              "neg_rate_drop": round(neg_drop, 4),
                              "separation_gain": round(sep_gain, 4),
                              "bar_pass_old": bpc_old,
                              "bar_pass_new": bpc_new})
    if q_new:
        out["class"] = ("TIME_CORRECTION_MATERIALLY_IMPROVES_BUT_"
                        "FRONTIER_REMAINS" if improving else
                        "TIME_CORRECTION_NO_MATERIAL_EFFECT")
        out["same_supported_set"] = sorted(q_new)
    else:
        out["class"] = ("TIME_CORRECTION_MATERIALLY_IMPROVES_BUT_"
                        "FRONTIER_REMAINS" if improving else
                        "TIME_CORRECTION_NO_MATERIAL_EFFECT")
    out["materially_improved_families"] = improving
    out["rule"] = ("thresholds frozen a priori: material = "
                   f"|delta| >= {MATERIAL_DELTA} on neg confirmed rate "
                   "(old - new) or separation (new - old), with "
                   "bar-pass count not decreasing")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--historical-dir", required=True)
    ap.add_argument("--corrected-dir", required=True)
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    old = load_rows(Path(a.historical_dir) / "metric-rows.json")
    shadow = load_rows(Path(a.shadow_dir) / "metric-rows.json")
    new = load_rows(Path(a.corrected_dir) / "metric-rows.json")
    agg_old = json.loads((Path(a.historical_dir) / "aggregate.json")
                         .read_text(encoding="utf-8"))
    agg_shadow = json.loads((Path(a.shadow_dir) / "aggregate.json")
                            .read_text(encoding="utf-8"))
    agg_new = json.loads((Path(a.corrected_dir) / "aggregate.json")
                         .read_text(encoding="utf-8"))

    per_family = {}
    for fam in FAMILIES:
        entry = {
            "transitions_old_to_corrected": transitions(old, new, fam),
            "transitions_old_to_shadow": transitions(old, shadow, fam),
            "transitions_shadow_to_corrected":
                transitions(shadow, new, fam),
            "delay_deltas_old_to_corrected":
                delay_deltas(old, new, fam),
            "bars_corrected": family_bars(Path(a.corrected_dir), fam),
            "bars_shadow": family_bars(Path(a.shadow_dir), fam),
        }
        per_family[fam] = entry

    q_old = {f for f in FAMILIES
             if family_bars(Path(a.historical_dir), f)["family_supported"]}
    q_shadow = {f for f in FAMILIES
                if per_family[f]["bars_shadow"]["family_supported"]}
    q_new = {f for f in FAMILIES
             if per_family[f]["bars_corrected"]["family_supported"]}
    bars_old = {f: family_bars(Path(a.historical_dir), f)
                for f in FAMILIES}
    bars_new = {f: per_family[f]["bars_corrected"] for f in FAMILIES}
    causal = classify(q_old, q_new, agg_old, agg_new, bars_old, bars_new,
                      agg_new.get("cohorts", {}))

    out = {
        "kind": "corrected_time_replay_comparison",
        "blind_written": "pre-run; frozen in replay-manifest commit",
        "sid_coverage": {
            "historical": len(old), "shadow": len(shadow),
            "corrected": len(new),
            "in_old_not_new": sorted(set(old) - set(new)),
            "in_new_not_old": sorted(set(new) - set(old)),
        },
        "state_transitions_old_to_corrected":
            state_transitions(old, new),
        "state_transitions_shadow_to_corrected":
            state_transitions(shadow, new),
        "per_family": per_family,
        "qualified_families": {"old": sorted(q_old),
                               "shadow": sorted(q_shadow),
                               "corrected": sorted(q_new)},
        "causal_classification": causal,
        "enum_candidates_mechanical": (
            "POST_TRIGGER_CONFIRMATION_SUPPORTED"
            if any(f.startswith("armB_") for f in q_new)
               and not (q_new - {f for f in q_new
                                 if f.startswith("armB_")})
            else "EPISODE_STATE_MODEL_SUPPORTED" if q_new == {"armC"}
            else "TWO_SIGNAL_MODEL_SUPPORTED" if q_new == {"armD"}
            else "NO_NEW_MODEL_SUPPORTED" if not q_new
            else "QUALIFIED_SET_NEEDS_STRUCTURAL_SELECTION"),
    }
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"causal": causal["class"],
                      "qualified_old": sorted(q_old),
                      "qualified_new": sorted(q_new),
                      "out": a.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
