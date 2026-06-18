# Command-Latency Event Attribution Design

## Goal

Extend the existing command-latency analyzer so one authoritative Markdown/JSON packet explains command-time differences using both worker aggregates and individual `nlm_source_content_command_completed` events.

The immediate comparison is the valid `3+3`, `home_300mb` soak pair:

- baseline: `fresh_state_3plus3_extract_schema_source_age_cadence_run01_current`
- candidate: `fresh_state_3plus3_extract_schema_source_age_cadence_local_retry_projection_run08_current`

## Chosen Approach

Extend `scripts/analyze_command_latency_attribution.py` in place. The script already owns aggregate command-latency comparison, output rendering, and the canonical packet paths. A second analyzer would duplicate run discovery, metric reconciliation, and report authority.

Preserve the existing worker-aggregate sections and add event-level sections. Existing CLI behavior remains compatible: repeated `--run-root` values select comparisons, and `--phase` limits both worker and event parsing.

## Inputs And Data Flow

For each selected run root:

1. Read `<run-root>/sharded_lane_series_summary.json` and reject missing or malformed summaries.
2. Read worker aggregate rows from phase/lane/batch `stdout.txt` files using the existing parser.
3. Read `term_*.jsonl` files under the selected phase.
4. Retain only `nlm_source_content_command_completed` events for command attribution.
5. Derive phase, lane, and batch from each event path; read profile, worker, attempt, status, elapsed time, source age, and video/source identifiers from event data.
6. Read projection evidence from events whose data contains `projected_local_retry_completion_age_cliff`.
7. Aggregate events by run, lane, batch, profile, attempt class (`attempt_1` or `retry`), and status.
8. Reconcile event command count and elapsed total against worker aggregate command count and elapsed total.

Raw logs and summaries remain immutable.

## Output Contract

The JSON packet retains the existing `runs` and `comparison` fields and adds an `event_attribution` section per run containing:

- overall event count and elapsed total;
- attempt-1 and retry count/elapsed/maximum;
- lane/batch/profile/attempt/status rows;
- projection-event counts by lane/batch/profile;
- count and elapsed reconciliation ratios;
- missing or malformed event counts.

The comparison adds event-delta rows sorted by elapsed delta. The Markdown report adds:

- attempt-1 versus retry totals;
- top lane/batch/profile deltas;
- status deltas;
- projection concentration;
- reconciliation gate and uncertainty.

No report may label the event packet `discriminating` unless both runs reconcile at least 95% of worker aggregate command count and elapsed time. Lower coverage is reported explicitly; the script does not invent missing attribution.

## Error Handling

- Missing run roots or summaries produce a clear nonzero exit.
- Invalid JSON lines are counted and skipped.
- Events missing required command fields are counted as malformed and excluded from elapsed attribution.
- Unknown lane names remain visible using their raw names.
- Missing attempt values are grouped as `unknown`, never assumed to be first attempts.
- Missing projection events mean `0 observed`; they do not prove the projection code was disabled.

## Testing

Add focused tests for synthetic run roots that prove:

1. event path context and command fields are parsed correctly;
2. attempt 1, retry, status, lane, batch, and profile aggregates are correct;
3. projection events are counted without double-counting commands;
4. malformed lines and missing fields are reported;
5. reconciliation reaches 100% for a complete fixture and fails the 95% gate for an incomplete fixture;
6. existing worker-aggregate report sections remain present.

After tests pass, regenerate the run01-vs-run08 packet and update the throughput operating plan only with conclusions supported by the reconciled event packet.

## Scope Boundaries

- No NotebookLM, browser, authentication, or benchmark commands.
- No changes to runtime retry or cadence behavior.
- No analysis of unrelated run roots.
- No deletion, movement, or rewriting of raw benchmark evidence.
- No causal claim without a named falsifier; this analyzer establishes concentration and correlation, not causation.
