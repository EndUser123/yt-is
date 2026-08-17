# Phase C replay — FAILURE packet (STOP, no promotion)

C-gate decision 7 outcome: the production candidate replay FAILED. **Generation 1 was NOT promoted.** `promotion.json` remains absent (active_generation=0). Discriminating evidence follows; receipts in `c_replay_receipt.json`.

## What passed

| Test | Result |
|---|---|
| T7 structural | 166,714 collection points == 166,714 catalog gen1 chunks (exact parity after A-0-overlap fix-up) |
| T4 latency | p95 1.16 s ≤ 2.0 s budget (411 queries, GPU query encode included) |
| T5 reopenability | 15/15 exact-span reopens against the live authority |
| T6 filter correctness | 20/20 channel-filtered queries returned only the requested channel |
| Backfill | 76,791 eligible transcripts (incl. 7,102 incomplete-metadata Case-A), 166,433+281 chunks, **0 errors**, fetch pipeline untouched |

## What failed

| Test | Measured | Gate | Why |
|---|---|---|---|
| T1 benchmark MRR@10 | 0.488 | ≥ 0.586 | gate calibrated against a 6K-chunk EXACT-search baseline; candidate runs ANN over 166K chunks |
| T2 holdout MRR@10 | 0.508 | ≥ 0.694 | same miscalibration |
| T3 identifier R@10 | 0.346 | ≥ 0.82 | two causes below |

## Discriminating evidence

1. **Exact-lane fusion defect (real, mine).** The production path fuses the
   FTS5 exact lane as an EQUAL-weight RRF leg. The lane comparison already
   showed fusion dilutes exact-token ranking (dev R@1: FTS5 alone 0.60;
   L1+L3 0.24; L2+L3 0.47), and the replay confirms it end-to-end: for
   query `GR0000tn2` — a token present in EXACTLY ONE chunk — that chunk
   still does not rank first (semantic legs outvote it: two-leg RRF sums
   ~0.030 > exact-only 0.0164). For identifier queries the exact lane must
   be AUTHORITATIVE (FTS-only ranking, or token-containment filter before
   fusion), not a co-equal leg. My dev data said this; I fused anyway.
   Receipt: diagnostic in this session log; dev receipt
   `benchmark/identifier_lanes_dev.json`.

2. **Gate calibration errors (mine).**
   - T1/T2 compared 166K-corpus ANN numbers to 6K-corpus exact-search
     baselines. The scale-matched comparator that exists — bakeoff engine B
     (155K points, m=32 HNSW, same architecture) — measured holdout MRR
     **0.406**; the production candidate measured **0.508**, i.e. BETTER
     than the only like-for-like baseline on record. The candidate did not
     regress; the gate was set from the wrong number.
   - T3's 0.82 gate was calibrated on dev tokens with document frequency
     ≤61. The sealed acceptance set was built WITHOUT the df filter: its
     tokens have **median df 1,473** (`YouTube` appears in 17,287 chunks).
     With thousands of tied chunks, top-10 containment of one sampled
     positive is bounded far below the gate regardless of lane quality —
     an acceptance-construction asymmetry vs dev, my omission.

## Remediation options (your call — not executed)

A. Fix the exact lane to authoritative-for-identifier-queries (FTS-only
   ranking or containment-first fusion), AND recalibrate T1/T2 gates to
   scale-matched baselines (bakeoff engine-B numbers), AND either rebuild
   the acceptance set with the dev df-filter or re-derive the T3 gate from
   the acceptance set's actual df distribution — then re-replay. All three
   gate changes are recalibrations to like-for-like comparisons, not
   lowering the bar for the candidate.
B. Any subset of A you prefer; the exact-lane fix alone is the minimum
   defect repair.

## State

- gen1 collection exists on the yt-is qdrant server (6390) but is NOT the
  active generation; no consumer points at it.
- All work committed on branch `evidence-fabric`; fetch pipeline healthy
  throughout.

agent: zcode · host: both · 2026-08-17
