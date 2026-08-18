Choose **(ii): comparison-targeted retrieval work**, with a narrow scope.

The evidence is now too consistent to justify simply lowering the gate:

* C3 comparison authored MRR ≈ **0.339**
* C4 comparison authored MRR ≈ **0.339**
* C5 judged any@3 = **0.70**
* In all three C5 misses, the relevant video is present and ranks first under simpler phrasing.

That is a reproducible **query-understanding/retrieval-policy weakness**, not noise and not an embedding/storage problem.

## What I would do

Treat comparison queries as their own semantic retrieval shape:

```text
"X versus Y"
"X vs Y"
"difference between X and Y"
"X compared with Y"
"should I use X or Y"
```

Instead of sending only the original comparison sentence through the normal semantic path, evaluate a structured comparison policy such as:

```text
original comparison query
        +
subquery for X
        +
subquery for Y
        +
optional "X Y comparison" normalized query
        ↓
candidate union
        ↓
existing BGE dense + learned sparse scoring
        ↓
comparison-aware fusion
```

The important constraint is that **both sides of the comparison should influence retrieval**. A generic “comparison” video should not outrank a video that actually contains evidence about the two named things merely because its language sounds contrastive.

### Development experiment

Use C3/C4/C5 comparison cases as development data now. Compare at least:

```text
A. current production query unchanged

B. normalized comparison query
   remove/discount generic comparison framing

C. decomposition:
   original + X + Y

D. decomposition:
   original + X + Y + "X Y"
   with candidate fusion
```

Also remeasure the sparse-heavier configuration **only on this comparison-specific development set**. The earlier global experiment did not justify changing fusion generally, but comparison queries may benefit differently.

Do not add a reranker yet.

## Metrics

For comparison queries, single-positive authored MRR has already proven too brittle. Use judged relevance.

Measure:

* **any relevant @3**
* **nDCG@3**
* optionally Recall@5
* coverage of **both comparison entities** among retrieved evidence

That last metric matters. A result about only X may be relevant but inadequate for answering “X vs Y.”

A useful comparison-specific property could be:

> At least one top-k result contains useful evidence about both sides, **or** the retrieved set collectively contains useful evidence for each side.

That better matches the eventual consumer need.

## C6

I would **not preregister `≥0.65` simply because 0.70 happened in C5**.

Instead:

1. Freeze C1–C5 as regression/development evidence.
2. Develop the comparison policy on those exposed cases.
3. Establish its measured development baseline.
4. Preregister C6 **before looking at C6**.
5. Make the C6 comparison stratum at least **20 cases**, preferably 30+ if the fresh corpus supports it.
6. Keep the other already-green production gates as regression/integrity gates.
7. Run C6 once.
8. If all gates pass, emit the C6 PASS receipt and use the separate promoter.

I would keep comparison as a **required production capability**, not best-effort. `/wiki`, `/www`, architecture review, and evidence analysis will frequently ask comparison-shaped questions; this is exactly the kind of query Evidence Fabric should handle well.

### Prompt to send the implementing LLM

> **C5 STOP accepted. Do not lower the comparison gate and do not declare comparison best-effort. Choose option (ii): bounded comparison-targeted retrieval work.**
>
> C5 has validated the rest of the production architecture. Generation 1 remains inactive.
>
> The comparison failure is now reproducible across C3/C4/C5 and is localized:
>
> ```text
> comparison phrasing → degraded retrieval
> plain phrasing       → relevant video often ranks first
> ```
>
> Treat this as a comparison-query retrieval-policy defect, not a vector/storage defect.
>
> ### 1. Preserve settled components
>
> Do not rebuild or replace:
>
> * EvidenceUnits/chunks
> * BGE-M3 dense vectors
> * BGE-M3 learned sparse vectors
> * Qdrant generation
> * ambiguous-token lane
> * exact/identifier behavior
> * promotion receipt separation
> * incremental indexing
>
> Preserve C1–C5 exactly as historical regression/development evidence.
>
> ### 2. Build a comparison-specific development set
>
> Use the exposed comparison cases from C3/C4/C5.
>
> Include forms such as:
>
> ```text
> X vs Y
> X versus Y
> X compared with Y
> difference between X and Y
> X or Y
> should I use X or Y
> ```
>
> Record the two comparison targets explicitly where reliably parseable.
>
> ### 3. Compare retrieval policies
>
> Evaluate at least:
>
> ```text
> A. current production query unchanged
>
> B. normalized comparison query
>    reducing generic comparison framing
>
> C. decomposition:
>    original query + X + Y
>
> D. decomposition:
>    original query + X + Y + compact "X Y" query
>    with candidate union/fusion
> ```
>
> You may also test a comparison-specific sparse-heavier fusion because this is now a bounded class-specific experiment.
>
> Do not change global fusion unless the evidence supports that.
>
> Do not add a reranker yet.
>
> ### 4. Judge comparison relevance properly
>
> Use human/operator judgments rather than authored-single-positive MRR alone.
>
> Measure at least:
>
> ```text
> any relevant @3
> nDCG@3
> Recall@5
> ```
>
> Also measure comparison coverage:
>
> ```text
> either:
>   one retrieved result contains useful evidence about both X and Y
>
> or:
>   the top-k result set collectively contains useful evidence for both sides
> ```
>
> A generic “comparison” result that does not substantively address the named entities should not receive full relevance credit.
>
> ### 5. Select by development evidence
>
> Choose the smallest policy that materially improves comparison retrieval without regressing the already-green semantic classes.
>
> Run C1–C5 regression suites after implementation.
>
> ### 6. C6 preregistration
>
> Only after the comparison policy is selected:
>
> * define C6 comparison metrics and thresholds from the development evidence;
> * use at least 20 fresh comparison cases, preferably 30+;
> * commit the preregistration before constructing/revealing C6;
> * use a genuinely untouched corpus region.
>
> Do not set the C6 threshold merely to fit C5's observed 0.70.
>
> ### 7. C6 promotion
>
> Run the full production battery once.
>
> If every preregistered C6 gate passes:
>
> 1. emit the promotion-authorized PASS receipt;
> 2. invoke the separate `ef.promote` command;
> 3. verify generation 1 is active;
> 4. verify freshness lag remains healthy;
> 5. emit the promotion receipt.
>
> If any C6 gate fails, STOP and return discriminating evidence.
>
> No post-hoc threshold changes.

At **25/26 gates**, I would resist the temptation to weaken the last one. The remaining failure has a coherent, reproducible cause and appears amenable to a targeted retrieval improvement. This is exactly the point where one more bounded development cycle has high expected value.
