---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_ab0a0135-9c07-432c-af42-c9305e09195e
status: COMPLETE_DECISION_ISSUED
decision: DECOMPOSED_CONTRACT_SUPPORTED (shadow gate 3/3 COMPLETE)
review_runs: run-60dee6f5bb3d (freeze), run-2104809e6a0f + run-e383c0c54d89 (AMENDMENT 2 hunks), run-3b7dad037e99 (clamp)
---

# CONTRACT ARCHITECTURE GENERATION v2 — RESULTS

Preregistration of record:
`contract-architecture-v2-preregistration.md` (+ AMENDMENT 2, reviewed).
Freeze commit `959cd1ad`; amendments `831944e5`, `4008a701`;
shadow-wiring `02f56240`. D0 = frozen citation from
`bakeoff-20260826T184845_plan_01b09359b3f05784` (never rerun).

## Live arm results (unlabeled current corpus; plan_01b09359b3f05784)

### Phase-1 object stage (independent semantic objects)

| arm | batches complete | valid interests | schema defects | semantic defects |
|-----|------------------|-----------------|----------------|------------------|
| D1 strict | 13/13 | 218 | 0 | 0 |
| D2 isolated | 13/13 | 197 | 0 | 0 |
| D3 full | 13/13 | 210 | 0 | 0 |

The decomposed phase-1 contract with provider-native strict output is
reliably perfect at batch granularity: 39/39 across arms, zero enum/
bounds/shape violations, zero cross-references even attempted.

### Relation stage

| arm | calls | valid edges | invalid endpoints | quarantined optional | repairs |
|-----|-------|-------------|-------------------|----------------------|---------|
| D1 | 2 (1 + 1 repair) | 0 | 1 | strict fail-closed | 1 exhausted |
| D2 | 1 | 221 | 3 (receipted) | 3 | 0 |
| D3 | 1 | 154 | 32 (receipted) | 32 | 0 |

Note: D2's relation stage had the strongest per-arm edge throughput of
the run (221 valid edges, only 3 defects — all quarantined with
receipts).

D1's designed strictness converts one endpoint violation into an arm
fail-closed after the sanctioned single repair — by preregistration.
D2/D3 quarantine the same class losslessly with receipts.

### Reconciliation

| arm | mode | outcome |
|-----|------|---------|
| D1, D2 | monolithic tree + R-1 sanitizer | receipted fail-closed on GENUINE monolithic defects: invented interest with empty cluster_ids (D2 att.1), unresolvable target_interest disposition (D2 att.2), out-of-universe cluster id (att. rerun); monolithic reconciliation failures now >=4 live (v2 artifacts alone: D2 attempt-1 empty-cluster_ids crash, D2 rerun unresolvable target_interest, D2 guarded rerun out-of-universe cluster id; plus bakeoff-1's arm-level failure) |
| D3 | decomposed: grouping -> coverage-retry -> mechanical assembly -> relation call over canonical ids | COMPLETED through final validated reconciliation, every time it ran |

## Shadow gate (packet-mandated 3/3 clean-root full-coverage bootstraps)

| shadow | objects in | explicit dispositions | canonical objects | outcome |
|--------|-----------|----------------------|-------------------|---------|
| 1of3 | 303 | 376 | 225 | COMPLETE |
| 2of3 | 329 | 445 | 307 | COMPLETE |
| 3of3 | 326 | 406 | 288 | COMPLETE |

Aggregate: 958 source objects -> 1227 receipts (multi-stage transforms
stack additional EXPLICIT receipts) -> 820 canonical objects; ZERO
silent loss in any run (accounting equality enforced mechanically);
required-link failures receipted and excluded (1+1+2); final graphs all
passed the UNMODIFIED frozen v1 validator.

Artifacts of record:
runs/shadow-v2-{1of3,2of3,3of3}-*/ under
P:/.data/yt-is/ef/interest-inference/runs/, plus bakeoff-v2-*_D{1,2,3}.

## Frozen decision mapping — applied

- Success requirements 1-6 verified for D3 (13/13 phases; reconciliation
  completes; dispositions cover every source; strict v1 validator pass;
  no semantic relaxation — field surfaces byte-equivalent, prompts'
  semantic prose pinned verbatim by drift-guard tests; run-scoped
  artifact isolation preserved incl. FROZEN_PLAN_ID pre-provider abort).
- D0 never completed end-to-end (citation), so the decomposed arm beats
  the frozen reference.

### DECISION: DECOMPOSED_CONTRACT_SUPPORTED

Shadow gate satisfied (3/3) on the wired shadow-only path
(`scripts/contract_v2_bakeoff.py --shadow [N]`). Reliability is now
calling-solved ON THE SHADOW PATH per the packet's own language.
Canonical persistence remains untouched — promotion to accepted state
requires the separate operator-designated Interest ground-truth curation
lane plus a valid semantic-recall gate, exactly as previously recorded.

## Key engineering facts for future lanes

1. Monolithic nested-payload reconciliation is the reliability wall —
   4 distinct live defect classes observed (empty-cluster invention,
   unresolvable disposition targets, fabricated cluster support,
   dangling references at scale). Decomposed reconciliation removes the
   wall by construction.
2. Phase-separation works because it changes WHO owns failure: object
   defects are contained per-object; relational defects are quarantined
   or fail closed without touching validated objects.
3. Mechanical bookkeeping (IDs, coverage, provenance union, duplicate-
   identity merges, dispositions) moved from provider to code; the
   provider only decides semantics (objects, equivalences beyond exact
   identity, relations).
4. Endpoint rejects `uniqueItems` and nested `$defs` (HTTP 400);
   everything else used here passes.
