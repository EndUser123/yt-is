# C2 final battery — STOP packet (1 gate failed of 20; NOT promoted)

D-gate rule 10 compliance: no promotion, thresholds untouched, set
untouched. Receipt: `c2_final_battery.json`.

## Passed (19)

All exact strata at literal_prefix 1.0 (df1 R@1 = 1.0 deterministic; df2-10,
df11-100, df101-1000, punct all 1.0) · semantic natural 0.72 / technical
0.57 / comparison 0.40 · latency p95 151 ms · reopen 30/30 exact-span ·
filter 20/20 · structural parity 166,714 · namespace VALID · freshness
lag 0 · **qdrant restart/reconnect PASS (stale cached client recovered,
no PowerShell)** · C1 regression df1 1.0 + semantic 0.4167 stable.

## Failed (1)

**near_twins: false_pin = 1 of 12** (gate: == 0).

- Observed: one mutant query (all 12 mutants are df=0 constructions)
  returned the twin's chunk at rank 1.
- Stage responsible: identifier-route semantic FILL.
- Leading explanation: with zero literal matches, fuse_identifier_priority
  fills every slot semantically (D-gate rule 3: "if exact >= K rank
  within; else exact first, semantic fill" — the else-branch includes
  zero literals). A one-character-different token is semantically
  near-identical to dense+sparse encoders, so the twin ranks first.
  The gate (no false-pin) tests behavior the routing contract permits.
- Strongest alternative: a genuine precision defect — a user querying a
  nonexistent identifier gets a look-alike presented as the top result
  without an explicit "no literal match existed" signal.
- Distinguishing test: fetch the mutant's results and check whether any
  literal match existed (none does, by construction df=0).
- Smallest remediation (operator's call):
  (a) amend the twin gate to "false_pin == 0 when mutant df > 0;
      zero-literal mutants report-only" — aligns the gate with rule 3; or
  (b) make identifier-intent queries with ZERO literal matches return
  empty (no fill) — arguably better UX for identifier lookups, but it
  NARROWS rule 3's fill semantics and needs the same treatment for
  strict-exact mode checks.

Everything else in the D-gate authorization is complete and green,
including the reconnect invariant.

agent: zcode · 2026-08-17
