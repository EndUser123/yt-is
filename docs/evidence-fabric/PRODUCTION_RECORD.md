# Evidence Fabric v1 — Production Acceptance Record

**Canonical status document.** Points to receipts; does not duplicate them.
Supersedes gate-narrative documents as the operational source of truth.
Created 2026-08-17 after C9 full pass and promotion.

## What is running

| Field | Value |
|---|---|
| System | Evidence Fabric v1 (yt-is semantic retrieval over transcripts) |
| build_id | `generation/gen1-1a9efe7e6128e4b1` |
| active_generation | **1** (promoted 2026-08-17) |
| rollback generation | 0 (promotion.json history; retraction archive exists) |
| Authority | `P:/.data/yt-is/transcripts.sqlite` (read-only; char-offset EvidenceUnits) |
| Encoder | BGE-M3 dense (1024d) + learned sparse |
| Projection | Qdrant server 1.19.0 native binary, ports 6390/6391, PID-owned lifecycle |
| Collection | `evidence_chunks__gen1` — 166,714 points (== catalog parity) |
| indexed_watermark at promotion | 2026-08-16T18:59:15Z (lag 0) |
| benchmark_protocol | v1.2 (behavioral gates; sealed fresh holdouts) |

## Why it was accepted

C9 consumed untouched shard03 under protocol v1.2: **27/27 gates passed**
(exact/literal strata 1.0; allcaps df=1 R@1 1.0; conventional behavioral
1.0; zero-df empty 1.0; twins 0; judged descriptive 0.846 / telegraphic
1.0 / technical 1.0 / comparison 0.80 / weak-common 1.0; latency p95
195 ms; reopen 17/17; filter 7/7; parity; namespace; lag 0;
restart/reconnect; four anchors green).

- C9 verdict receipt: `docs/evidence-fabric/receipt_c9_final_battery_ab64871d8c95.json` (sha256 ab64871d8c95ac54…)
- Promotion receipt: `P:/.data/yt-is/ef/promotion.json` (emitted by the
  separate fail-closed promoter after suite authorization; the
  promoter's earlier REFUSAL on an unauthorized suite is preserved
  evidence the P0 gate-integrity repair works)
- Protocol: `BENCHMARK_PROTOCOL_V1_2.md`; preregistration:
  `PREREGISTRATION_C9` line (in V1_2 doc); shard03 seal:
  `benchmark/shard03_seal.txt`

## Settled decisions (require new falsifying evidence to reopen)

Authority/provenance (char-offset EUs) · BGE-M3 dense+learned-sparse ·
Qdrant server · weighted semantic fusion · literal guarantees + explicit
exact mode · ambiguity-aware dual lane · ALLCAPS ambiguous + df=1
singleton pin · comparison sparse-heavy lane · zero-literal = no
fabricated primary evidence · protocol-controlled fresh holdouts ·
behavioral gates not route labels · receipt/promoter separation.

C1–C8 = exposed regression/development evidence (their verdicts,
including C8's FAIL, are historical truth; see respective STOP packets).

## Known non-blocking issues

1. **Incremental catch-up pending**: transcripts fetched after the
   backfill snapshot need an incremental pass (mechanism live and
   verified; lag was 0 at promotion; run
   `ef.freshness.incremental_update` / schedule it). Exposed to the
   operational monitor via `P:/.data/yt-is/ef/operational-status.json`.
2. **Cold-start encode latency** (~2.7 s first query vs 195 ms warm
   p95): not a correctness blocker. Planned fix is lifecycle warmup
   (load encoder → representative warmup encodes → mark READY), with
   stage-decomposed measurement before any model change.

## Rollback

`active_generation` lives solely in `P:/.data/yt-is/ef/promotion.json`
(single promotion authority). Rollback = restore generation-0 state
(remove/rewrite that file; gen0 had no prior promotion, so full
rollback = delete file + halt consumers). The erroneous C2-era
promotion+retraction archive (`promotion.retracted.bprime5.json`)
documents the procedure.

## Sealed future holdouts

shard04 (region [505:535]), shard05 (region [535:565]) — sealed under
v1.2 before any C9 retrieval; unconsumed; for the next promotion cycle
(e.g., encoder upgrade or consumer-driven change).

agent: zcode · host: both

## Update 2026-08-18: operational phase begun

- First live incremental catch-up ran: ~8,400 transcripts indexed
  (76,791 -> ~85K points on disk), watermark advanced to
  2026-08-18T02:50Z. Remaining backlog ~21.7K (fetch pipeline is
  outpacing the drainer — expected until a scheduled incremental cadence
  replaces manual drains).
- Two operational fixes landed: tz-aware watermark parsing; deletion
  reconciliation batched via a single authority scan (per-EU connection
  opens caused disk I/O errors past ~78K EUs).
- KNOWN ISSUE (operate): long incremental drains hit Windows ephemeral
  port exhaustion (WinError 10048) after ~6K Qdrant HTTP calls — pace
  the drainer (sleep between batches) or add connection reuse; not a
  correctness issue (resumable, watermark-tracked).

## Update 2026-08-18 (K-gate operationalization): COMPLETE

- **Incremental service**: `scripts/ef_incremental_service.py` — paced
  daemon (8s pause; fixes port exhaustion), error-tolerant, watermark-
  safe across Qdrant outages. Soak in progress at press time: points
  166,714 → 199,409+, lag 29K → ~18K and falling, ingestion unimpeded,
  gen1 serving throughout.
- **Cold-start decomposed**: import 0.12s | model load 10-12s | first
  encode 0.96s | subsequent 39ms. Readiness lifecycle
  (`ef/readiness.py`, states starting/warming/ready/degraded, durable
  `readiness.json`): warm-start verified ready-in-10.3s, post-warmup
  probe 37ms. No model change — hypothesis confirmed.
- **Status surface complete** (`operational-status.json`): all K-gate
  monitor fields incl. last_index_error, incremental_worker_state,
  readiness, rollback_generation=0, sealed shards 04/05.
- **Operational tests**: 4 green (idempotence, status completeness,
  readiness contract, outage isolation with watermark survival).
- **/wiki consumer handoff**: `WIKI_INTEGRATION_HANDOFF.md` (ef-query
  CLI seam, three maintenance modes, A/B rule). No /wiki changes.
