> State detail is a working handoff; verify against [PROJECT_STATE.md](PROJECT_STATE.md) (master entry) before relying on it.

# yt-is Personal Intelligence — Inference State
Updated: 2026-09-16 by the distill-source/IL reconciliation and evaluator-hardening pass

## Goal & constraints

- [seen] Infer durable and active interests, goals, information needs,
  questions, and adjacent/regret candidates from multi-view corpus evidence.
- [seen] Preserve provenance sufficient to audit every material inference back
  to evidence clusters and ultimately source evidence.
- [seen] LLMs perform semantic interpretation; mechanical layers perform
  candidate generation, counting, breadth, temporal statistics, and coverage.
- [seen] Narrow but important interests must not be excluded merely because
  broad clusters have greater channel breadth.
- [seen] Inference quality is a separate gate from recommendation quality.

## Non-goals

- [seen] Do not infer interests directly from raw entity counts.
- [seen] Do not treat syntactically parseable JSON as validated typed state.
- [seen] Do not let prose summaries substitute for relational provenance.
- [seen] Do not run the regret-ranking falsifier until inference recall and
  provenance are adequate.

## Decisions

- 2026-08-24: [seen] Multi-view evidence clusters are the inference unit.
- 2026-08-24: [seen] Observation, Interest, Goal, and InformationNeed remain
  separate concepts.
- 2026-08-24: [seen] Every inferred interest must reference supplied evidence.
- 2026-08-24: [seen] Counterevidence must remain representable.
- 2026-08-24: [seen] Inferred-adjacent/regret candidates must remain distinct
  from observed interests.
- 2026-08-24: [seen] A blinded interest-recovery plus perturbation/stability
  gate precedes downstream recommendation evaluation.

## Current state

- [seen] v1.5 evidence clusters shipped at `e7c2b6c0`.
- [seen] `scripts/build_interest_graph.py` sends evidence-cluster packets to
  an LLM provider.
- [seen] Codex JSONL extraction was added at `7446d526`.
- [seen] The prompt requests interests, goals, questions, cluster IDs,
  counterevidence, relationships, and regret candidates.
- [implemented-in-working-tree] The stable entrypoint preserves the prior
  owner body for recovery while delegating runtime calls to the strict
  contract driver in `scripts/build_interest_graph_contract.py`.
- [implemented-in-working-tree] Provider output is mechanically validated for
  schema, enums, confidence, referential integrity, and supplied-cluster
  evidence references before canonical artifacts or graph state are written.
- [implemented-in-working-tree] Full-coverage bootstrap plans every eligible
  cluster into bounded batches, reconciles all validated fragments, and
  persists the final typed graph transactionally; the legacy top-25 path is
  retained only as the explicit baseline.
- [implemented-in-working-tree] Typed persistence materializes goals,
  information needs, parent/related/question edges, regret candidates, and
  evidence-cluster support links; each stored run also records a provenance
  envelope joining its plan, artifact directory, and grounded source hashes.
- [implemented-in-working-tree] The canonical `/distill-source` boundary is
  represented by `GroundedSourceArtifact`; transcript, frame, and full-media
  provenance is validated, source IDs are immutable, and oversized prompt
  inputs fail closed instead of being silently truncated.
- [implemented-in-working-tree] Transcript analysis preserves complete input
  through the direct summarizer and legacy CLI fallback; fallback results carry
  typed transcript provenance, and the optional CKS sink is loaded only for an
  explicit CKS write.
- [implemented-in-working-tree] All provider prompt projections use shared
  injection-marker redaction plus explicit untrusted-data instructions without
  truncating source text; successful Gemini
  passthrough records its actual model in the transcript-cache receipt. This
  includes transcript, OCR/visual-label, evidence-cluster, and grounded-source
  projections; the canonical typed artifact remains unmodified.
- [implemented-in-working-tree] Reconciliation lineage is keyed by source
  fragment identity, so duplicate normalized interest names emitted by
  separate bounded groups cannot redirect or lose leaf provenance.
- [implemented-in-working-tree] An operator manifest adapter binds typed
  analysis results to actual catalog `chunk_clusters` associations without
  broadcasting a source to unrelated clusters.
- [implemented-in-working-tree] YouTube routing requires a supported video host
  and a URL video ID matching the requested ID; canonical analysis publication
  is atomic. Transcript-result bridges also reject an embedded result ID that
  differs from the requested ID, preventing stale provider output from being
  relabeled as the current source. Explicit providers pass through the same
  request-level URL/ID validation, and the legacy `bin/csf-analyze` entrypoint
  applies that validation before explicit mode overrides.
- [verified-offline] The current candidate's scope-matched semantic-evaluator
  and interest-graph regression suite passes 78 tests; bytecode compilation
  and diff checks also pass. The repository-wide suite is not green: the
  unrelated `tests/ef/test_incremental_watermark.py::test_watermark_advances_when_boundary_tie_has_ineligible_rows`
  test currently reports `processed == 5` instead of `4` after 70 passing
  tests.
- [verified-offline] The structural readiness check reports `READY` and the
  canonical `/distill-source` marker check reports `READY`, but the semantic
  evaluator freeze is currently `INVALID` because its receipt still binds
  the earlier implementation SHA and its frozen artifact hashes drift from
  the latest candidate.
- [unverified] The semantic recall, perturbation/stability, and recommendation
  gates have not passed. The evaluator receipt remains
  `NOT_YET_FROZEN`, and no private holdout was opened.
- [historical-context] An earlier live inference reportedly produced
  coherent software, trading, options, macro, media-production, and
  knowledge-automation interests/goals, but it is not acceptance evidence.
- [historical-context] That earlier result reportedly did not visibly recover
  several deliberately relevant validation domains including longevity, ADHD
  mitigation, and cognitive enhancement; this remains a hypothesis for the
  pending semantic-recall evaluation, not a current verdict.
- [verified-offline] Focused tests cover schema validation, malformed provider
  output, typed relationship persistence, provenance integrity, and the
  active-entrypoint dashboard/bootstrap paths.
- [verified-runtime-ready] The read-only warm-service dependency preflight
  reports `READY` for the exact WinSW environment declared in
  `deploy/winsw/ef_warm_query.xml`; machine-only encoder gaps are retained as
  non-blocking diagnostics because the XML explicitly supplies the user
  overlay. The live service is `Running`, `/health` returns `ready`, and the
  merged MCP face answers `initialize` and `tools/list` over streamable HTTP.
  The typed-interest catalog currently contains zero rows, so no populated
  detail page has been claimed.
- [verified-operational-limitation] On 2026-09-16, the current workspace
  topology record says v6 was retired on 2026-09-14, but the old
  `P:/.data/git-plane.json` and `P:/.git-workers` remnants remain while
  `P:/.git-authority/canonical.git` is absent and `control_plane boundary`
  reports `tier: none`. The package checkout is not attached to a v6 worker
  lane and the common repo has no pending pins. No v6 landing or publication
  was attempted; v6 must not be recreated merely to freeze this work.
- [unverified] Candidate recall and perturbation stability on the frozen
  semantic holdout remain unevaluated.

## Open questions

- Does the reconciled full-coverage implementation meet the preregistered
  semantic recall, negative-case, and perturbation/stability thresholds?
- What exact committed implementation identity should be bound into the
  evaluator receipt after independent review?
- After the inference gate passes, does goal-aware ranking beat the registered
  similarity-plus-recency baseline in the blinded regret evaluation?

## Next action

The reconciled driver, provenance boundary, resume path, and pre-parse sealed
holdout guard are committed on the isolated candidate branch at
`2805ed43456a693f5e9a3cbbd38f5ee985b3231a` and pushed to
`codex/yt-is-il-frozen-candidate`. Do not recreate the retired v6 plane.

First perform the independent freeze review and deliberately regenerate the
evaluator receipt against the final reviewed implementation; do not open the
private holdout while the receipt is stale. Then obtain fresh live-provider
capacity and explicit authorization, run the resumable full-coverage
bootstrap with native JSON-schema enforcement, and verify transactional graph
and source-artifact writes. Only after the semantic recall/provenance and
stability gates pass should the recommendation-regret candidate arms be
frozen and the private recommendation evaluation run. The live typed-interest
catalog currently contains zero rows.
