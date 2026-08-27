---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_ab0a0135-9c07-432c-af42-c9305e09195e
status: FROZEN_PRE_RESULTS
supersedes: none (additive to contract-compliance-bakeoff-preregistration.md)
decision_space: DECOMPOSED_CONTRACT_SUPPORTED | MONOLITHIC_CONTRACT_SUPPORTED | NO_CONTRACT_ARCHITECTURE_SUPPORTED | INSUFFICIENT_PROVIDER_CAPABILITY
---

# CONTRACT ARCHITECTURE GENERATION v2 — PREREGISTRATION

Frozen before any measured v2 provider call. Same contamination rules as
the prior bakeoff: unlabeled current corpus only; no private Interest
labels; no holdout access; no persistence; no semantic tuning; the
319-cluster universe and <=25-cluster batch size unchanged.

## Core hypothesis under test

DECOMPOSE OBJECT INFERENCE FROM RELATION CONSTRUCTION. One response can
reliably emit enum/shape-clean independent objects (provider-native
schema enforces #2); it cannot reliably also assign cross-references,
build acyclic graphs, link questions/regrets AND stay globally consistent
(bakeoff-1 evidence: all three arms died at exactly these stages).

## Reuse dispositions (packet-mandated survey)

| mechanism | disposition |
|---|---|
| codex --output-schema structured output | ADOPT (phase/relation/grouping schemas; single-level $defs pattern) |
| bounded repair machinery (`validated_reference_repair`) | DONOR-EXTRACT: repair-call pattern reused for D1 relation-stage retry; hash-chain receipts pattern reused everywhere |
| Pydantic / Instructor / PydanticAI | REJECT: pydantic present but zero-dep conformance checker already covers the exact vocabulary; no material outperformance; instructor/pydantic_ai absent |
| existing reconciliation tree (`run_reconciliation_tree` + `validate_reconciliation`) | ADOPT verbatim for D1/D2 monolithic arms; its coverage/support rules ADAPT into the mechanical assembler (`verify_group_coverage`, provenance union, dispositions) |
| deterministic IDs (`fragment_identity_id` recipe) | ADOPT as `make_object_id` (kind+batch+normname+sorted cids); LLM never invents ids |

Machinery lives in `ef/contract_v2.py`; schemas in `ef/inference_contract.py`.

## Arms

- **D0** — frozen reference, NOT rerun (per architect order): bakeoff-1
  results `bakeoff-20260826T184845_plan_01b09359b3f05784/metrics.json`
  (batch valid-rate A 9/13, B 8/13, C 13/13; reconciliation failed in ALL
  arms; tokens/latency recorded there). Comparison against these numbers
  is by citation, not re-measurement.
- **D1** — decomposed phases, strict discipline: any phase-1 item defect
  fails that batch's object stage (valid sibling survival NOT claimed);
  relation-stage endpoint violations trigger ONE repair round against an
  explicit valid-ID list then fail closed.
- **D2** — D1 + per-object fault isolation: optional-object defects are
  quarantined with receipts (batch survives with >=1 valid core
  interest); malformed core objects are recorded invalid-core and
  excluded explicitly (never silently absorbed); optional relations are
  quarantined directly (no repair round).
- **D3** — D2 + decomposed reconciliation: provider proposes merge
  groups over source-object IDs; mechanical verifier demands exhaustive
  coverage (omission -> ONE bounded completeness retry listing uncovered
  ids; residue => fail-closed); mechanical assembly owns canonical
  selection (max confidence, id tiebreak), provenance union, identity-
  equality merges (receipted), drop_noise receipts (non-core only);
  single relation call over the CANONICAL set; assembly maps back to the
  EXACT v1 payload shape; gate = frozen `validate_inference` unchanged.

Semantic prompts equivalence guard: PHASE-1 prompt embeds the frozen
v2-template semantic paragraph VERBATIM (drift-guard test pins prefix
equality up to the structural return block).

Relation-stage pipeline position: D1/D2 relations attach BEFORE the
monolithic reconciliation tree (which keeps generating nested payloads —
that is what makes them 'reference'); SANITATION RULE R-1 applies
identically: relational fields inside a reconciliation OUTPUT are
discarded and replaced by mechanically-translated accepted edges mapped
through the tree's own flattened dispositions; dangling residue routes
through the sanctioned reference-only repair once, else fail-closed.
Questions/regret candidates emitted by the monolithic recon remain
subject to the strict validator (no relaxation).

## Phase definitions / metric equations (frozen)

Phase-1 per batch: object-stage complete iff provider rc=0 AND envelope
schema-conforms AND >=1 valid core interest survives that arm's policy.
Counters: calls, valid_object_payloads, valid_interest_objects,
invalid_core_objects, invalid_optional_objects, schema_failures
(envelope+item), semantic_failures.

Relations (per arm): calls, valid_edges, invalid_endpoints
(quarantined or repaired-failed), quarantined_optional_edges,
required_link_failures (= canonical questions left unlinked after the
relation stage — explicitly receipted, never fabricated).

Reconciliation: objects_in, objects_explicitly_dispositioned (MUST equal
objects_in; silent loss MUST BE ZERO by construction and is tested),
merge_groups_valid, provenance_preserved (union verified),
final_graph_validated (strict frozen validator, unchanged shape).

End-to-end: full 319-cluster coverage plan reused verbatim
(plan_01b09359b3f05784); provider_call_count, retries, repairs, tokens,
latency, wall time. Cost derived from token events exactly as bakeoff-1.

## SUCCESS REQUIREMENT (frozen)

A candidate architecture may be called SUPPORTED only if ALL hold:
1. 13/13 batch object stages complete;
2. final reconciliation completes;
3. every source object has an explicit disposition (accounting equality);
4. final graph passes the strict existing v1 validator unmodified;
5. no unsupported semantic relaxation occurred (field sets unchanged;
   envelopes only bind structure; prompts semantically identical);
6. run-scoped artifact isolation intact (existing `_new_run_dir` tests
   extended into v2 suite).

Decision mapping:
- arms D1..D3 evaluated independently against success items;
- if >=1 decomposed arm passes 1-6 AND beats D0's frozen end-to-end
  outcome (which NEVER completed): DECOMPOSED_CONTRACT_SUPPORTED for the
  best-scoring decomposed arm;
- if only the monolithic D0-referenced path passes 1-6 (impossible given
  frozen recon failures, kept for formality): MONOLITHIC_CONTRACT_SUPPORTED;
- provider/schema enforcement absent-in-volume despite bakeoff-1 proof ->
  INSUFFICIENT_PROVIDER_CAPABILITY;
- otherwise NO_CONTRACT_ARCHITECTURE_SUPPORTED.
Wire-into-shadow order on SUPPORTED: shadow flag default-off entry in
build script family; then 3/3 full shadow bootstraps from clean roots,
no persistence, before "solved" language is permitted anywhere.

FALSIFIER battery unit-pinned pre-freeze (tests/test_contract_v2.py):
dangling related_to proposed / invalid parent proposed / question linkage
failure / duplicate semantic objects across batches / one malformed
non-core object / reconciliation provider omission (detected +
mechanically recovered + zero-loss asserted) / same-second concurrent
runs (distinct roots).

## Review checklist

phase independence real (relation/generation defects cannot corrupt
phase-1 stores); accounting equality enforced; no semantic rewriting in
assembly; sanitizer R-1 cannot resurrect dropped edges; prompt-equivalence
guard active; artifact isolation unchanged; artifacts under runs/bakeoff-v2 dirs.

## AMENDMENT 2 (2026-08-27, mid-bakeoff; containment + shadow plan)

Two genuine live defects were receipted fail-closed during measured runs:
(1) D2 first attempt crashed uncaught when the monolithic reconciliation
tree's provider output carried an invented interest with empty
cluster_ids; (2) the D2 rerun crashed on ReconciliationContractError
(unresolvable target_interest). Both belong to the class the packet
requires to become RECEIPTED arm results, so run_reconciliation_tree in
the D1/D2 path is now wrapped `except Exception` -> classified _finish
row (type name recorded). Additionally: write_schemas persists and
returns inference-output-schema.json, which the post-recon one-shot
reference-repair closure attaches. Reviewed: run-2104809e6a0f,
run-e383c0c54d89.

### Live receipts (pre-shadow)

- D1: phase-1 13/13 COMPLETE (218 interests, ZERO schema or semantic
  defects); relation stage strict-failed after 1 endpoint violation +
  1 exhausted repair -> receipted fail-closed (frozen D1 semantics).
- D2: phase-1 13/13 twice; both attempts then receipted fail-closed on
  distinct genuine monolithic-recon provider defects (empty cluster_ids;
  unresolvable target_interest).
- D3: phase-1 13/13 COMPLETE (210 interests, zero defects); grouping
  coverage-retry engaged once; relation stage = 1 call, 154 valid edges,
  32 quarantined optional edges (receipted), 0 repairs, 1 required-link
  failure receipted-and-excluded; decomposed assembly COMPLETED through
  the STRICT FROZEN validator: 328 objects in / 410 explicit
  dispositions / 262 canonical objects.

Frozen success requirements 1-6 verified for D3 => provisional decision
DECOMPOSED_CONTRACT_SUPPORTED, PENDING the shadow gate below.

### Shadow gate implementation

Host script: `scripts/contract_v2_bakeoff.py --shadow [N]` (N defaults
3, clamped >= 1) — runs N clean-root full-coverage decomposed
bootstraps sequentially under ARTIFACT_ROOT/runs/shadow-v2-<i>ofN-<ts>_
<uid>/ (uniqueness by index+timestamp+uuid), printing PER-SHADOW
COMPLETE/FAILED and exiting nonzero unless 3/3 complete. Persistence is
UNREACHABLE by construction: no store primitive exists on this path
(reviewed; all reachable sqlite seams are mode=ro readers).
