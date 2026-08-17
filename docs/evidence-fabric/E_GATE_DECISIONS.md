The STOP is correct. I would make **three decisions and one gate-control repair** before C4.

## Decisions

**1. Telegraphic queries: do not lower the 0.40 gate yet.**
C3 has revealed a new query class, but `MRR` against a single authored positive may be the wrong measurement. Use C3 now as development evidence: judge top-10 relevance for the telegraphic cases, compute nDCG@10/Recall@10 as well as MRR, and compare the existing fusion against dense-only, sparse-only, and a justified sparse-heavier semantic mix. Then preregister C4 against the metric that actually represents useful retrieval.

**2. TikTok: remove df from intent routing.**
Do not add a brand stoplist or move the `df < 1000` threshold. `TikTok` demonstrates that corpus spelling frequency is not query intent.

Use:

```text
explicit exact request
    → exact semantics

strong structural identifier shape
    → identifier semantics

ambiguous human-language token
    → semantic semantics
```

So `TikTok`, `Google`, `Python`, `YouTube` default semantic; `RPC9`, `hizoJc`, `--resume-worker`, `ClassName.method`, etc. remain identifier-like. DF can affect ranking/evaluation, but should not decide intent.

**3. Latency: authorize the proposed fix.**
Revalidate the Qdrant client **once per top-level `relevant()` call**, then reuse it for all legs. Retain reconnect-on-actual-failure. Keep the 250 ms gate.

---

## More important: fix promotion control before C4

The accidental C2 auto-promotion is a **P0 gate-integrity defect**.

A regression battery should not merely be told not to promote. It should be **incapable of promotion**.

I would require:

```text
C1/C2/C3/C4 evaluator
        ↓
immutable PASS/FAIL receipt
        ↓
NO ability to change active_generation

separate promote command
        ↓
validates promotion-authorized PASS receipt
        ↓
atomic generation switch
        ↓
promotion receipt
```

The promote operation should verify at least:

* suite is explicitly promotion-authorized;
* receipt hash/integrity;
* all required gates passed;
* correct BuildSpec/build ID;
* correct candidate generation;
* freshness still acceptable;
* expected current active generation;
* no stale/retracted receipt.

Add a test proving C1/C2/C3 can **never** alter `active_generation`, even if every check returns PASS.

Preserve the accidental promotion and retraction receipts.

---

## Prompt to send the implementing LLM

> **C3 STOP accepted. Generation 1 remains inactive. Do not modify C3 gates or sealed cases. C3 is now regression/development evidence.**
>
> Before C4, execute the following.
>
> ### 1. Repair promotion integrity — P0
>
> Remove all generation-promotion capability from evaluation/battery scripts.
>
> C1/C2/C3 and future batteries must only emit immutable verdict receipts.
>
> Implement a separate explicit promotion command that accepts only a promotion-authorized PASS receipt and mechanically verifies:
>
> * suite identity and promotion authorization;
> * receipt integrity/hash;
> * candidate generation;
> * BuildSpec/build ID;
> * complete required gate set;
> * PASS verdict;
> * freshness/lag;
> * expected current active generation.
>
> It must fail closed, switch atomically, be idempotent, and emit a separate promotion receipt.
>
> Add a test proving regression suites cannot modify `active_generation`, even on all-pass results.
>
> Preserve the accidental C2 promotion and retraction as historical evidence.
>
> ### 2. Telegraphic semantic queries
>
> Do **not** lower the 0.40 threshold yet.
>
> C3 may now be used as development data. For its telegraphic queries:
>
> * judge the top-10 retrieved results for actual relevance;
> * compute MRR@10, judged Recall@10, and nDCG@10;
> * determine whether the failure is a single-positive-ground-truth problem or genuine retrieval weakness;
> * compare current D-weighted fusion with:
>
>   * BGE dense only,
>   * BGE learned-sparse only,
>   * justified sparse/lexical-heavier semantic fusion.
>
> Do not add a reranker or rebuild embeddings unless the component evidence requires it.
>
> Telegraphic queries remain semantic queries.
>
> After development analysis, preregister the C4 telegraphic metric and threshold **before constructing C4**.
>
> ### 3. Remove document frequency from intent classification
>
> `TikTok` falsifies df-based intent routing.
>
> Do not introduce a brand stoplist and do not move the df threshold.
>
> Implement:
>
> ```text
> explicit exact=true / quoted literal
>     → exact semantics
>
> strong structural identifier
>     → identifier semantics
>
> ambiguous ordinary human token
>     → semantic semantics
> ```
>
> Development cases must include:
>
> ```text
> semantic/common:
> Google
> Python
> YouTube
> TikTok
>
> identifier:
> hizoJc
> GR0000tn2
> RPC9
> --resume-worker
> ERROR_RESOURCE_EXHAUSTED
> Qwen3-Reranker-4B
> ClassName.method_name
> ```
>
> DF may remain useful for ranking, literal-set handling, diagnostics, and evaluation. It must not independently determine intent.
>
> Run this against C1/C2/C3 regression evidence before freezing C4.
>
> ### 4. Latency correction
>
> Authorize the smallest correctness-preserving change:
>
> * validate/revalidate the cached Qdrant client once per top-level `relevant()` call;
> * reuse that validated client across all retrieval legs in that call;
> * on an actual connection failure, invalidate, reconnect in a bounded manner, and retry;
> * preserve the restart/reconnect invariant;
> * do not restore PowerShell polling or a blind multi-second trust window.
>
> Keep:
>
> ```text
> preferred warm p95 <100 ms
> hard gate p95 <250 ms
> ```
>
> Measure the resulting stage decomposition.
>
> ### 5. C4
>
> Only after items 1–4 are implemented, tested, measured, and committed:
>
> 1. Run C1/C2/C3 as regression/development suites only.
> 2. Freeze and commit C4 preregistration.
> 3. Use a genuinely untouched corpus region.
> 4. Construct C4 only after preregistration.
> 5. Run C4 once as promotion evidence.
>
> C4 must cover:
>
> * zero-df identifiers;
> * df=1 identifiers;
> * literal-prefix strata;
> * punctuation-heavy identifiers;
> * near twins;
> * ambiguous words such as TikTok;
> * telegraphic semantic queries;
> * normal semantic/technical/comparison queries;
> * exact-span reopenability;
> * filters;
> * namespace/build integrity;
> * structural parity;
> * freshness lag;
> * Qdrant restart/reconnect;
> * latency.
>
> If C4 passes, emit its PASS receipt and then invoke the **separate promotion command**. Verify generation 1 becomes active and incremental indexing remains current.
>
> If C4 fails, STOP. Do not change its gates or reuse it as fresh evidence.
>
> Do not rebuild vectors, EvidenceUnits, or Qdrant storage absent new representation-level evidence.

One audit detail should also be corrected: the report says **“C3 run 2”** and later says C3 “ran once on the fresh sealed region.” Have the agent record exactly how many executions occurred and which execution first exposed the C3 cases. It does not change their current status—they are exposed now—but the evidence history should be unambiguous.

## Audit correction (execution history, recorded 2026-08-17)

C3 battery executed TWICE: run 1 (c3bat.log) crashed at the first gate
(exact_df1) with connection refused — the 30s trust-window defect — after
exposing only exact_df1 cases; no receipt written. Run 2 (c3bat2.log)
executed all gates and wrote the receipt. The C3 cases were therefore
FIRST exposed in run 1 (partial), fully exposed in run 2. C3's status
(regression/development evidence) is unchanged by this correction.
