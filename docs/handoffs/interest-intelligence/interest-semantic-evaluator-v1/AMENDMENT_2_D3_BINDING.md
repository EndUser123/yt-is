---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_801cc604-24b9-48ea-b625-b4820e8f6679
status: BOUND_WAITING_ON_FRESH_PRE_UNSEAL_REVIEW
---

# ISEM v1 — BINDING AMENDMENT 2 (pre-unseal D3 binding)

Amends: `METRIC_PLAN_PREREGISTRATION.md` (ARCHITECT_AMENDMENT_1 chain)
at binding time, BEFORE any holdout contact. Recorded per the
recorded-amendment norm; the frozen metric semantics are untouched.

Binds: the published Interest Semantic Evaluator (ISEM v1, lineage
`02fd3a7e` candidate → `ff9696ee` integration → `a91bdec1` bookkeeping,
all on origin/main) to the frozen D3 inference contestant (freeze
commit `f7bd24fdb917aa5e35112d0b2f2eae1c2129bf59`, architecture
D3_DECOMPOSED, implementation manifest sha256
`3652da82817f5a373eef1011312e756fa1c42ef41d8635b553a9ab3d3e996ba3`).

Machine-readable binding artifact:
`docs/handoffs/interest-intelligence/isem-d3-pre-unseal-binding.json`
(status field there is authoritative:
`BOUND_WAITING_ON_FRESH_PRE_UNSEAL_REVIEW`).

## What this amendment does NOT touch

Exact/alias/semantic-judge matching policy, scorability policy,
negative taxonomy, finite-set semantics, MIN_N_PER_TYPE, judge prompts,
perturbation schemes, the score CLI's authorization surface (still
unconditional `--allow-holdout` + sealed-sha256 + manifest-drift
verification), and all historical receipts. The FREEZE_RECEIPT
gate precondition "this same evaluator binds --inference-sha and
re-verifies manifest hashes unchanged" is discharged by the binding
artifact: it binds the inference implementation manifest sha256 and
re-verifies every manifest file unchanged at binding time (score's CLI
surface is deliberately NOT extended post-freeze).

## F1 — support authorization boundary (FIXED)

`support` previously opened whatever `--gt` path it was handed with no
gate, so the sealed real GT path could be opened casually. Now
(`scripts/eval_interest_holdout.py`):

- `support` accepts `--allow-holdout` and refuses otherwise when the
  `--gt` file (a) resolves under the private holdout directory
  (`P:/.data/yt-is/private`) or (b) hashes to the sealed holdout
  digest. The private-directory check is purely path-based — the
  sealed artifact's bytes are never read during the check; the digest
  check catches relocated copies.
- Synthetic/public GT files keep working with no flag (tests pin both
  polarities). `score` is unchanged (already stricter).

## F2 — historical freeze-chain hash ledger (COMPLETE)

Full superseded-hash record, mechanically recovered from git objects
wherever recoverable; historical receipts preserved verbatim, nothing
rewritten.

### Recovered from git (committed states; sha256 over landed LF blob content)

| artifact | commit | landed sha256 |
|---|---|---|
| ef/eval_interest_semantic.py | 02fd3a7e (== a91bdec1 == origin/main) | `a22b50a8…3b503` |
| scripts/eval_interest_holdout.py | 02fd3a7e (== a91bdec1 == origin/main) | `623ea5b8…91f8` |
| tests/test_eval_interest_semantic.py | 02fd3a7e (== a91bdec1 == origin/main) | `bac1a1f0…1548` |
| METRIC_PLAN_PREREGISTRATION.md | 02fd3a7e | `f3bcd0e7…46fb` |
| METRIC_PLAN_PREREGISTRATION.md | a91bdec1 (current canonical) | `7b0a3c65…1dbf0` |

Anomaly recorded, not rewritten: HANDOFF.md at a91bdec1 still cites the
prereg at `f3bcd0e7…` in its "Hashes at generation time" section while
the same commit's FREEZE_RECEIPT.json records `7b0a3c65…`. The receipt
is correct for the landed AMENDMENT_1 chain; the handoff line is the
superseded 02fd3a7e-time value that the bookkeeping commit missed.
Both citations stand in history; this ledger is the resolution.

Documented-only (never committed; cited from the AMENDMENT 1 Addendum,
not mechanically recoverable from git): pre-amendment working chain
`d03755c1…`, `623ea5b8…` (superseded restatement), `ea789ad9…`,
`604d17fd…`; pre-normalization disk-EOL variants `3321d8aa…` (ef) and
`a7234474…` (tests).

### NEW evidence found at binding time — D3 manifest EOL duality

The D3 freeze manifest's per-file `content_sha256` values were taken
over the lane's mixed-EOL working tree, not the landed LF blobs. Blob
identity (git sha1) is EXACT for all six files; the digest duality is:

| path | manifest content_sha256 | equals |
|---|---|---|
| ef/contract_v2.py | `65bcb7dc…85cc0` | CRLF working-tree variant (LF landed: `eebd16c7…e6a3c`) |
| ef/inference_contract.py | `a249fdae…7f4ce` | landed LF form |
| ef/evidence_clusters.py | `fd5d5609…88263a` | landed LF form |
| ef/interest_candidates.py | `4b1a0706…330d5` | landed LF form |
| scripts/build_interest_graph.py | `138458b4…e89dd3` | landed LF form |
| scripts/contract_v2_bakeoff.py | `9002d650…ed293` | CRLF working-tree variant (LF landed: `99cd4d8d…b7e7e`) |

This is the same reviewed-working-tree-hash vs canonical-repo-content
distinction documented by the ISEM bookkeeping (`a91bdec1`), now
recorded for the D3 side. `ef/isem_d3_binding.py` verifies BOTH forms
and records which form matched per file; it refuses only when neither
matches. Historical D3 receipts are preserved unchanged.

## F3 — zero-scorable finite-set regression (TEST_ADDED)

`tests/test_eval_interest_semantic.py` gains committed proofs that a
type with zero corpus-scorable positives plus its own negative class
remains EVALUABLE with the frozen finite-set semantics:

- matching own-type negative → IMPERFECT with the hit recorded
  (never silently elided, never NOT_EVALUABLE);
- clean own-type negative → PERFECT (negative side decides);
- boundary pin: probe-resolved unknown scorability makes the positive
  scorable (branch boundary between zero and one scorable positives).

## Reconstruction disclosure (unchanged defect, now bound)

The v2 shadow driver never persisted assembled finals. Contestant
identity is recovered by DETERMINISTIC_ASSEMBLY_REPLAY_V1
(`ef/isem_d3_binding.py`): pure replay of frozen committed code over
the frozen provider artifacts, strict frozen `validate_inference` pass
required, canonical payload sha256 over
`json.dumps(payload, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` (the unique recipe reproducing all three bound
hashes). At this binding the replay reproduced all three contestant
hashes byte-exact with ZERO provider calls and the holdout unopened:

- shadow_1 `8eafbfd8…fea00` (303 in → 376 dispositions → 225 canonical)
- shadow_2 `acbbda7c…1462` (329 in → 445 dispositions → 307 canonical)
- shadow_3 `277a1a3a…ddc48` (326 in → 406 dispositions → 288 canonical)

A mismatch would have been fatal (`CONTESTANT_RECONSTRUCTION_MISMATCH`
— no replacement runs, no reruns).

## Binding rule (pre-unseal, restated from the freeze)

ISEM scores ALL THREE contestant outputs independently.
REPEATABLE_PERFECT = YES iff shadow_1 AND shadow_2 AND shadow_3
Interest finite-set conformance = PERFECT; any IMPERFECT → NO.
No majority vote, no best-run selection, no rerun after labels are
opened. Goal / InformationNeed / Question are reported per run with no
invented promotion thresholds for their tiny denominators.

Later evaluation reports three DISTINCT outcome families —
(A) finite-set correctness, (B) generalization evidence,
(C) run-to-run semantic stability — and known-set success must not
hide the already-measured label-free instability (exact 3-way Interest
intersection 8 of union 500, IoU 0.016; descriptive, not a reason to
alter D3).

## Tests

`python -m pytest tests/test_eval_interest_semantic.py
tests/test_isem_d3_binding.py -q` — full offline synthetic suite plus
the binding/F1/F3 tests added by this amendment (seven binding refusal
proofs: wrong payload hash, wrong implementation manifest, missing
contestant, fourth contestant, reorder-invariant binding identity,
provider-seam impossibility by source scan, GT-path absence in the
binding module).
