# Interest Intelligence ground truth v1 — curation policy and receipt

Frozen: 2026-08-26. Curator: zcode cold-start implementer session (curation only).
Status label returned to parent: `INTEREST_GROUND_TRUTH_READY`.

## What this is

Private evaluation ground truth for Interest Intelligence. The labels judge a
future interest inference implementation. Nothing here tunes inference; nothing
here scores inference outputs.

- Private artifact (NOT in git): `P:/.data/yt-is/private/interest-intelligence-holdout-v1.json`
  - sha256: `97cb8d59ec201fbd39f3ce526b6dae705e1cc49d402558ac0283abb55362b542`
  - bytes: 22227
- This directory carries only schema, policy, hashes, counts, aggregate
  distributions. No private label identities are committed.

## Curation policy (binding for any future re-curation)

Authority order, strictly tiered:

1. Explicit operator statements in session transcripts ("I care about...",
   "I want...", "my goal is...", repeated explicit questions/projects/
   preferences), cited by store message id and date.
2. Operator-authored decision/state artifacts that preserve explicit user
   statements (persisted directives, operator-authored skill/instruction
   content).
3. Conversation exports / other user-authored records if locally available.

Promotion rules:

- Architect inference, LLM-authored text (including relayed plans between
  models), browsing/corpus consumption patterns, document frequency, and wiki
  concept emergence are evidence FOR inference systems and are NEVER labels.
- Discovery temporal-emergence holdouts (`discovery-retrospective-holdout-v4`
  and kin) were NOT used as Interest ground truth. Concept emergence is not
  user interest.
- "Not on the positive list" does NOT mean negative. Every committed negative
  has an explicit operator rejection/exclusion statement with scope.
- Topics without operator stance remain UNKNOWN and are enumerated in the
  private artifact's unknown ledger so a future evaluator cannot score them
  as negatives.

Label model:

- Types: `Interest`, `Goal`, `InformationNeed`, `Question`.
- Interests carry temporal types only where operator evidence supports them:
  `durable`, `active`, `current_problem`, `episodic`, `dormant`.
- Label authority: `OPERATOR_CONFIRMED` or
  `OPERATOR_STRONGLY_IMPLIED_BY_MULTIPLE_EXPLICIT_STATEMENTS`. Purely
  architect-inferred interests were excluded from the formal set.
- Each positive records: stable private id, canonical label, aliases, type,
  verbatim operator quote(s) with provider/session/date, corpus-plausibility
  flag, and optional scorability probe note.

Scorability contract for the future evaluator:

- Separate `VALID_PERSONAL_INTEREST_LABEL` from
  `SCORABLE_FROM_CURRENT_CORPUS`.
- Distinguish `UNSCORABLE_MISSING_EVIDENCE` from `INFERENCE_FAILURE`.
- Per-type precision/recall; unknown topics never counted as negatives.
- The evaluator must not open the private holdout until its metric plan and
  the inference implementation are both frozen.

## Frozen aggregates (private artifact hash above binds these numbers)

| Set | Count |
|---|---|
| Interest positives | 5 |
| Goal positives | 3 |
| InformationNeed positives | 2 |
| Question positives | 2 |
| Explicit negatives | 7 |
| Unknown ledger groups | 2 |

Authority distribution over all positives (12):
`OPERATOR_CONFIRMED` 8, `OPERATOR_STRONGLY_IMPLIED_BY_MULTIPLE_EXPLICIT_
STATEMENTS` 4.

Temporal distribution over Interest positives: durable 3, active 2
(no forced current_problem/episodic/dormant labels where evidence lacked).

Evidence tier distribution over all cited evidence entries: tier-1 transcript
statements ~90%, tier-2 operator artifacts ~10%.

Limitations, honestly stated:

- Positives skew toward work-context interests because the queryable stores
  are agent-session transcripts; personal-life coverage beyond health relies
  on few sentences. Expanded first-person consumption-preference capture
  (e.g., explicit feedback buttons on surfaced items) should feed v2.
- Explicit negatives number 7 and include mechanism/tool-scope exclusions;
  topical negatives are only those with direct operator sentences.
- One label authority nuance is preserved verbatim inside the private
  artifact: wording that originated in architect/model text attached to an
  operator-driven direction was NOT promoted to CONFIRMED.

Contamination attestation (recorded in the private artifact's
`contamination_attestation` block): no inference outputs inspected for label
selection; no Discovery holdouts used as labels; no architect/LLM inference,
corpus frequency, or consumption patterns promoted into labels.

`NEXT IMPLEMENTER DECISION: FRESH EVALUATOR REQUIRED after inference
reliability is frozen.`
