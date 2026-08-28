"""E3.1 portability packets — NEW blinded reviewer compares, per sampled
cluster: A0 stored label + Hy3 generative label (from E3, byte-frozen) +
Lightning generative label (e31 cache). Three candidates, W/X/Y/Z
randomized per single reviewer; arm key stays outside the packet dir."""
from __future__ import annotations

import json
import sys

import e3lib as L

OUT = L.EF_DATA / "portability-review"
REVIEWER = "reviewer-e31-portability"


def main() -> int:
    clusters = L.load_freeze()
    sample = json.loads((L.EF_DATA / "SAMPLE.json").read_text(encoding="utf-8"))
    sample_ids = [c for b in ("large", "medium", "small")
                  for c in sample["selection"][b]]

    t0 = {}
    for line in (L.EF_DATA / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] == "t0":
                t0[r["cluster_id"]] = r

    hy3_label, lit_label = {}, {}
    for line in (L.EF_DATA / "e31-cache.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            if c.get("valid") and c["kind"] == "port":
                lit_label[c["cluster_id"]] = c["label"]
    for cid in sample_ids:
        # Hy3 t0 label comes from frozen E3 rows; accept repairs too (last good)
        hy3_label[cid] = t0[cid]["C"].get("label") or ""

    missing_lit = [c for c in sample_ids if c not in lit_label]
    print(f"lightning coverage: {len(sample_ids)-len(missing_lit)}/{len(sample_ids)}")
    if len(missing_lit) > 5:
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    ordered = sorted(sample_ids, key=lambda c: L.sha(f"e31port|{c}"))
    mask = {cid: f"P-{n+1:02d}" for n, cid in enumerate(ordered)}
    (L.EF_DATA / "PORTABILITY-MASK-KEY.json").write_text(
        json.dumps({v: k for k, v in mask.items()}), encoding="utf-8")

    rdir = OUT / REVIEWER
    rdir.mkdir(exist_ok=True)
    template = """# Blinded label comparison packet (E3.1 portability)

Three anonymized candidate labels W/X/Y/Z per item. Score EVERY item on:
REFERENT_FIDELITY, SPECIFICITY, CLARITY, GRANULARITY, ARTIFACT_FREE
(integers 1-5; anchors identical to your INSTRUCTIONS below) plus flags
TOO_GENERIC/TOO_NARROW/WRONG_TOPIC/ARTIFACT/AMBIGUOUS (booleans) and
OVERALL_PREFERRED (one of "W"/"X"/"Y"/"Z").

Anchors: REFERENT_FIDELITY 5 exact subject/3 partially right or vague/
1 wrong. SPECIFICITY 5 discriminates this topic/3 right domain but
generic/1 could name many clusters. CLARITY 5 instantly readable/3
awkward/1 word salad. GRANULARITY 5 scope matches evidence/3 somewhat
off/1 far off. ARTIFACT_FREE 5 clean/3 minor blemish (odd casing, one
junk token)/1 numbers/timestamps/UI words/channel names/decorated
unicode/broken fragments.

## Output contract

Write results.json next to this file:

{"items":[{"cid":"P-01","REFERENT_FIDELITY":{"W":4,"X":2,"Y":5,"Z":3},
"SPECIFICITY":{...},"CLARITY":{...},"GRANULARITY":{...},
"ARTIFACT_FREE":{...},"TOO_GENERIC":{"W":false,...},"TOO_NARROW":{...},
"WRONG_TOPIC":{...},"ARTIFACT":{...},"AMBIGUOUS":{...},
"OVERALL_PREFERRED":"Z"} , ... ]}

Every P-mask exactly once.
"""
    (rdir / "INSTRUCTIONS.md").write_text(template, encoding="utf-8")

    key = {}
    chunks = []
    for cid in ordered:
        row = t0[cid]
        c = clusters[cid]
        cands = {
            "A0": c.label,
            "HY3": row["C"].get("label") or "",
            "P2": lit_label.get(cid, ""),
        }
        perm = sorted(cands, key=lambda a: L.sha(f"{REVIEWER}|{cid}|{a}"))
        key[str(cid)] = {a: {"alias": z, "label": cands[a]}
                         for a, z in zip(perm, "XYZ")}
        titles = "\n".join(
            f"  {n+1}. {t}" for n, t in enumerate(row["_evidence"]["display_titles"]))
        clines = [f"- **{z}**: `{cands[a] or '(empty)'}`"
                  for a, z in zip(perm, "XYZ")]
        mix = L.source_mix(c)
        chunks.append(f"""## {mask[cid]}  ({c.video_count_stored} documents;
sources: {json.dumps(mix)})

### Member document titles (representative span)
{titles}

### Candidate labels
{chr(10).join(clines)}
""")
    (rdir / "PACKET.md").write_text("\n---\n\n".join(chunks), encoding="utf-8")
    (L.EF_DATA / "PORTABILITY-ARM-KEY.json").write_text(
        json.dumps(key, indent=1), encoding="utf-8")
    print("packets written:", OUT / REVIEWER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
