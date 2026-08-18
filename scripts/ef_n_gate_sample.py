"""N-gate #2: stratified wiki-claim sample selection (protocol executed
before seeing any EF results). Strata: recency (recent/older/unknown
last_verified). 34 claims target."""
import json
import random
import re
from pathlib import Path

VAULT = Path(r"P:/.data/wiki/concepts")
pages = []
for p in sorted(VAULT.glob("*.md")):
    text = p.read_text(encoding="utf-8")[:3000]
    m = re.search(r"last_verified:\s*([0-9-]+)", text)
    lv = m.group(1) if m else None
    sm = re.search(r"summary:\s*>?\s*\n((?:\s{2,}.*\n)+)", text)
    if sm:
        claim = " ".join(l.strip() for l in sm.group(1).splitlines()[:4])
    else:
        sm2 = re.search(r'summary:\s*"?([^"\n]+)"?', text)
        claim = (sm2.group(1).strip() if sm2 else p.stem.replace("-", " "))
    pages.append({"file": p.name, "claim": claim[:180], "last_verified": lv})

recent = [p for p in pages if p["last_verified"] and p["last_verified"] >= "2026-08"]
older = [p for p in pages if p["last_verified"] and p["last_verified"] < "2026-07"]
mid = [p for p in pages if p["last_verified"] and "2026-07" <= p["last_verified"] < "2026-08"]
unknown = [p for p in pages if not p["last_verified"]]
rng = random.Random(42)
sel = (rng.sample(recent, min(14, len(recent))) +
       rng.sample(older, min(10, len(older))) +
       rng.sample(mid, min(4, len(mid))) +
       rng.sample(unknown, min(6, len(unknown))))
print(f"vault: {len(pages)} | recent {len(recent)} mid {len(mid)} "
      f"older {len(older)} unknown {len(unknown)}")
print(f"selected: {len(sel)}")
json.dump(sel, open(r"P:\packages\yt-is\docs\evidence-fabric\benchmark"
                    r"\n_gate_sample_selection.json", "w"), indent=1)
for s in sel[:4]:
    print(" ", s["last_verified"], "|", s["claim"][:65])
