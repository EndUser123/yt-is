"""Parity gate, live-reference variant: compares production
burst-policy-v2 replay decisions against a stateless reference computed
from the CURRENT catalog (removes calibration-snapshot drift).

For each target at each calibration checkpoint:
  reference candidate = any live name-matched entity passes D30-1.5
  reference emerging  = any live name-matched entity promotes under the
                       per-entity episode machine (signals ungated)
  production values come from a v2 replay sequence in one registry.

Zero disagreements on both axes = implementation parity.
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
from ef import burst_policy_v2 as bp2  # noqa: E402
from ef import concept_discovery as cd  # noqa: E402

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
    import sqlite3
    targets = _load_targets_local(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    cat = sqlite3.connect(
        "file:P:/.data/yt-is/ef/catalog.sqlite?mode=ro", uri=True,
        timeout=30)
    cat.row_factory = sqlite3.Row
    cand_dis = em_dis = n_cmp = 0
    diffs = []
    with tempfile.TemporaryDirectory(prefix="parity-live-") as tmp:
        tmp = Path(tmp)
        for i, t in enumerate(targets):
            tid = t["target_id"]
            t_date = date.fromisoformat(scor[tid])
            names = [ev._norm(n) for n in
                     [t["canonical_name"]] + list(t["aliases"])]
            registry = tmp / f"reg-{tid}.sqlite"
            prod_em = False
            # live reference per checkpoint
            sigs_by_cid = {}
            for cp in CHECKPOINTS:
                d = min(t_date + timedelta(days=OFFSET[cp]),
                        date.today()).isoformat()
                # production
                ev.replay_as_of(registry, d,
                                policy_version="burst-policy-v2")
                concepts = ev._concept_names(registry)
                matched = [c for c in concepts if ev.match_concept(c, t)]
                prod_cand = False
                for m in matched:
                    meta = json.loads(m.get("metadata_json") or "{}")
                    evals = meta.get("v2_evals") or []
                    if (evals and evals[-1].get("as_of") == d
                            and meta.get("v2_candidate")):
                        prod_cand = True
                    if m["lifecycle_state"] == "emerging":
                        prod_em = True
                # live reference
                eo = cd._entity_observations(cat, d)
                ref_cand = False
                for nid, obs in eo.items():
                    ln = ev._norm(obs[0]["label"])
                    if not any(
                            tn and (ln == tn or
                                    ev._word_boundary_contains(ln, tn) or
                                    ev._word_boundary_contains(tn, ln))
                            for tn in names):
                        continue
                    dec = bp2.evaluate(obs, date.fromisoformat(d), [])
                    if dec["candidate"]:
                        ref_cand = True
                    sigs_by_cid.setdefault(nid, []).append(
                        (cp, dec["positive"], dec["posterior"],
                         dec["channels"]))
                ref_em = False
                for nid, sigs in sigs_by_cid.items():
                    prev_cp, prev_pos = None, False
                    for cp2, pos, post, ch in sigs:
                        if pos and prev_pos and prev_cp is not None and \
                                abs(OFFSET[cp2] - OFFSET[prev_cp]) <= 30:
                            ref_em = True
                        if post >= 0.99 and ch >= 2:
                            ref_em = True
                        prev_cp, prev_pos = cp2, pos
                n_cmp += 1
                if prod_cand != ref_cand:
                    cand_dis += 1
                    diffs.append((tid, cp, "candidate", prod_cand,
                                  ref_cand))
            if prod_em != ref_em:
                em_dis += 1
                diffs.append((tid, "-", "emerging", prod_em, ref_em))
            print(f"[parity-live] {i + 1}/42 {tid}", flush=True)
    cat.close()
    out = {"candidate_disagreements": cand_dis,
           "emerging_disagreements": em_dis,
           "checkpoint_comparisons": n_cmp,
           "reference": "stateless live-catalog (drift-free)",
           "diffs": diffs[:20],
           "verdict": "PASS" if cand_dis == 0 and em_dis == 0 else "FAIL"}
    (CAL / "parity-gate-live-result.json").write_text(
        json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in
                      ("candidate_disagreements",
                       "emerging_disagreements", "verdict")}))
    if diffs:
        print(json.dumps(out["diffs"][:10], indent=1))


if __name__ == "__main__":
    main()
