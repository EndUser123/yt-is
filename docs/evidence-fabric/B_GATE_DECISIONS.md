# Evidence Fabric — B-Gate Decision and Authorization

The Phase A-0/A/B work is accepted **subject to the following B-gate decisions**.

Stop remains in force before full-corpus backfill.

## Decision 1 — Dense model: NO post-hoc override; run one confirmatory B.1

Do **not** select MiniLM merely because the original rule says so, and do **not** override the preregistration to select BGE-M3 after seeing the results.

Record the original experiment exactly as it occurred:

```text
Original preregistered decision:
MiniLM retained.

Observed new evidence:
BGE-M3 materially outperformed MiniLM on the hand-authored
question tier (+0.062 nDCG) and substantially improved MRR,
while remaining operationally affordable.

Interpretation:
The preregistered decision criterion may overweight a query
shape that is not representative of the intended production workload.
```

That is evidence for a **new confirmatory experiment**, not permission to rewrite the old experiment.

### B.1 experiment

Freeze the existing benchmark.

Create a **new holdout set that has not been scored against any candidate model**.

Its query distribution must represent intended Evidence Fabric consumers, including:

```text
yt-is natural-language search
/wiki evidence discovery
/wiki contradiction/staleness
/www prior evidence and disconfirmation
/review-arch implementation/precedent questions
exact technical identifiers
title/entity lookups where actually useful
```

Do not choose weights by looking at model performance.

Derive them from expected production use and record them before evaluation.

Compare only:

```text
MiniLM
BGE-M3
```

Qwen3-4B is rejected unless new evidence gives a specific reason to reopen it. It currently has no demonstrated quality/compute advantage.

### B.1 selection rule

Before running B.1, preregister a rule that evaluates:

```text
weighted production-query metric
critical-stratum regressions
candidate Recall@K
nDCG@10
MRR
indexing throughput
VRAM/storage
```

If BGE-M3 wins the representative production workload by a material, statistically credible margin without a critical regression, promote BGE-M3.

If the difference disappears on untouched data, retain MiniLM.

### Additional BGE-M3 probe

Because BGE-M3 can produce multiple retrieval representations, include a bounded comparison of:

```text
A. MiniLM dense + FTS5 BM25

B. BGE-M3 dense + FTS5 BM25

C. BGE-M3 dense + BGE-M3 learned sparse

D. BGE-M3 dense
   + BGE-M3 learned sparse
   + FTS5 exact/BM25
```

Do not assume learned sparse should replace FTS5.

Exact technical identifiers remain an explicit acceptance stratum.

The objective is to determine whether BGE-M3 provides additional useful candidate evidence beyond its dense vector.

---

# Decision 2 — Projection engine: Qdrant LOCAL is rejected; Qdrant SERVER is not

Accept this measured conclusion:

```text
Qdrant local mode:
REJECT for production corpus retrieval.

Observed:
9.7 s p95 at 154,719 points.
```

Do **not** generalize that to:

```text
Qdrant:
REJECT.
```

The local implementation and production Qdrant server have materially different retrieval behavior at scale.

The FAISS HNSW + FTS5 result:

```text
204 ms p95 end-to-end
```

is sufficiently strong that it becomes a first-class architecture candidate.

Run one controlled projection-engine bakeoff before Phase C commits the architecture.

## Required candidates

```text
A. FAISS HNSW + FTS5

B. Qdrant server using HNSW
   + required payload indexes
   + equivalent retrieval configuration
```

Use:

```text
same evidence points
same vectors
same query corpus
same machine
same top-K
same warmup procedure
same concurrency
```

## Measure more than latency

Compare:

```text
retrieval quality / Recall@K
p50 / p95 / p99 latency
RAM
disk
build time
incremental add/update behavior
delete/tombstone behavior
metadata-filtered retrieval
authority/source filtering
concurrent readers
concurrent indexing/build behavior
generation rebuild behavior
startup/recovery
operational failure modes
```

Also test relevant corpus growth, not merely 154,719 points if a larger representative projection can be produced cheaply.

## Decision rule

Choose the architecture with the highest long-term useful capability and measured ROI.

Do not favor Qdrant because it was named in the design.

Do not favor FAISS because it avoids a server.

Transition effort and implementation complexity are not decision penalties.

If FAISS+FTS5 remains materially faster while satisfying the Evidence Fabric's filtering, generation, update, concurrency, and future multimodal requirements, adopt it.

If Qdrant server closes the latency gap sufficiently and materially improves required filtering/index-management functionality, retain Qdrant.

The Evidence Fabric contracts must remain independent of this choice.

---

# Decision 3 — The 7,110 records: distinguish missing metadata from missing provenance

Do **not** automatically skip all 7,110 transcripts.

First classify the defect.

## Case A — canonical source identity survives

If the row has sufficient identity to:

```text
identify the authoritative transcript
reopen the exact original transcript
verify its content/hash
associate it with its canonical video/source ID
```

then missing:

```text
title
channel
```

is **metadata incompleteness, not provenance failure**.

For these records:

```text
INDEX = YES
title = null/missing
channel = null/missing
metadata_state = incomplete
```

Never fabricate values.

Schedule deterministic metadata recovery independently where an authoritative source exists.

Retrieval/indexing must not wait for cosmetic/display metadata if source provenance is valid.

## Case B — canonical authority cannot be established

If a transcript cannot be mapped reliably back to its authoritative source:

```text
INDEX = NO
state = provenance_unresolved
```

Quarantine it for recovery.

Never invent provenance.

### Report separately

Return counts for:

```text
missing title only
missing channel only
missing both
missing canonical video/source identity
unable to reopen source
other provenance defect
```

The number 7,110 should not remain one undifferentiated bucket.

---

# Decision 4 — Preserve the char-offset provenance result

Accept the discovered fact that current authoritative transcripts contain no usable timestamps.

For the current text Evidence Fabric:

```text
canonical provenance coordinate = character offset/span
```

The demonstrated exact-span reopen is the important acceptance property.

Do not fabricate timestamp provenance.

If timestamp-bearing transcript data becomes available later, add temporal coordinates as another representation of provenance without changing the logical evidence identity unnecessarily.

---

# Decision 5 — Do not bulk-index yet

Authorization is extended only through **B.1 and the projection-engine bakeoff**.

Do not start full-corpus embedding/backfill.

The next gate packet must contain:

1. untouched B.1 holdout definition and preregistration;
2. MiniLM vs BGE-M3 confirmatory results;
3. BGE-M3 learned-sparse probe results;
4. Qdrant-server HNSW vs FAISS-HNSW+FTS5 results;
5. metadata/provenance-gap classification of the 7,110 records;
6. resulting recommended canonical dense model;
7. resulting recommended projection architecture;
8. any required changes to Evidence Fabric contracts;
9. receipts for each claim;
10. explicit evidence that the sibling transcript-fetch pipeline remained unaffected.

## Preserve existing successful work

Do not redesign Phase A-0/A contracts merely because these technology decisions remain open.

The following findings are accepted unless new contrary evidence appears:

```text
transcripts.sqlite is transcript authority
stable EvidenceUnit contract
char-offset exact-span provenance
Evidence Catalog
index off ingestion critical path
hybrid retrieval contract
EvidenceResult reopenability
projection/backend abstraction
authority != relevance invariant
```

## Stop boundary

After B.1 and projection-engine comparison:

**STOP before full backfill and return the gate packet.**

Do not resolve an ambiguous result by silently choosing an implementation.

If evidence falsifies a design assumption, report it and recommend changing the design.
