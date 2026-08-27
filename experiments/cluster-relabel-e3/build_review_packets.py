"""E3 step 4 — build blinded review packets for the 45-cluster eval sample.

Four candidates per cluster (v3 amendment): A0 = stored production label,
A1 = mechanical recompute, B = KeyBERTInspired-adapted, C = generative.
Two reviewer dirs with DIFFERENT arm orderings; the arm key never enters
either dir. Cluster identities masked R-01..R-45 (hash-ordered).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import e3lib as L

OUT_BASE = L.EF_DATA / "reviews"
REVIEWERS = ["reviewer-nemotron-ultra", "reviewer-zcode-glm"]
ARMS = ["A0", "A1", "B", "C"]

TEMPLATE = """# Blinded cluster-label review packet

You are one of two independent reviewers scoring candidate topic-cluster
labels. Score EVERY item below against the evidence shown. Do not guess
which mechanism produced any candidate; candidates are anonymized W/X/Y/Z
in a random order that differs between reviewers.

## Scoring rubric (1-5 integers)

- REFERENT_FIDELITY: does the label name what the documents are actually
  about? 5 exact subject; 3 partially right/vague but on-topic; 1 wrong
  referent.
- SPECIFICITY: does it pick out THIS topic among plausible neighbors?
  5 discriminating; 3 generic-but-right domain; 1 could name dozens of
  unrelated clusters.
- CLARITY: instantly readable, natural phrase? 5 clean; 3 understandable
  but awkward; 1 word salad/broken.
- GRANULARITY: scope matches the evidence breadth? 5 right level;
  3 somewhat too broad or narrow; 1 far off (whole-industry or
  single-video).
- ARTIFACT_FREE: free of junk? 5 clean topic language; 3 minor blemish
  (odd casing, one junk token); 1 contains numbers/timestamps/UI words/
  channel names/decorated unicode/broken fragments.

Binary flags (true/false): TOO_GENERIC (correct domain but no
discriminating content), TOO_NARROW (covers a slice only), WRONG_TOPIC,
ARTIFACT (any junk from the list above), AMBIGUOUS (cannot tell what it
refers to).

OVERALL_PREFERRED: exactly one of "W"/"X"/"Y"/"Z" - which candidate would
you ship as this cluster's public name?

## Output contract

Write results.json next to this file:

{{
  "items": [
    {{
      "cid": "R-07",
      "REFERENT_FIDELITY": {{"W": 4, "X": 2, "Y": 5, "Z": 3}},
      "SPECIFICITY": {{"W": 3, "X": 2, "Y": 4, "Z": 3}},
      "CLARITY": {{"W": 5, "X": 1, "Y": 4, "Z": 3}},
      "GRANULARITY": {{"W": 4, "X": 2, "Y": 3, "Z": 4}},
      "ARTIFACT_FREE": {{"W": 5, "X": 1, "Y": 4, "Z": 4}},
      "TOO_GENERIC": {{"W": false, "X": true, "Y": false, "Z": false}},
      "TOO_NARROW": {{"W": false, "X": false, "Z": false, "Y": false}},
      "WRONG_TOPIC": {{"W": false, "X": true, "Y": false, "Z": false}},
      "ARTIFACT": {{"W": false, "X": true, "Y": false, "Z": false}},
      "AMBIGUOUS": {{"W": false, "X": false, "Y": false, "Z": false}},
      "OVERALL_PREFERRED": "Y"
    }}
  ]
}}

Every cid from the item list MUST appear exactly once.


"""


def main() -> int:
    freeze = L.load_freeze()
    sample = json.loads((L.EF_DATA / "SAMPLE.json").read_text(encoding="utf-8"))
    sample_ids = [cid for b in ("large", "medium", "small")
                  for cid in sample["selection"][b]]

    rows = {}
    for line in (L.EF_DATA / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] == "t0":
                rows[r["cluster_id"]] = r
    missing = [i for i in sample_ids if i not in rows]
    if missing:
        print(f"t0 labels missing for {len(missing)} sampled clusters:", missing)
        return 1

    OUT_BASE.mkdir(parents=True, exist_ok=True)
    ordered = sorted(sample_ids, key=lambda c: L.sha(f"mask|{c}"))
    mask = {cid: f"R-{n+1:02d}" for n, cid in enumerate(ordered)}
    (L.EF_DATA / "MASK-KEY.json").write_text(
        json.dumps({v: k for k, v in mask.items()}, indent=1),
        encoding="utf-8")  # PRIVATE: outside reviewer dirs

    arm_key = {}
    for rev in REVIEWERS:
        rdir = OUT_BASE / rev
        rdir.mkdir(exist_ok=True)
        (rdir / "INSTRUCTIONS.md").write_text(TEMPLATE, encoding="utf-8")
        chunks = []
        for cid in ordered:
            row = rows[cid]
            c = freeze[cid]
            perm = sorted(ARMS, key=lambda a: L.sha(f"{rev}|blind|{cid}|{a}"))
            labels_by_arm = {
                "A0": c.label,
                "A1": row["A"].get("label") or "",
                "B": row["B"].get("label") or "",
                "C": row["C"].get("label") or "",
            }
            arm_key.setdefault(str(cid), {})[rev] = {
                a: {"alias": z, "label": labels_by_arm[a]}
                for a, z in zip(perm, "WXYZ")}
            mix = L.source_mix(c)
            cand_lines = []
            for a, z in zip(perm, "WXYZ"):
                lab = labels_by_arm[a] or "(empty)"
                cand_lines.append(f"- **{z}**: `{lab}`")
            titles = "\n".join(
                f"  {n+1}. {t}" for n, t in enumerate(row["_evidence"]["display_titles"]))
            chunks.append(f"""## {mask[cid]}  ({c.video_count_stored} documents;
sources: {json.dumps(mix)})

### Member document titles (representative span)
{titles}

### Candidate labels
{chr(10).join(cand_lines)}
""")
        (rdir / "PACKET.md").write_text("\n---\n\n".join(chunks),
                                        encoding="utf-8")

    (L.EF_DATA / "ARM-KEY.json").write_text(
        json.dumps(arm_key, indent=1), encoding="utf-8")  # PRIVATE
    print(f"wrote packets for {len(ordered)} clusters x {len(REVIEWERS)} reviewers "
          f"under {OUT_BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
