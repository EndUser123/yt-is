"""E3 step 7 — adjudicate blinded reviews + apply the frozen decision rule.

Reads reviews/<reviewer>/results.json x2 + ARM-KEY.json (+ STABILITY.json
and LABELING-SUMMARY.json when present). Emits EVALUATION.json with the
decision enum result. Every threshold mirrors PREREGISTRATION.md v3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import e3lib as L

AXES = ["REFERENT_FIDELITY", "SPECIFICITY", "CLARITY", "GRANULARITY",
        "ARTIFACT_FREE"]
FLAGS = ["TOO_GENERIC", "TOO_NARROW", "WRONG_TOPIC", "ARTIFACT", "AMBIGUOUS"]
ARMS = ["A0", "A1", "B", "C"]
REVIEWERS = ["reviewer-nemotron-ultra", "reviewer-zcode-glm"]


def load():
    key = json.loads((L.EF_DATA / "ARM-KEY.json").read_text(encoding="utf-8"))
    res = {}
    for rev in REVIEWERS:
        p = L.EF_DATA / "reviews" / rev / "results.json"
        res[rev] = json.loads(p.read_text(encoding="utf-8"))["items"]
        assert isinstance(res[rev], list) and res[rev]
    return key, res


def main() -> int:
    key, results = load()
    def alias_of(cid, rev):
        return {arm: v["alias"] for arm, v in key[cid][rev].items()}
    mask2cid = json.loads(
        (L.EF_DATA / "MASK-KEY.json").read_text(encoding="utf-8"))
    # rekey reviewer results by REAL cluster id via their R-mask
    by_cid = {}
    for rev in REVIEWERS:
        m = {}
        for it in results[rev]:
            mask = it["cid"]
            real = str(mask2cid[mask])
            m[real] = it
        by_cid[rev] = m
    cids = sorted(key.keys())
    missing = [(rev, cid) for rev in REVIEWERS for cid in cids
               if cid not in by_cid[rev]]
    if missing:
        print("missing reviewer items:", missing[:10])
        return 1

    # ---- adjudication ----
    axes_mean = {a: {} for a in ARMS}          # arm -> axis -> mean
    flags_rate = {a: {} for a in ARMS}
    harsh_resolved = 0; total_axis_items = 0; within1 = 0
    detail = {}
    for ax in AXES:
        for arm in ARMS:
            vals = []
            for cid in cids:
                s = []
                for rev in REVIEWERS:
                    a2 = alias_of(cid, rev)
                    s.append(by_cid[rev][cid][ax][a2[arm]])
                total_axis_items += 1
                if abs(s[0] - s[1]) <= 1:
                    within1 += 1
                    vals.append((s[0] + s[1]) / 2)
                else:
                    harsh_resolved += 1
                    vals.append(float(min(s)))
            axes_mean[arm][ax] = sum(vals) / len(vals)
            detail.setdefault(ax, {})[arm] = [
                {"cid": cid,
                 "s": [by_cid[rev][cid][ax][alias_of(cid, rev)[arm]]
                     for rev in REVIEWERS]}
                for cid in cids]
    for fl in FLAGS:
        for arm in ARMS:
            hits = []
            for cid in cids:
                ored = False
                for rev in REVIEWERS:
                    a2 = alias_of(cid, rev)
                    ored |= bool(by_cid[rev][cid][fl][a2[arm]])
                hits.append(ored)
            flags_rate[arm][fl] = sum(hits) / len(hits)

    pref = {"decisive": {}, "ambiguous": []}
    for cid in cids:
        picks = []
        for rev in REVIEWERS:
            pick_alias = by_cid[rev][cid]["OVERALL_PREFERRED"]
            inv = {v["alias"]: arm for arm, v in key[cid][rev].items()}
            picks.append(inv[pick_alias])
        if picks[0] == picks[1]:
            pref["decisive"][cid] = picks[0]
        else:
            pref["ambiguous"].append({"cid": cid, "votes": picks})

    n_dec = len(pref["decisive"])
    ambiguity_rate = len(pref["ambiguous"]) / len(cids)
    agree_frac = within1 / max(total_axis_items, 1)

    def winrate(x: str) -> float | None:
        # share of decisive items where the contest reduced to X vs A0
        rel = [w for w in pref["decisive"].values() if w in (x, "A0")]
        if not rel:
            return None
        return sum(1 for w in rel if w == x) / len(rel)

    # ---- gates ----
    stability = None
    sp = L.EF_DATA / "STABILITY.json"
    if sp.exists():
        stability = json.loads(sp.read_text(encoding="utf-8"))
    burden_ok = None
    bp = L.EF_DATA / "LABELING-SUMMARY.json"
    if bp.exists():
        b = json.loads(bp.read_text(encoding="utf-8"))
        burden_ok = {
            "elapsed_s": b["elapsed_s"],
            "within_2h": b["elapsed_s"] <= 7200,
            "peak_rss_kb": b.get("peak_rss_kb"),
            "within_4gb": bool(b.get("peak_rss_kb") and
                               b["peak_rss_kb"] <= 4 * 1024 * 1024),
            "new_pinned_deps": 0,   # declared: uses preinstalled packages only
        }

    def stab_gate(arm):
        """Frozen gate: mean cosine >= 0.82; Arm C additionally must have
        its temperature-zero nondeterminism repeats DOCUMENTED (nonzero
        usable groups). Fail-closed when absent (prereg v6)."""
        if not stability:
            return None
        candidates = {"A1": ("A1", "A")}.get(arm, (arm,))
        rows_ = [r for name in candidates for r in stability["stability"]
                 if r["arm"] == name]
        if not rows_ or not rows_[0].get("n"):
            return False
        base = rows_[0]["mean_cosine"] >= 0.82
        if arm == "C":
            nd = stability.get("arm_c_nondeterminism") or {}
            base = base and bool(nd.get("groups")) and                 (nd.get("bitwise_identical_group_rate") or 0) > 0
        return base

    material = {}
    for arm in ("A1", "B", "C"):
        d_ref = axes_mean[arm]["REFERENT_FIDELITY"] - axes_mean["A0"]["REFERENT_FIDELITY"]
        d_cla = axes_mean[arm]["CLARITY"] - axes_mean["A0"]["CLARITY"]
        art_drop = flags_rate["A0"]["ARTIFACT"] - flags_rate[arm]["ARTIFACT"]
        gen_drop = flags_rate["A0"]["TOO_GENERIC"] - flags_rate[arm]["TOO_GENERIC"]
        sg = stab_gate(arm)
        checks = {
            "delta_referent": round(d_ref, 4),
            "delta_clarity": round(d_cla, 4),
            "both_primary_ge_035": d_ref >= 0.35 and d_cla >= 0.35,
            "artifact_rate_drop_pp": round(art_drop * 100, 2),
            "too_generic_drop_pp": round(gen_drop * 100, 2),
            "flag_condition": art_drop >= 0.05 or gen_drop >= 0.10,
            "stability_mean_cosine": next(
                (r["mean_cosine"] for nm in
                 ({"A1": ("A1", "A")}.get(arm, (arm,)))
                 for r in (stability or {}).get("stability", [])
                 if r["arm"] == nm), None),
            "stability_gate": sg if sg is not None else None,
            "burden": burden_ok,
        }
        checks["pass"] = (
            checks["both_primary_ge_035"]
            and checks["flag_condition"]
            and (sg is not None and sg)
            and bool(burden_ok and burden_ok["within_2h"]
                     and burden_ok["within_4gb"]))
        material[arm] = checks

    reliability_collapse = (agree_frac < 0.60) or (ambiguity_rate > 0.30)

    wr = {a: winrate(a) for a in ("A1", "B", "C")}
    challengers_pass = {a: material[a]["pass"] for a in ("B", "C")}
    weak_zone = all(
        material[a]["delta_referent"] > -0.05 for a in ("B", "C"))

    if reliability_collapse:
        decision = "INSUFFICIENT_EVIDENCE"
        reason = (f"reliability collapse: agreement={agree_frac:.2f}, "
                  f"ambiguity={ambiguity_rate:.2f}")
    elif all(challengers_pass.values()):
        decision = "HYBRID_REPRESENTATION_SUPPORTED"
        reason = "both B and C pass MATERIAL_BAR"
    elif challengers_pass["B"]:
        decision = "KEYBERT_REPRESENTATION_SUPPORTED"
        reason = "only B passes MATERIAL_BAR"
    elif challengers_pass["C"]:
        decision = "GENERATIVE_RELABEL_SUPPORTED"
        reason = "only C passes MATERIAL_BAR"
    elif weak_zone and any(w is not None and w >= 0.55 for w in wr.values()):
        a0_margin_ref = min(
            axes_mean["A0"]["REFERENT_FIDELITY"] - axes_mean[x]["REFERENT_FIDELITY"]
            for x in ("B", "C"))
        a0_dominates = (
            all(w is not None and w < 0.5 for w in wr.values())
            or a0_margin_ref >= 0.15)
        if a0_dominates:
            decision = "CURRENT_LABELS_SUPPORTED"
            reason = "no challenger passes bar; A0 dominant in decisive prefs"
        else:
            decision = "NO_MATERIAL_DIFFERENCE"
            reason = "signal present but below MATERIAL_BAR"
    else:
        decision = "CURRENT_LABELS_SUPPORTED"
        reason = "default: no challenger passes MATERIAL_BAR"

    out = {
        "n_items": len(cids),
        "reviewer_agreement_within1_fraction": round(agree_frac, 4),
        "harsh_resolved_fraction": round(harsh_resolved / max(total_axis_items, 1), 4),
        "preferred_ambiguous_rate": round(ambiguity_rate, 4),
        "axis_means": {a: {k: round(v, 4) for k, v in axes_mean[a].items()}
                       for a in ARMS},
        "flag_rates": {a: {k: round(v, 4) for k, v in flags_rate[a].items()}
                       for a in ARMS},
        "win_rates_vs_A0": wr,
        "preferred_distribution": {
            a: sum(1 for w in pref["decisive"].values() if w == a) for a in ARMS},
        "material_bar_checks": material,
        "reliability_collapse": reliability_collapse,
        "decision": decision,
        "decision_reason": reason,
    }
    (L.EF_DATA / "EVALUATION.json").write_text(json.dumps(out, indent=2),
                                               encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
