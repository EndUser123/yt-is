# Evidence Fabric — Phase B report (STOP for operator review)

Phase: B (amendment v1.1 §15) · Date: 2026-08-16 · Agent: zcode · Branch: `evidence-fabric`
Scope honored: A-0 + A + B complete; **no bulk indexing, no model commitment applied,
no FTS5 replacement** — engine findings below require your decision before Phase C.

---

## 1. Verdict per preregistered rules (PREREGISTRATION_B.md, committed before results)

| Rule | Result | Detail |
|---|---|---|
| R1: 0.6B (bge-m3) over baseline | **NOT MET** | ΔnDCG@10 +0.020, ΔRecall@20 +0.017 (needed ≥+0.05 both) |
| R2: 4B (Qwen3-4B) over 0.6B | **NOT MET** | ΔnDCG@10 **−0.016**, ΔRecall@20 +0.008; latency/throughput criteria passed (0.43s / 2.5h proj) |
| R3: reranker stage needed | **NO** | best Recall@20 = 0.959 ≥ 0.85 |
| R4: ties → smaller | n/a | no tie |
| R5: Qdrant local ≤500ms at scale | **FAILED** | p95 **9.72 s** at 154,719 points → local mode flagged for C redesign, exactly as the rule prescribes |

**Literal rule outcome: baseline MiniLM remains the committed dense model.**
No promotion fired; per preregistration discipline the rules are reported as
run, not re-weighted after seeing results.

## 2. Full metrics (decision tier n=242 title/description queries; smoke tier n=26 hand-authored natural questions; hybrid dense+BM25+RRF throughout)

| Model | Tier | Rec@5 | Rec@20 | MRR@10 | nDCG@10 | p95 | ch/s (device) |
|---|---|---|---|---|---|---|---|
| MiniLM-22M | decision | 0.884 | 0.942 | 0.641 | 0.729 | 0.51s | 752 (cuda) |
| MiniLM-22M | smoke | 0.923 | 1.000 | 0.721 | 0.692 | 0.38s | |
| bge-m3-568M | decision | 0.926 | 0.959 | 0.686 | 0.749 | 0.43s | 54 (cuda) |
| bge-m3-568M | smoke | 0.962 | 1.000 | **0.812** | **0.754** | 0.37s | |
| Qwen3-4B | decision | 0.942 | **0.967** | 0.677 | 0.733 | 0.43s | 15 (cuda bf16) |
| Qwen3-4B | smoke | 0.962 | 1.000 | 0.808 | 0.743 | 0.45s | |

## 3. The divergence the rules did not anticipate (for your review, not a rule override)

The decision tier is lexically easy (title/description queries) — the BM25 leg
carries much of it, compressing differences between dense models. On the
smoke tier (paraphrased human questions — the shape /wiki //www queries will
actually have), **bge-m3 clears the same +0.05 bar the decision tier denied
it: ΔnDCG@10 +0.062, ΔMRR@10 +0.091 vs MiniLM.** Qwen3-4B does not separate
from bge-m3 on smoke (−0.001 MRR) at 3.6x the build cost — R2's rejection is
robust across tiers.

**Decision needed (B-gate):** keep MiniLM per rules, or override to bge-m3 on
smoke-tier evidence. Cost of bge-m3: build 41 min vs 3 min (both inside any
maintenance window), 2.3 GB VRAM, 0.5 GB vectors — all feasible per Phase A
capacity numbers. My recommendation: **bge-m3** — the smoke tier is the
tier that resembles real consumers, and every operational cost is well
within budget.

## 4. Rule 5 failure and the measured C-redesign path

Qdrant **local mode** brute-force scans (no ANN index): 0.43s at 5.6K points
→ 9.3s p50 / 9.7s p95 at 154,719 points. Linear in corpus; unusable for
interactive consumers; would also breach the 10s research budget as the
corpus grows toward 665K chunks.

Measured embedded alternative at the **same 154,719-chunk scale**
(`benchmark/ann_fts5_probe.json`, this branch):

| Component | Build | p50 | p95 |
|---|---|---|---|
| faiss-cpu HNSW (dense, IP) | 3 s | 0.1 ms | 0.1 ms |
| sqlite FTS5 (BM25 lexical) | 21 s | 63 ms | 204 ms |
| Python RRF fusion | — | 0.0 ms | 0.1 ms |
| **end-to-end hybrid** | — | **63 ms** | **204 ms** |

All Windows-native, zero servers, rebuildable, no Docker/WSL. End-to-end
p95 is 47x better than Qdrant local and fits every consumer budget (§9:
interactive ≤2s, evidence ≤5s, research ≤10s) with headroom for the 665K
future corpus (HNSW is sublinear; FTS5 leg is the one to watch).
**Recommendation: Phase C projection engine = faiss-cpu + FTS5 behind the
existing projection/query contracts** (swap is contained: `projection.py` +
`query.py` internals; EvidenceUnit/ChunkRecord/EvidenceResult unchanged,
Qdrant path stays for benchmark-scale work). The amendment's "Qdrant
accepted" presumed a server deployment this host cannot run; rule 5 was
written for exactly this discovery.

## 5. Receipts (all on branch `evidence-fabric`)

- `PREREGISTRATION_B.md` — rules committed before any run
- `benchmark/results.json` — 3 models × 2 tiers
- `benchmark/verdict.json` — executable rule application
- `benchmark/smoke_queries.json` — 26 hand-authored queries (3 of 30 sampled
  chunks were unusable: title-only transcript, lyric-only, chant-only)
- `benchmark/scale_check.json` — rule 5 failure receipt (154,719 pts)
- `benchmark/ann_fts5_probe.json` — faiss+FTS5 receipts at same scale
- `a0_smoke_receipt.json`, `PHASE_A_FINDINGS.md` — earlier gates
- Harness: `scripts/ef_benchmark_b.py`, `ef_scale_check_b.py`, `ef_ann_probe_b.py`
  (benchmark corpus.json 36 MB is regenerable; gitignored)

## 6. What I need from you to start C

1. **Dense model**: MiniLM (rules-literal) or bge-m3 (smoke-tier override) — recommendation: bge-m3.
2. **Projection engine**: faiss+FTS5 embedded (recommendation) vs Qdrant server (requires installing Docker/WSL).
3. Optional: any adjustment to the 7,110 provenance-gap transcripts (backfill vs skip) — default skip.

Fetch pipeline (sibling session) remains healthy and untouched throughout;
corpus grew 75,706 → 76,846+ during these measurements.

agent: zcode · host: both
