---
agent: zcode
host: zcode
created: 2026-08-26
session: sess_ab0a0135-9c07-432c-af42-c9305e09195e
status: COMPLETE_DECISION_ISSUED
decision: CONTRACT_RELIABILITY_NOT_SUPPORTED (batch phase strongly favorable to C; end-to-end bar not met — recon-stage failures under every arm)
review_runs: run-ad3e8ae1ae70 (final code state); earlier: run-77fe71d482a6 / run-69ccdfff5b2e / run-cd60477fb695
---

# Interest inference contract-compliance bakeoff — RESULTS

Artifacts of record:
`P:/.data/yt-is/ef/interest-inference/bakeoff-20260826T184845_plan_01b09359b3f05784/`
(plan plan_01b09359b3f05784, 319/319 eligible clusters, 13 bounded batches,
interleaved A/B/C; metrics.json holds batch rows + both recon attempts;
preregistration sha256 recorded at run start).

## Batch phase (39 live codex gpt-5.6-luna calls, medium reasoning)

| arm | attempted | first-pass valid | final valid | repaired | dropped edges | unrecoverable |
|-----|-----------|------------------|-------------|----------|---------------|---------------|
| A prose JSON | 13 | 9 | 9 | 0 | 0 | 4 |
| B strict schema | 13 | 8 | 8 | 0 | 0 | 5 |
| C strict schema + repair | 13 | 13 | 13 | 7 | 10 | 0 |

A failure classes: 2 no_json_extractable (prose/fence parsing) + 2
semantic_invalid. B failure classes: 5 semantic_invalid, ALL dangling
same-payload references; ZERO schema-enforcement violations across 26
schema-attached calls (enums, confidence bounds [0,1], nullability,
required fields, additionalProperties all perfectly enforced).
C: hygiene receipts cover every mutation; repair rounds bounded (max
attempts=2 policy), hash chains recorded.

Approximate measured token cost (input tokens, incl. the fixed ~74k/call
codex scaffold): A 748k, B 708k, C 783k for the batch phase.

## Reconciliation phase

First attempt failed all arms (schema nesting defect, AMENDMENT 3);
rerun `--recon-from` over the same canonical fragments:

| arm | fragments | outcome |
|-----|-----------|---------|
| A | 115 | FAILED — dangling `related_to` produced AT recon stage |
| B | 121 | FAILED — reconciler cited cluster ids its assigned fragments never supported (`[64,97,318,343]`): invented evidence |
| C | 186 | FAILED — provider emitted `parent references itself` inside final; cycle-fixing is outside the frozen reference-repair scope → correct fail-closed |

These are genuine provider-contract failures of three DIFFERENT kinds —
parse robustness (A eliminated by schema), cross-reference integrity
(A and C-at-scale), and evidential honesty (B) — none expressible in
JSON Schema, and cycle/evidence repair prohibited by the packet's scope
ceiling ("never decide which Interests exist"; no invented support).

## Frozen-rule DECISION

Mapping applied verbatim from the preregistration:

- CONTRACT_RELIABILITY_SUPPORTED requires 13/13 batch compliance by the
  winning arm AND <=1 unrecoverable across ALL its calls AND completion
  of its reconciliation tree through final validated disposition.
- Only C reaches 13/13 batches (with 0 batch unrecoverables), but NO arm
  completes reconciliation. The end-to-end bar fails under every arm.
- INSUFFICIENT_PROVIDER_CAPABILITY does not apply: schema enforcement is
  demonstrably real (0 violations / 26 calls); residual failure modes
  are referential/evidential, i.e. outside any schema's expressive reach.

### DECISION: CONTRACT_RELIABILITY_NOT_SUPPORTED

Per delegation, production wiring is NOT implemented: run_bootstrap /
run_inference remain byte-for-byte as before this lane (defaults off;
repair machinery present but unwired, reviewed at commits 666f8f60,
b68c3fdbc, 8b50c1328). Full-shadow bootstrap requirement was therefore
not exercised — shadows were gated on SUPPORTED.

## Provider-capability findings (recorded for future lanes)

1. codex exec --output-schema works end-to-end with gpt-5.6-luna and
   returns BARE conforming JSON as the final agent message (no fences),
   eliminating prose-extraction failures entirely (A lost 2 calls there).
2. The endpoint HTTP-400-rejects two constructs (live bisected):
   `uniqueItems`, and nested `$defs`-within-`$defs`. Single-level
   `$defs`/`$ref`, enums, numeric bounds, anyOf-null unions, minItems
   all pass. Both constraints remain enforced mechanically post-schema.
3. Output truncation risk grows with payload size: prose-A's largest
   recon call returned truncated JSON (rc=0). Schema-B/C outputs on the
   same prompts did NOT truncate across any observed call.

## Failure-mode arithmetic (why 0/3 kept happening)

Per-call valid rates imply a 14-20-call pipeline completing only if EVERY
leg survives: A ~69% -> near-zero completion probability; B ~62% ->
near-zero; C 100% at batch granularity still meets the recon-stage wall
(relationship cycles emerging once merged context exceeds single-batch
scope). Reliability next lever is the recon leg, not the batches:
bounded relationship hygiene (self-parent -> null WITH receipt) and
mechanical cluster-id clipping to fragment-supported sets are the two
candidate extensions IF the operator authorizes widening the repair
ceiling beyond the frozen "reference strings only" scope. Neither is
implemented here.
