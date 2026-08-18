I would choose a **refined version of (i)**, not (ii) or (iii).

The important discovery is not “VPN should be semantic.” It is:

> **A bare ALLCAPS token is intrinsically ambiguous from syntax alone.**

`VPN` and `BTRFS` prove that. So treating every pure ALLCAPS token as a strong identifier is too confident. But the attempted mid-gate fix also showed that simply throwing ALLCAPS into the existing permeable dual lane weakens a valuable invariant.

## The contract I would adopt

Treat **pure ALLCAPS single tokens as ambiguous**, but strengthen the ambiguous lane:

```text
pure ALLCAPS single token
        ↓
ambiguous retrieval

if exact df == 1:
    pin the unique literal match at rank 1
    semantic evidence may fill after it

if exact df > 1:
    retrieve literal + semantic evidence
    rank jointly under the ambiguous policy

if exact df == 0:
    semantic evidence may be returned
    but never labelled as literal/exact
```

That uses df for **ranking behavior**, not for deciding intent, which is consistent with the rule we've been converging on.

So:

* `BTRFS`, appearing once → exact occurrence cannot disappear.
* `VPN` → can retrieve both `VPN` and semantically relevant “virtual private network” material.
* `RPC9`, `GR0000tn2`, `--flag`, dotted identifiers, mixed alphanumeric IDs → remain strong identifiers.
* `exact=true` → remains strictly literal.

This is better than option (iii), which knowingly accepts the 0.94 regression, and better than option (ii), which keeps a classifier label that we now know overstates certainty.

## One benchmark correction

I would also stop gating on:

> “VPN must route conventional.”

That's an implementation label.

Gate **observable behavior** instead.

For a new ALLCAPS-ambiguous stratum, require both sides of the contract:

```text
opaque acronym-like / unique literal:
    df=1 exact match R@1 = 1.0

conventional acronym:
    judged semantic relevance passes
    lexical/exact evidence remains discoverable
```

Then moving pure ALLCAPS tokens out of the “strong identifier” stratum is **not benchmark laundering**. You're replacing an invalid semantic assumption with an explicit new product class and continuing to test those cases under appropriate behavioral gates.

## Do not consume shard02 yet

First use exposed C1–C7 data as development evidence to test this amended ambiguous policy.

Specifically compare:

1. current ALLCAPS→identifier;
2. existing ambiguous lane;
3. **ambiguous lane + singleton exact pin**;
4. any slightly stronger literal weighting needed for multi-hit ALLCAPS cases.

My leading choice is **#3**.

It should recover the exact invariant that the naive dual-lane patch lost while preserving VPN-class semantic behavior.

Once that passes exposed regression/development cases, freeze **benchmark protocol v1.1**, then consume shard02.

## Prompt for the implementing LLM

> **C7 STOP accepted. The benchmark protocol is validated; do not change the retrieval architecture, comparison lane, vectors, storage, or judged-query protocol. Generation 1 remains inactive.**
>
> The C7 failure exposes a genuine syntax ambiguity:
>
> ```text
> VPN
> BTRFS
> ```
>
> are both bare pure-ALLCAPS single tokens. Syntax alone cannot reliably determine “conventional term” versus “opaque identifier.”
>
> Choose **option (i′)** below, not the original (i)/(ii)/(iii).
>
> ### 1. Amend the routing contract
>
> Pure ALLCAPS single tokens become an **ambiguous syntactic class**, not automatically strong identifiers.
>
> Do not use corpus df to decide their intent.
>
> Preserve strong identifier routing for structurally stronger cases such as:
>
> ```text
> RPC9
> GR0000tn2
> --resume-worker
> ERROR_RESOURCE_EXHAUSTED
> ClassName.method
> Qwen3-Reranker-4B
> ```
>
> `exact=true` / quoted literal remains strictly exact.
>
> ### 2. Strengthen the ambiguous lane
>
> The existing generic ambiguous lane's bounded permeability must not destroy a unique exact occurrence.
>
> Implement/test:
>
> ```text
> ambiguous token, exact df == 1
>     → unique literal match MUST rank 1
>     → semantic candidates may fill afterward
>
> ambiguous token, exact df > 1
>     → literal and semantic candidates both available
>     → ambiguous-lane ranking may interleave them
>
> ambiguous token, exact df == 0
>     → semantic candidates may be returned
>     → none may masquerade as an exact/literal hit
> ```
>
> This use of df is **ranking behavior after ambiguity has been established**, not intent classification.
>
> ### 3. Development experiment before shard02
>
> C1–C7 are exposed and may now be used for development/regression.
>
> Build an ALLCAPS ambiguity development set containing both:
>
> ```text
> conventional acronyms/terms:
> VPN
> API
> GPU
> JSON
> HTTP
> ```
>
> and real opaque acronym-like tokens from the existing acceptance/regression sets, including BTRFS-class cases.
>
> Compare:
>
> ```text
> A. ALLCAPS → identifier
> B. existing ambiguous lane
> C. ambiguous lane + df=1 exact singleton pin
> D. justified stronger literal weighting, only if C is insufficient
> ```
>
> Measure:
>
> ```text
> opaque/unique cases:
>     literal R@1
>     literal Recall@K
>
> conventional cases:
>     judged any@3
>     nDCG@3
>     exact/literal discoverability
> ```
>
> Select by joint performance.
>
> Do not accept the previously measured 0.94 exact regression merely as the cost of ambiguity if a ranking-policy change can preserve both properties.
>
> ### 4. Benchmark protocol v1.1
>
> After selecting the policy, amend the benchmark taxonomy before consuming another sealed shard.
>
> Do **not** gate an internal route label such as:
>
> ```text
> VPN must route semantic
> ```
>
> Gate observable behavior.
>
> Add an `ambiguous_allcaps` stratum with requirements representing both sides:
>
> ```text
> unique literal ALLCAPS:
>     exact occurrence R@1 = 1.0
>
> conventional ALLCAPS:
>     judged semantic relevance meets preregistered threshold
>     literal evidence remains discoverable
> ```
>
> Pure ALLCAPS tokens should no longer appear in a “strong identifier” stratum whose contract assumes automatic identifier intent.
>
> This is a taxonomy correction, not exclusion: ALLCAPS cases remain required acceptance cases.
>
> Preserve all other benchmark_protocol_v1 query-contract, validity, distribution, sealing, and judging rules.
>
> Commit protocol v1.1 and the next promotion preregistration **before consuming shard02**.
>
> ### 5. Next promotion gate
>
> Use sealed, currently unconsumed shard02 only after:
>
> 1. routing amendment is implemented;
> 2. exposed regressions pass;
> 3. protocol v1.1 is committed;
> 4. promotion gates are frozen.
>
> Run the full battery once.
>
> If every gate passes:
>
> * emit the promotion-authorized PASS receipt;
> * invoke the separate promoter;
> * verify generation 1 active;
> * verify incremental lag remains healthy;
> * emit the promotion receipt.
>
> **Do not invent another gate after a full pass.**
>
> If any gate fails, STOP and preserve shard02 as consumed evidence.
>
> ### 6. Preserve settled evidence
>
> C7 demonstrated that `benchmark_protocol_v1` fixed the authoring-variance problem: every judged stratum passed. Preserve that evidence.
>
> Do not modify:
>
> * BGE-M3 representations;
> * Qdrant storage;
> * EvidenceUnits;
> * comparison-specific sparse-heavy lane;
> * weak mixed-case ambiguous policy;
> * zero-literal exact semantics;
> * promotion/verdict separation;
> * exact-span reopenability;
> * incremental freshness;
> * reconnect behavior.
>
> The unresolved issue is only the semantics of bare pure-ALLCAPS tokens.

The mid-battery failed patch was valuable: it tells us **“make acronyms ambiguous” is directionally right but the current ambiguous ranking policy is not strong enough for unique literals**. That is much more actionable than either weakening the exact gate or hardcoding vocabulary knowledge.

And importantly, **shards 02/03 remain untouched**, so the evaluation design has finally reached the point where this contract adjustment can get a genuinely clean confirmation.
