#!/usr/bin/env python
"""Compute telegraphic-query metrics per fusion config from judgments.

Judged set = union of each config's top-3 (config-independent per video).
Metrics: MRR@10 (authored positive, full top-10 from rerun), judged P@3,
judged nDCG@3 (ideal = all relevant first). Answers the E-gate question:
gold problem vs retrieval weakness.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"


def main() -> int:
    listing = json.load(open("P:/tmp/telegraphic_listing.json", encoding="utf-8"))
    J = json.loads((BENCH / "telegraphic_judgments.json")
                   .read_text(encoding="utf-8"))["judgments"]
    hand = json.loads((BENCH / "acceptance_c3_hand.json").read_text(encoding="utf-8"))
    tele = [h for h in hand if h["stratum"] in ("short_natural",
                                                "comparison_questions")]

    # per (query, config): top-3 videos from the listing
    top3: dict[tuple, list] = {}
    for it in listing:
        top3.setdefault((it["query"], it["config"]), []).append(
            (it["rank"], it["video"]))
    cfgs = ["prod_rrf", "dense_only", "sparse_only", "sparse_heavy"]
    out = {}
    for cfg in cfgs:
        p3, ndcg3, judged_any3, n = 0.0, [], 0, 0
        for q in tele:
            vids = [v for _, v in sorted(top3.get((q["query"], cfg), []))]
            if not vids:
                continue
            rels = [J.get(f"{q['query']}||{v}", 0) for v in vids]
            p3 += sum(rels) / len(rels)
            judged_any3 += 1 if any(rels) else 0
            dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
            idcg = sum(1 / math.log2(i + 2) for i in range(min(3, sum(1 for r in rels if r))))
            ndcg3.append(dcg / idcg if idcg > 0 else 0.0)
            n += 1
        out[cfg] = {"n": n,
                    "judged_P@3": round(p3 / n, 4),
                    "judged_any@3": round(judged_any3 / n, 4),
                    "judged_nDCG@3": round(sum(ndcg3) / n, 4)}
    print(json.dumps(out, indent=1))
    (BENCH / "telegraphic_analysis.json").write_text(
        json.dumps({"configs_top3": out,
                    "judgment_count": len(J),
                    "method": "union-of-top3 judgments; authored MRR from "
                              "c3_final_battery: short_natural 0.313, "
                              "comparison 0.242"}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
