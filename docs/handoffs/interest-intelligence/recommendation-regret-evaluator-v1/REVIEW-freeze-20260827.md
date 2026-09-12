---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_453bc40e-9bbd-4d62-b7da-5cbc2c9f655a
session_name: II Recommendation / Regret Ranking Evaluator Freeze Reviewer
reviewee: RRE v1 freeze (author session sess_3396ff71-5f79-48fb-973a-9672dd3ca3c8; not reused)
status: FREEZE_REVIEW_VERDICT_REJECTED
---

# RRE v1 freeze review — independent reviewer report

## VERDICT

**REJECTED**

Grounds R1 (generalization semantics) and R2 (absent-similarity PASS) are
each independently disqualifying under the review packet; R3–R7 compound.
No findings were repaired. Frozen-candidate hashes still verify after
review (review added no code to the candidate).

## Verification receipts

- **Candidate hashes**: all four `frozen_artifacts` sha256-verified
  against FREEZE_RECEIPT.json (full hashes, computed fresh):
  `ef/eval_recommendation_regret.py` 95369544…, `scripts/eval_
  recommendation_regret.py` 26f265b6…, `tests/test_eval_recommendation_
  regret.py` 0c56aff6…, `METRIC_PLAN_PREREGISTRATION.md` 3308d67d… —
  all MATCH.
- **Judge prompt hashes**: runtime `FROZEN_PROMPTS_SHA256` equals the
  receipt `judge_prompts_sha256` (4/4); judge model config (gpt-5.6-luna,
  low, 300s, 2 attempts) and the 5/5 thresholds equal the receipt.
- **Tests**: `python -m pytest tests/test_eval_recommendation_regret.py
  -q` → **39 passed** (matches the author report).
- **Adversarial probes**: 26 scratch probes over two scratch scripts
  (`P:/tmp/rre_freeze_review_probe.py`, `P:/tmp/rre_freeze_review_
  probe2.py`; scratch-only per the review packet, transcripts embedded
  below). Frozen candidate unmodified.
- **Independent second lane**: a GLM-5.3 (max reasoning) adversarial
  code-review agent reviewed the same surface with no conclusions shared
  from the parent. Its findings were adopted only after parent
  re-verification; the two new claims it contributed (similarity-view
  leak R4, sidecar binding R5) were re-proven mechanically in probe set
  2. It additionally verified `FEEDBACK_ROLES` against its declared
  source of truth `ef/personal_graph.py:515-517` (9/9 verdicts) and
  independently reproduced the receipt hash checks.

## Mandated axis results

| Axis | Result |
|---|---|
| Core decision contract | CORRECT — questions separate everywhere, no merging metric; `ITEM_1\|ITEM_2\|TIE\|NEITHER` preserved; no forced choice; TIE/NEITHER never enter strict denominators (probe 5) |
| Blinding | DEFECT (R3, R4) — sanctioned build path sound; validation layer incomplete and unenforced on the judge path; similarity diagnostic judge-visible |
| Provenance | CORRECT — title-only cannot win (probe 3); missing/empty inventory fails closed (probe 4); provisional pairs excluded from every decided denominator (probes 3, 11) |
| Finite-set outcome | CORRECT — exact accounting, ties/neither/duplicates/provisional all visible (probes 2, 3, 5) |
| Generalization semantics | DEFECT (R1) — claims inferential/generalization sufficiency at n=5 |
| Personal regret grounding | CORRECT — no conflation: the LLM judge is the frozen, labeled measurement instrument; operator feedback enters only as descriptive unknown-propensity counts; nothing presents judge judgment as operator-confirmed labels. Labelling gap tracked under R6 |
| Similarity falsifier | DEFECT (R2) — confound and counter-similarity logic correct when tested (probes 10a, 10b); absent-diagnostics path passes anyway (probe 10c) |
| Feedback/off-policy | CORRECT — role map fail-closed, annotation exclusion overrides, propensity UNKNOWN descriptive-only, no estimator, `OffPolicyError` seam (probes 6, 7) |
| Pairwise/listwise | DEFECT (R5, R7) — silent sidecar-mismatch flip; listwise unbind unmechanized; marks and packet counts lack integrity checks |

## Grounds for REJECTION

### R1 — Generalization status claims more than n=5 warrants (mandated REJECT ground)

Determination: **B — the evaluator claims inferential/generalization
sufficiency**, not a mere diagnostic floor.

- The axis is named `GENERALIZATION_STATUS` with value
  `SUFFICIENT_EVIDENCE` (ef/eval_recommendation_regret.py:892-911);
  receipt keys are `min_decided_pairs_for_generalization` /
  `min_distinct_packets_for_generalization`.
- Tests assert `SUFFICIENT_EVIDENCE` at n=5 as the desired behavior
  (tests/test_eval_recommendation_regret.py:316-334).
- `PASSED_PRIMARY_FALSIFIER` — the evaluator's strongest claim — becomes
  available exactly at n≥5 (ef:944-946). A 3-2 strict split
  (sign-test p≈1.0) yields `SUFFICIENT_EVIDENCE` +
  `PASSED_PRIMARY_FALSIFIER`.
- No confidence interval, hypothesis test, power analysis, or statistical
  model exists anywhere in module or plan. The prereg constrains only
  what `INSUFFICIENT_EVIDENCE` means ("stays printed next to any finite
  claim", METRIC_PLAN §10.B) and never defines what SUFFICIENT licenses.

Per the review packet: five observations do not justify generalization
language; preferred semantics keep `FINITE_SET_OUTCOME` exact and demote
the companion status to a diagnostic-floor label (or
`INSUFFICIENT_GENERALIZATION_EVIDENCE`) unless a real statistical design
is added. NOT repaired.

### R2 — `PASSED_PRIMARY_FALSIFIER` reachable with the similarity axis entirely untested (blocker; unmarked plan drift)

`primary_falsifier_verdict` gates the confound check behind
`if agg_question["similarity_axis_tested"]:` (ef:929); with no
diagnostics present, CHALLENGER_PREFERRED + SUFFICIENT_EVIDENCE maps to
`PASSED_PRIMARY_FALSIFIER` carrying only an `untested_axes` flag
(ef:944-952). Probe 10c receipt: 5/5 challenger wins, zero similarity
diagnostics anywhere → `PASSED_PRIMARY_FALSIFIER`.

The frozen ladder is conjunctive — PASSED requires "≥1 counter-similarity
win" (METRIC_PLAN §11, lines 232-234), which is unsatisfiable without
diagnostics — so the code path contradicts the normative plan, and the
plan itself says unmarked drift invalidates the run. It also violates the
review packet rule: absent similarity diagnostics must report
UNKNOWN/UNTESTED, **not pass**. The test suite never exercises the
absent-similarity + sufficient-evidence combination (the only PASSED
test supplies diagnostics, tests:337-342; the untested-axis test runs at
n=2, tests:394-397).

### R3 — Leak validation is incomplete and not enforced where it matters

- `validate_packet`'s denylist covers 5 tokens; `strip_to_blinded_view`'s
  forbidden set has 9 — `arm_id`, `policy_name`, `policy_version`,
  `ranking_policy_version` are never scanned (ef:238-240 vs ef:628-629).
  Probe receipts: hand-crafted packets carrying `arm_id`, `policy_name`,
  `policy_version`, `ranking_policy_version`, `raw_ranking_score`, or
  nested `arm`/`raw_score` keys all validate `ok: true`.
- The scan sees only the `blinded_packet` sub-document (ef:608), so an
  inlined sidecar escapes — the exact co-mingling `cmd_blind` refuses.
- The only path that talks to a real judge — `cmd_judge` — renders the
  prompt **without calling `validate_packet` at all**
  (scripts:82-96); validation is an opt-in subcommand.

Prereg fail-closed binding #2 ("hand-crafted packet docs containing them
fail validation") is only partially honored. The sanctioned build path
itself is sound (allowlist projection; determinism, no arm tokens, and
sidecar-completeness fail-closure probe-verified).

### R4 — `similarity_diagnostic` is judge-visible: an arm-correlated side channel

`strip_to_blinded_view` passes `similarity_diagnostic` through to the
judge-facing view (ef:244). It is outside the frozen judge-input list
(title, claims, why_surfaced, evidence_refs, related_records —
METRIC_PLAN §2) and is ranking-adjacent for arm A, the
similarity+recency baseline. Probe set 2 receipt: a sanctioned build with
sims 0.9/0.1 emits both values into the blinded packet and validates ok.
The judge can infer arm with above-chance accuracy and anchor on the
number — contaminating both the preference outcomes and the very
covariate the falsifier measures.

### R5 — Unblinding is not bound: a wrong sidecar silently flips arms

`unbind_result` never compares `blind_sidecar["packet_id"]` to
`blinded_packet["packet_id"]` (ef:702-735) although `assign_slots`
stores it (ef:227). Probe set 2 receipt: unbinding packet pk_m0 with
pk_m4's sidecar succeeds and attributes BOTH slot outcomes to the wrong
arm, no error. No CLI `unbind` subcommand exists; listwise unblinding is
unmechanized (curator hand-maps LIST_x→arm; the prereg §12 fail-closed
bindings — reverse-map completeness, vocabulary membership — are
implemented pairwise-only).

### R6 — The report emits no evidence class; `EVIDENCE_CLASS_CURATED` is dead code

`evaluate()` (ef:1013-1051) carries no judgment evidence class;
`EVIDENCE_CLASS_CURATED = "blinded_curated_judgment"` (ef:160) is
referenced nowhere (rg receipt). The prereg requires "Every quantified
outcome declares its evidence class" (§9). Feedback counts and the demo
do carry classes, proving the mechanism existed and was not wired into
the headline verdict — the strongest emitted claim travels without its
epistemic basis.

### R7 — Counting integrity gaps (single-pass rule is procedural, not mechanical)

- No packet_id dedup anywhere: duplicate records inflate decided counts
  (probe 13a); 5 packets judged twice satisfy the 5-distinct threshold.
- Provisional packets count toward the distinct-packet threshold
  (ef:898): probe 13b receipt — `SUFFICIENT_EVIDENCE` reached with
  decided evidence spanning only 4 distinct packets.
- Regret marks are counted raw (ef:509-512): repeated or foreign item ids
  inflate the "exact totals" (probe 14: 2 marks counted for 1 id).

## Minor notes (not grounds alone)

- Dead code: `challenger_all_sim_supported` computed-never-read
  (ef:769, 810); `_JudgeRecorder` defined-never-used while the section
  comment promises ISEM-style caching — judge runs leave no reproducible
  artifact trail (ef:638-651); `SIMILARITY_CONFOUNDED_DELTA_MAX`
  tombstone (ef:127); `PENALTY_DIMENSIONS` unused (ef:115).
- `parse_outcome` returns the LAST in-vocabulary object in the raw text
  (ef:654-667): a decoy JSON emitted after the final answer silently
  wins.
- `cmd_judge` exits 0 on any non-None transport stdout without parsing
  it (scripts:98-103).
- `cmd_validate` can never mark a packet provenance-VALID (no inventory
  argument, scripts:42-46), pushing curators into ad-hoc Python —
  compounding R3/R5 exposure.
- The codex transport runs read-only over `P:/` and the sidecar is
  "sealed" only by storage convention; the judge model can in principle
  read it (scripts:121-133).
- The judge-visible packet document embeds both questions (ef:283-291);
  harmless on the rendered-prompt path, hazardous for any future
  transport that feeds the raw doc.
- `cmd_demo` overwrites `similarity_diagnostic` with values swapped
  relative to the fixtures (scripts:177) — cosmetic.

## Probe transcripts

### Probe set 1 (P:/tmp/rre_freeze_review_probe.py)

```
== 0. freeze receipt cross-checks ==
[OK] prompt_hashes_match_receipt  (4/4 equal)
[OK] model_config_matches_receipt
[OK] thresholds_match_receipt
== 1. blinding determinism + safe unblinding ==
[OK] slot_assignment_caller_order_insensitive
[OK] blinded_packet_carries_no_arm_tokens
[OK] unbind_maps_slot_to_sidecar_arm
[OK] incomplete_sidecar_fails_closed  (PacketBindError)
== 2. same candidate in A and B / duplicate candidate ==
[OK] duplicate_across_arms_detected_and_excluded  (NOT_EVALUABLE, n_decided 0)
== 3. title-only candidate cannot carry a gate ==
[OK] title_only_provenance_state  (MISSING_CLAIMS)
[OK] title_only_win_excluded_from_gate  (excluded 1, verdict NOT_EVALUABLE)
== 4. missing evidence inventory fails closed ==
[OK] no_inventory_not_valid  (UNRESOLVED_EVIDENCE_REF)
[OK] empty_inventory_not_valid  (UNRESOLVED_EVIDENCE_REF)
== 5. TIE / NEITHER stay out of strict denominators ==
[OK] tie_neither_never_counted_for_arms  (rates None/None, DECISION_INERTIA)
== 6. feedback: annotation exclusion overrides; unknown fails closed ==
[OK] annotation_exclusion_overrides_strongest_verdict
[OK] unknown_verdict_fails_closed
== 7. unknown propensity stays descriptive; no reward path ==
[OK] impression_linked_event_is_unknown_propensity
[OK] summary_declares_unknown_propensity_descriptive_only
[OK] offline_reward_estimate_refused  (OffPolicyError)
== 8. list permutation cannot change accounting ==
[OK] list_permutation_invariant
== 9. nested metadata leakage probes (hand-crafted packets) ==
[OK] validate_blocks_score / rank_position / ranking_policy /
     experiment_id / propensity / nested score
[FAIL->DEFECT R3] validate_blocks_arm_id           (ok: True)
[FAIL->DEFECT R3] validate_blocks_policy_name      (ok: True)
[FAIL->DEFECT R3] validate_blocks_policy_version   (ok: True)
[FAIL->DEFECT R3] validate_blocks_ranking_policy_version (ok: True)
[FAIL->DEFECT R3] validate_blocks_raw_ranking_score (ok: True)
[FAIL->DEFECT R3] validate_blocks_nested arm        (ok: True)
[FAIL->DEFECT R3] validate_blocks_nested raw_score  (ok: True)
== 10. similarity falsifier: three mandated scenarios ==
[OK] all_sim_supported_challenger_wins_do_not_pass  (SIMILARITY_CONFOUNDED_NOT_PASSING)
[OK] counter_similarity_win_counted  (1)
[OK] counter_sim_win_defeats_confounding  (PASSED not confounded)
[FAIL->DEFECT R2] absent_similarity_diagnostics_behaviour
      (PASSED_PRIMARY_FALSIFIER with untested_axes flag)
== 11. unresolvable evidence refs ==
[OK] unresolvable_ref_state / unresolvable_pair_provisional_excluded
== 12. judge transport failure never invents a decision ==
[OK] parse_outcome_none_inputs  ([None, None, None, None])
[OK] none_judgment_fails_closed  (PacketBindError)
== 13. distinct-packet threshold integrity ==
[OK] duplicate_packet_record_inflates_decided_count  (INSUFFICIENT held)
[OK->DEFECT R7] provisional_packet_counts_toward_distinct_threshold
      (n_decided 5, n_distinct 5, SUFFICIENT_EVIDENCE with decided
       evidence from only 4 distinct packets)
== 14. listwise regret_marks duplicate id double-count ==
[OK->DEFECT R7] duplicate_mark_id_counts_twice  (2 marks for 1 id)
== 15. verdict evidence-class labelling in report ==
[FAIL->DEFECT R6] report_carries_judgment_evidence_class  (absent)

PROBE SUMMARY: 35 as-expected, 9 deviations-from-reviewer-expectation
```

### Probe set 2 (P:/tmp/rre_freeze_review_probe2.py)

```
== A. similarity_diagnostic reaches the judge-visible view ==
key present in blinded packet: True
ITEM_1 sim = 0.9
ITEM_2 sim = 0.1
validate_packet ok on sim-bearing packet: True
== B. sidecar never bound to its packet ==
p1 reverse_map: {'A': 'ITEM_1', 'B': 'ITEM_2'} (packet_id pk_m0)
p2 reverse_map: {'B': 'ITEM_1', 'A': 'ITEM_2'} (packet_id pk_m4)
ITEM_1: correct sidecar -> A | wrong sidecar -> B | error raised: NO
ITEM_2: correct sidecar -> B | wrong sidecar -> A | error raised: NO
arm-attributions flipped by wrong sidecar: 2 of 2; unbind_result raised: never
```

## Compliance

- Private Recommendation labels: NOT accessed. Future ranking outputs:
  NOT inspected (none exist). Interest GT v1.1: NOT used as
  recommendation labels. Private evaluator outcomes: none accessed.
- No live ranking experiment, no live judging, no real judge transport
  invoked; synthetic fixtures only.
- No repairs: the frozen candidate was not modified by this review;
  post-review `verify-freeze`-equivalent hash checks still reproduce.

## VERDICT

**REJECTED**
