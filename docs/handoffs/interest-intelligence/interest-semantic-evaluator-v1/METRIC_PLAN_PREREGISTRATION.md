---
agent: zcode
host: zcode
created: 2026-08-26
status: FROZEN_PRE_RESULTS
delegation: INTEREST INTELLIGENCE SEMANTIC EVALUATOR
contamination_state: BLIND_TO_HOLDOUT_AT_FREEZE_TIME
---

# Interest semantic evaluator v1 — METRIC PLAN PREREGISTRATION

Frozen before any contact with the sealed private holdout
`.data/yt-is/private/interest-intelligence-holdout-v1.1.json`
(public sha256 echo `1c788508…5aa48f`). Built from: public v1/v1.1
policy + aggregate receipts (`interest-ground-truth-v1{,.1}/`), the
public inference contract (`ef/inference_contract.py`), and synthetic
fixtures only. No private label identities were read at design time;
no inference outputs over the live corpus were inspected either.

This document is normative for the code in `ef/eval_interest_semantic.py`
and `scripts/eval_interest_holdout.py`. Any behavioral change after this
freeze is recorded inline as a post-hoc **AMENDMENT** with its reason;
unmarked drift invalidates the run (the score-time manifest check makes
undetected drift mechanically impossible).

## 0. Role and boundaries

- This workstream scores inference output against the Interest ground
  truth. It does NOT build, tune, or diagnose the inference
  implementation (Contract Reliability owns that) and never proposes
  inference changes from scoring evidence.
- Post-run constraint: single formal scoring pass per bound inference
  artifact; exact per-item outcomes reported regardless of verdict;
  no tuning afterward.

## 1. Semantic matching policy

Per target/candidate pair, tried strictly in order, recorded verbatim:

1. `exact` — normalized-equality of surface text vs canonical name.
   Normalization = NFKC → casefold → whitespace collapse (frozen).
2. `alias` — normalized needle containment (needles ≥4 chars from
   canonical name + aliases) of surface text+context hay, OR
   significant-token-set subset (tokens ≥5 chars, stoplist frozen)
   when the target has ≥2 such tokens.
3. `semantic_judge` — blinded LLM judge; accepts only if it returns
   `match:true`.
4. `no_match`.

Judge contract: ONE candidate object vs ONE reference object per call.
The judge never sees target status (formal/disputed), arm identity,
aggregate outcomes, thresholds, corpus metadata beyond the candidate's
own context string, or scorability information. Prompts are byte-frozen
constants (`FROZEN_JUDGE_PROMPT_POSITIVE`,
`FROZEN_JUDGE_PROMPT_NEGATIVE_INTEREST`; sha256s pinned in
FREEZE_RECEIPT.json). Model/config frozen:
`codex exec -m gpt-5.6-luna`, reasoning effort `low`, timeout 300s,
max 2 attempts; judge transport failures retry once, then record
`judge_error` on the pair — a judge_error never invents a match. Pair
cache keyed by prompt+ids sha256 so partially completed judging can be
resumed without changing decisions already made by the same prompt.

Assignment rule (frozen deterministic greedy): targets are processed in
ground-truth file order; each target takes the best available candidate
by path rank (`exact` > `alias` > judge-tier candidates by payload
order), consuming the matched candidate. With tiny N, determinism beats
global optimality; no tie-breaking beyond listed order exists.

## 2. Scorability policy

Ground-truth validity and corpus scorability are independent, and
scorability NEVER consults inference output. Inputs: the label's own
tri-state field plus a mechanical evidence-cluster probe (deterministic
needle search over public cluster inventory text).

Precedence (frozen):

- `corpus_scorable` field → `SCORABLE` (probe misses do not override).
- `corpus_unscorable` field → `UNSCORABLE_MISSING_EVIDENCE`
  (probe hits logged as curiosity, excluded from denominators all the
  same).
- `unknown`/absent field → probe decides: support found → `SCORABLE`;
  none → `SCORABILITY_UNKNOWN`.

A valid operator Interest with no corpus evidence is NOT an inference
miss: `UNSCORABLE_MISSING_EVIDENCE` items are excluded from recall
denominators and reported with their exclusion reason.
`SCORABILITY_UNKNOWN` likewise excluded (we cannot distinguish missing
evidence from probe failure). Required states `SCORABLE`,
`UNSCORABLE_MISSING_EVIDENCE`, `SCORABILITY_UNKNOWN` are exactly the
module constants.

## 3. Per-type denominators (never combined)

Five separated tracks; there is deliberately NO combined/micro/macro
recall anywhere in the code:

| Track | Targets | Candidate surfaces |
|---|---|---|
| Interest | `Interest` positives | interests with `observed_vs_inferred ∈ {observed, inferred}` |
| Goal | `Goal` positives | distinct non-null `goal` strings across those interests |
| InformationNeed | `InformationNeed` positives | distinct non-null `information_need` strings |
| Question | `Question` positives | `questions[].text`, normalized-deduped |
| InterestNegative | `InterestNegative` targets | same surfaces as Interest track |

Recall denominator per positive track = scorable positives of that
type. Adjacent-inferred objects are excluded from core denominators
(separate diagnostic count); regret candidates never enter any track.

## 4. Precision/recall definitions

- `recall_gross(T)` = matches / scorable positives (a match counts even
  if provenance-invalid).
- `recall_provenance_ok(T)` = matches whose provenance state ∈ {valid} /
  scorable positives. **Gate-authoritative metric.**
- Interest false-positive rate comes ONLY from the InterestNegative
  track: `fp_hits = # interest-core surfaces consumed by an
  InterestNegative match`; denominator for reporting =
  n_interest_core surfaces.
- Zero-denominator tracks report `None` (recorded UNDEFINED), never 0.0
  masquerading as performance.
- A matched hit with unsupported provenance is surfaced as an
  `unsupported_matched_hit` and does NOT silently count as success.

## 5. Negative matching policy

Only `InterestNegative` scores Interest false positives. The ladder and
the blinded judge apply unchanged, but the negative judge prompt asks
whether the candidate ASSERTS the reference subject matter as a pursued
interest (per policy: rejecting a source/mechanism/framing is not
disinterest in subject matter; dual-use statements score as constraints,
not topical interest either way).

`GoalNegative`, `InformationNeedNegative`, `SourceNegative`,
`ImplementationConstraintNegative`, `ToolOrMechanismPreference`,
`StyleOrProcessPreference`, `AmbiguousNegative` never fold into any
denominator here; they are listed unmachinable in-track. Retyped classes
(`WorkPreference`, `ImplementationConstraint`, `Observation`) bind but
open no track at all.

## 6. Unknown-topic handling

Surfaces not matching any GT item are reported per track as
`extra_surfaces_unmatched` and neither rewarded nor penalized beyond
Interest-negative scrutiny. Non-GT hypotheses cannot inflate recall.

## 7. Provenance requirements (separate axis)

Evaluated independently of label agreement. A scored surface needs
non-empty `cluster_ids` ⊆ eligible cluster inventory; questions inherit
their parent interest's refs and fail closed to
`missing_parent_interest` when unresolvable. States: valid /
invalid_refs / missing_refs / missing_parent_interest. Every report
carries both recalls; promotion-grade claims cite
`recall_provenance_ok` only.

## 8. Stability / perturbation plan (frozen pre-results)

Mechanically reproducible, label-blind schemes over the cluster
inventory consumed by the full pipeline:

- `S1_RANDOM_DROP_5PCT` — seed 1337, min 8 clusters removed.
- `S2_TOP_BREADTH_DROP_10` — ten highest channel-breadth clusters.
- `S3_REPS_TRIM` — representative documents truncated to first two per
  cluster.
- `S4_ORDER_SHUFFLE` — packet-order shuffle, seed 20260826.

Applied AFTER the challenger implementation freezes: variants feed the
same pipeline; each variant's scored report is compared via
matched-target-id sets (`stability-check`). Any matched-set change ⇒
that scheme records unstable. Defined here, unseen at definition time,
not editable post-unsealing.

## 9. Verdict logic

Per type, given stability known (else Interest-family verdict is
`INCOMPLETE_PERTURBATION_PENDING`):

- No formal positives → `NOT_APPLICABLE`.
- Scorable positives < MIN_N_PER_TYPE (=5) → `INSUFFICIENT_EVIDENCE`
  (exact per-item outcomes still reported).
- Otherwise `PASS` iff `recall_provenance_ok == 1.0` AND, for the
  Interest family, `fp_hits == 0`; else `FAIL`.

Overall: `DIAGNOSTIC_ONLY_INSUFFICIENT_EVIDENCE` when every type is
insufficient; `MIXED_WITH_FAIL` / `SUFFICIENT_PASS` otherwise as
computed.

## 10. Minimum-sample rules

`MIN_N_PER_TYPE = 5` (frozen constant). Public receipt says the largest
positive denominator is 4 (Interest), then 2, 2, 1, negatives 1. By
construction the v1.1 holdout therefore returns INSUFFICIENT_EVIDENCE on
every type while still yielding the full diagnostic matrix; that is the
honest outcome this contract is designed to produce. Raising denominators
is a new-curation workstream, not an evaluator knob.

## Fail-closed bindings (score time)

1. `--gt` artifact sha256 must equal the sealed echo above, else abort.
2. FROZEN_RECEIPT.json artifact hashes must reproduce, else abort
   (drift exit code 3).
3. `score` refuses to run without explicit `--allow-holdout`.
4. GT binding is synonym-tolerant over KEY NAMES only; ambiguity
   missing/unknowable value shapes raise `SchemaBindError` rather than
   improvising a mapping post-unseal.
5. Challenger (Arm B) binds only after the Contract Reliability
   engineer publishes the selected implementation and the architect
   freezes its SHA; then: bind SHA/config → re-verify this manifest →
   open v1.1 → one run → report → stop.

## Baseline handling

Arm A (preserved legacy top-25 baseline) is scored through THIS
evaluator wherever its artifacts remain reproducible; missing Arm A
material downgrades comparison coverage (reported as absent), never
re-creates a weaker proxy. Both arms' statuses publish side by side
under identical metric definitions.

## AMENDMENTS

## AMENDMENT 1 — ARCHITECT_AMENDMENT_1 (2026-08-27, PRE-UNSEAL)

Applied before any contact with the sealed v1.1 artifact. Basis: the
architect amendment directive plus PUBLIC sample counts only
(v1.1 RECEIPT.json aggregates). No label identities or outcomes were
seen. Sections 1–3 and 5–10 (matching, scorability, denominators,
negative policy, unknown-topic handling, provenance rules, stability,
verdict logic, minimum-sample) are UNCHANGED.

### What changed

The evaluator now emits TWO orthogonal outputs:

1. GENERALIZATION EVIDENCE — unchanged §4/§9/§10 logic per type:
   `SUFFICIENT_EVIDENCE` path stays available only when scorable n ≥
   MIN_N_PER_TYPE=5; otherwise `INSUFFICIENT_EVIDENCE`. No confidence is
   manufactured from tiny n.
2. FINITE_SET_CONFORMANCE — new exact deterministic result over the
   sealed operator-confirmed set, per semantic type:
   - `PERFECT`: every corpus-scorable positive of that type is a
     `provenance_valid_match` AND no explicit matching negative of that
     type is semantically inferred.
   - `IMPERFECT`: ≥1 scorable positive missed (semantic or provenance)
     OR ≥1 matching negative inferred.
   - `NOT_EVALUABLE`: zero corpus-scorable labels of that type.
   No percentage threshold, no statistical inference, no partial
   cutoffs invented. The exact item vector accompanies every status.

### Frozen readings recorded pre-unseal

- FINITE_SET_CONFORMANCE consumes `provenance_valid_match`
  (semantic match + valid evidence refs); both `semantic_match` and
  `provenance_valid_match` are reported per item.
- Only a type's OWN explicit negative class can make it IMPERFECT:
  `InterestNegative→Interest`, `GoalNegative→Goal`,
  `InformationNeedNegative→InformationNeed`. All other negative classes
  stay unmachinable-in-track exactly as §5.
- A negative whose semantic class maps to no existing type (none exist:
  taxonomy has no QuestionNegative) scores nothing here.
- Mechanical reading of NOT_EVALUABLE: it requires zero SCORABLE
  positives AND zero negatives assigned to that type. If a type has
  zero scorable positives but its own negative labels exist, the
  negative side still decides PERFECT vs IMPERFECT — this reading is
  recorded NOW, not improvised post-unseal.
- Negative matches count as inferred at SEMANTIC level (any ladder
  path); provenance invalidity does not rescue a false-positive hit,
  because the pipeline asserted the disinterest topic regardless.

### Test additions required by the amendment

Synthetic fixtures prove: (1) n=4 yields INSUFFICIENT_EVIDENCE +
PERFECT; (2) one miss yields INSUFFICIENT_EVIDENCE + IMPERFECT;
(3) one matching explicit negative yields IMPERFECT; (4) a wrong-class
negative does not affect Interest conformance; (5) zero scorable labels
yields NOT_EVALUABLE.

### Addendum (2026-08-27, post-landing bookkeeping — no policy change)

Landing: commit `02fd3a7e` on lane agent/sess_8b2b8fbd…, integrated to
main as `ff9696ee` (reviewed tree a6efd016, reviewer
agent-reviewer-71042b81, run run-bc79ae6be0d4).

Hash convention clarified: canonical freeze hashes are REPO CONTENT
(git blob) hashes. The originally receipted ef/eval_interest_semantic.py
(3321d8aa…) and tests/test_eval_interest_semantic.py (a7234474…) were
disk-byte hashes of CRLF/mixed-EOL working copies; `core.autocrlf=input`
normalized those files to LF at commit time (a lossless, deterministic
byte transformation of identical logic). Landed canonical hashes:

- ef/eval_interest_semantic.py      a22b50a868b1946588355c0f4ec7edc83db812c64ff078297a67c2d7f1c3b503
- scripts/eval_interest_holdout.py  623ea5b80435321b5a0b4b12de5c8402ebfe7b4bc481eeae328f9a7c932d91f8
- tests/test_eval_interest_semantic.py bac1a1f0ba2793c6a3816734507cc94a2430f17d2496f3682a2aa99d9be11548
- METRIC_PLAN_PREREGISTRATION.md    f3bcd0e72bbafbd461b6e868ff755990544ac0215bbec83898bee112381f46fb

Working-tree bytes equal these blob hashes after integration checkout;
verify_manifest now reproduces them locally. The pre-normalization
values are recorded here as historical disk variants and are SUPERSEDED.
No metric-policy text changed in this addendum.
