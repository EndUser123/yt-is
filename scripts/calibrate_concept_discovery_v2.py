"""Calibration experiment for a burst-policy-v2 family (packet 2026-08-25).

TRAINING/DIAGNOSTIC ONLY on consumed formal holdout-v4. Not imported by
production; never modifies production policy, the frozen evaluator, the
formal ledger, or holdout files.

Preregistered plan (frozen, hashed BEFORE any grid computation):
  P:/.data/yt-is/ef/concept-discovery-calibration/v2-policy-family/
  preregistered-plan.json
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "scripts"))

import evaluate_concept_discovery as ev  # noqa: E402
from ef import concept_discovery as cd  # noqa: E402
from ef import concept_registry as cr  # noqa: E402

def _load_targets_local(path):
    """Local legacy-targets loader (evaluator-v4 removed load_targets;
    concluded-campaign diagnostics keep a self-contained copy)."""
    import json as _json
    payload = _json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for t in payload.get("targets", []):
        t.setdefault("aliases", [])
        out.append(t)
    return out

PLAN_PATH = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
                 "v2-policy-family/preregistered-plan.json")
PLAN_SHA256 = "bb1c02999ef36f5ad474ea0b7336e8d3514d9e141f64508e51314b768d4f221a"
ART = Path("P:/.data/yt-is/ef/concept-discovery-eval/"
           "eval-20260825T114338-FORMAL")
CAL = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
           "v2-policy-family")
CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
CHECKPOINTS = ["T-30", "T", "T+7", "T+14", "T+30", "T+60"]
CP_OFFSETS = {"T-30": -30, "T": 0, "T+7": 7, "T+14": 14,
              "T+30": 30, "T+60": 60}

CANDIDATES = {
    "C0": {"window": 30, "min_recent": 2},
    "C1": {"window": 60, "min_recent": 2},
    "C2": {"window": 90, "min_recent": 2},
    "C3": {"window": 30, "min_recent": 1, "min_lifetime": 2},
    "C4": {"window": 60, "min_recent": 1, "min_lifetime": 2},
    "C5": {"window": 90, "min_recent": 1, "min_lifetime": 2},
}
RATIO_LEVELS = ["DISABLED", 1.25, 1.5, 2.0]
RECENT_LEVELS = [4, 5, 6]
CHANNEL_LEVELS = [1, 2, 3]
HOLDOUT = "P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json"

# aggregate tuple layout stored per entity per checkpoint
AGG = ("r30", "ratio30", "ch30", "st30", "r60", "r90", "lifetime", "fs")


# ---------------------------------------------------------------------------
# stats (mirror ef.concept_discovery._stats_for)
# ---------------------------------------------------------------------------

def stats_for_window(obs, as_of_d: date, window: int) -> dict:
    recent_start = as_of_d - timedelta(days=window)
    baseline_start = recent_start - timedelta(days=90)
    recent = [o for o in obs
              if recent_start < date.fromisoformat(o["obs_date"]) <= as_of_d]
    baseline = [o for o in obs
                if baseline_start < date.fromisoformat(o["obs_date"])
                <= recent_start]
    rc = len(recent)
    return {
        "recent_count": rc,
        "baseline_count": len(baseline),
        "channels": len({o["channel_id"] for o in recent}),
        "source_types": len({cd.SOURCE_LABELS.get(o["source"], o["source"])
                             for o in recent}),
        "smoothed_ratio": round((rc + 1) / (len(baseline) + 1), 4),
        "first_seen": min(o["obs_date"] for o in obs),
    }


def entity_feature(obs, as_of_d: date) -> dict:
    s30 = stats_for_window(obs, as_of_d, 30)
    return {
        "r30": s30["recent_count"], "ratio30": s30["smoothed_ratio"],
        "ch30": s30["channels"], "st30": s30["source_types"],
        "r60": stats_for_window(obs, as_of_d, 60)["recent_count"],
        "r90": stats_for_window(obs, as_of_d, 90)["recent_count"],
        "lifetime": len({o["eu_id"] for o in obs}),
        "fs": s30["first_seen"],
    }


def agg_to_f(tup) -> dict:
    return dict(zip(AGG, tup))


# ---------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------

def cand_pass(f, cdef) -> bool:
    rc = f["r30"] if cdef["window"] == 30 else \
        f["r60"] if cdef["window"] == 60 else f["r90"]
    if rc < cdef["min_recent"]:
        return False
    if cdef.get("min_lifetime") and f["lifetime"] < cdef["min_lifetime"]:
        return False
    return True


def emerge_pass(f, recent_min, ratio, channels) -> bool:
    return (f["r30"] >= recent_min
            and (ratio == "DISABLED" or f["ratio30"] >= ratio)
            and f["ch30"] >= channels)


def v1_pass(f) -> bool:
    return (f["r30"] >= 4 and f["ratio30"] >= 2.0 and f["ch30"] >= 3
            and f["st30"] >= 2)


def baseline_b_pass(f, as_of_d: date) -> bool:
    if f["r30"] < 4:
        return False
    return (as_of_d - date.fromisoformat(f["fs"])).days <= 60


def cfg_id(candidate, recent_min, ratio, channels):
    return {"candidate": candidate, "recent_min": recent_min,
            "ratio": ratio, "channels_min": channels}


# ---------------------------------------------------------------------------
# phase 1: extraction
# ---------------------------------------------------------------------------

def _matches(label, names_norm):
    ln = ev._norm(label)
    if not ln:
        return False
    for tn in names_norm:
        if tn and (ln == tn or ev._word_boundary_contains(ln, tn)
                   or ev._word_boundary_contains(tn, ln)):
            return True
    return False


def extract() -> None:
    digest = hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    if digest != PLAN_SHA256:
        sys.exit(f"plan hash mismatch: {digest}; refusing to run")
    targets = _load_targets_local(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    negs = json.loads((ART / "negative-controls.json").read_text())
    controls_by_target = {}
    for n in negs:
        controls_by_target.setdefault(n["target_id"], []).append(n["control_id"])

    out = {"plan_sha256": digest, "targets": []}
    cat = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    cat.row_factory = sqlite3.Row
    t0 = time.time()
    try:
        for i, t in enumerate(targets):
            entry = {"target_id": t["target_id"], "checkpoints": {}}
            t_date = date.fromisoformat(scor[t["target_id"]])
            names_norm = [ev._norm(n) for n in
                          [t["canonical_name"]] + list(t["aliases"])]
            my_controls = set(controls_by_target.get(t["target_id"], []))
            for cp in CHECKPOINTS:
                d = min(t_date + timedelta(days=CP_OFFSETS[cp]),
                        date.today()).isoformat()
                entity_obs = cd._entity_observations(cat, d)
                allv, matched, controls, target_obs = {}, [], {}, {}
                for node_id, obs in entity_obs.items():
                    label = obs[0]["label"]
                    cid = cr.concept_identity_id("entity", label)
                    f = entity_feature(obs, date.fromisoformat(d))
                    allv[cid] = tuple(f[k] for k in AGG)
                    if _matches(label, names_norm):
                        matched.append(cid)
                        target_obs[cid] = obs
                    if cid in my_controls:
                        controls[cid] = dict(f)
                entry["checkpoints"][cp] = {
                    "as_of": d, "all": allv, "matched": matched,
                    "controls": controls, "target_obs": target_obs}
            out["targets"].append(entry)
            print(f"[extract] {i + 1}/42 {t['target_id']} "
                  f"({time.time() - t0:.0f}s)", flush=True)
    finally:
        cat.close()
    (CAL / "features.json").write_text(json.dumps(out), encoding="utf-8")
    print(f"[extract] wrote {CAL / 'features.json'}")


# ---------------------------------------------------------------------------
# evaluation (stateless, per preregistered plan)
# ---------------------------------------------------------------------------

def eval_config(data, candidate, recent_min, ratio, channels,
                v1_arm=False, baseline_b_arm=False):
    cdef = CANDIDATES[candidate]
    n = len(data["targets"])
    cand_hits, emerge_hits = [], []
    ctl_cand, ctl_emerge, n_ctl = 0, 0, 0
    cand_counts, emerge_counts, entity_counts = [], [], []
    for t in data["targets"]:
        cps = t["checkpoints"]
        t_cand = t_em = False
        for cp in CHECKPOINTS:
            c = cps[cp]
            as_of_d = date.fromisoformat(c["as_of"])
            cnt = ecnt = 0
            for tup in c["all"].values():
                f = agg_to_f(tup)
                if not cand_pass(f, cdef):
                    continue
                cnt += 1
                if v1_arm:
                    if v1_pass(f):
                        ecnt += 1
                elif baseline_b_arm:
                    if baseline_b_pass(f, as_of_d):
                        ecnt += 1
                elif emerge_pass(f, recent_min, ratio, channels):
                    ecnt += 1
            cand_counts.append(cnt)
            emerge_counts.append(ecnt)
            entity_counts.append(len(c["all"]))
            for cid in c["matched"]:
                tup = c["all"].get(cid)
                if not tup:
                    continue
                f = agg_to_f(tup)
                if not cand_pass(f, cdef):
                    continue
                t_cand = True
                if v1_arm:
                    if v1_pass(f):
                        t_em = True
                elif baseline_b_arm:
                    if baseline_b_pass(f, date.fromisoformat(c["as_of"])):
                        t_em = True
                elif emerge_pass(f, recent_min, ratio, channels):
                    t_em = True
        cand_hits.append(t_cand)
        emerge_hits.append(t_em)
        c30 = cps["T+30"]
        for cid in t.get("control_ids", list(c30["controls"])):
            tup = c30["all"].get(cid)
            n_ctl += 1
            if not tup:
                continue  # topic_cluster control: never passes any arm
            f = agg_to_f(tup)
            if not cand_pass(f, cdef):
                continue
            ctl_cand += 1
            if v1_arm:
                if v1_pass(f):
                    ctl_emerge += 1
            elif baseline_b_arm:
                if baseline_b_pass(f, date.fromisoformat(c30["as_of"])):
                    ctl_emerge += 1
            elif emerge_pass(f, recent_min, ratio, channels):
                ctl_emerge += 1
    tr = sum(emerge_hits) / n if n else 0.0
    cr_ = ctl_emerge / n_ctl if n_ctl else 0.0
    return {
        **cfg_id(candidate, recent_min, ratio, channels),
        "candidate_recall": round(sum(cand_hits) / n, 4) if n else 0.0,
        "emerging_recall": round(tr, 4),
        "control_emerging_rate": round(cr_, 4),
        "control_candidate_rate": round(ctl_cand / n_ctl, 4) if n_ctl else 0.0,
        "separation": round(tr - cr_, 4),
        "mean_candidates": round(sum(cand_counts) / len(cand_counts), 1)
        if cand_counts else 0,
        "max_candidates": max(cand_counts) if cand_counts else 0,
        "mean_emerging": round(sum(emerge_counts) / len(emerge_counts), 2)
        if emerge_counts else 0,
        "mean_entities": round(sum(entity_counts) / len(entity_counts), 1)
        if entity_counts else 0,
        "_ctl_n": n_ctl,
    }


# ---------------------------------------------------------------------------
# perturbation (deterministic in-memory equivalent of the frozen scheme)
# ---------------------------------------------------------------------------

def perturb_feature(target_id, obs, fraction, as_of_d):
    rnd = int(hashlib.sha256(target_id.encode()).hexdigest()[:8], 16)
    eu_ids = sorted({o["eu_id"] for o in obs})
    take = int(len(eu_ids) * fraction)
    start = rnd % max(len(eu_ids) - take, 1) if take else 0
    removed = set(eu_ids[start:start + take])
    kept = [o for o in obs if o["eu_id"] not in removed]
    return entity_feature(kept, as_of_d), len(removed)


def perturb_metric(data, candidate, recent_min, ratio, channels,
                   v1_arm=False):
    cdef = CANDIDATES[candidate]
    res = {}
    for frac in (0.1, 0.2):
        key = int(frac * 100)
        n_c = n_e = n = 0
        reasons = {"no_matched_entity": 0, "emerging_lost_recent": 0,
                   "emerging_lost_ratio": 0, "emerging_lost_channels": 0}
        for t in data["targets"]:
            n += 1
            c30 = t["checkpoints"]["T+30"]
            if not c30["matched"]:
                reasons["no_matched_entity"] += 1
                continue
            cid = c30["matched"][0]
            obs = c30["target_obs"].get(cid, [])
            if not obs:
                reasons["no_matched_entity"] += 1
                continue
            f, _ = perturb_feature(t["target_id"], obs, frac,
                                   date.fromisoformat(c30["as_of"]))
            if cand_pass(f, cdef):
                n_c += 1
                ok = v1_pass(f) if v1_arm else \
                    emerge_pass(f, recent_min, ratio, channels)
                if ok:
                    n_e += 1
                else:
                    if f["r30"] < recent_min:
                        reasons["emerging_lost_recent"] += 1
                    if not v1_arm and ratio != "DISABLED" \
                            and f["ratio30"] < ratio:
                        reasons["emerging_lost_ratio"] += 1
                    if f["ch30"] < (3 if v1_arm else channels):
                        reasons["emerging_lost_channels"] += 1
        res[f"retained_{key}"] = {"candidate": n_c, "emerging": n_e,
                                  "denominator": n, "loss_reasons": reasons}
    r20 = res["retained_20"]
    return {"perturb20_candidate_retention":
            round(r20["candidate"] / r20["denominator"], 4)
            if r20["denominator"] else 0.0,
            "perturb20_emerging_retention":
            round(r20["emerging"] / r20["denominator"], 4)
            if r20["denominator"] else 0.0,
            "perturb10_candidate_retention":
            round(res["retained_10"]["candidate"]
                  / res["retained_10"]["denominator"], 4)
            if res["retained_10"]["denominator"] else 0.0,
            "perturb_detail": res}


# ---------------------------------------------------------------------------
# baseline reproduction guard (formal row-staleness semantics)
# ---------------------------------------------------------------------------

def compute_formal_matched_t30() -> set:
    """Target ids with ANY matched registry row in the formal T-30 replay
    (entity candidates plus topic_cluster label collisions). Computed once
    via the frozen evaluator replay path and cached."""
    cache = CAL / "formal-matched-t30-cache.json"
    if cache.exists():
        return set(json.loads(cache.read_text(encoding="utf-8")))
    targets = _load_targets_local(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    out = []
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for t in targets:
            reg = tmp / f"n-{t['target_id']}.sqlite"
            ev.replay_as_of(reg, ev._shift(scor[t["target_id"]], -30))
            rows = ev._concept_names(reg)
            if any(ev.match_concept(r, t) for r in rows):
                out.append(t["target_id"])
    cache.write_text(json.dumps(sorted(set(out))), encoding="utf-8")
    return set(out)


def reproduction_check(data, formal_matched=None) -> dict:
    """Formal semantics: matched_rows captured from the T-30 replay only
    (entity candidates plus topic_cluster label collisions, the latter
    carrying recent_count 0); row metadata = T+30 stats if still candidate
    there, else stale T-30 stats; controls = all 126 formal ids,
    non-entity controls have recent_count 0 by construction."""
    t_hit = t_den = c_hit = 0
    c_den = 126
    for t in data["targets"]:
        cps = t["checkpoints"]
        rows = []
        for cid in cps["T-30"]["matched"]:
            tup = cps["T-30"]["all"].get(cid)
            if tup and tup[0] >= 2:
                rows.append((cid, tup))
        cluster_row = (formal_matched is not None
                       and t["target_id"] in formal_matched)
        if rows or cluster_row:
            t_den += 1
            if rows:
                cid, tup30 = rows[0]
                g30 = cps["T+30"]["all"].get(cid)
                val = g30 if (g30 and g30[0] >= 2) else tup30
                if val[0] >= 6:
                    t_hit += 1
        for cid_c in t.get("control_ids", []):
            g30c = cps["T+30"]["all"].get(cid_c)
            g30m = cps["T-30"]["all"].get(cid_c)
            v = g30c if (g30c and g30c[0] >= 2) else g30m
            if v and v[0] >= 6:
                c_hit += 1
    tr = round(t_hit / t_den, 4) if t_den else None
    cr_ = round(c_hit / c_den, 4) if c_den else None
    return {"baseline_A_target_rate": tr, "baseline_A_control_rate": cr_,
            "separation": round(tr - cr_, 3),
            "n_target": t_den, "n_control": c_den,
            "formal_reference": {"target": 0.625, "control": 0.246,
                                 "separation": 0.379},
            "reproduced": (tr == 0.625 and cr_ == 0.246)}


# ---------------------------------------------------------------------------
# frozen selection rule
# ---------------------------------------------------------------------------

def gates_enabled(ratio, channels):
    return 1 + (0 if ratio == "DISABLED" else 1) + (0 if channels <= 1 else 1)


def select_config(rows, base_sep):
    ok = [r for r in rows
          if r["control_emerging_rate"] < 0.5
          and r["perturb20_candidate_retention"] > 0.3
          and r["separation"] > base_sep]
    qual = [r for r in ok
            if r["candidate_recall"] >= 0.70
            and r["emerging_recall"] >= 0.50
            and r["control_emerging_rate"] <= 0.20
            and r["perturb20_candidate_retention"] >= 0.50]
    if qual:
        qual.sort(key=lambda r: (r["control_emerging_rate"],
                                 -r["emerging_recall"],
                                 -r["perturb20_candidate_retention"],
                                 -r["candidate_recall"], r["mean_candidates"],
                                 gates_enabled(r["ratio"], r["channels_min"]),
                                 -r["recent_min"]))
        return {"selected": cfg_id(qual[0]["candidate"],
                                   qual[0]["recent_min"], qual[0]["ratio"],
                                   qual[0]["channels_min"]),
                "n_qualified": len(qual)}
    return {"selected": None, "pareto": pareto(rows, base_sep)}


def pareto(rows, base_sep):
    cand = [r for r in rows if r["control_emerging_rate"] < 0.5
            and r["separation"] > base_sep]
    front = []
    for r in cand:
        dominated = any(
            o is not r
            and o["emerging_recall"] >= r["emerging_recall"]
            and o["control_emerging_rate"] <= r["control_emerging_rate"]
            and o["perturb20_candidate_retention"]
            >= r["perturb20_candidate_retention"]
            and (o["emerging_recall"] > r["emerging_recall"]
                 or o["control_emerging_rate"] < r["control_emerging_rate"]
                 or o["perturb20_candidate_retention"]
                 > r["perturb20_candidate_retention"])
            for o in cand)
        if not dominated:
            front.append(cfg_id(r["candidate"], r["recent_min"], r["ratio"],
                                r["channels_min"]))
    return front[:30]


def same_cfg(r, c):
    return (r["candidate"] == c["candidate"]
            and r["recent_min"] == c["recent_min"]
            and r["ratio"] == c["ratio"]
            and r["channels_min"] == c["channels_min"])


# ---------------------------------------------------------------------------
# folds
# ---------------------------------------------------------------------------

def fold_of(target_id: str) -> int:
    return int(hashlib.sha256(target_id.encode()).hexdigest()[:8], 16) % 5


def subset(data, keep):
    return {"targets": [copy.deepcopy(t) for t in data["targets"]
                        if t["target_id"] in keep]}


def eval_all(data, with_perturbation=True):
    rows = [eval_config(data, ck, rm, rt, ch)
            for ck in CANDIDATES for rm in RECENT_LEVELS
            for rt in RATIO_LEVELS for ch in CHANNEL_LEVELS]
    if with_perturbation:
        for r in rows:
            r.update(perturb_metric(data, r["candidate"], r["recent_min"],
                                    r["ratio"], r["channels_min"]))
    return rows


def run_folds(data, base_sep):
    ids = {t["target_id"] for t in data["targets"]}
    folds = {i: {tid for tid in ids if fold_of(tid) == i} for i in range(5)}
    fold_results = []
    oof = {"cand": 0.0, "em": 0.0, "ctl_em": 0, "ctl_n": 0, "n": 0}
    for held in range(5):
        train_ids = set().union(*(folds[i] for i in range(5) if i != held))
        held_ids = folds[held]
        train_rows = eval_all(subset(data, train_ids))
        sel = select_config(train_rows, base_sep)
        rec = {"fold": held, "n_train": len(train_ids),
               "n_held": len(held_ids),
               "selected": sel["selected"],
               "n_qualified": sel.get("n_qualified", 0),
               "pareto_size": len(sel.get("pareto", []))}
        cfg = sel["selected"]
        if cfg:
            held_rows = eval_all(subset(data, held_ids))
            r = next(r for r in held_rows if same_cfg(
                {"candidate": r["candidate"], "recent_min": r["recent_min"],
                 "ratio": r["ratio"], "channels_min": r["channels_min"]},
                cfg))
            rec["held_out"] = {k: r[k] for k in
                               ("candidate_recall", "emerging_recall",
                                "control_emerging_rate", "separation",
                                "perturb20_candidate_retention")}
            n_h = len(held_ids)
            oof["cand"] += r["candidate_recall"] * n_h
            oof["em"] += r["emerging_recall"] * n_h
            oof["ctl_em"] += round(r["control_emerging_rate"] * r["_ctl_n"])
            oof["ctl_n"] += r["_ctl_n"]
            oof["n"] += n_h
        fold_results.append(rec)
        print(f"[fold {held}]", json.dumps(rec), flush=True)
    (CAL / "fold-results.json").write_text(
        json.dumps(fold_results, indent=1), encoding="utf-8")
    n = oof["n"]
    if n == 0:
        oof_summary = {
            "status": "NO_SELECTION_IN_ANY_FOLD",
            "note": "no configuration qualified on any training split; "
                    "per the frozen selection rule no winner is forced and "
                    "no OOF prediction exists"}
    else:
        oof_summary = {
            "candidate_recall": round(oof["cand"] / n, 4),
            "emerging_recall": round(oof["em"] / n, 4),
            "control_emerging_rate": round(oof["ctl_em"] / oof["ctl_n"], 4),
            "n_targets": n, "n_controls": oof["ctl_n"]}
        oof_summary["separation"] = round(
            oof_summary["emerging_recall"]
            - oof_summary["control_emerging_rate"], 4)
    (CAL / "oof-summary.json").write_text(json.dumps(oof_summary, indent=1),
                                          encoding="utf-8")
    print("[oof]", json.dumps(oof_summary))
    return fold_results, oof_summary


# ---------------------------------------------------------------------------
# phase 2 driver
# ---------------------------------------------------------------------------

def load_data():
    data = json.loads((CAL / "features.json").read_text(encoding="utf-8"))
    negs = json.loads((ART / "negative-controls.json").read_text())
    ctl = {}
    for n in negs:
        ctl.setdefault(n["target_id"], []).append(n["control_id"])
    for t in data["targets"]:
        t["control_ids"] = ctl.get(t["target_id"], [])
    return data


def run() -> None:
    data = load_data()
    formal_matched = compute_formal_matched_t30()
    repro = reproduction_check(data, formal_matched)
    print("[reproduction]", json.dumps(repro))
    if not repro["reproduced"]:
        sys.exit("reproduction guard FAILED; refusing to evaluate grid")

    bA = eval_config(data, "C0", 6, "DISABLED", 1)
    bA["perturb20_candidate_retention"] = \
        perturb_metric(data, "C0", 6, "DISABLED", 1)[
            "perturb20_candidate_retention"]
    bB_sep = eval_config(data, "C0", 4, "DISABLED", 1,
                         baseline_b_arm=True)["separation"]
    base_sep = max(bA["separation"], bB_sep, 0.0)

    rows = eval_all(data)
    (CAL / "full-grid-summary.json").write_text(json.dumps(
        {"plan_sha256": PLAN_SHA256, "reproduction": repro,
         "baseline_A": bA, "baseline_B_separation": bB_sep,
         "baseline_max_separation": base_sep,
         "grid": rows}, indent=1), encoding="utf-8")
    print(f"[grid] {len(rows)} configs evaluated")

    fold_results, oof_summary = run_folds(data, base_sep)
    _final_report(data, rows, bA, base_sep, repro, fold_results, oof_summary)


def _final_report(data, rows, bA, base_sep, repro, fold_results, oof):
    v1 = eval_config(data, "C0", 4, 2.0, 3, v1_arm=True)
    v1.update(perturb_metric(data, "C0", 4, 2.0, 3, v1_arm=True))
    v1_nosrc = eval_config(data, "C0", 4, 2.0, 3)
    v1_nosrc.update(perturb_metric(data, "C0", 4, 2.0, 3))
    bB = eval_config(data, "C0", 4, "DISABLED", 1, baseline_b_arm=True)
    bB.update(perturb_metric(data, "C0", 4, "DISABLED", 1))

    ok = [r for r in rows if r["control_emerging_rate"] < 0.5
          and r["perturb20_candidate_retention"] > 0.3
          and r["separation"] > base_sep]
    qual = [r for r in ok if r["candidate_recall"] >= 0.70
            and r["emerging_recall"] >= 0.50
            and r["control_emerging_rate"] <= 0.20
            and r["perturb20_candidate_retention"] >= 0.50]

    cc = [r for r in rows if r["ratio"] == "DISABLED" and r["channels_min"] > 1]
    crc = [r for r in rows if r["ratio"] != "DISABLED"
           and r["channels_min"] > 1]
    best_cc = max(cc, key=lambda r: (r["emerging_recall"]
                                     - r["control_emerging_rate"]))
    best_crc = max(crc, key=lambda r: (r["emerging_recall"]
                                       - r["control_emerging_rate"]))
    for name, r in (("best_count_channels", best_cc),
                    ("best_count_ratio_channels", best_crc)):
        r["perturb_detail"] = perturb_detail(data, r)

    (CAL / "ablations.json").write_text(json.dumps({
        "burst_policy_v1": v1,
        "v1_minus_source_types": v1_nosrc,
        "baseline_A": bA, "baseline_B": bB,
        "best_count_channels": {k: best_cc[k] for k in
                                ("candidate", "recent_min", "ratio",
                                 "channels_min", "candidate_recall",
                                 "emerging_recall",
                                 "control_emerging_rate", "separation",
                                 "mean_candidates")},
        "best_count_ratio_channels": {k: best_crc[k] for k in
                                      ("candidate", "recent_min", "ratio",
                                       "channels_min", "candidate_recall",
                                       "emerging_recall",
                                       "control_emerging_rate",
                                       "separation", "mean_candidates")},
    }, indent=1), encoding="utf-8")
    (CAL / "perturbation-summary.json").write_text(json.dumps({
        "v1": v1["perturb_detail"],
        "v1_minus_source_types": v1_nosrc["perturb_detail"],
        "best_count_channels": best_cc["perturb_detail"],
        "best_count_ratio_channels": best_crc["perturb_detail"],
    }, indent=1), encoding="utf-8")

    # decision class
    classes = []
    v1_cand = v1["mean_candidates"] or 1.0
    for rec in fold_results:
        if rec.get("selected"):
            ho = rec.get("held_out", {})
            if ho and ho.get("emerging_recall", 0) >= 0.50 \
                    and ho.get("control_emerging_rate", 1) <= 0.20:
                classes.append(True)
            else:
                classes.append(False)
        else:
            classes.append(False)
    if classes and all(classes):
        conclusion = "SIMPLE_V2_SUPPORTED"
    elif any(classes):
        conclusion = "PARTIAL_V2_SUPPORTED"
    else:
        conclusion = "NO_SIMPLE_POLICY_SUPPORTED"

    full_sel = select_config(rows, base_sep)
    sel_cfg = full_sel["selected"]
    full_metrics = None
    if sel_cfg:
        full_metrics = next(
            r for r in rows
            if same_cfg({"candidate": r["candidate"],
                         "recent_min": r["recent_min"], "ratio": r["ratio"],
                         "channels_min": r["channels_min"]}, sel_cfg))
    report = {
        "plan_sha256": PLAN_SHA256,
        "training_status": "consumed holdout-v4, TRAINING_DIAGNOSTIC_ONLY",
        "reproduction": repro,
        "full_selection": full_sel,
        "full_selected_metrics": full_metrics,
        "n_qualified_full": len(qual),
        "oof": oof,
        "fold_selected_configs": [r["selected"] for r in fold_results],
        "candidate_volume": {
            "v1_mean_candidates": v1["mean_candidates"],
            "v1_mean_emerging": v1["mean_emerging"],
            "grid_max_mean_candidates": max(r["mean_candidates"]
                                            for r in rows),
            "qualified_max_mean_candidates": max(
                (r["mean_candidates"] for r in qual), default=None)},
        "ablations": {
            "v1_separation": v1["separation"],
            "v1_minus_source_types_separation": v1_nosrc["separation"],
            "v1_minus_source_types_emerging_recall":
                v1_nosrc["emerging_recall"],
            "baseline_A_separation": bA["separation"],
            "best_count_channels": cfg_id(
                best_cc["candidate"], best_cc["recent_min"],
                best_cc["ratio"], best_cc["channels_min"]),
            "best_count_ratio_channels": cfg_id(
                best_crc["candidate"], best_crc["recent_min"],
                best_crc["ratio"], best_crc["channels_min"])},
        "conclusion_class": conclusion,
        "note": "calibration conclusion only; promotion requires a NEW "
                "unseen holdout after any v2 implementation",
    }
    (CAL / "final-calibration-report.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print("[final]", json.dumps(
        {k: report[k] for k in ("full_selection", "oof",
                                "conclusion_class")}, indent=1))


def perturb_detail(data, r):
    return perturb_metric(data, r["candidate"], r["recent_min"],
                          r["ratio"], r["channels_min"])["perturb_detail"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["extract", "run", "all"])
    a = ap.parse_args()
    if a.phase in ("extract", "all"):
        extract()
    if a.phase in ("run", "all"):
        run()


if __name__ == "__main__":
    main()
