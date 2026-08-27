"""E3 step 2 — deterministic evaluation sample per PREREGISTRATION.md (v2).

Cells: size bucket x source-diversity(>=3 families). Artifact-suspect swap
stratum. Deterministic; writes SAMPLE.json once and refuses overwrite.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import e3lib as L

QUOTA = {"large": 8, "medium": 22, "small": 15}
ARTIFACT_SEATS_PER_BUCKET = 8  # ~4 per cell


def main() -> int:
    out = L.EF_DATA / "SAMPLE.json"
    if out.exists():
        print(f"REFUSED: {out} exists — sample is frozen")
        return 1

    clusters = L.load_freeze()

    by_bucket: dict[str, list] = {"small": [], "medium": [], "large": []}
    for c in clusters.values():
        n_fam = len(L.source_families(c))
        af, awhy = L.artifact_flag(c)
        by_bucket[L.size_bucket(c.video_count_stored)].append(
            {"cid": c.cluster_id, "div_hi": n_fam >= 3,
             "art": af, "art_why": awhy})

    selection: dict[str, dict] = {}
    for bucket in ("large", "medium", "small"):
        q = QUOTA[bucket]
        hi = [t for t in by_bucket[bucket] if t["div_hi"]]
        lo = [t for t in by_bucket[bucket] if not t["div_hi"]]
        hi.sort(key=lambda t: L.sha(f"{L.SEED_SAMPLE}|{t['cid']}"))
        lo.sort(key=lambda t: L.sha(f"{L.SEED_SAMPLE}|{t['cid']}"))
        # even split, remainder to larger cell
        first = q // 2 + (q % 2)
        counts = [first, q - first] if len(hi) >= len(lo) else [q - first, first]
        picked, shortages = [], []
        used_here: set[int] = set()
        for cell, want in zip((("high", hi), ("low", lo)), counts):
            name, members = cell
            have = [m for m in members if m["cid"] not in used_here][:want]
            for m in have:
                picked.append(m["cid"])
                used_here.add(m["cid"])
            if len(have) < want:
                shortages.append({"cell": name, "deficit": want - len(have)})
        # intra-bucket refill: take surplus from the other cell by hash order
        for s in shortages:
            other = lo if s["cell"] == "high" else hi
            fill = [m for m in sorted(other, key=lambda t: L.sha(
                f"{L.SEED_SAMPLE}|rb|{t['cid']}"))
                if m["cid"] not in used_here][:s["deficit"]]
            for m in fill:
                picked.append(m["cid"])
                used_here.add(m["cid"])
            s["refilled"] = len(fill)
            s["residual"] = s["deficit"] - len(fill)
        selection[bucket] = {
            "quota": q, "available_high": len(hi), "available_low": len(lo),
            "picked": picked, "shortages": shortages}

    total = sum(len(v["picked"]) for v in selection.values())
    deficit = sum(QUOTA.values()) - total
    # cross-bucket deficit absorption order: medium -> small -> large
    used = {cid for v in selection.values() for cid in v["picked"]}
    seat = 0
    while deficit > 0:
        moved = False
        for b in ("medium", "small", "large"):
            cand = [m for m in by_bucket[b] if m["cid"] not in used]
            cand.sort(key=lambda t: L.sha(f"{L.SEED_SAMPLE}|xb{seat}|{t['cid']}"))
            take = min(deficit, len(cand))
            for m in cand[:take]:
                selection[b]["picked"].append(m["cid"])
                used.add(m["cid"])
            deficit -= take
            if take:
                moved = True
            if deficit <= 0:
                break
        if not moved or seat > 50:
            break
        seat += 1

    # artifact-suspect swap stratum: up to ARTIFACT_SEATS_PER_BUCKET picks per
    # bucket replaced with lowest-hash artifact-flagged candidates from same bucket
    swaps_log = {}
    for b in ("large", "medium", "small"):
        picked_set = set(selection[b]["picked"])
        pool_art = sorted([t for t in by_bucket[b] if t["art"] and t["cid"] not in picked_set],
                          key=lambda t: L.sha(f"{L.SEED_SAMPLE}|{t['cid']}"))
        pool_plain = sorted([cid for cid in selection[b]["picked"]
                             if not next(t for t in by_bucket[b] if t["cid"] == cid)["art"]],
                            key=lambda c: -int(L.sha(f"{L.SEED_SAMPLE}|{c}")[:16], 16))
        n_swaps = min(ARTIFACT_SEATS_PER_BUCKET, len(pool_art), len(pool_plain))
        for i in range(n_swaps):
            selection[b]["picked"].remove(pool_plain[i])
            selection[b]["picked"].append(pool_art[i]["cid"])
        swaps_log[b] = n_swaps

    manifest = {
        "seed": L.SEED_SAMPLE,
        "freeze_sha256": L.FREEZE_SHA256,
        "policy_version": "v2",
        "policy": {"quota": QUOTA,
                   "cells": "size x source_diversity(>=3 families)",
                   "artifact_swap_seats_per_bucket": ARTIFACT_SEATS_PER_BUCKET},
        "selection": {b: v["picked"] for b, v in selection.items()},
        "shortages": {b: v["shortages"] for b, v in selection.items()},
        "artifact_swaps": swaps_log,
        "coverage": {},
    }
    flat = [(b, cid) for b in ("large", "medium", "small")
            for cid in manifest["selection"][b]]

    cov: Counter = Counter()
    for b, cid in flat:
        c = clusters[cid]
        af, _ = L.artifact_flag(c)
        cov["n"] += 1
        cov[f"bucket_{b}"] += 1
        cov["srcdiv_high" if len(L.source_families(c)) >= 3 else "srcdiv_low"] += 1
        cov["recency_heavy" if L.recency_stats(c)["recency_heavy"] else "durable"] += 1
        cov["artifact_suspect"] += int(af)
    manifest["coverage"] = dict(cov)

    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest["coverage"], indent=2))
    print("swaps:", swaps_log, "| wrote", out, f"n={len(flat)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
