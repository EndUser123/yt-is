# Packaging incident and sealed-packaging receipt

- date: 2026-08-28
- agent: zcode
- host: zcode
- session: sess_b1bed89e-93f9-484d-8b94-7b809fcb6bee
- artifact: Interest Intelligence ground-truth holdout v1.1
- source sha256: `1c7885081dcb6a61e419273c42b3326428727f6718f47736582717b8535aa48f`
- package sha256: `cd2e1a0c2f2376c3890543e0cafe9dae51bf0852c0e0bd10b6668417946763f3`

## Incident accounting (accurate statements only)

1. The original process seal on the v1.1 holdout was BREACHED by a prior
   session (`sess_801cc604-24b9-48ea-b625-b4820e8f6679`) during
   Amendment-4 CLI verification, when Python accidentally parsed the
   artifact. The statement "holdout never opened" is NO LONGER TRUE and
   must not be written anywhere.
2. Per the prior session's incident receipt: no scoring occurred, no
   semantic judge received label content, no label entries were displayed
   or persisted; the exception exposed only root/top-level structural
   information. This session did not contact the prior session and treats
   the source as CONTENT-SEALED, STRUCTURE-DISCLOSED.
3. This packaging session performed CONTROLLED, MACHINE-ONLY access to
   the source: every read went through
   `scripts/gt_sealed_packaging.py` (inspect/convert/verify), which
   verifies the source SHA256 before parsing and emits structural
   receipts only (key names, JSON types, presence patterns, taxonomy
   bindability booleans, digests, status codes). The model operating
   this session received ZERO semantic values from the artifact.
4. The source's structure is now further disclosed by this session's
   receipts (root containers, field-name sets, presence patterns,
   nested-object sub-key shapes). Structural facts disclosed here are
   limited to those whitelist classes.
5. ROLE EXCLUSIONS: this session is permanently excluded from the ISEM
   evaluator author, D3 author, ISEM reviewer, and sealed-evaluation
   executor roles. Its output is a DATA-PACKAGING CANDIDATE only.

## What was produced

- `P:/.data/yt-is/private/packaged-holdouts/interest-intelligence-holdout-v1.1-ISEM-PACKAGE.json`
  (gitignored private storage; canonical package accepted by the frozen
  `load_ground_truth`, 19 labels bound)
- `PACKAGE_MANIFEST.json` (this directory; machine-readable, no label
  content)
- `packaging-inspect-receipt.json`, `packaging-verify-receipt.json`
  (structural evidence)
- `scripts/gt_sealed_packaging.py` + `tests/test_gt_sealed_packaging.py`
  (converter, inspector, 25 synthetic adversarial tests; the frozen
  evaluator's own 23 tests still pass)

## What was NOT done

- No scoring, no semantic judge calls, no D3 contestant outputs
  inspected.
- No modification to `ef/eval_interest_semantic.py`,
  `scripts/eval_interest_holdout.py`, the frozen test file, the
  preregistration doc, or any freeze/binding hash.
- `verify_sealed` was deliberately not run against the package: it
  refuses any digest other than the frozen source digest by design.
  Rebinding the sealed-hash check to the package digest is the
  evaluator-binding author's decision and step.
- The source artifact was not modified; its digest was re-verified
  unchanged after packaging.
