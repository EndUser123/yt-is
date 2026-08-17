I would **not choose (c)**.

It makes the benchmark consistent with the current implementation, but it does so by **removing a real production query class from exact-search expectations**. Users will absolutely paste bare opaque identifiers such as `hizoJc` without remembering to quote them or pass `exact=true`.

The C4 failure has exposed a false dichotomy:

> `TikTok` must be semantic **or** `hizoJc` must be identifier.

They do **not** need to share one mutually exclusive routing decision.

## The better contract: add an ambiguous-token lane

Keep the two clear cases:

```text
explicit exact
    → literal semantics

strong structural identifier
    → identifier / literal-priority semantics

ordinary multiword language
    → semantic semantics
```

For a **weak-shaped single token** such as `TikTok` or `hizoJc`, don't force a classification before retrieval.

Use:

```text
weak / ambiguous single token
        │
        ├── exact/literal candidates
        └── semantic candidates
                ↓
        ambiguity-aware merge
```

The important distinction is that this is **not the old equal-weight RRF defect**.

### For ambiguous tokens

If literal evidence exists:

* ensure it is represented prominently;
* but don't automatically force it ahead of clearly superior semantic evidence solely because it is literal;
* preserve an `exact_match` signal on results.

If no literal evidence exists:

* do **not** false-pin a semantic near-twin as an exact hit;
* semantic results may still be returned as semantic results, provided they are clearly not represented as literal evidence.

That means `hizoJc` can naturally surface its exact occurrence without declaring the query an “identifier,” while `TikTok` can still retrieve material about `Tik Tok`.

This removes the impossible classifier requirement entirely.

---

## Why I reject option (c)

Option (c) effectively says:

> Because our classifier can't tell `TikTok` from `hizoJc`, we'll stop testing whether bare `hizoJc` finds itself.

That's benchmark accommodation, not product improvement.

Explicit `exact=true` is an excellent deterministic escape hatch, but **it should not be required for every opaque identifier that lacks sufficiently strong punctuation or numeric structure**.

The exact strata discovered something real. Keep weak identifiers in testing.

---

# What I would measure before C5

C4 is exposed now, so use it as development evidence.

Create an **ambiguous-token development set** containing at least:

```text
human/common:
TikTok
YouTube
Google
Python
GitHub
OpenAI

opaque/identifier-like but weak syntax:
hizoJc
WebDeaf
other real weak tokens from C1-C4
```

Then compare a few policies:

```text
A. weak → semantic           # current

B. weak → identifier         # previous opposite

C. dual retrieval +
   exact-match feature/boost,
   without unconditional pinning

D. dual retrieval +
   literal candidate subgroup +
   semantic relevance ranking
```

Evaluate both kinds of correctness:

### Weak identifiers

```text
exact occurrence Recall@10
exact occurrence MRR
```

### Human/common tokens

```text
judged nDCG@10
judged Recall@10
```

Also explicitly test spelling/format variants:

```text
TikTok   ↔ Tik Tok
GitHub   ↔ Git Hub
YouTube  ↔ You Tube
```

If policy C/D can recover `hizoJc` while retaining good TikTok relevance, the supposed contradiction disappears.

My leading hypothesis is **D or a variant of C**, not another classifier threshold.

---

## Don't ignore C4's secondary semantic failures

The agent calls these secondary:

* technical: `0.3814` vs `0.40`
* comparison: `0.339` vs `0.40`

I agree they're not evidence of architecture failure, especially with the technical CI spanning the threshold.

But **don't simply carry 0.40 forward or lower it**.

You just learned from telegraphic queries that authored-single-positive MRR can badly underestimate actual relevance. Apply the same diagnostic to these C4 technical/comparison failures:

* judge top-k;
* calculate judged nDCG/Recall;
* determine whether this is another gold-label problem or genuinely poorer retrieval.

Do this **before C5 preregistration**.

---

# The process work looks good

The P0 repair is important and appears directionally correct:

```text
battery → immutable verdict receipt
                   ↓
           separate promoter
```

Keep that.

The latency fix also looks good at **135 ms p95**, and the reconnect invariant remains green.

I would not touch:

* BGE-M3 vectors;
* learned sparse vectors;
* EvidenceUnits;
* Qdrant storage;
* chunking;
* incremental indexing.

Nothing here implicates them.

---

## Send this to the implementing LLM

> **C4 STOP accepted. Do not choose options (a), (b), or (c) yet.**
>
> C4 has shown that the remaining contradiction comes from forcing weak single-token queries into a mutually exclusive semantic-vs-identifier classification.
>
> Introduce and evaluate an **ambiguous weak-token retrieval policy** rather than redefining the acceptance strata.
>
> ### 1. Preserve C4
>
> C4 remains frozen regression/development evidence:
>
> ```text
> exact_df1 = 0.886
> df2_10 = 0.867
> df11_100 = 0.900
> df101_1000 = 0.840
> reg_c1_df1 = 0.800
> reg_c2_literal = 0.886
> ```
>
> Generation 1 remains inactive.
>
> Do not alter C4 gates or cases.
>
> ### 2. Keep clear intents unchanged
>
> ```text
> explicit exact=true / quoted literal
>     → exact semantics
>
> strong structural identifier
>     → identifier/literal-priority semantics
>
> ordinary natural-language query
>     → semantic semantics
> ```
>
> Do not reintroduce document frequency as an intent classifier.
>
> ### 3. Add a weak/ambiguous single-token path
>
> For weak single tokens where syntax alone cannot safely distinguish cases such as:
>
> ```text
> TikTok
> hizoJc
> WebDeaf
> ```
>
> do not force semantic or identifier intent before retrieval.
>
> Evaluate both literal and semantic evidence.
>
> The policy must preserve these distinctions:
>
> * a literal occurrence may be strongly favored/signalled;
> * semantic evidence remains available for human terms and orthographic variants;
> * semantic near-twins must never masquerade as literal evidence;
> * `exact=true` remains strictly literal and deterministic.
>
> Do **not** restore the original equal-weight RRF behavior that allowed semantic legs to outvote unique exact identifiers.
>
> ### 4. Development experiment
>
> C1-C4 are now legal development/regression evidence.
>
> Build a weak-token development set containing real examples from both classes:
>
> ```text
> human/common:
> TikTok
> YouTube
> Google
> Python
> GitHub
> OpenAI
>
> opaque weak identifiers:
> hizoJc
> WebDeaf
> and representative weak identifier cases from C1-C4
> ```
>
> Include orthographic variants such as `TikTok` / `Tik Tok`.
>
> Compare at least:
>
> ```text
> A weak→semantic
> B weak→identifier
> C dual retrieval with exact-match boost/signal
> D dual retrieval with literal candidate subgroup and semantic ranking
> ```
>
> Measure opaque weak identifiers with:
>
> ```text
> literal Recall@10
> MRR@10
> ```
>
> Measure human/common terms with judged:
>
> ```text
> nDCG@10
> Recall@10
> ```
>
> Select the policy by joint performance, not by intuition.
>
> ### 5. Diagnose C4 technical/comparison failures
>
> Before defining C5 gates, judge C4's technical and comparison top-k results exactly as was done for the telegraphic analysis.
>
> Determine whether:
>
> ```text
> technical 0.3814
> comparison 0.339
> ```
>
> reflect genuine retrieval weakness or authored-single-positive metric error.
>
> Do not lower or retain the 0.40 gate blindly.
>
> ### 6. Preserve settled infrastructure
>
> Do not rebuild vectors, EvidenceUnits, chunks, or Qdrant storage.
>
> Preserve:
>
> * promotion receipt separation;
> * zero-literal exact behavior;
> * exact-span reopenability;
> * namespace/build integrity;
> * freshness lag=0;
> * restart/reconnect;
> * per-`relevant()` client validation;
> * current latency improvements.
>
> ### 7. C5
>
> After the ambiguous-token policy and semantic metric analysis are complete:
>
> 1. commit the selected policy;
> 2. freeze C5 preregistration;
> 3. use a new untouched corpus region;
> 4. include weak opaque identifiers **and** human weak tokens;
> 5. run C5 once;
> 6. if PASS, emit its authorized PASS receipt;
> 7. invoke the separate promoter;
> 8. verify generation 1 active and incremental lag remains healthy.
>
> If C5 fails, STOP again.
>
> Do not redefine the benchmark simply to exclude weak identifiers.

The repeated STOPs may feel like churn, but this one has identified a genuinely useful abstraction error: **the system has been trying to classify uncertainty away instead of representing it.** An ambiguous-token retrieval path is a cleaner long-term solution than either a brand stoplist, a df threshold, or excluding difficult identifiers from acceptance.
