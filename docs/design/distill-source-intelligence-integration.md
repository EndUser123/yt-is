# Design: `/distill-source` and the yt-is intelligence layer

Status: implemented transport boundary; semantic-recall and ranking gates remain
separate acceptance work.

## Decision

`/distill-source` remains the source-faithful authoring procedure. yt-is owns
source acquisition, provider routing, Evidence Fabric persistence, and typed
personal-graph inference. The integration point is a grounded-source artifact,
not a second intelligence layer and not a Markdown parser.

```text
source acquisition/provider
  -> GroundedSourceArtifact
  -> evidence clusters and typed evidence links
  -> validated interest/goal/question/claim graph
  -> read-only typed-interest drill-down (`/interest/{id}`)
  -> novelty/opportunity detection
  -> recommendation/research
  -> feedback
```

## Runtime contract

`ef/grounded_source.py` defines `GroundedSourceArtifact` and
`GroundedSpan`. The artifact records the stable source id, exact source URL
when supplied, representations actually inspected, transcript language and
kind when known, source spans with only available locator precision, retrieval
provenance, a content hash, and explicit completeness status.

The status is `complete`, `partial`, `unknown`, or `unavailable`. A successful
transcript fetch defaults to `unknown`, because retrieval success does not prove
that the transcript covers the whole source. Hash and shape validation fail
closed before persistence.

The transcript summarizer passes the complete fetched transcript to its
provider. It does not create a suffix-only prompt or append a truncation marker;
if the provider cannot accept the input, the result remains an explicit
fallback while the full grounded artifact is retained for downstream handling.
All provider prompt projections use the shared source sanitizer: it redacts
known prompt-injection markers and labels source/model blocks as untrusted data,
but does not silently cap the transcript before provider analysis. This is
defense in depth, not a provider-enforced structured-content boundary. It
applies to transcript, OCR/visual-label, evidence-cluster, and grounded-source
projections. The unmodified text remains in the grounded artifact.

`TranscriptProvider`, `OcrClipProvider`, and local-model providers carry the
artifact on `VideoAnalysisResult`. `bin/csf-analyze` serializes it, and the
personal graph stores it idempotently in `source_artifacts`. The inference
packet builder accepts optional grounded sources and refuses silent truncation.
The transcript-result bridge also rejects a provider result whose embedded
`video_id` differs from the requested ID; the provider cannot relabel a stale
transcript as the current source.
When OCR/CLIP actually inspects frames, the artifact records `frames` as an
inspected representation while retaining only the textual spans that are
available; derived visual labels are not silently promoted to source spans.
Successful Gemini URL-passthrough analysis emits a `full_media` provenance
artifact even when no transcript span is available. That media-only artifact
identifies the inspected source but contributes no invented textual evidence.
If Gemini passthrough falls back to transcript analysis, the fallback carries a
transcript artifact instead of losing provenance behind the provider error.
Replayed source IDs must match all immutable provenance metadata; only explicit
evidence-cluster associations may be extended.
The public `analyze_video` boundary validates and binds the YouTube ID and URL
even when a caller supplies an explicit provider, so tier selection cannot
bypass source validation.
The legacy `bin/csf-analyze` API applies the same binding before its explicit
mode overrides, preserving the invariant for CLI callers as well.

The operator-facing handoff is explicit: `csf-analyze` result JSON files carry
the typed `grounded_source`; `scripts/make_grounded_source_manifest.py` reads
those results plus the catalog in read-only mode and resolves each video's
actual `chunk_clusters` associations. The resulting versioned manifest is
accepted by the full-coverage CLI:

```text
python scripts/make_grounded_source_manifest.py \
  --input P:/.data/yt-is/analyses/runs/<video-id>/<run-id>.json \
  --out P:/.data/yt-is/ef/grounded-sources.json
python scripts/build_interest_graph.py --run-bootstrap --allow-spend \
  --grounded-source-manifest P:/.data/yt-is/ef/grounded-sources.json \
  --store
```

The adapter fails when the artifact is absent, its source id disagrees with
the analysis result, or the catalog has no cluster association. The driver
then rejects associations outside the eligible bootstrap plan; source context
is never silently broadcast to unrelated batches. The manifest's explicit
`cluster_ids` replace provider-carried cluster associations at this boundary;
provider IDs are checked against the eligible plan but are not treated as
independent routing authority. Repeated source IDs must also agree on every
immutable provenance field, not merely the content hash; only cluster
associations may be extended.

Bootstrap persistence records a JSON provenance envelope on `inference_runs`
with the plan ID, run artifact directory, and grounded source IDs/hashes. This
joins a stored semantic run back to its exact execution artifacts while the
deduplicated graph edges remain graph-level relationships.

Resumable provider batches also bind both metadata files to a deterministic hash
of the exact hydrated cluster inputs and grounded-source lineage. The runner
rehydrates current inputs before reusing a cached batch, while still avoiding a
provider call for a matching artifact; any input drift fails closed.

YouTube routing requires a supported YouTube host and a URL video ID matching
the requested video ID. Each CLI analysis publishes an immutable run artifact
under `analyses/runs/{video_id}/{run_id}.json` using atomic replacement. The
legacy `analyses/{video_id}.json` path is still updated for compatibility, but
it is explicitly a last-writer-wins alias and is not the canonical manifest
input.

## Freeze boundary and branch identity

The shared repository also contains a historical D3 inference candidate
(`f7bd24fd`) and a later pre-unseal evaluator-hardening branch. Those artifacts
are not interchangeable with the current reconciled driver: the D3 manifest
binds an older six-file implementation, does not include
`ef/grounded_source.py` or the manifest adapter, and records that persistence
was not performed. Its implementation-manifest hash must therefore not be
copied into the current evaluator receipt. The current receipt now binds the
reviewed implementation identity
`dc05c855c4fe3072b708ce77f62bfe48ce8ec30c`. That binding is a public
integrity precondition only; it does not open the private holdout or promote
semantic quality. The holdout and recommendation gates remain separately
authorized acceptance work.

Landing invariant: the semantic evaluator binding is to one exact committed
implementation identity. A cherry-pick produces a new commit SHA even when
the resulting implementation files are byte-identical. Therefore the bound
holdout run must either execute from the original bound commit/worktree or
rebind the evaluator receipt to the final landed implementation SHA and
re-verify the public manifest before scoring.

During reconciliation, `scripts/build_interest_graph.py` remains the stable
entrypoint and retains the prior owner implementation for recovery. Its active
runtime delegates to `scripts/build_interest_graph_contract.py`, the canonical
strict-driver implementation. The bridge synchronizes the documented provider
test seam so imports and mocked offline tests exercise the same implementation.

## Readiness check

Run `python scripts/check_intelligence_readiness.py` from the repository root.
This is a structural, fail-closed check only: `status: READY` means the
executable contract is present, not that the workflow is complete. It also
checks the canonical `/distill-source` skill markers and reports
`distill_source_contract: READY`; this verifies contract presence without
turning the Markdown Grounded Reference into graph state. Read
`workflow_status`, `semantic_recall_gate_detail`, and `evaluator_freeze` as
well; the current implementation reports `BOUND` after the exact committed
inference SHA is recorded and the public freeze hashes reproduce. The check
also reports whether the current checkout is `EXACT_COMMIT`,
`CONTENT_EQUIVALENT`, `MISMATCH`, or `UNVERIFIED` relative to that binding;
the current isolated candidate is `CONTENT_EQUIVALENT` because its later
documentation/test commits do not alter inference code. The check never
contacts a provider or opens private evaluation artifacts.

## Deliberate non-goals

- Do not invoke `/distill-source` for every bulk-ingested video. Use it for an
  operator-requested source, a high-value source selected for deeper analysis,
  or a source whose evidence needs a grounded reference.
- Do not treat a Markdown Grounded Reference as the authoritative graph input.
  The typed artifact and Evidence Fabric records are authoritative; Markdown
  is a user-facing projection.
- Do not infer visual, acoustic, music, or sound-effect claims from a transcript.
- Do not enable goal-aware recommendation ranking until the existing semantic
  recall, provenance, and blinded comparison gates pass.

## Acceptance gates

1. Strict inference validation, full-coverage bootstrap, reconciliation, and
   atomic typed persistence remain the only path that writes inferred graph
   state.
2. Every source-backed inference can trace through an evidence cluster or
   grounded source artifact without invented locator precision.
3. Semantic recall passes the frozen holdout, including explicit negative
   cases and perturbation/stability checks.
4. Goal-aware ranking beats the similarity-plus-recency baseline on the
   preregistered blinded regret evaluation before it becomes the default
   recommendation policy.
5. The deployed warm-query runtime passes the read-only dependency preflight,
   then `/health` reaches `ready` and `/interests` plus `/interest/{id}` are
   verified against the live typed catalog.
