#!/usr/bin/env python3
"""Seal corrected replay outputs before any historical artifact is read."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
            "temporal-emergence-modelgen-v1-replay-corrected-time")
FAMILIES = ["armB_EU1-W30", "armB_EU2-W60", "armB_BUCKETS-W120",
            "armB_POSTERIOR-EXCL-W30", "armB_CHANNELNEW-W30",
            "armC", "armD"]
SEALED = ["metric-rows.json", "aggregate.json", "decision-bars.json",
          "segments.json", "counterfactuals.json", "run-config.json"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    agg = json.loads((BASE / "corrected" / "aggregate.json").read_text(
        encoding="utf-8"))
    dec = json.loads((BASE / "corrected" / "decision-bars.json").read_text(
        encoding="utf-8"))
    cf = json.loads((BASE / "corrected" / "counterfactuals.json").read_text(
        encoding="utf-8"))
    d5 = bool(agg["determinism"]["identical"]) and all(
        r["checks"]["inject_future"]["identical"]
        for r in cf if r.get("checks", {}).get("inject_future"))
    bars = {}
    for fam in FAMILIES:
        e = dec[fam]["bars"]
        ax = agg["perturbation"]["perturbation20"].get(
            fam + "_confirmed_retained")
        d4 = bool(ax and ax.get("ratio") is not None
                  and ax["ratio"] >= 0.50)
        stab = agg["stability_leave_one_out"].get(fam, {})
        rng = stab.get("lo_negative_rate_range")
        d7 = True
        if rng and stab.get("n_neg", 0) > 1:
            lo, hi = rng
            anchor = 72 / 124
            if ((lo <= 0.35 and lo <= 0.5 * anchor)
                    != (hi <= 0.35 and hi <= 0.5 * anchor)):
                d7 = False
        supported = bool(e["D1_material_neg_drop"] and e["D2_pos_useful"]
                         and e["D3_separation_beats_A"] and d4 and d5
                         and d7)
        bars[fam] = {"D1": e["D1_material_neg_drop"],
                     "D2": e["D2_pos_useful"],
                     "D3": e["D3_separation_beats_A"],
                     "D4_perturb20_retention": d4, "D5": d5, "D7": d7,
                     "family_supported": supported}
    qualified = sorted(f for f, v in bars.items() if v["family_supported"])
    if not qualified:
        decision = "NO_NEW_MODEL_SUPPORTED"
    elif all(f.startswith("armB_") for f in qualified):
        decision = "POST_TRIGGER_CONFIRMATION_SUPPORTED"
    else:
        decision = "STRUCTURAL_SELECTION_REQUIRED:" + ",".join(qualified)
    seal = {
        "kind": "corrected_time_replay_seal",
        "agent": "zcode",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "run": "temporal-emergence-modelgen-v1-replay-corrected-time/"
               "corrected",
        "sealed_file_sha256": {n: sha(BASE / "corrected" / n)
                               for n in SEALED},
        "frozen_decision_under_original_mapping": decision,
        "qualified_families": qualified,
        "mechanical_bars": bars,
        "cohorts": agg["cohorts"],
        "determinism": agg["determinism"],
        "parity_note": "armA parity vs pre-repair references fails as "
                       "expected under the corrected substrate (frozen "
                       "interpretation in REPLAY_MANIFEST.json); "
                       "machinery equivalence carried by byte-frozen "
                       "code + shadow-prerepair run which passes "
                       "pass_current_reference exactly (36/42, 75/124)",
    }
    out = BASE / "corrected" / "SEALED_OUTPUTS.json"
    out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    seal["seal_file_sha256"] = sha(out)
    out.write_text(json.dumps(seal, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, "qualified": qualified,
                      "bars": bars,
                      "seal_sha256": seal["seal_file_sha256"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
