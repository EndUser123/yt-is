"""Parity gate: production burst-policy-v2 must reproduce the calibration
implementation's target decisions on consumed holdout-v4 (packet
2026-08-25, NON_BLIND parity check; no FORMAL run, no ledger access).

Compares, per target at the 6 calibration checkpoints:
  candidate classification (D30-1.5 gate vs production concept row)
  emerging classification  (calibration episode promotion vs production
                            lifecycle reaching 'emerging')
Zero disagreements required on both axes.
"""
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "scripts"))

import evaluate_concept_discovery as ev  # noqa: E402

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

ART = Path("P:/.data/yt-is/ef/concept-discovery-eval/"
           "eval-20260825T114338-FORMAL")
CAL = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
           "stateful-burst-v1")
HOLDOUT = "P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json"
CHECKPOINTS = ["T-30", "T", "T+7", "T+14", "T+30", "T+60"]
OFFSET = {"T-30": -30, "T": 0, "T+7": 7, "T+14": 14, "T+30": 30,
          "T+60": 60}


def main():
    targets = _load_targets_local(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    calib = json.loads((CAL / "features.json").read_text()) if \
        (CAL / "features.json").exists() else None
    # calibration decisions live in the stateful-burst sqlite aggregates
    import sqlite3
    fdb = sqlite3.connect(
        f"file:{CAL / 'features.sqlite'}?mode=ro", uri=True)
    fdb.row_factory = sqlite3.Row
    agg = {}
    for r in fdb.execute("SELECT * FROM entities"):
        agg.setdefault((r["t_key"], r["cp"]), {})[r["cid"]] = dict(r)
    # control ids per target (excluded from the matched set)
    negs = json.loads((ART / "negative-controls.json").read_text())
    ctl_by_target = {}
    for n in negs:
        ctl_by_target.setdefault(n["target_id"], set()).add(n["control_id"])
    fdb.close()
    matched_calib = _calib_matched(ctl_by_target)

    cand_dis = em_dis = 0
    n_cmp = 0
    with tempfile.TemporaryDirectory(prefix="parity-") as tmp:
        tmp = Path(tmp)
        for i, t in enumerate(targets):
            tid = t["target_id"]
            t_date = date.fromisoformat(scor[tid])
            registry = tmp / f"reg-{tid}.sqlite"
            prod_emerging_ever = False
            for cp in CHECKPOINTS:
                d = min(t_date + timedelta(days=OFFSET[cp]),
                        date.today()).isoformat()
                ev.replay_as_of(registry, d,
                                policy_version="burst-policy-v2")
                concepts = ev._concept_names(registry)
                matched = [c for c in concepts if ev.match_concept(c, t)]
                # candidate = FRESH evaluation at this checkpoint (a row
                # left from an earlier checkpoint is not a current
                # candidate)
                prod_cand = False
                for m in matched:
                    meta = json.loads(m.get("metadata_json") or "{}")
                    evals = meta.get("v2_evals") or []
                    if (evals and evals[-1].get("as_of") == d
                            and meta.get("v2_candidate")):
                        prod_cand = True
                        break
                if any(c["lifecycle_state"] == "emerging" for c in matched):
                    prod_emerging_ever = True
                # calibration decision: ANY matched entity passes the
                # D30-1.5 gate (multi-match targets)
                calib_cand = any(
                    (a := agg.get((tid, cp), {}).get(cid)) is not None
                    and a["d30"] >= 1.5 and a["lifetime"] >= 2
                    for cid in matched_calib.get((tid, cp), []))
                n_cmp += 1
                if prod_cand != calib_cand:
                    cand_dis += 1
            # emerging across checkpoints (calibration promotion)
            calib_em = _calib_emerging(tid, agg, matched_calib)
            prod_em = prod_emerging_ever
            if prod_em != calib_em:
                em_dis += 1
            print(f"[parity] {i + 1}/42 {tid} cand_ok_so_far="
                  f"{cand_dis == 0}", flush=True)
    out = {"candidate_disagreements": cand_dis,
           "emerging_disagreements": em_dis,
           "checkpoint_comparisons": n_cmp,
           "verdict": "PASS" if cand_dis == 0 and em_dis == 0 else "FAIL"}
    (CAL / "parity-gate-result.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))


def _calib_matched(ctl_by_target):
    """Matched (name-matched, non-control) entity cid sets per
    (target, cp): the calibration obs table stores matched entities plus
    the target's negative controls; controls are excluded here."""
    import sqlite3
    fdb = sqlite3.connect(
        f"file:{CAL / 'features.sqlite'}?mode=ro", uri=True)
    out = {}
    for r in fdb.execute("SELECT DISTINCT t_key, cp, cid FROM obs"):
        if r[2] not in ctl_by_target.get(r[0], set()):
            out.setdefault((r[0], r[1]), set()).add(r[2])
    fdb.close()
    return out


def _calib_emerging(tid, agg, matched):
    """Recompute the calibration episode promotion exactly as the
    calibration evaluator did: signals from ALL matched entities,
    interleaved per checkpoint (cp outer, sorted cid inner)."""
    sigs = []
    for cp in CHECKPOINTS:
        for cid in sorted(matched.get((tid, cp), [])):
            a = agg.get((tid, cp), {}).get(cid)
            if not a:
                continue
            from ef import burst_policy_v2 as bp2
            post = bp2.prob_rate_above(
                a["k60"], a["b180_60"], exp_recent=2.0, exp_base=6.0,
                mult=1.5)
            sigs.append((cp, post >= 0.80 and a["ch60"] >= 1, post,
                         a["ch60"]))
    # two consecutive positives with gap <= 30, or single strong
    promoted = False
    prev_cp, prev_pos = None, False
    for cp, pos, post, ch in sigs:
        if pos and prev_pos and prev_cp is not None and \
                abs(OFFSET[cp] - OFFSET[prev_cp]) <= 30:
            promoted = True
        if post >= 0.99 and ch >= 2:
            promoted = True
        prev_cp, prev_pos = cp, pos
    return promoted


if __name__ == "__main__":
    main()
