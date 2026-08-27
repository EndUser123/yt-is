"""E3 step 6 — stability metrics.

Semantic label stability per arm between t0 and perturbed phases
(bge-m3 cosine) + token Jaccard; Arm C temperature-zero nondeterminism
from c-repeats.jsonl.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import e3lib as L
import evidence as EV


def load_phase(phase: str) -> dict[int, dict]:
    out = {}
    for line in (L.EF_DATA / "labels.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] == phase:
                out[r["cluster_id"]] = r
    return out


def tokset(s: str) -> set[str]:
    return set(EV.L._label_tokens(s or ""))


def main() -> int:
    labels = {p: load_phase(p) for p in ("t0", "pert")}
    common = sorted(set(labels["t0"]) & set(labels["pert"]))
    reps = []
    for a in ("A", "B", "C"):
        pairs = []
        for i in common:
            t0 = ((labels["t0"][i][a] or {}).get("label") or "").strip()
            t1 = ((labels["pert"][i][a] or {}).get("label") or "").strip()
            if t0 and t1:
                pairs.append((t0, t1))
        if not pairs:
            reps.append({"arm": a, "n": 0,
                         "usable": False,
                         "note": "no usable label pairs in both phases"})
            continue
        texts0 = [p[0] for p in pairs]
        texts1 = [p[1] for p in pairs]
        uniq = list({t for t in texts0 + texts1})
        pos = {t: i for i, t in enumerate(uniq)}
        dense = EV.get_embed_server().encode_dense(uniq)
        cos, jac = [], []
        for t0, t1 in pairs:
            if t0 == t1:
                cos.append(1.0)
                jac.append(1.0)
                continue
            if (t0 in pos) and (t1 in pos):
                cos.append(float(dense[pos[t0]] @ dense[pos[t1]]))
            else:
                cos.append(0.0)
            ja = tokset(t0); jb = tokset(t1)
            union = ja | jb
            jac.append(len(ja & jb) / len(union) if union else 1.0)
        same = sum(1 for t0, t1 in pairs if t0 == t1)
        total_pairs = sum(1 for i in common)
        reps.append({
            "arm": a,
            "n": len(pairs),
            "n_total_clusters": total_pairs,
            "coverage": round(len(pairs) / max(total_pairs, 1), 4),
            "mean_cosine": round(float(np.mean(cos)), 4),
            "median_cosine": round(float(np.median(cos)), 4),
            "frac_below_0.82": round(float(np.mean([c < 0.82 for c in cos])), 4),
            "mean_token_jaccard": round(float(np.mean(jac)), 4),
            "exact_same_label_rate": round(same / len(pairs), 4),
        })

    # ---- Arm C temperature-zero nondeterminism on the eval sample ----
    rep_groups = defaultdict(list)
    rp = L.EF_DATA / "c-repeats.jsonl"
    for line in rp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["status"] == 0:
                rep_groups[r["cluster_id"]].append(r["label"])
    bitwise = 0; groups = 0; varc = []
    uniq_all = sorted({lab for g in rep_groups.values() for lab in g})
    pos = {t: i for i, t in enumerate(uniq_all)}
    dense = EV.get_embed_server().encode_dense(uniq_all) if uniq_all else None
    for cid, labs in sorted(rep_groups.items()):
        groups += 1
        bitwise += int(len(set(labs)) == 1)
        if len(labs) >= 2 and dense is not None:
            idx = [pos[x] for x in labs if x in pos]
            m = dense[idx]
            sims = m @ m.T
            iu = np.triu_indices(len(idx), 1)
            varc.extend(float(v) for v in sims[iu])
    c_rep = {
        "groups": groups,
        "bitwise_identical_group_rate": round(bitwise / groups, 4) if groups else None,
        "pairwise_cosine_mean": round(float(np.mean(varc)), 4) if varc else None,
        "pairwise_cosine_min": round(float(np.min(varc)), 4) if varc else None,
    }
    out = {"stability": reps, "arm_c_nondeterminism": c_rep}
    (L.EF_DATA / "STABILITY.json").write_text(json.dumps(out, indent=2),
                                              encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
