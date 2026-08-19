"""Generate the BLINDED 35-claim disposition packet + unblinded key.

For each claim: top-5 evidence candidates with reopened evidence text
(fuller than the snippet — reopen span with generous context), source
identity, published/captured dates. Rank and score STRIPPED; candidate
order shuffled per claim (seeded, recorded in the key). The key file
maps claim->candidate ids in true retrieval order; join only after
dispositions are recorded.
"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, r"P:\packages\yt-is")
sys.path.insert(0, r"P:\packages\yt-is\scripts")
from ef import embedding, buildspec
from ef.query_server import ProductionQuery
from ef import authority
import ef_wiki_maintenance as M

BENCH = Path(r"P:\packages\yt-is\docs\evidence-fabric\benchmark")
pq = ProductionQuery(embedding.BGEM3Dual(), generation=buildspec.active_generation())

claims = json.load(open(BENCH / "n_gate_sample_selection.json", encoding="utf-8"))
claims += json.load(open(BENCH / "n_gate_sample_extension.json", encoding="utf-8"))
assert len(claims) == 35

rng = random.Random(2026)   # shuffling seed, recorded here
packet, key = [], []
M.mode_staleness(pq, "warmup", "2026-08-01T00:00:00Z", 2)

for idx, item in enumerate(claims):
    claim = item["claim"]
    lv = (item["last_verified"] or "") + "T00:00:00Z"
    ev = M.mode_evidence(pq, claim, top_k=5)
    cands = []
    for c in ev["candidates"]:
        # reopen the full span with generous context for genuine judgment
        vid = c["video_id"]
        s, e = c["char_span"]
        try:
            text = authority.reopen_span(vid, s, e, context=1200)
        except Exception:
            text = c["evidence_text"]
        cands.append({
            "cid": f"C{idx:02d}-{len(cands)}",
            "evidence_text": text[:2400],
            "source_title_hint": None,  # added below from payload? keep blind-safe
            "video_id": vid,
            "channel": c["channel"],
            "url": c["source_url"],
            "char_span": [s, e],
        })
    # attach dates (not rank/score — dates are judgment inputs per rubric)
    ts_map = M._captured_at_batch([f"{c['video_id']}:transcript" for c in cands])
    pub_map = M._published_at_batch([f"{c['video_id']}:transcript" for c in cands])
    for c in cands:
        eu = f"{c['video_id']}:transcript"
        c["published_at"] = pub_map.get(eu)
        c["captured_at"] = ts_map.get(eu)
    shuffled = list(cands)
    rng.shuffle(shuffled)
    packet.append({
        "claim_id": f"K{idx:02d}",
        "claim": claim,
        "last_verified": item["last_verified"],
        "file": item["file"],
        "candidates": shuffled,
    })
    key.append({
        "claim_id": f"K{idx:02d}",
        "true_rank_order": [c["cid"] for c in cands],
        "video_ids_in_rank_order": [c["video_id"] for c in cands],
    })
    print(f"[b] K{idx:02d} {len(shuffled)} cands | {claim[:50]}", flush=True)

(BENCH / "n_gate_blind_packet.json").write_text(
    json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "shuffle_seed": 2026, "claims": packet}, indent=1,
               ensure_ascii=False), encoding="utf-8")
(BENCH / "n_gate_disposition_key.json").write_text(
    json.dumps(key, indent=1), encoding="utf-8")
print("DONE 35 claims; blind packet + key written")
