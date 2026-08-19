"""Reclassify the original 21 claims under the O-gate two-tier semantics."""
import sys, json
from collections import Counter
sys.path.insert(0, r"P:\packages\yt-is")
sys.path.insert(0, r"P:\packages\yt-is\scripts")
from ef import embedding, buildspec
from ef.query_server import ProductionQuery
import ef_wiki_maintenance as M

pq = ProductionQuery(embedding.BGEM3Dual(), generation=buildspec.active_generation())
orig = json.load(open(r"P:\packages\yt-is\docs\evidence-fabric\benchmark\n_gate_sample_selection.json", encoding="utf-8"))
M.mode_staleness(pq, "warmup", "2026-08-01T00:00:00Z", 2)
reclassified = []
for item in orig:
    lv = (item["last_verified"] or "") + "T00:00:00Z"
    st = M.mode_staleness(pq, item["claim"], lv, top_k=5)
    reclassified.append({
        "file": item["file"], "signal": st["signal"],
        "genuinely_newer": sum(1 for c in st["candidates"] if c.get("published_after_last_verified")),
        "newly_available_only": sum(1 for c in st["candidates"] if c.get("captured_after_last_verified") and not c.get("published_after_last_verified"))})
print("original-21 corrected:", dict(Counter(r["signal"] for r in reclassified)))
print("w/ genuinely newer:", sum(1 for r in reclassified if r["genuinely_newer"] > 0))
json.dump(reclassified, open(r"P:\packages\yt-is\docs\evidence-fabric\benchmark\n_gate_run_reclassified.json", "w"), indent=1)
