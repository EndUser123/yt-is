---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_801cc604-24b9-48ea-b625-b4820e8f6679
status: BOUND_WAITING_ON_FRESH_PRE_UNSEAL_REVIEW
---

# ISEM v1 — AMENDMENT_3_PRE_UNSEAL_EXECUTION_AND_CONSTRUCT_HARDENING

Repairs the FRESH-PRE-UNSEAL-REVIEW rejection (F-R1) of candidate
`0a5d7b73` (rejection ACCEPTED) and the additional construct-validity
and isolation defects found by architect red-team — all BEFORE any
label contact. Amendments 1 and 2 remain immutable history; the
Amendment 2 candidate commit stays in the branch history as rejected.

Machine-readable chain: `isem-d3-pre-unseal-binding.json`
(amendment_3 block), `FREEZE_RECEIPT.json` (8 frozen artifacts),
`isem-d3-contestant-materialization.json` (byte-identical mirror of
the store manifest).

## A. Exact-contestant execution (review blocker F-R1 — FIXED)

Generic `score` previously accepted `--result <arbitrary json>` with no
mechanical binding to the contestants. Now:

- If the `--gt` digest equals the sealed v1.1 holdout digest, generic
  `score` REFUSES outright — `--allow-holdout` does NOT override it.
  Synthetic/development GT keeps working.
- The ONE authoritative sealed surface is
  `scripts/run_sealed_isem_d3.py`:

      verify evaluator candidate (FREEZE_RECEIPT drift guard)
      -> verify binding manifest (identity recomputation, contestant set)
      -> verify materialization manifest
      -> per contestant: re-hash stored bytes -> score
      -> mechanical aggregate -> REPEATABLE_PERFECT

- `--run` on the formal runner refuses any GT that does NOT hash to the
  sealed digest: the surface is sealed-only.

## B. Contestants materialized pre-unseal (durable, content-addressed)

Store: `P:/.data/yt-is/ef/interest-inference/frozen-contestants/isem-d3-v1/`
(canonical yt-is data home, traced from `big.ARTIFACT_ROOT`; NOT under
`.data/sessions/` or `P:/tmp/`).

| contestant | sha256 (file name) | bytes |
|---|---|---|
| shadow_1 | `8eafbfd8cf05bb3931dd0c93393f1c6a0bbeeadde8cc911bc641d37b860fea00` | 132751 |
| shadow_2 | `acbbda7c9c9cee141b7d97d35571dd75eafb46db79da38f18cef04785d041462` | 173406 |
| shadow_3 | `277a1a3af5c22537a1785b48d4f3668c8d9820e9c378f96fb5a04a4a49addc48` | 174482 |

Manifest binding per contestant: logical id, canonical payload sha256,
byte length, storage path, D3 freeze commit `f7bd24fd…`, implementation
manifest sha256 `3652da82…`, reconstruction version
DETERMINISTIC_ASSEMBLY_REPLAY_V1, strict-validator status PASSED.
Manifest sha256 `d64261e0460a6b72f157774a3eef781f4c4a647af953515ae644adc79e756663`
(byte-identical mirror committed in the repo). Formal scoring re-hashes
the bytes immediately before every use; contestants are NEVER
regenerated after unseal. Proven offline: scoring depends only on the
materialized store, not the scattered run directories.

## C. Sealed report identity

Every per-contestant scored report embeds
`sealed_execution_identity`: evaluator frozen-artifact hashes + freeze
receipt sha256, binding manifest identity (status + binding identity
sha256), contestant logical id, contestant payload sha256, D3
implementation manifest sha256, D3 freeze commit, holdout sha256,
support artifact sha256, cluster-inventory sha256, judge prompt
hashes, judge model/config, judge-cache sha256, run id, generation
timestamp, and the match-policy amendment stamp. The aggregate binds
all three report hashes (`report_hashes_sha256`,
`aggregate_binds_report_hashes`, `aggregate_sha256`). A report without
contestant identity is unrepresentable on the formal path — the stamp
is built inside `score_contestant`.

## D. REPEATABLE_PERFECT is code (`ef/sealed_execution.py::aggregate_reports`)

YES iff Interest finite-set == PERFECT on shadow_1 AND shadow_2 AND
shadow_3. Any non-PERFECT (IMPERFECT or NOT_EVALUABLE) → NO. Any
missing report, duplicate contestant, or identity/hash failure →
INCOMPLETE with `final_gate: null`. No majority vote, no best-run, no
averaging, no omitted run. Goal / InformationNeed / Question statuses
are reported per contestant; no promotion thresholds for their small
denominators.

## E. Match-policy amendment (construct validity, pre-unseal)

Red-team finding: the old alias tier auto-matched on substring
containment of the target name/alias inside candidate text+context and
on a significant-token-subset shortcut — a candidate could count as a
known Interest because its evidence summary MENTIONED it.

New frozen ladder (`ef/eval_interest_semantic.py`):

1. EXACT — normalized candidate surface TEXT == normalized canonical
   name.
2. ALIAS — normalized candidate surface TEXT == one normalized
   explicitly supplied alias.
3. SEMANTIC_JUDGE — otherwise; context IS rendered into the judge
   prompt but can NEVER produce an automatic match.

Substring auto-match and token-subset auto-match are REMOVED
(`alias_hit`/`_sig_tokens` deleted; `match_one` raises
`JudgeUnavailable` when the judge cannot answer). This is a recorded
pre-unseal construct-validity amendment made blind: the private labels
were never opened or inspected; all tests are synthetic fixtures.

## F. Judge sandbox (tool-free; canary-proven)

Old transport: `codex exec -s read-only -C P:/` + "Read <file>" —
read-only is not blind, and a broad repository root was the working
directory.

New transport (same frozen judge model gpt-5.6-luna, effort low,
timeout 300s, 2 attempts — instrument unchanged, channel hardened):

- shell tool disabled STRUCTURALLY (`-c tools.shell=false`) — the
  model has no filesystem channel at all;
- the rendered pair prompt is the ONLY input, passed inline via stdin
  (no file read);
- working root is a fresh per-call EMPTY temp sandbox directory
  (`--skip-git-repo-check` because no repo is involved); never `P:/`
  or any repository/data root;
- `--ignore-user-config --ignore-rules` (no user hooks/execpolicy),
  `shell_environment_policy.inherit="none"`, `--ephemeral`.

Mechanical canary probe (`run_sealed_isem_d3.py --probe-judge`,
synthetic nonces only, no labels), run live 2026-08-27:

    outside_canary_leaked:          false
    judge_can_read_outside_sandbox: false
    prompt_canary_processed:        true
    verdict: PASS

The probe executes the EXACT command shape used for real decisions and
is re-run at every binding-manifest emit; a failing probe refuses the
emit (JUDGE_SANDBOX_BLOCKED, do not unseal).

## G. Judge transport failure is fail-closed

A required semantic pair the judge cannot answer after bounded attempts
raises `JudgeUnavailable` → the runner writes an
`EVALUATION_INCOMPLETE` receipt (exit 4) with the unresolved prompt
hashes and NO final gate. Resume rules (frozen pre-unseal): re-run
resolves ONLY unresolved prompt hashes via the write-once cache; same
evaluator, same exact prompt, same model/config (the cache header pins
model+effort and refuses resume under any other identity); successful
decisions are immutable (cache served first; write-once merge); model
config may not change after unseal; no final gate with unresolved
required judgments.

## H. Single holdout contact / support artifact

The formal runner verifies the sealed GT digest, produces the
scorability support artifact ONCE (or reuses an existing artifact
after verifying its recorded `holdout_sha256`), hashes it
(`ISEM_SUPPORT_V1`, includes `cluster_inventory_sha256` and
`eligible_cluster_ids`), and reuses that exact artifact for all three
contestants. No mutable support state is recomputed between
contestants.

## I. Pre-unseal tests (all 61 pass; offline synthetic only)

Retained: the full prior suite. Added adversarial regressions
(`tests/test_sealed_execution.py`, `tests/test_eval_interest_semantic.py`,
`tests/test_isem_d3_binding.py`):

1. generic score + sealed digest + arbitrary result → REFUSED (even
   with `--allow-holdout`)
2. binding manifest refuses a fourth contestant
3. aggregate refuses an omitted contestant (INCOMPLETE, no verdict)
4. same-schema different payload substituted at the content-addressed
   path → refused by the immediate re-hash
5. every scored report carries the exact contestant hash
6. aggregate refuses a report with wrong contestant payload identity
7. aggregate refuses duplicate shadow_1 substituted for shadow_2
8. REPEATABLE_PERFECT = YES requires all three PERFECT
9. one IMPERFECT → NO
10. one missing/incomplete → INCOMPLETE, no final verdict
11. materialized contestants reproduce all three bound hashes
12. source run directories irrelevant: formal scoring reads only the
    materialized store
13. context mention alone does NOT auto-match
14. token-subset overlap alone does NOT auto-match
15. exact candidate name matches (EXACT)
16. exact explicitly supplied alias matches (ALIAS)
17. ambiguous relation goes to the judge, with context in the prompt
18. judge transport argv is tool-free + sandboxed (`tools.shell=false`,
    no broad root, stdin prompt); canary logic: outside nonce ⇒ BLOCKED,
    clean echo ⇒ PASS, missing prompt nonce ⇒ BLOCKED
19. judge transport failure → JudgeUnavailable/EVALUATION_INCOMPLETE,
    never no_match (exact pairs unaffected)
20. resume issues provider calls for unresolved prompt hashes only
21. completed judgment cache cannot be silently changed; cache identity
    mismatch refuses resume
22. sealed holdout unreachable through every generic/diagnostic surface
    (support gated, score digest-refused, other subcommands take no
    `--gt`, formal runner refuses non-sealed GT)

## J. Freeze chain; holdout remains sealed

Updated with this amendment: `FREEZE_RECEIPT.json` (8 artifacts, status
BOUND_WAITING_ON_FRESH_PRE_UNSEAL_REVIEW), the pre-unseal binding
manifest (amendment_3 block: evaluator hashes, materialization
manifest sha256, formal runner hash, aggregate implementation hash,
judge-sandbox configuration + live probe receipt, match-policy
amendment, transport-failure/resume semantics), and this document.

P:/.data/yt-is/private/interest-intelligence-holdout-v1.1.json was
NEVER opened. No real label IDs anywhere in this amendment or its
tests. Provider semantic calls against real labels: ZERO (the only
provider calls made were the synthetic-canary isolation probes).
