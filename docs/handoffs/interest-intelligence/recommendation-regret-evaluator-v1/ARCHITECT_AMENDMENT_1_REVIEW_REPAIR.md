---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_453bc40e-9bbd-4d62-b7da-5cbc2c9f655a
amendment: ARCHITECT_AMENDMENT_1_REVIEW_REPAIR
supersedes: RRE v1 freeze (2026-08-27T07:33:05, REJECTED by REVIEW-freeze-20260827.md)
status: AMENDED_CANDIDATE_WAITING_ON_FRESH_REVIEW
---

# ARCHITECT AMENDMENT 1 — REVIEW REPAIR (RRE v1)

Authority: the architect accepted the fresh freeze review's REJECTED
verdict (REVIEW-freeze-20260827.md, integrated at commit
732e2cfa5dbe2848421b48cf5b46d9bf4c68039) and directed an explicit
pre-data amendment implementing every review defect fix. No
Recommendation holdout exists; no real ranking output was inspected;
no private label was used; nothing was tuned. The rejected v1
candidate is preserved verbatim as historical evidence; v1 is not
silently rewritten — this document and the AMENDMENT 1 section of
METRIC_PLAN_PREREGISTRATION.md mark every behavior change.

## Hash chain

| Artifact | sha256 | Role |
|---|---|---|
| `ef/eval_recommendation_regret.py` (v1) | `95369544be76f418e9ae86ecbe0882af27ee051e5f9722b85f17444f85b05e85` | rejected candidate (preserved under `rejected-candidate-v1/ef/`) |
| `scripts/eval_recommendation_regret.py` (v1) | `26f265b6f6ea559138717c1b0813407e7f6872584a233476e819fe0fa7712ce5` | rejected candidate (preserved under `rejected-candidate-v1/scripts/`) |
| `tests/test_eval_recommendation_regret.py` (v1) | `0c56aff6cfcae3cd700d17121937f012a4e8a8f803408af680178a3df79ab992` | rejected candidate (preserved under `rejected-candidate-v1/tests/`) |
| `METRIC_PLAN_PREREGISTRATION.md` (v1) | `3308d67d7deb889fb64d543b808d714cf82d0ef9030cc8fb1d29dc1aa247b255` | rejected plan (preserved as `rejected-candidate-v1/METRIC_PLAN_PREREGISTRATION.v1.md`) |
| `FREEZE_RECEIPT.json` (v1) | — (content preserved) | rejected receipt (preserved as `rejected-candidate-v1/FREEZE_RECEIPT.v1.json`) |
| `REVIEW-freeze-20260827.md` | — | review of record (commit 732e2cfa5dbe2848421b48cf5b46d9bf4c68039, verdict REJECTED) |
| `ef/eval_recommendation_regret.py` (amended) | `259954611bda88525ff6b06b063349b90c72244055b46edee4f17b59897a1e85` | amended candidate (rev 2, after landing-review round 1) |
| `scripts/eval_recommendation_regret.py` (amended) | `66456c213980f10ac9aafde1ad4bf59abd0246182de3a9dc58b6293270fdfbb6` | amended candidate (rev 2) |
| `tests/test_eval_recommendation_regret.py` (amended) | `49879c8c59029fc2703eae83c00b5bee1f5e177cc1bdb0e180112d86e972bc8c` | amended candidate (rev 2) |
| `METRIC_PLAN_PREREGISTRATION.md` (amended) | `c4f863bd9e1a17a426fd9ec05f64e567e0ebb4ac9a5d3a660931f748c8446e22` | amended plan (AMENDMENT 1 section marks superseded sections) |
| `ARCHITECT_AMENDMENT_1_REVIEW_REPAIR.md` (this file) | hashed in the regenerated FREEZE_RECEIPT.json | amendment of record |

Revision history: rev 1 of the amended candidate (module
`9259db8f7da299b4…`, CLI `d2e71c72c8cdb8fe…`, tests
`67e2098c1f296e29…`) was dispatch-reviewed pre-landing
(run-f9f644ffc144) and REJECTED with 2 major + 3 minor findings. All
five were repaired in rev 2:

1. MAJOR — the judge-view ALLOWLIST was not enforced on listwise list
   entries (only pairwise `items`); a hand-crafted listwise view
   carrying an `arm` key validated ok and reached the judge prompt.
   Fixed: `validate_packet` now applies the allowlist to every
   listwise view (regression-tested).
2. MAJOR — `finite_set_outcome` subtracted
   `duplicate_records_deduplicated` from `pairs_total`, which already
   excludes them (the R7 dedup pass runs first): 3 unique + 3 exact
   duplicates reported NOT_EVALUABLE against its own counts
   (B_strict_wins=3). Fixed; regression-tested
   (exact duplicates now leave CHALLENGER_PREFERRED intact with the
   dedup receipt).
3. MINOR — `evaluate()` defaulted `judgments_evidence_class` instead
   of requiring it (the R6 claim held only at the CLI layer). Fixed:
   keyword-only required parameter (regression-tested).
4. MINOR — `make_demo_pair_record` ignored its `sidecar` parameter
   (rebuilt internally and unbound with its own sidecar while the
   docstring claimed binding enforcement). Fixed: the PASSED sidecar
   performs the unblinding and a mismatched sidecar fails closed
   (regression-tested); the rebuild mirrors the caller's
   `recent_surfaced_items` so binding stays consistent.
5. MINOR (pre-existing in v1) — `cmd_judge` raised KeyError on a bare
   packet document that `validate_packet` accepts. Fixed: bare and
   wrapped packet documents both judge (regression-tested).

The four frozen judge prompts are byte-identical to v1 (receipt
`judge_prompts_sha256` values unchanged; verified programmatically).

## R1 — false generalization claim REMOVED

- The v1 `GENERALIZATION_STATUS` / `SUFFICIENT_EVIDENCE` /
  `INSUFFICIENT_EVIDENCE` vocabulary no longer exists anywhere in
  module, CLI, or emitted reports (regression-tested against both the
  report blob and the sources).
- Companion output is now `DIAGNOSTIC_COVERAGE_STATUS`:
  `MINIMUM_DIAGNOSTIC_FLOOR_MET` / `MINIMUM_DIAGNOSTIC_FLOOR_NOT_MET`
  — non-inferential bookkeeping over the judged set (>=5 decided
  strict conformance pairs across >=5 distinct eligible decided
  packets). Threshold constants renamed accordingly.
- `FINITE_SET_OUTCOME` is unchanged: an exact statement about the
  evaluated set.
- No statistical design was invented. Population-level claims require
  a separate preregistered statistical design.

## Primary falsifier semantics refocused (finite set only)

`PASSED_PRIMARY_FALSIFIER` now requires ALL of: finite
CHALLENGER_PREFERRED over the judged set; similarity axis TESTED; >=1
counter-similarity challenger win; diagnostic floor MET. Its scope
field states: finite-set evidence against the similarity+recency
baseline — no population-superiority, generalization, or
production-promotion claim. New ladder state
`CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED` covers the
finite-win-but-untested case.

## R2 — similarity axis fails closed

- Similarity diagnostics absent => axis UNTESTED => the falsifier
  CANNOT pass (regression-tested at n=5 with a 5-0 sweep).
- Counter-similarity evidence remains a required conjunct
  (`SIMILARITY_CONFOUNDED_NOT_PASSING` when every challenger win sat
  on higher similarity).
- `similarity_diagnostic` never enters a judge payload (removed from
  the view; regression-tested).

## R3/R4 — judge view is allowlist-based

- `build_judge_item_view` constructs judge payloads ONLY from
  `JUDGE_ITEM_VIEW_FIELDS` (item_id, title, claims, why_surfaced,
  evidence_refs, related_records). No pass-through projection exists
  (the v1 `strip_to_blinded_view` is gone).
- `validate_packet` recursively scans the ENTIRE packet document for
  machinery keys — exact names (`arm_id`, `policy_name`,
  `policy_version`, `ranking_policy`, `ranking_policy_version`,
  `score`, `raw_ranking_score`, `rank_position`, `experiment_id`,
  `propensity`, `similarity_diagnostic`, `blind_sidecar`,
  `blind_salt`, `blind_slot_map`) plus a substring-variant layer — at
  any nesting depth, wrapper included. An item view containing any
  non-allowlisted key fails validation. A document that inlines a
  sidecar fails validation.
- `cmd_judge` validates the packet BEFORE rendering or invoking any
  judge and exits nonzero on validation failure. No bypass path.

## R5 — sidecar mechanically bound; mechanical listwise unblinding

- Sidecars carry `packet_id`, `blinded_packet_sha256` (canonical
  sorted-key hash of the blinded packet), the arm/slot map, and
  `binding_version` (`rre_v1_sidecar_binding_1`).
- `unbind_result` and the new `unbind_list_result` refuse any sidecar
  whose binding fields do not reproduce — including same-layout
  sidecars of a different packet and tampered packets
  (regression-tested).
- Listwise unblinding is fully mechanical (no manual LIST_n -> arm
  mapping); the listwise sidecar binds the packet's real packet_id.

## R6 — evidence class travels with every result

- `evaluate()` requires an explicit `judgments_evidence_class` from
  the frozen vocabulary (`blinded_curated_judgment` |
  `synthetic_fixture`); anything else fails closed.
- The emitted report carries the evidence class at top level, per
  question, and on the primary falsifier block.
  `EVIDENCE_CLASS_CURATED` is dead code no more.

## R7 — counting integrity

- Exact duplicate packet ids deduplicate and are receipted
  (`duplicate_records_deduplicated`); same id + different content
  raises `PacketBindError` (pairwise AND listwise).
- `n_distinct_decided_packets` counts ONLY eligible decided packets;
  provisional/unprovenanced packets cannot satisfy the diagnostic
  floor.
- Listwise regret marks are validated, deduplicated sets of candidate
  ids at unbind time; foreign ids fail closed; totals cannot be
  inflated by repeats.
- `finite_set_outcome` excludes deduplicated duplicates from its
  decidable denominator.

## Parser / CLI hardening

- `parse_outcome`: contradictory in-vocabulary outcome tokens (decoy
  after the final answer) yield NO decision; identical repeats are
  safe.
- `cmd_judge`: nonzero exit on unparsed judge garbage (exit 6);
  validation failure exits 5; missing `--transport` still exits 3.
- `cmd_validate`: accepts `--evidence-inventory`; reports
  `inventory_supplied` and never claims provenance-VALID without the
  evidence to prove it (states fail closed to UNRESOLVED).
- `cmd_evaluate`: `--evidence-class` is required; conflicting-count
  refusals exit nonzero.

## Preserved unchanged (reviewer-confirmed correct)

- Personal regret grounding: the LLM judge remains an evaluation
  instrument with frozen prompts, never operator ground truth; future
  regret ground truth still requires operator-confirmed judgment,
  curated operator evidence, or an explicitly-limited observable
  proxy.
- Off-policy contract: unknown propensity stays descriptive-only;
  annotation exclusions override; feedback roles explicit; no reward
  estimator; `OffPolicyError` seam fails closed.
- Orthogonality of WOULD_REGRET_MISSING / MORE_USEFUL_NOW; outcome
  vocabulary with TIE/NEITHER and no forced choice; nine judgment
  dimensions with no aggregation; quadrant profiles; frozen prompts.

## Tests

73 offline tests (v1 suite was 39), including the 16 mandated
regression tests mapping 1:1 to the review defects plus 5
landing-review round-1 repair regressions (listwise allowlist, dedup
double-subtraction, required evidence class, sidecar-honoring demo
record, bare-packet judge path): n=5 yields no
generalization claim; finite-set preference reportable; similarity
absent => UNTESTED and cannot pass; similarity diagnostics never in
judge payload; nested machinery cannot leak; cmd_judge cannot bypass
validation; wrong sidecar rejected; same-layout wrong sidecar
rejected; listwise unbinding mechanical; duplicate packets cannot pad
counts; provisional packets cannot satisfy the floor; repeated marks
cannot double count; conflicting duplicate packet_id fails;
evidence_class present in report; contradictory/decoy parser output
fails; garbage judge output exits nonzero.

## Known minor (recorded, deliberately not repaired)

No scope broadening: `unbind_list_result` emits
packet_id/arms/preferred_list/regret_marks_by_arm, while
`aggregate_list_results` documents a fuller record shape (also
challenger_arm, baseline_arm, list_ids_by_arm, prov_valid_all). A
caller feeding the bare unbind output into the aggregator gets
fail-closed provisional exclusion, not silent mislabeling. The
fail-closed direction is correct; a shape-unifying amendment may come
later.

## Stop condition

Implemented, tested (73 passed), frozen (receipt regenerated), and
published on the implementer lane branch. Per the architect decision:
STOP — a fresh top-level reviewer reviews the amended candidate; no
self-review was performed.
