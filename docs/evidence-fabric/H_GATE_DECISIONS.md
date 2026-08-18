The correct move is a **fourth option: fix the benchmark protocol, not the retrieval system and not the threshold**.

C6 passed all 22 non-judged gates, including the comparison tripwire, while the freshly authored judged strata dropped sharply.  More importantly, the fixed-query regressions remained stable and the new comparison lane improved the previously exposed C5 comparison cases.  That is strong evidence that the fresh-set generation process has become a material nuisance variable.

But I would **not use fixed anchors as promotion gates**. Repeatedly tuning against them makes them development data.

## Choose option (d): three-tier evaluation

```text
1. FIXED REGRESSION ANCHORS
   C1-C6 + other exposed cases
   → always run
   → detect regressions
   → never independently authorize promotion

2. FRESH CONTROLLED PROMOTION HOLDOUT
   authored under a frozen query-spec protocol
   → untouched
   → promotion authority

3. FRESH CHALLENGE/DISCOVERY SET
   deliberately weird/hard/unconstrained
   → finds new weaknesses
   → informational, not promotion-blocking
```

This takes the useful parts of (a) and (c) without turning C5 or another repeatedly observed benchmark into the thing the system optimizes against.

### The key addition: validate the query before retrieval

The C6 examples are not merely “short.” Some appear to depend on information the author knows from the source:

* `free api key week offer`
* `saas build series part seven`
* `gcp cert practice question five`

A benchmark should not require the retriever to infer hidden author context.

Before a promotion query is sealed, record:

```text
query_text
intended_information_need
consumer_class
query_style
required concepts/entities
relevance criteria
```

Then have a **blind query-validity check before any retrieval occurs**:

> Given only `query_text` and its stated information need—not the originating transcript—does the query itself contain enough information for a reasonable retriever to identify what is being sought?

Reject only queries that depend on hidden source context.

Crucially, **do not reject queries merely for being terse**. `python async sqlite locking` can be terse and perfectly recoverable.

---

## Keep telegraphic queries as a real product stratum

I would not conclude from C6 that terse queries don't matter. Humans absolutely issue them.

Split them:

```text
telegraphic-but-sufficient
    → required promotion stratum

context-deficient / source-dependent
    → invalid benchmark case

hard but valid
    → remains in benchmark even if retrieval struggles
```

That distinction is much better than controlling query length to resemble C5.

---

## Make this durable now

Instead of manually inventing C7, C8, C9 one batch at a time, build a **versioned evaluation bank** under this protocol.

For example:

```text
EF benchmark protocol v1
        │
        ├── exposed-development
        │
        ├── stable-regression
        │
        ├── sealed-promotion-shard-01
        │
        ├── sealed-promotion-shard-02
        │
        ├── sealed-promotion-shard-03
        │
        └── challenge/discovery
```

Seal several promotion shards **before seeing system results**.

Then future failures do not require another round of subjective query authoring. You consume the next untouched shard.

Eventually, actual yt-is query telemetry can replace part of this synthetic distribution with empirical consumer behavior.

---

# What I would do now

**Do not modify the comparison lane.** Its specific development evidence is positive, its tripwire passed, and fixed-set comparison performance improved. 

**Do not lower the gates because C6 scored poorly.**

**Do not promote from C5 anchors.**

Create benchmark protocol v1, produce C7 under it, and make C7 the final promotion gate.

If C7 passes: **promote. No C8.**

If a valid, properly authored C7 query class genuinely fails, then you finally have evidence of a product retrieval weakness rather than benchmark noise.

## Prompt for the implementing LLM

> **C6 STOP accepted. Choose neither (a), (b), nor (c) as written. Implement option (d): a durable three-tier benchmark protocol. Do not change the retrieval system based on C6 judged failures yet. Generation 1 remains inactive.**
>
> ### 1. Preserve the comparison implementation
>
> The comparison lane has positive discriminating evidence:
>
> * comparison-specific sparse-heavy policy won development;
> * exposed C5 misses improved materially;
> * fixed regressions remain stable;
> * C6 comparison authored tripwire passed;
> * structural/system gates remain green.
>
> Do not retune fusion or rebuild representations.
>
> ### 2. Reclassify existing evidence
>
> C1-C6 are all exposed.
>
> They are now:
>
> ```text
> regression/development evidence
> ```
>
> They may detect regressions and guide development but may not independently authorize promotion.
>
> ### 3. Create `benchmark_protocol_v1`
>
> Define three roles:
>
> ```text
> A. stable regression anchors
> B. untouched promotion holdouts
> C. challenge/discovery cases
> ```
>
> Promotion requires B.
>
> A never becomes promotion evidence merely because it is stable.
>
> C may discover weaknesses but is informational unless a finding is explicitly promoted into a future product requirement before the next untouched gate.
>
> ### 4. Define a query contract
>
> Every judged promotion query must contain, before retrieval:
>
> ```text
> query_text
> intended_information_need
> consumer_class
> query_style
> required concepts/entities
> relevance criteria
> ```
>
> Include consumer classes relevant to Evidence Fabric:
>
> ```text
> yt-is direct
> /wiki
> /www
> /review-arch
> ```
>
> Include styles:
>
> ```text
> descriptive semantic
> telegraphic-but-sufficient
> technical
> comparison
> ambiguous/common term
> ```
>
> ### 5. Add a blind pre-retrieval query-validity gate
>
> Before constructing retrieval results, independently validate:
>
> > Does `query_text`, by itself, express enough information to recover the stated information need without knowing the originating transcript?
>
> Validation must not inspect retrieval results.
>
> Do not reject a query merely for being short.
>
> Reject/replace queries that depend on hidden source context.
>
> Examples such as:
>
> ```text
> "part seven"
> "practice question five"
> ```
>
> are invalid if the intended target cannot reasonably be inferred without source knowledge.
>
> A terse but independently meaningful query remains valid.
>
> Record every pre-retrieval rejection and reason.
>
> ### 6. Freeze the distribution
>
> Do not simply match C5's observed lengths.
>
> Define the intended proxy distribution by consumer and query style before C7.
>
> Use fixed proportions so future holdouts measure the same product target.
>
> Document the rationale.
>
> ### 7. Build a reusable sealed benchmark bank
>
> Prefer creating multiple untouched promotion shards now under the same frozen protocol rather than manually re-authoring after every future failure.
>
> Conceptually:
>
> ```text
> development/exposed
> regression
> promotion_shard_01
> promotion_shard_02
> promotion_shard_03
> challenge
> ```
>
> Hash/seal promotion shards and do not inspect their retrieval outcomes until consumed.
>
> C7 consumes `promotion_shard_01`.
>
> ### 8. C7 preregistration
>
> Commit before running C7:
>
> * query protocol/version;
> * stratum proportions;
> * validity procedure;
> * relevance-judgment procedure;
> * judged metrics;
> * thresholds;
> * all existing structural/integrity gates.
>
> Do not derive thresholds from C7.
>
> Keep genuinely useful telegraphic queries as a required stratum.
>
> ### 9. C7 execution
>
> Run:
>
> ```text
> stable regression anchors
> +
> untouched C7 promotion shard
> +
> structural/integrity/freshness/reconnect/latency gates
> ```
>
> Challenge/discovery cases may run separately and must be clearly labeled informational.
>
> ### 10. Terminal rule
>
> If all preregistered C7 promotion gates pass:
>
> 1. emit the authorized PASS receipt;
> 2. invoke the separate promotion command;
> 3. atomically promote generation 1;
> 4. verify active generation = 1;
> 5. verify incremental lag remains healthy;
> 6. emit promotion receipt.
>
> **Do not invent C8 after a C7 pass.**
>
> If C7 fails on a valid query stratum, STOP with discriminating evidence.
>
> Do not change C7 gates or reuse the consumed shard as fresh evidence.
>
> ### 11. No architecture churn
>
> Do not rebuild vectors, chunks, EvidenceUnits, Qdrant storage, ambiguous routing, or the comparison lane absent new discriminating evidence.

This also addresses the STOP packet's own conclusion that identical system behavior produced judged results around **0.83 versus 0.63** as authoring style changed.  The answer is not to remove fresh evidence; it is to make **fresh evidence come from a stable experimental protocol**.
