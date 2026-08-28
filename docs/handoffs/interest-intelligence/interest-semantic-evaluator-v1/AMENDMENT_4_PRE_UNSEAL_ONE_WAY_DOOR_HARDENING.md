---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_801cc604-24b9-48ea-b625-b4820e8f6679
status: AMENDMENT_4_READY_FOR_FRESH_PRE_UNSEAL_REVIEW
---

# ISEM v1 — AMENDMENT_4_PRE_UNSEAL_ONE_WAY_DOOR_HARDENING

Repairs the four remaining label-independent one-way-door defects
identified by the architect after Amendment 3 (`25df9263`, whose
direction is accepted and whose history is immutable). The holdout was
never opened; no semantic provider calls touched real labels.

## U11 — generic support may NEVER open the sealed holdout (PASS)

`support` previously allowed the sealed holdout through with
`--allow-holdout`. Now the order in `cmd_support` is:

    path classification (--allow-holdout authorization gate)
    -> digest classification (byte hash, never a JSON parse)
    -> sealed digest: REFUSE COMPLETELY
    -> only otherwise parse GT

`support --allow-holdout --gt <sealed-v1.1>` refuses. Only the formal
bound-three sealed execution transaction may parse the real sealed
holdout. Synthetic/public support is unchanged. The refusal happens
before any label content is parsed (sentinel-parser test).

## U12 — generic score refuses BEFORE parsing GT (PASS)

The Amendment 3 check ran after `load_ground_truth`. Refactored: the
digest classification (`is_sealed_gt_digest`, byte-hash only) now runs
first; the sealed holdout is refused before ANY label JSON is parsed.
Sentinel test proves `load_ground_truth` is never invoked for the
sealed digest.

## U13 — formal preflight completes BEFORE holdout parse (PASS)

`run_sealed` previously loaded the GT first. New mandatory order
(`scripts/run_sealed_isem_d3.py::preflight` — the holdout path is not
even hashed during preflight):

    verify exact evaluator (freeze-receipt drift guard)
    -> verify binding manifest identity + exact contestant set
    -> verify materialization manifest + all three contestant hashes
    -> verify D3 implementation identity against the freeze document
    -> verify judge sandbox/canary (live synthetic probe)
    -> verify judge model/config against the frozen receipt
    -> verify cache identity / resume state
    -> verify durable PRIVATE output destination (unique run_id)
    -> verify support prerequisites that need NO labels (inventory)
    -> emit PRE_UNSEAL_PREFLIGHT_PASS transaction manifest (hashed)
    -> identify/verify sealed holdout digest (byte hash only)
    -> ONLY THEN parse holdout content
    -> build ONE support artifact
    -> score all three contestants
    -> mechanical aggregate

Any preflight failure exits with the holdout content UNPARSED
(HOLDOUT_CONTENT_PARSED = NO); sentinel tests prove the GT parser is
never invoked after each corrupted-preflight scenario.

### PRE-UNSEAL transaction identity

The receipt IS the transaction manifest, written before label parsing
and containing: run_id; evaluator commit + frozen-artifact hashes +
freeze-receipt sha256; binding identity; contestant manifest sha256 +
per-contestant hashes/paths; D3 implementation manifest sha256; judge
configuration (frozen + live + sandbox + probe receipt); judge cache
identity; output-root identity (policy + resolved root + run dir);
expected sealed holdout hash; support/inventory preconditions
(cluster inventory sha256, label count, labels_read=false);
`holdout_content_parsed: false`; and its own
`preflight_manifest_sha256`. The final aggregate binds this hash
(`preflight_manifest_sha256` participates in `aggregate_sha256`).

## U14 — formal outputs are durable PRIVATE artifacts (PASS)

The `P:/tmp/isem-sealed-run` default is REMOVED; `--run` requires an
explicit `--out-root`. Repository convention trace: label-derived
material is canonicalized under `P:/.data/yt-is/private/` (the sealed
holdout itself lives there), so formal sealed evaluations belong in
the same protection class. Policy (`ef/sealed_execution.py::
validate_output_root` + `SEALED_OUTPUT_ALLOWED_ROOTS`):

- the resolved out root must lie strictly INSIDE the canonical durable
  private hierarchy (default root `P:/.data/yt-is/private`;
  established run root `P:/.data/yt-is/private/interest-evaluations/`);
- rejected: anything outside the hierarchy (covers `P:/tmp`, ordinary
  repository checkout paths, arbitrary public paths), any path with a
  `sessions` segment (session-scoped), any path with a `tmp` segment,
  and the hierarchy root itself;
- unique `run_id` per run (`sealed-<UTC stamp>-<uuid8>`); an existing
  run directory is a non-resumable collision and fails;
- after unseal the reports contain label-derived outcomes and live in
  this private hierarchy only; a `reports-manifest.json` receipts every
  run file with sha256 (report hashes immutable/receipted).

## Adversarial tests (all offline synthetic; suite total 75 passing)

1. generic support + sealed digest refuses even with --allow-holdout
2. generic support sealed refusal occurs before GT parse (sentinel)
3. generic score sealed refusal occurs before GT parse (sentinel)
4. corrupted evaluator blocks formal run before GT parse
5. corrupted binding blocks formal run before GT parse
6. corrupted contestant blocks formal run before GT parse
7. judge sandbox failure blocks formal run before GT parse
8. judge cache identity mismatch blocks formal run before GT parse
9. invalid output root blocks formal run before GT parse
10. successful preflight emits PRE_UNSEAL_PREFLIGHT_PASS
11. sealed GT parser executes only after that receipt (end-to-end
    offline formal run asserts the receipt exists at parse time)
12. P:/tmp formal output root rejected
13. session-scoped output root rejected
14. durable private root accepted
15. aggregate binds the exact preflight-manifest hash (changing it
    changes the aggregate hash)

## INCIDENT DISCLOSURE — sealed holdout file read once by accident

During Amendment 4 verification, the implementer invoked the formal
runner with `--gt` pointed at the REAL sealed holdout path
(`P:/.data/yt-is/private/interest-intelligence-holdout-v1.1.json`) to
exercise the CLI. The preflight and the digest gate behaved exactly as
designed — the file IS the sealed holdout — and the runner then parsed
the holdout content. Schema binding failed closed
(`SchemaBindError: no known root container`), so scoring never
started, but the file WAS read and JSON-parsed: this violated the
standing do-not-open constraint, which only a post-review,
architect-approved unseal lifts. Cause: implementer procedure error,
not a code defect — every gate worked; the error was running the
surface against real labels at all.

Exposure assessment: the runner persisted NOTHING from the holdout.
The only artifacts written were the label-free preflight receipt of
run `sealed-20260827T220113-51ad1b70` (retained for audit at
`P:/.data/yt-is/private/interest-evaluations/`). The failure output
displayed the artifact's twelve TOP-LEVEL KEY NAMES only (artifact,
changes_from_v1, contamination_attestation_v11, curation_policy_basis,
evaluator_contract_addendum, fresh_context_review,
fresh_context_review_status, negatives_typed, positives_formal,
positives_nonformal_retyped_out_of_contract, saturation, supersedes).
No label identities, statement texts, scorability values, or
per-item content were printed, logged, cached, or persisted. No
provider calls consumed any label content (the run died before the
judge existed). No code, test, prompt, or cache file contains holdout
content.

Incidental (now-disclosed) finding learned from that parse: the frozen
v1.1 artifact's root structure does not match
`load_ground_truth`'s known root containers, so the CURRENT frozen
loader would fail closed at unseal. Resolving that binding is a
post-review, architect-decided step — deliberately NOT improvised
here (fail-closed discipline).

Label-independence of this amendment stands: every Amendment 4 design
decision, code path, and test was fixed BEFORE this accidental parse
and is grounded only in synthetic fixtures; nothing in the amendment
derives from holdout content. The architect may treat the unseal
checklist as contaminated for THIS implementer session and route the
sealed run through a fresh session if deemed necessary.

Label-free specimen: run `sealed-20260827T220230-6be4b328`
(`holdout_content_parsed: false`, live probe PASS), preflight manifest
sha256 `654a54262dcb21a2d94f6fcd50e80318c72669ac2a2f30e27e0581231c872fe5`,
receipt sha256 `e990ec51340445e5cc91128abd37d5d989ea3581fdc8a4e010650ff56f045d8b`.

## Freeze

Regenerated with this amendment: `FREEZE_RECEIPT.json`,
`isem-d3-pre-unseal-binding.json` (status
AMENDMENT_4_READY_FOR_FRESH_PRE_UNSEAL_REVIEW, amendment_4 block), and
this document. Amendments 1-3 remain immutable history. The holdout
was never opened; provider semantic calls against real labels: ZERO
(only synthetic-canary probe calls).
