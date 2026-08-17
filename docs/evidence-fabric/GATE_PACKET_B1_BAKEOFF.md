# Evidence Fabric — B.1 + projection-engine bakeoff gate packet (STOP)

Per B_GATE_DECISIONS.md. Everything through B.1 and the bakeoff is complete;
**full-corpus backfill NOT started.** All receipts in `benchmark/` on branch
`evidence-fabric`. Agent: zcode · 2026-08-17.

---

## 1. Untouched holdout + preregistration ✓

`PREREGISTRATION_B1.md` committed BEFORE any B.1 run: 114 queries (89
hand-authored from fresh-video excerpts + 25 auto), 7 consumer strata,
weights fixed from production expectations (ytis 0.30, wiki-evidence 0.20,
www 0.15, contradiction 0.10, review-arch 0.10, title 0.10, identifiers
0.05-critical). Positives exclusively from videos never in any prior index
(frozen corpus cap + 12/category). Exact dense search — no ANN confound.

## 2. MiniLM vs BGE-M3 confirmatory ✓

`b1_results.json` (run 2 after the nDCG first-occurrence fix, defect logged
per R-B1.4; MRR/Recall unaffected by the defect):

| Config | W-MRR@10 | W-Rec@10 | W-nDCG@10 |
|---|---|---|---|
| A MiniLM+FTS5 | 0.7013 | 0.8462 | 0.7372 |
| B BGE-M3+FTS5 | 0.7936 | 0.8679 | 0.8112 |
| C BGE-M3+learned-sparse | 0.8102 | 0.9045 | 0.8331 |
| D BGE-M3+both | **0.8121** | **0.9045** | **0.8335** |

R-B1.1: ΔW-MRR = **+0.0923**, stratified bootstrap 95% CI **[+0.048, +0.138]**
(point +0.092) — significant well beyond the +0.03 bar. Identifier MRR
improves (0.50 vs 0.36). **But PROMOTION = BLOCKED**: exact_identifiers
Recall@10 dropped 0.60→0.50 (one query at n=10) against a ≤0.02 guard.
The guard's granularity (0.02) is finer than the stratum's resolution
(0.1/query) — a preregistration design flaw surfaced by execution, NOT
rewritten post-hoc. **Recommendation for your call**: the guard as
preregistered cannot distinguish a one-query flip from a real regression at
n=10; either accept BGE-M3 on the recorded evidence or require a ≥50-query
identifier stratum before deciding. Qwen3-4B remains rejected (no new
evidence; not rerun).

## 3. BGE-M3 learned-sparse probe ✓

R-B1.2 satisfied: D over B by **+0.0223** W-nDCG (≥ +0.02) with no
identifier regression → **ADOPT learned sparse (advisory pass)**.
Notable: C (learned sparse replacing FTS5 entirely) beats B — learned
sparse alone is not worse than FTS5 alone on this corpus; D (both) is
best. FTS5 is NOT replaced in the recommendation; D keeps it.

## 4. Qdrant server vs FAISS HNSW+FTS5 ✓ — my Phase B recommendation is REVERSED

`bakeoff_results.json` + `bakeoff_addendum.json` (154,719 points, bge-m3
1024d vectors, same queries, same machine, HNSW m=32 both, top-20, warmup;
FAISS re-measured at efSearch=64 for configuration equivalence):

| Axis | A: FAISS+FTS5 (ef=64) | B: Qdrant server 1.19.0 native |
|---|---|---|
| hybrid p50/p95/p99 | 167/442/626 ms | **5.4/27/31 ms** |
| ANN recall@20 | 0.888 | **0.969** |
| holdout MRR@10 | **0.467** | 0.406 |
| filtered p95 (0.19% selectivity) | 442 ms (post-filter) | **26 ms (native)** |
| 4 concurrent readers | 240 q in 16.1 s | **240 q in 0.85 s (281 qps)** |
| incremental add 2K | rebuild-class operation | **2.18 s live** |
| delete 1K + verify | manual | **0.05 s, counts exact** |
| kill -9 mid-upsert + restart | (faiss save not atomic — noted) | **1.38 s to first query; count = last committed exactly (159,719)** |
| build | **35 s** | 126 s |
| disk / RAM | **2.1 GB** / in-process | 4.9 GB / 3.6 GB RSS |

Quality is parity-within-noise (±0.06 MRR at n=114); every operational axis
except build-time/disk favors Qdrant server decisively — because FTS5
OR-queries at 155K docs are the latency floor of engine A, and FAISS has no
native filtering/updates. Per your decision rule ("do not favor FAISS
because it avoids a server"): **recommended Phase C projection = Qdrant
SERVER via the native Windows binary (v1.19.0, `P:/.data/yt-is/ef/tools/`),
dedicated port 6390/6391, config-file driven.** The Phase B FAISS+FTS5
recommendation was an artifact of comparing against LOCAL mode with no
server available to test — your insistence on testing the server was
correct.

**Incident disclosure:** early bakeoff attempts unknowingly talked to the
host's pre-existing OpenWhispr qdrant (`qdrant-win32-x64.exe`, port 6333)
and created `bakeoff_b` on it; I deleted that collection via API (server
now serves only `notes`, verified) and their process was never killed (my
taskkill matched only `qdrant.exe`, not their image name; their uptime
spans the whole session). Contracts updated: dedicated ports + config file,
PID-tracked cleanup only, never image-name kills (D014).

## 5. The 7,110 records classified ✓

`gap_classification.json`: 7,102 missing title only · 5 missing both ·
2 missing channel only · **0 missing canonical identity** · 1 unable to
reopen (the test fixture 'transcript A', 12 chars → Case B quarantine).
**7,109 = Case A: INDEX=YES with `metadata_state=incomplete`** (channel
intact; reopen verified 25/25; video_catalog offers zero recovery —
deterministic recovery would need a fresh fetch, scheduled independently).
Phase A's "lack title/channel" was imprecise: it is overwhelmingly
title-only.

## 6. Recommended dense model

**BGE-M3** — pending your decision on the identifier-guard artifact (§2).
If accepted: build cost 41 min full-corpus (measured 168 ch/s fp16 GPU),
2.3 GB VRAM, learned sparse included (one encode pass).

## 7. Recommended projection architecture

**Qdrant server, native binary, dedicated port, HNSW m=32 + sparse vectors
(client-encoded BM25 now; bge-m3 learned-sparse vectors if D-config
adopted), payload indexes on channel_id/video_id.** BuildSpec from Phase A
§7 unchanged (immutable spec, generations, single promotion authority) —
engine field changes from `qdrant-local` to `qdrant-server:6390`.

## 8. Contract changes required

None to EvidenceUnit/ChunkRecord/EvidenceResult (accepted findings list
unchanged). Changes are internal: `ef/projection.py` (server client instead
of embedded path), `ef/query.py` (unchanged interface), plus two new
operational invariants recorded in DECISIONS.md (D013 dedicated ports, D014
no image-name process kills on this shared host).

## 9. Receipts

`PREREGISTRATION_B1.md` · `benchmark/b1_results.json` (with defect_log) ·
`benchmark/bakeoff_results.json` · `benchmark/bakeoff_addendum.json` ·
`benchmark/gap_classification.json` · harnesses `ef_b1_run.py`,
`ef_bakeoff.py`, `ef_bakeoff_addendum.py`, `ef_holdout_build.py`,
`ef_classify_gaps.py` (all committed).

## 10. Sibling fetch pipeline unaffected ✓

`pipeline_health_watch.py`: **all checks passed** at packet time;
transcripts.sqlite at **76,846** rows, read-only access throughout (all
joins via `mode=ro`); no state/notebook/DB writes from this work.

---

**STOP. No full-corpus embedding/backfill has started. Awaiting operator
decions on: (a) dense model promotion given the identifier-guard artifact,
(b) projection architecture adoption, (c) unblock Phase C.**
