# Interest Intelligence ground truth v1.1 — closure-pass policy addendum

Frozen: 2026-08-26. Curator: zcode (same session that produced v1), one
ground-truth-integrity closure pass. Aggregate-only receipt:
`RECEIPT.json` (same directory). No private label identities or verbatim
operator quotations appear in git.

## What changed relative to v1 (policy deltas, binding)

1. Negative taxonomy split (mechanically explicit). Classes:
   `InterestNegative`, `GoalNegative`, `InformationNeedNegative`,
   `SourceNegative`, `ImplementationConstraintNegative`,
   `ToolOrMechanismPreference`, `StyleOrProcessPreference`,
   `AmbiguousNegative`. Only `InterestNegative` is false-positive ground
   truth for an Interest classifier; every other class scores only its
   matching semantic object. Principle enforced by example: rejecting a
   source, a mechanism, or an engineering framing is not evidence of
   disinterest in subject matter.
2. Positive type audit. Process/architecture directives were re-audited for
   semantic TYPE and moved out of the Interest contract into
   `WorkPreference` / `ImplementationConstraint` where the statement regulates
   how work or choices are made rather than naming a pursued/engaged subject.
3. Evidence-authorship audit per label. Tier-1 transcript user messages are
   `OPERATOR_AUTHORED`. Persisted artifacts may corroborate only when
   operator authorship/approval is mechanically traceable; otherwise the
   citing label is downgraded out of the contract or the tier-2 line is
   recorded as corroboration with its provenance call attached.
4. Saturation expansion under preregistration. The search-family/batch plan,
   qualification rule, stopping rule, and reviewer-adjudication rule were
   written and hashed BEFORE additional searching
   (sha256 `3aa9640206c8dc2e84206cfffe8f4defd88d5db8ea0401837f789bae2d511d6e`).
   Four batches executed; two consecutive terminal batches yielded zero new
   formal labels; contingency batch never triggered; four rejected-borderline
   candidates were retained for reviewer sampling instead of being dropped.
5. Scorability fields three-valued (`corpus_scorable`, `corpus_unscorable`,
   `unknown`) on every positive-side entry, each with probe receipts.
6. Supersession discipline. v1 preserved byte-identical; superseded marker
   written beside it; successor at v1.1. Mechanical no-consumption evidence:
   ledger chronology, zero content references, zero private-file copies.

## Review protocol result

Fresh-context subagent reviewer saw ONLY a blinded extract (verbatim quotes,
sources, dates, probe notes) — curator classifications stripped. Full-
enumeration sample: 19 labels + 4 rejected-borderline candidates.

- Semantic-class agreement 19/19, including independent confirmation of all
  three retypings.
- Field-level agreement 71/76 (93.4%); the five disagreements are
  authority-strength calls on existing classes, all disposed under the
  preregistered rule and recorded as DISPUTED_RETAINED in the private
  artifact (no silent adoption in either direction).

## Evaluator-facing reminders

- Per-type recall denominators stay separated; INSUFFICIENT_EVIDENCE is a
  legal outcome per semantic type. No PASS threshold is defined here.
- Dual-use evidence caveat adopted from reviewer findings: statements of the
  form "I don't care about <framing X> (I care about Y)" dually support a
  constraint record and its negative mirror; evaluators must score framings
  as constraints/criteria, not as topical interests either way.
- The evaluator gate is unchanged: private holdout sealed until metric plan
  and inference implementation are frozen.

NEXT IMPLEMENTER DECISION: RETIRE CURATOR IF READY (curator side complete);
fresh evaluator implementation remains pending after inference reliability
is frozen.
