# Intelligence-layer review — 2026-09-16

This package review assessed the active yt-is intelligence-layer and its
`/distill-source` integration. The public wrapper resolves the strict
`scripts.build_interest_graph_contract` driver. Grounded source artifacts flow
through evidence-cluster packets, typed persistence, and source-backed graph
links. The final affected offline regression suite passed 358 tests after the
URL-routing, prompt-boundary, run-provenance, and event-lineage additions.
The stable wrapper also no longer exposes the retired unvalidated `store()`
writer.

Current status is `needs_attention`, not complete. The readiness receipt remains
structurally `READY` but workflow `INCOMPLETE`; semantic recall is outstanding,
and the evaluator is publicly bound to implementation
`dc05c855c4fe3072b708ce77f62bfe48ce8ec30c`. The private holdout remains
closed until the authorized semantic evaluation is run.

Residual review risks:

1. `ef/personal_graph.py` keeps `evidence_links` deduplicated for graph
   consumers and records each emitting run in the append-only
   `inference_edge_events` table.
2. `csf/prompt_safety.py` is defense in depth. Prompt templates now label
   source/model blocks as untrusted data, but arbitrary instruction-like source
   text remains model-visible and there is no provider-enforced structured-data
   boundary.
3. `bin/csf-analyze` now publishes a unique canonical run artifact under
   `analyses/runs/{video_id}/{run_id}.json`; the flat `{video_id}.json` file
   remains a documented compatibility alias and is still last-writer-wins.
4. Transcript artifacts now reject a `TranscriptResult` whose embedded
   `video_id` differs from the requested ID. This closes the observed
   provider-to-artifact relabeling gap, but does not independently prove
   transcript completeness or semantic quality.
5. The public `analyze_video` entrypoint now applies the same URL/ID binding
   before an explicit provider is invoked, closing the direct-provider bypass.
6. The legacy `bin/csf-analyze` entrypoint now applies the same binding before
   explicit mode overrides, closing the equivalent CLI/API bypass.

Next actions are parent-controlled landing of the isolated candidate, followed
by the authorized semantic recall/stability and recommendation gates. The
final affected suite and readiness/driver gates have been rerun successfully;
private holdout access remains closed. The implementation review was a
self-review (`verify_mode=self`), not an independent specialist review.
