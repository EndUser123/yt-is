---
agent: zcode
host: zcode
created: 2026-08-27
status: FROZEN_PRE_RESULTS
delegation: II RECOMMENDATION / REGRET RANKING EVALUATOR
contamination_state: BLIND_BY_CONSTRUCTION — no ranking candidates exist yet, no private holdout created
---

# Recommendation regret evaluator v1 — METRIC PLAN PREREGISTRATION

Frozen before any ranking challenger exists. Built from: the public
recommendation project state
(`project-state-recommendation.md`), the public impression/feedback
contract source (`ef/personal_graph.py`), the ISEM v1 preregistration
and code (for the dual-output lesson and document form), and synthetic
fixtures only. No future ranking outputs were inspected (none exist);
no private regret/usefulness holdout was created — curation belongs to
a later independent curator AFTER this evaluator and the ranking
candidates freeze; Interest Ground Truth v1.1 labels were never used as
recommendation labels; no feedback-event outcomes were tuned on.

This document is normative for `ef/eval_recommendation_regret.py`,
`scripts/eval_recommendation_regret.py`, and
`tests/test_eval_recommendation_regret.py`. Any behavioral change after
this freeze is recorded inline as a post-hoc **AMENDMENT** with its
reason; unmarked drift invalidates the run (the CLI `verify-freeze`
receipt check makes drift mechanically visible).

## 0. Role and boundaries

- This workstream freezes HOW we will tell whether a challenger improves
  recommendations. It is an evaluator ONLY: it does not rank, score,
  weight, train, or optimize any policy. No utility-weight arithmetic
  exists anywhere in the module.
- Architectural target being evaluated (not built here): "Of the things
  the system could surface, what would this user most regret missing?"
  Candidate conceptual utility dimensions exist in the architecture
  discussion only; they are evaluation dimensions here, never weights.
- Post-run constraint: single judging pass per curated packet set;
  exact per-packet outcomes reported regardless of verdict; no tuning
  of anything afterward.

## 1. Future arms

| Arm | Policy family | Status |
|---|---|---|
| A | similarity + recency baseline | absent-unverified |
| B | goal/claim-aware utility ranking | absent-unverified |
| C | information-gain / regret-aware challenger | later-justified only |

Do not assume B wins. Arm C joins by AMENDMENT that adds pair
schedules; it never changes the meaning of already-judged packets.

## 2. Evaluation unit — blinded comparison packet

One packet = two items (one per arm) presented as ITEM_1/ITEM_2 in one
operator-moment context, plus `recent_surfaced_items` (already-seen-
lately ids). The judge answers TWO SEPARATE questions with SEPARATE
frozen prompts:

1. `WOULD_REGRET_MISSING` — "Which item would the operator most regret
   missing?" (PRIMARY falsifier axis)
2. `MORE_USEFUL_NOW` — "Which item is more useful NOW?"

Outcomes are kept distinct everywhere: no metric merges them; separate
prompts prevent cross-contamination inside one judgment.

### Outcome vocabulary (frozen, both questions)

`ITEM_1 | ITEM_2 | TIE | NEITHER`

Frozen readings:
- `ITEM_1`/`ITEM_2`: genuine strict preference on THIS question.
- `TIE`: equivalent quality ON THIS QUESTION. Ties are real outcomes,
  never forced into a preference and never counted for either arm.
- `NEITHER`: neither item merits surfacing at all; rejects both.

Judge inputs per item: `title, claims, why_surfaced, evidence_refs,
related_records`. Explicitly HIDDEN from the judge: arm identity,
ranking scores, ranks, policy names/versions, experiment ids,
propensity, aggregate results, thresholds (enforced structurally:
`build_blinded_packet` → `strip_to_blinded_view`, validated by
`validate_packet` leak detection).

### Judgment dimensions (nine, frozen definitions)

`regret_if_missed(+), relevance_to_active_goal(+), novelty_to_user(+),
consequence(+), actionability(+), evidence_quality(+), redundancy(-),
known_already(-), timing_appropriateness(-)` — exact HIGH/MEDIUM/LOW/
UNKNOWN ratings collected by a THIRD prompt (`FROZEN_JUDGE_PROMPT_
DIMENSIONS`) that never sees or influences either choice outcome.

Known-already/redundancy never collapse into relevance: dimensions are
reported separately forever; the module contains NO aggregation
function over them (test-enforced). The only derived object is the
frozen quadrant classifier (§7), which labels — never scores.

Transfer_value is deliberately NOT in the v1 dimension set (judge load;
addable by amendment without invalidating collected dimensions).

## 3. Blinding scheme (deterministic)

`slot = f(sha256(BLIND_SALT | packet_id | arm_id))` — byte-frozen salt
`rre_v1_slot_blind_salt_2026_08_27`; the lexicographically-lower digest
takes ITEM_1. Stable regardless of caller order; reversible ONLY via
the sealed sidecar stored out-of-band from judge-visible documents.
Listwise packets salt with `packet_id + "|listwise"` and use LIST_1/
LIST_2 tokens.

## 4. Provenance requirements (separate axis)

Mechanical, symmetric across arms, fail-closed:

- `VALID` requires ALL of: non-empty claims; non-empty why_surfaced;
  non-empty evidence refs; every ref resolvable against the supplied
  evidence inventory. Absent inventory ⇒ unverifiable ⇒ NOT VALID
  (absence of checking infrastructure is never silent success).
- Empty `related_records` does NOT invalidate — attribution coverage is
  reported (`n_items_without_record_relation` via dims/diagnostics),
  not gated (requiring it would pre-condemn any baseline lacking goal
  attachment).
- A pair is CONFORMANCE-grade iff BOTH items are VALID. Provisional
  pairs are REPORTED BY COUNT (`provisional_excluded`) but excluded
  from every decided denominator. An unsupported win therefore cannot
  carry a gate verdict — a title-only attractiveness win dies here.

## 5. Pairwise metrics (exact, frozen)

Per question, over normal-kind conformance pairs:
- counts vector `{A_strict_wins, B_strict_wins, ties, neither,
  provisional_excluded, duplicate_across_arms, pairs_total}`;
- `strict_preference_rate(arm) = wins_arm / decided_strict_pairs`
  with the denominator reported beside it (never a naked rate);
- zero-decided denominators report `None` (UNDEFINED), never 0.0.

Tie handling frozen: ties/neither stay OUT of strict denominators AND
out of arm totals. No half-credit convention exists anywhere.

Both-same-item pairs (`DUPLICATE_ACROSS_ARMS`) are detected, reported,
and excluded — preference between identical items is meaningless.

## 6. Listwise / top-k metrics (exact, frozen)

Blinded list packets truncate each arm to k at build time. Judge
returns `{outcome: LIST_1|LIST_2|TIE|NEITHER, regret_marks: {LIST_1:
[item_ids], LIST_2: [item_ids]}}` (zero marks allowed; malformed
payload parses to None and NEVER invents a result).

Mechanical listwise accounting: same counts/rates shape as pairwise;
`regret_marked_items_{challenger,baseline}` exact totals;
`mean_overlap_at_k` over per-list id sets (exact set intersection /
k; None when lists differ in length or are empty). Lists containing
any provenance-invalid item are provisional-excluded from gate counts.

## 7. Quadrant profiles (distinctness checks, not scores)

Frozen precedence `redundancy > known_already > importance+novelty >
novel-low-value > MIXED_UNCLASSIFIED` maps dimension RATINGS to
REDUNDANT_WITH_RECENT_SURFACE / USEFUL_BUT_ALREADY_KNOWN /
IMPORTANT_AND_NOVEL / NOVEL_BUT_LOW_VALUE. The four stylized profiles
(`useful but already known`, `novel but low-value`, `important and
novel`, `redundant with recently surfaced`) can never produce one
merged number because no merged number exists.

## 8. Feedback contract (frozen role map; descriptive only)

Verdict vocabulary source-of-truth: `ef/personal_graph.py VERDICTS`.

| verdict | role | polarity | note |
|---|---|---|---|
| useful | direct_outcome | + | |
| acted_on | direct_outcome | + | strongest behavioral direct outcome |
| not_interested | direct_outcome | − | |
| save | weak_proxy | + | value-signaling attention w/o action |
| investigate | weak_proxy | + | resolution lives in workflow state |
| more_like | weak_proxy | + | direction only |
| less_like | weak_proxy | − | direction only |
| known_already | exclusion | 0 | retained as redundancy evidence |
| wrong_inference | exclusion | 0 | premise fault, not ranking quality |

Unlisted verdicts fail closed to `unknown`. Events annotated
`exclude_from_evaluation` (e.g. live-verification probes) become
exclusions overriding everything else. Clicks/actions are explicitly
NOT equivalent reward: polarity and strength metadata travel with every
role. NOTHING TRAINS. The module exposes no estimator at all — the
offline-reward seam raises `OffPolicyError` by construction.

## 9. Off-policy honesty (evidence classes, frozen)

Every quantified outcome declares its evidence class:
`blinded_curated_judgment | observed_online_feedback |
synthetic_fixture | unknown_propensity_historical_impression`.
Impression-linked historical events classify as UNKNOWN PROPENSITY —
the impressions schema records propensity NULL (no randomized policy
exists); this evaluator may DESCRIBE such counts and may NEVER present
them as an unbiased offline ranking estimate. Zero-propensity-policy
remains exactly that: unknown.

## 10. Small-n handling (ISEM lesson, adopted verbatim in spirit)

Every evaluation returns BOTH orthogonal outputs:

A. **FINITE_SET_OUTCOME** — exact deterministic status over judged
packets per question:
   - `CHALLENGER_PREFERRED` / `BASELINE_PREFERRED` (strict integer
     superiority)
   - `DEAD_HEAT` (equal nonzero strict wins)
   - `DECISION_INERTIA` (decidable pairs exist, zero strict decisions —
     all tie/neither; ties preserved as outcomes, not failures)
   - `NOT_EVALUABLE` (zero decidable pairs)
   The full exact counts vector ALWAYS accompanies the status.

B. **GENERALIZATION_STATUS** — `SUFFICIENT_EVIDENCE` only when BOTH
frozen thresholds hold on that question: decided strict conformance
pairs ≥ 5 AND distinct packets ≥ 5; otherwise `INSUFFICIENT_EVIDENCE`
with both numbers shown. Small n implies neither "we learned nothing"
(finite-set still reports exactly) nor population-level confidence
(insufficiency stays printed next to any finite claim).

Combined verdicts like `CHALLENGER_WINS_FINITE_SET` +
`INSUFFICIENT_EVIDENCE` are first-class results, produced at n<5, and
are the honest terminal state of small pilots.

## 11. Primary falsifier (frozen gate)

Question: `WOULD_REGRET_MISSING` only. `MORE_USEFUL_NOW` is reported
side-by-side and gates nothing (a long-horizon regret win trading off
immediate usefulness is information, not failure).

Verdict ladder:
- `PASSED_PRIMARY_FALSIFIER` — finite CHALLENGER_PREFERRED AND
  generalization SUFFICIENT_EVIDENCE AND ≥1 counter-similarity win
  (below).
- `CHALLENGER_WINS_FINITE_SET` — finite win, generalization pending.
- `SIMILARITY_CONFOUNDED_NOT_PASSING` — finite win where EVERY
  challenger strict win sat on a strictly higher
  `similarity_diagnostic` than its opponent. Semantic similarity alone
  does NOT pass: the diagnostic axis (optional per item, e.g. max
  cosine to active-goal centroid) makes "merely more similar" detectable.
- `BASELINE_PREFERRED` / `DEAD_HEAT` / `DECISION_INERTIA` /
  `NOT_EVALUABLE` — mirrored finite statuses.

Untested axes report honestly: when similarity diagnostics are absent
the verdict carries
`untested_axes=["semantic_similarity_diagnostic"]` — an untested axis
is not a covered axis.

## 12. Curation protocol (later, independent)

A later independent curator — AFTER this evaluator freezes AND ranking
candidates A/B freeze — creates ONE private blinded regret/usefulness
packet set under this frozen evaluator: builds packets via the CLI
(`blind` writes the sidecar sealed out-of-band), judges once
(`judge --transport codex` or an equivalent injected transport bound in
an amendment), unbinds, runs `evaluate`, reports both outputs. Fail-
closed bindings at run time: sidecar reverse-map completeness;
judgment vocabulary membership; single pass, no re-judging.

## Fail-closed bindings (structural, test-enforced)

1. Judgment strings outside the frozen vocabulary raise instead of
   coercing; malformed judge payloads parse to None, never a decision.
2. Blinded-view projection cannot carry machinery fields; hand-crafted
   packet docs containing them fail validation.
3. Unknown feedback verdicts → ROLE_UNKNOWN; annotation exclusion
   overrides all roles.
4. Manifest check (`verify-freeze`) exits non-zero on any artifact hash
   drift; frozen bytes below pin the plan.

## Frozen artifact manifest

Hashes recorded in FREEZE_RECEIPT.json at generation time. Any drift
invalidates the run; post-hoc changes require an AMENDMENT entry here
AND a regenerated receipt chain documenting the basis.
