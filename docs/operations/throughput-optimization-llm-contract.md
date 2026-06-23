# Throughput Optimization LLM Contract

This contract is for LLM agents working on sustained NotebookLM hot-path throughput in `yt-is`.

The goal is not to run more benchmarks. The goal is to find and use the highest sustained videos-per-hour rate with the least wasted live-run time.

## Required Bootstrap

Before proposing or running any throughput experiment, read these files in this order:

1. `P://packages/yt-is/AGENTS.md`
2. `P://packages/yt-is/HANDOFF.md`
3. `P://packages/yt-is/docs/operations/hot-path-throughput-next-test-plan.md`
4. `P://packages/yt-is/docs/operations/test-registry.md`
5. `P://packages/yt-is/docs/operations/observability-contract-checklist.md`
6. `P://packages/yt-is/docs/operations/sharded-lane-artifact-audit.md`

If those files disagree, the current dated operations docs and raw artifacts win over memory or chat summaries.

## Metric Authority

- Headline metric: `combined.hot_path_videos_per_hour`
- Owning artifact: `sharded_lane_series_summary.json`
- Excluded from sustained hot-path VPH: Whisper fallback time, Whisper recovery counts, cleanup timing outside the lane-process throughput span
- Throughput truth: completed-worker totals, stage timings, worker result files, and live stderr/stdout when they explain missing or contradictory structured traces
- Diagnostic-only signals unless promoted by a current plan: retry counts, auth-refresh spread, worker-profile spread, source-list probe time, idle wait, source-add/materialization counters, command-latency buckets

Always label these separately:

- historical high-water mark
- current control
- candidate result
- invalid or partial run
- environment-scoped result such as `hotel_wifi`

## Live Benchmark Gate

Do not launch a live benchmark until a completed decision packet exists.

Use `P://packages/yt-is/docs/operations/templates/throughput-decision-packet.md`.

The packet must answer:

- What exact hypothesis is being tested?
- Which raw artifacts support the hypothesis?
- Why is offline reducer/audit analysis insufficient?
- What result would falsify the hypothesis?
- What early-abort signal stops the run before it wastes hours?
- What exact artifact and threshold promote the candidate?
- What docs will be updated immediately afterward?

If any answer is missing, the decision is `offline attribution`, `harness fix`, or `code fix`, not `live benchmark`.

If a no-patch design packet already exists for the branch, do not use this template to reopen it; create or update the design packet first.

## Default Decision Rules

- Do not rerun old benchmark shapes unless the named code path or harness path changed.
- Do not run a same-shape benchmark just to see whether it improves.
- Do not treat a current observed leader as proven optimal sustained VPH unless a current run reproduces it.
- Do not run margin, projection, or geometry neighbors unless offline attribution shows that exact lever is still active.
- Do not compare hotel-network runs numerically against home-network ceilings.
- Do not treat partial, invalidated, or missing-summary runs as throughput ceilings.
- Do not promote a candidate from a single unpaired run when current-control variance is unresolved.
- Do not use a historical high-water mark as the current control unless a current run reproduces it.
- Do not implement code without a narrower mechanism than the existing projection/retry guard path.

## Current Branch Guardrails

For the current `3+3` source-age / command-latency branch:

- Keep `margin20` as the current guard unless a later registry row supersedes it.
- Treat `margin25` as negative evidence.
- Treat `margin15` as invalidated dead-branch evidence.
- Do not launch another nearby margin/projection run until there is a concrete source-add/materialization or command-latency fix.
- Prefer offline attribution or a narrow code-path fix around source-content command latency, source-add/materialization stability, worker skew, or batch-tail pressure.
- Before any future throughput proposal, run `scripts/analyze_command_latency_attribution.py` first so the active lever is explicit before code or benchmark planning.

## Early-Abort Contract

Every live benchmark packet must define stop gates before launch.

Use these defaults unless the packet gives stricter gates:

- If smoke invalidates on source-add/materialization/auth failures, stop and classify the branch. Do not wait for soak.
- If no top-level or phase summary appears after the expected smoke window and lane logs repeat the same source-add/materialization failure class, stop and classify as invalidated.
- If the run is still emitting only auth cleanup, source-count probe, source-add, or materialization retries after the packet's maximum wait, stop and classify as invalidated or partial.
- If browser health, profile ownership, worker shape, or environment label is wrong, stop and classify as invalidated.
- If the run root is dirty before launch, stop and relaunch only after creating a clean root and preserving failed-launch logs outside the run root.

The maximum wait must be stated in the packet. A cheaper LLM must not keep polling indefinitely.

## Required Operating Loop

1. Read the bootstrap files.
2. Identify historical best and current control separately.
3. Inspect the latest registry rows for the proposed branch.
4. Run offline reducer/audit attribution first when raw artifacts already exist.
5. Fill out the decision packet.
6. If the packet fails, update docs with `deferred` or `blocked` and do not run a benchmark.
7. If the packet passes, run exactly one benchmark.
8. Apply the early-abort contract.
9. Classify the result as `proven`, `negative`, `partial`, or `invalidated`.
10. Update `test-registry.md` and any plan/handoff doc before starting another branch.

## Handoff Prompt For Cheaper LLMs

Use this prompt when delegating throughput work:

```text
You are working in P://packages/yt-is on NotebookLM hot-path throughput. Before running any command, read AGENTS.md, HANDOFF.md, docs/operations/throughput-optimization-llm-contract.md, docs/operations/hot-path-throughput-next-test-plan.md, docs/operations/test-registry.md, and docs/operations/observability-contract-checklist.md.

Do not launch a live benchmark unless you first complete docs/operations/templates/throughput-decision-packet.md. Prefer offline reducer/audit attribution when artifacts already exist. Keep historical high-water marks, current controls, candidate results, partial runs, invalidated runs, and hotel/home environments separate. If the packet cannot name a falsifier, early-abort gate, raw artifact path, and promotion rule, do not run; report the missing evidence and recommend offline attribution, harness fix, or code fix.
```
