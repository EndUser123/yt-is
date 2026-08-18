Choose **(ii)**, and make it a **general protocol correction**, not a Node.js exception.

C8 has shown that the remaining failure is again caused by gating an **internal routing label** rather than a user-visible retrieval property. `Node.js` routing through the strong dotted-name path is not itself a defect if:

* relevant evidence is retrieved,
* literal evidence remains discoverable,
* ranking quality is good,
* exact mode remains deterministic,
* no false exact claim is made.

The fact that six of seven conventional terms passed and `Node.js` failed only because of the route classification is strong evidence the gate is testing implementation taxonomy rather than product behavior.

## Do not choose option (i)

I would reject:

> `.js/.py/.ai/.io` → ambiguous

That starts a vocabulary/suffix patch cycle:

```text
Node.js
OpenAI
.NET
C++
R
Go
Deno
CUDA
npm
...
```

There is no clean syntactic taxonomy that will encode “conventional technology name” correctly.

We have now rediscovered this several times with:

```text
TikTok vs hizoJc
VPN vs BTRFS
Node.js vs ClassName.method
```

That is enough evidence to stop trying to make route classification itself externally correct.

## The durable contract

Routes are implementation mechanisms.

**Acceptance should constrain behavior.**

For ambiguous/conventional technology terms, regardless of which internal route executes:

```text
judged relevance       → must pass
literal discoverability → must pass when literals exist
no false exact labeling → must hold
exact=true              → deterministic literal semantics
```

For genuine unique identifiers:

```text
unique literal R@1 = 1.0
```

Those are observable properties that matter.

Whether `Node.js` internally traverses `identifier`, `ambiguous`, or some future optimized lane is not something a production acceptance gate should care about unless that route itself carries user-visible semantics.

---

# One important consequence

Do **not** merely edit the single `Node.js` expectation.

Audit the whole benchmark protocol for assertions like:

```text
X must route semantic
Y must route ambiguous
Z must route identifier
```

Classify them into two groups:

### Keep route assertions only where routing is itself contractual

For example, perhaps:

```text
exact=true → exact route/semantics
```

because the caller explicitly requested that mode.

### Replace inferred-intent route assertions with behavioral gates

For automatically classified input:

```text
routing implementation
    ≠
product acceptance criterion
```

This prevents C9 from failing on the next taxonomy edge.

---

# C8 remains a legitimate STOP

Do not retroactively pass it.

Record:

```text
C8:
  behavioral retrieval properties: green
  preregistered route-label gate: failed
  verdict: FAIL
  generation 1 inactive
```

C8 is now exposed regression evidence.

Then fix the **protocol**, not C8.

---

# Before consuming shard03

I would do one extra thing because you are down to the last currently sealed shard:

**create and seal additional untouched promotion shards under the corrected protocol before running C9.**

For example:

```text
protocol_v1.2
    ├── shard03   existing untouched
    ├── shard04   newly generated + sealed
    ├── shard05   newly generated + sealed
    └── challenge
```

Do this before seeing any C9 retrieval results.

That prevents another genuine discovery from forcing fresh authoring after the result is known.

Shard03 can still be the next promotion shard if its query construction was compatible with the behavioral protocol; don't rewrite its cases.

---

## Prompt for the implementing LLM

> **C8 STOP accepted. Choose option (ii), generalized: automatic route labels are implementation details and must not be promotion gates unless the route itself is explicitly caller-selected.**
>
> Generation 1 remains inactive. Do not modify C8 or reinterpret it as PASS.
>
> ### 1. Preserve the i′ implementation
>
> C8 successfully validated:
>
> ```text
> ALLCAPS ambiguous classification
> df=1 singleton exact pin
> ambiguous_allcaps_df1 R@1 = 1.0
> conventional ALLCAPS literal discoverability = 1.0
> all exact/literal strata = 1.0
> weak ambiguous lane
> zero-df behavior
> twins
> comparison lane
> latency
> reopenability
> filters
> namespace/build integrity
> freshness lag=0
> reconnect
> ```
>
> Do not change these components because `Node.js` chose a different internal route.
>
> ### 2. Do not add suffix heuristics
>
> Reject rules such as:
>
> ```text
> .js/.py/.ai/.io → ambiguous
> ```
>
> Do not add product-name stoplists or corpus-specific lexical exceptions.
>
> The sequence:
>
> ```text
> TikTok vs hizoJc
> VPN vs BTRFS
> Node.js vs ClassName.method
> ```
>
> demonstrates that syntax alone cannot cleanly encode human conventionality.
>
> Stop trying to make automatic route labels equal semantic truth.
>
> ### 3. Protocol v1.2: gate behavior, not inferred route labels
>
> Audit the benchmark protocol and preregistration for assertions of the form:
>
> ```text
> <automatically classified query> must route semantic
> <query> must route ambiguous
> <query> must route identifier
> ```
>
> For automatically inferred intent, replace those gates with observable behavioral requirements.
>
> Keep route assertions only where the caller explicitly selects semantics, e.g.:
>
> ```text
> exact=true
> quoted literal if contractually exact
> ```
>
> ### 4. Behavioral contracts
>
> For conventional/ambiguous technology terms such as:
>
> ```text
> Node.js
> VPN
> TikTok
> YouTube
> MongoDB
> GraphQL
> ```
>
> gate:
>
> ```text
> judged relevance ≥ preregistered threshold
> literal evidence discoverable when literals exist
> semantic variants may be retrieved
> semantic evidence must never be mislabeled as exact
> ```
>
> Do not gate the internal route name.
>
> For unique literal/identifier cases:
>
> ```text
> df=1 literal R@1 = 1.0
> ```
>
> For multi-hit literal identifiers:
>
> preserve the existing literal-prefix/discoverability contracts.
>
> For `exact=true`:
>
> preserve deterministic literal-only semantics.
>
> ### 5. Verify Node.js behavior on exposed data
>
> C8 is now development/regression evidence.
>
> Measure `Node.js` and representative dotted conventional names under the current implementation:
>
> ```text
> judged any@3 / nDCG
> literal discoverability
> exact-match labeling correctness
> ```
>
> If those behavioral properties pass, make **no retrieval-code change**.
>
> If they fail, report the actual behavioral defect before changing routing.
>
> ### 6. Preserve C8 as FAIL
>
> C8 remains:
>
> ```text
> preregistered verdict = FAIL
> reason = route-label gate mismatch
> generation 1 = inactive
> ```
>
> Do not modify its gate or receipt.
>
> ### 7. Freeze protocol v1.2 before next promotion run
>
> Commit:
>
> * removal/replacement of non-contractual automatic-route gates;
> * behavioral replacement gates;
> * unchanged query-authoring/validity protocol;
> * unchanged judging procedure;
> * next promotion thresholds.
>
> Do this before consuming shard03.
>
> ### 8. Replenish the sealed holdout bank before C9
>
> Because shard03 is currently the last unconsumed sealed shard, create additional untouched promotion shards under protocol v1.2 **before any C9 retrieval results are observed**.
>
> Prefer:
>
> ```text
> shard03
> shard04
> shard05
> ```
>
> Seal/hash them and record their protocol version.
>
> Do not inspect retrieval outputs.
>
> This is evaluation-infrastructure work, not another product gate.
>
> ### 9. C9
>
> Consume shard03 once under protocol v1.2.
>
> Run:
>
> ```text
> exposed regression anchors
> +
> untouched promotion shard03
> +
> judged behavioral gates
> +
> exact/literal invariants
> +
> structural/integrity/freshness/reconnect/latency gates
> ```
>
> If every preregistered gate passes:
>
> 1. emit the promotion-authorized PASS receipt;
> 2. invoke the separate promoter;
> 3. atomically activate generation 1;
> 4. verify `active_generation == 1`;
> 5. verify incremental lag remains healthy;
> 6. emit the promotion receipt.
>
> **If C9 fully passes, promote. Do not invent C10.**
>
> If C9 fails a genuine behavioral gate, STOP and preserve shard03 as consumed evidence.
>
> ### 10. No architecture churn
>
> Do not change vectors, chunks, EvidenceUnits, Qdrant storage, comparison fusion, or ambiguous-lane implementation without behavioral evidence requiring it.

The broader lesson from C8 is useful: **the retrieval system is allowed to have internal heuristics; the acceptance system should care whether those heuristics produce the right observable behavior.** That should eliminate this recurring syntax-taxonomy loop rather than moving it from ALLCAPS to dotted names to whatever notation comes next.
