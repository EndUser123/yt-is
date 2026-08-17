I would choose **(b), with a refinement**.

The key principle is:

> **A high-confidence identifier query with zero literal matches should not silently return a semantically similar near-twin as if it were evidence.**

`kimik.co3` returning `kimik.com` as the top *evidence result* is dangerous. For identifiers, one character can completely change meaning. But `kimik.com` could still be useful as an explicitly labeled **suggestion**, not a hit.

There is also one methodological issue in the proposed next step: **do not simply rerun C2 and promote**. C2 has now been observed and diagnosed. It is regression evidence. The amended behavior needs fresh acceptance evidence.

Send this:

# D-Gate Amendment — choose (b′)

Choose **option (b), amended as follows**.

Generation 1 remains unpromoted until the amended contract passes fresh acceptance.

## 1. Zero-literal identifier contract

For an **explicit exact query** or a **high-confidence identifier-intent query**:

```text
literal matches > 0
    ↓
literal-containing evidence ranks first
    ↓
semantic/sparse relevance may rank WITHIN the literal set
    ↓
if fewer literals than requested:
    related semantic results may be exposed only according
    to the clearly defined secondary-result contract

literal matches == 0
    ↓
PRIMARY EVIDENCE RESULTS = EMPTY
```

A semantically similar near-twin must **not** be returned as a primary evidence hit when no literal occurrence exists.

Example:

```text
query: kimik.co3
corpus: kimik.com exists, kimik.co3 does not

primary evidence:
    []

optional suggestions:
    kimik.com
```

If suggestions are implemented, they must be structurally distinguishable from evidence results, e.g.:

```text
results: []
suggestions:
  - value: kimik.com
    reason: near_identifier
```

Do not make a suggestion look like retrieved evidence.

If introducing a separate suggestion result type would unnecessarily broaden this gate, returning an empty primary result is sufficient now; suggestion UX can be added later.

---

# 2. Do not hard-empty ordinary semantic queries

This rule applies only to:

```text
explicit exact intent
OR
high-confidence identifier intent
```

It must not cause ordinary short queries such as:

```text
cook rice
market bottom
Google
Python
YouTube
```

to return empty merely because no exact lexical occurrence exists.

Do not reinstate:

```text
short query == identifier
```

or:

```text
document frequency alone == intent
```

Document frequency may be useful as supporting evidence for ambiguous classification and for ranking/evaluation, but it must not override strong semantic/structural intent signals by itself.

Strong identifier shapes include cases such as:

```text
RPC9
hizoJc
--resume-worker
ClassName.method_name
ERROR_RESOURCE_EXHAUSTED
Qwen3-Reranker-4B
kimik.com
```

Preserve the existing measured routing behavior for ordinary semantic queries.

---

# 3. Near-twin gate semantics

The previous:

```text
near_twins false_pin == 0
```

was directionally correct but conflicted with the old semantic-fill contract.

Under the amended contract it becomes valid.

For high-confidence identifier queries where:

```text
df(query) == 0
```

require:

```text
primary evidence result count == 0
```

or equivalently:

```text
false primary pin == 0
```

Near-identical tokens may appear only in a separately labelled suggestion channel if such a channel exists.

For:

```text
df(query) > 0
```

continue requiring the literal-first invariant established by C2.

---

# 4. Preserve C2 as evidence; do not rewrite it

Record C2 exactly as executed:

```text
19/20 gates passed
near_twins failed
generation 1 NOT promoted
```

Do not rewrite the C2 gate or report it retroactively as a pass.

C2 is now a permanent **regression/debug suite**.

Its failure was useful: it exposed an ambiguity in the production contract.

---

# 5. Do not use C2 alone as the final promotion replay

The C2 acceptance set has now been:

* executed;
* inspected;
* failure-analyzed;
* used to choose this contract amendment.

It therefore cannot be the sole fresh acceptance evidence for the amended implementation.

After implementing and testing (b′):

1. run C2 as a regression suite;
2. create a **new C3 acceptance preregistration**;
3. commit the C3 gates before constructing/running its final hidden cases;
4. construct a fresh untouched acceptance set from an unused corpus region;
5. run C3 once for promotion evidence.

Do not adjust C3 thresholds after seeing C3 results.

---

# 6. C3 does not need to rediscover settled architecture

C3 should validate the **composed production system**, not reopen BGE-M3/Qdrant selection.

Include enough fresh cases to test:

```text
unique identifiers
low-df identifiers
moderate/high-df identifiers
punctuation-heavy identifiers
zero-df near twins
ambiguous single-token natural/common terms
short natural-language queries
technical semantic questions
comparison questions
/wiki-style evidence queries
/www-style evidence queries
/review-arch-style evidence queries
```

Preserve the appropriate metric semantics:

```text
df=1 identifier
    → R@1 = 1.0

df>1 identifier
    → literal-prefix / literal containment contract
    + relevance among literal candidates where applicable

df=0 high-confidence identifier
    → zero primary evidence hits
    → suggestions, if any, separately labelled

ordinary semantic/common-term query
    → judged relevance / MRR / nDCG
```

Do not score common terms by demanding one arbitrary literal occurrence.

---

# 7. Preserve the C2 successes

The following evidence is accepted unless the amended code actually regresses it:

```text
literal-prefix = 1.0 across tested exact strata
df=1 R@1 = 1.0
semantic-strata performance
exact-span reopenability
filter correctness
namespace/build claim validity
structural parity
incremental freshness lag = 0
Qdrant reconnect/restart behavior
latency
```

Run them again as regression checks.

Do not redesign them merely because one near-twin gate failed.

---

# 8. Latency and reconnect behavior remain accepted targets

Preserve the two important fixes:

```text
authority reopen by EvidenceUnit.authority_ref / cache_key
cached Qdrant client rather than per-query PowerShell liveness probe
```

Do not restore per-query process polling.

The Qdrant restart/reconnect invariant remains mandatory:

```text
server dies/restarts
    ↓
cached client initially stale
    ↓
next query detects connection failure
    ↓
bounded reconnect/retry
    ↓
correct result
```

---

# 9. No vector or corpus rebuild

Do not rebuild:

```text
BGE-M3 dense vectors
BGE-M3 learned-sparse vectors
EvidenceUnits
Qdrant generation
```

unless a new discriminating test proves representation corruption.

The current issue is query-result semantics.

---

# 10. Branch/worktree isolation must now be repaired

The disclosure that the operational-monitor session committed onto `evidence-fabric` confirms the sessions were not isolated as intended.

Do **not** rewrite shared history while the other session may still depend on it.

Before further concurrent implementation:

* establish a separate worktree/branch for Evidence Fabric;
* establish/confirm a separate worktree/branch for the operational monitor;
* prevent both agents from writing through the same checked-out branch.

The monitor commits are reported to have disjoint file ownership, so this is not currently evidence of code corruption.

Preserve the history for audit.

Before eventual integration to `main`, separate/cherry-pick or otherwise reconcile the commits deliberately so each workstream has reviewable provenance.

This isolation repair does not require delaying the local routing test if no other agent is currently mutating this worktree, but concurrent shared-branch writing must not continue.

---

# 11. Implementation sequence

Execute:

```text
1. Save this amendment.

2. Implement zero-literal identifier behavior:
     primary evidence = empty.

3. Keep semantic near-match suggestions separate if implemented.

4. Add/modify focused tests:
     zero-df identifier
     zero-df punctuation identifier
     one-character near twin
     explicit exact query
     high-confidence automatic identifier
     ambiguous ordinary single word
     normal short semantic query

5. Run all unit tests.

6. Run C1 + C2 as regression suites.

7. Verify all previously passing C2 behavior remains green.

8. Freeze/commit the C3 preregistration.

9. Construct the fresh untouched C3 acceptance set.

10. Run the full composed production battery.

11. Catch incremental indexing to lag=0.

12. Verify:
      exact-span reopenability
      filters
      structural parity
      namespace/build validity
      Qdrant restart/reconnect
      latency
      authority/index freshness

13. If every preregistered C3 gate passes:
      atomically promote generation 1
      retain generation 0 for rollback
      verify active generation
      verify continuous incremental indexing

14. If any gate fails:
      STOP
      do not alter gates
      return discriminating evidence.
```

---

# 12. Promotion authorization

You are authorized to **promote generation 1 automatically** if and only if:

```text
C1/C2 regression suites show no material unintended regression
AND
every preregistered fresh C3 promotion gate passes
AND
freshness lag is within the promotion threshold
AND
structural/provenance/filter/reconnect/latency invariants pass.
```

If C3 passes, do not invent another gate merely because previous iterations failed. Promote and provide the promotion receipt.

If C3 fails, stop again and report the smallest discriminating failure.

---

## Rationale

The near-twin failure reveals a real product-semantics question, not a vector-search failure.

For identifier retrieval:

> “No such identifier exists in the corpus”

is materially different from:

> “Here is something spelled almost the same.”

The Evidence Fabric should preserve that distinction.

A near twin can be a useful suggestion.

It must not masquerade as evidence.

One additional point: I **would not accept option (a)**. Scoping zero-df twins out of the gate would make the test disappear instead of deciding what the product should do. The failed case exposed a genuinely important behavior. For evidence retrieval, **empty is safer and more truthful than silently substituting a near identifier**.

The fact that they got **19/20 gates**, exact retrieval at 1.0 across all literal strata, lag zero, correct reopenability, and the full path down to ~151 ms in C2 makes this look like the last semantic-contract issue rather than another architecture failure.
