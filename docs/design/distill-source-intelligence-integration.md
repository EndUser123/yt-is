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
  -> novelty/opportunity detection
  -> recommendation/research
  -> feedback
```

## Runtime contract

`ef/grounded_source.py` defines `GroundedSourceArtifact` and
`GroundedSpan`. The artifact records:

- the stable source id and exact source URL when supplied;
- the representations actually inspected (`transcript`, `captions`,
  `metadata`, `audio`, `frames`, or `full_media`);
- transcript language and kind when known;
- source spans with only the locator precision actually available;
- retrieval provenance, a content hash, and an explicit completeness status.

The status is `complete`, `partial`, `unknown`, or `unavailable`. A successful
transcript fetch defaults to `unknown`, because retrieval success does not prove
that the transcript covers the whole source. Hash and shape validation fail
closed before persistence.

`TranscriptProvider`, `OcrClipProvider`, and local-model providers carry the
artifact on `VideoAnalysisResult`. `bin/csf-analyze` serializes it, and the
personal graph stores it idempotently in `source_artifacts`. This makes the
source representation available to IL without refetching or uploading the
same media.

`scripts/build_interest_graph.py` accepts an optional
`grounded_sources_by_cluster` mapping. The packet builder renders each
artifact with its source id, hash, status, representation, language, and
source spans; the context builder refuses silent truncation. The full-coverage
bootstrap can persist the same artifacts in the transaction that persists the
validated graph, so an inference run can be replayed and audited as one unit.

During reconciliation, `scripts/build_interest_graph.py` remains the stable
entrypoint and retains the prior owner implementation for recovery. Its active
runtime delegates to `scripts/build_interest_graph_contract.py`, the canonical
strict-driver implementation. The bridge synchronizes the documented provider
test seam so imports and mocked offline tests exercise the same implementation.

## Readiness check

Run `python scripts/check_intelligence_readiness.py` from the repository root.
This is a structural, fail-closed check only: `READY` means the executable
contract is present, not that semantic recall or recommendation quality has
passed. It never contacts a provider or opens private evaluation artifacts.

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
