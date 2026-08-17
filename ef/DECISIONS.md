# Evidence Fabric — decision log

Running log of non-obvious decisions with rationale. Append-only. One entry per
decision. Scope: Evidence Fabric implementation (Phase A-0/B per amendment v1.1).

Format: `D### YYYY-MM-DD decision — rationale — evidence/falsifier`

- D001 2026-08-16 Qdrant **local mode** (embedded via qdrant-client `path=`)
  adopted for A-0/A/B. — Host has no Docker and no WSL; local mode uses the
  same client API as server deployment, so promotion to a server later is a
  config change, not a rewrite. — Falsifier: local mode lacking a feature the
  design requires (checked: hybrid RRF works, see D002).
- D002 2026-08-16 Hybrid query (dense+sparse prefetch, RRF fusion) **verified
  in local mode** via isolated probe. — Receipt in `PHASE_A_FINDINGS.md`.
  Server-side BM25 (`models.Document`, `model='bm25'`) raises ImportError
  (needs fastembed) in local mode.
- D003 2026-08-16 Sparse vectors are computed **client-side** (own BM25
  encoder, Lucene-style k1=1.2 b=0.75) and upserted as explicit sparse
  vectors. — Avoids fastembed dependency for A-0; keeps projection dumb and
  rebuildable; matches authority/retrieval separation. — Phase B may compare
  against fastembed/bm25s encoders on the decision benchmark.
- D004 2026-08-16 protobuf upgraded 3.19.6 → 5.29.6 (user site-packages).
  — 3.19.6 already violated the pins of 5 installed packages (pip check
  receipt) and blocked qdrant-client import (`builder` missing). 5.x satisfies
  every existing constraint (<6 and <7 pins).
- D005 2026-08-16 Evidence Unit provenance uses **char offsets** into the
  authoritative transcript, not timestamps. — Verified by sampling: transcript
  text is plain prose with caption line breaks, zero timestamps in cache
  (sampled newest notebooklm rows, 2026-08-16).
- D006 2026-08-16 Authority sources are read via sqlite `mode=ro` URI:
  `P:/.data/yt-is/transcripts.sqlite` (text) joined to
  `P:/.data/yt-is/batch_status.sqlite` (video/channel metadata). — Operator
  directive: assume nothing about schema; both were verified by inspection
  this session. NOTE: earlier session summaries referenced a
  `transcript_cache` table *inside* batch_status.sqlite — that table does not
  exist there; the real table `transcript_cache` lives in transcripts.sqlite.
- D007 2026-08-16 A-0 dense model = `all-MiniLM-L6-v2` (22M params). —
  Plumbing proof only; explicitly NOT the committed corpus model. Amendment
  §8 forbids model commitment before the Phase B decision benchmark.
- D008 2026-08-16 Qdrant local mode takes an **exclusive lock** on its storage
  path (single process). — Multi-process build workers must funnel projection
  writes through one writer process; encoded in BuildSpec contract
  (Phase A deliverable).
- D009 2026-08-16 Phase B model rules applied literally: **no promotion** —
  baseline MiniLM remains committed per R1/R2 (deltas +0.02, below +0.05
  bars). Smoke-tier divergence (bge-m3 +0.062 nDCG on hand queries) reported
  to operator as override option, NOT applied unilaterally. — Preregistration
  discipline (rules committed pre-results in PREREGISTRATION_B.md).
- D010 2026-08-16 Rule 5 FAILED: Qdrant local mode p95=9.7s at 154,719 pts
  (brute-force scan, no ANN). Measured embedded alternative at same scale:
  faiss-cpu HNSW 0.1ms + FTS5 204ms → end-to-end hybrid p95 204ms.
  Recommendation to operator: C projection engine = faiss+FTS5 behind
  existing contracts. — Receipts: benchmark/scale_check.json,
  benchmark/ann_fts5_probe.json.
- D011 2026-08-16 benchmark/corpus.json (36 MB, regenerable deterministically)
  excluded via .gitignore; digest recorded in results.json.
- D012 2026-08-16 Qwen3-Embedding-4B loads on GPU in bf16 alongside the
  fetch pipeline (batch 8, max_seq 512 cap required — padding inflation
  caused OOM at default settings; same cap needed for bge-m3 at batch 64).
