# Local Retry Source-Age Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a slow failed `nlm source content` attempt from launching another same-notebook retry when the measured prior attempt duration projects completion beyond the source-age cliff.

**Architecture:** Keep the existing fetch-round entry guard. Inside the local retry loop, project the next attempt's completion age as current source age plus planned retry sleep plus the prior attempt's elapsed time. If that projection reaches the configured cliff, stop local retries, prevent the same source from entering the local retry queue, and emit a distinct reason plus the projected age.

**Tech Stack:** Python, pytest, `unittest.mock`, existing `csf.nlm_batch` event logging.

---

### Task 1: Guard Local Retries With Measured Command Duration

**Files:**
- Modify: `csf/nlm_batch.py:3180-3810`
- Test: `tests/test_nlm_batch.py:4237-4438`
- Modify: `docs/operations/hot-path-throughput-next-test-plan.md:1421-1425`

- [x] **Step 1: Write the failing test**

Add a test that starts a source at age `150s`, makes the first content attempt consume `35s`, and configures a `20s` retry delay. Assert that only one content command runs, no retry sleep occurs, and the completed event records `projected_local_retry_completion_age_cliff` with a projection of at least `200s`.

- [x] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest -q tests/test_nlm_batch.py -k local_retry_projection`

Expected: FAIL because the current loop sleeps and launches a second content attempt.

- [x] **Step 3: Implement the minimal guard**

After a retryable failed attempt and before sleeping, compute:

```python
projected_local_retry_completion_age_s = round(
    time.time() - ready_reference_epoch + delay_s + attempt_elapsed_s,
    3,
)
```

When the projection reaches `_SOURCE_AGE_CLIFF_S`, break without sleeping, carry `projected_local_retry_completion_age_cliff` into the existing retry-queue skip reason, and include the projected completion age in the final fetch-completed event.

- [x] **Step 4: Run focused and neighboring tests**

Run: `python -m pytest -q tests/test_nlm_batch.py -k "local_retry_projection or source_content_fetch_retries_transient_not_found_and_recovers or source_content_retry_queue"`

Expected: all selected tests pass.

- [x] **Step 5: Run syntax and diff checks**

Run: `python -m py_compile csf/nlm_batch.py tests/test_nlm_batch.py scripts/analyze_command_latency_attribution.py`

Run: `git diff --check`

Expected: both commands exit `0`.

- [x] **Step 6: Record the decision**

Update the canonical throughput plan with the event-level evidence: retries account for `91.6%` of the measured run04 command-time delta, and the new guard is a code-path candidate requiring targeted validation before any live benchmark.
