# Refine brief — batch-1 old-window `nlm source content` command latency

**Status:** implementation-ready brief (no code written). Produced by `/refine`
methodology: codebase inspection + attribution evidence + falsifiable test
strategy. Supersedes the no-patch design packet's "no patch candidate yet"
conclusion by identifying a mechanism narrower than the projection/retry guard
path.

## 1. Problem statement (one sentence)

The content-fetch retry loop (`_fetch_content_round`, `csf/nlm_batch.py:5019`)
runs up to 4 attempts at 30s timeout each (120s worst-case command wall time)
with **no inter-attempt source-age check**, so sources that enter the retry
loop at ~140s ready-age pass the pre-fetch projection guard but age to ~260s
across retries, burning 120s of command time on sources destined to fail.

## 2. Code path evidence

### Existing guard architecture (what already exists)

| Guard | Location | When it fires | What it checks |
|---|---|---|---|
| Pre-fetch age cliff | `_fetch_content_round:4840` | Once, before attempt 1 | `source_ready_age_s >= _SOURCE_AGE_CLIFF_S (200)` |
| Pre-fetch projection | `_fetch_content_round:4855-4861` | Once, before attempt 1 | `source_ready_age_s + projection_s + margin_s >= 200` (default OFF: projection=0, margin=0) |
| Retry-queue projection | `_fetch_content_round:5188+` | Before admitting to second-pass queue | Queued retry's projected drain age >= cliff |
| Per-command timeout | `_fetch_content_round:5026` | Each `_run_cmd` call | `timeout=30` (hardcoded) |

**The gap:** none of these guards fire BETWEEN retry attempts within the retry
loop. A source at 140s ready-age passes the pre-fetch projection (even at
projection=60: 140+60=200, borderline), then enters the retry loop. After
attempt 1 fails (30s), the source is at 170s. After attempt 2 fails (30s),
200s. After attempt 3 fails, 230s. The loop continues to attempt 4 with no
check that the source has already crossed the cliff.

### Attribution evidence (from the command-latency packet)

- projection-60 leader soak Free batch_01: retry rows at 60-119s source age,
  max command time 141.598s (4-5 attempts at 30s each).
- control07 soak: content-fetch command total 5041.379s; retry-attempt
  elapsed was 94.938s across 30 events (3.16s avg — clean, few retries).
- cadence01 soak: content-fetch command total 2652.777s; retry-attempt
  elapsed 437.802s across 136 events (3.22s avg — more retries, but each
  is short because the cadence keeps sources young).
- The regression from cadence01 to projection-60 is concentrated in retry
  command elapsed: 437.802s → 2964.890s (+2427s). Attempt-1 elapsed barely
  changed: 2191.209 → 1523.223. The delta is almost entirely retry attempts
  on aged sources.

### Config constants (verified from `csf/nlm_config.py`)

```
source_content_retry_attempts = 4
source_content_retry_initial_delay_s = 1.0
source_content_retry_max_delay_s = 8.0
source_content_retry_budget_s = 30.0
source_content_retry_queue_delay_s = 30.0
source_content_retry_queue_budget_s = 30.0
source_content_primary_command_age_projection_s = 0.0  (env override)
source_content_primary_command_age_margin_s = 0.0      (env override)
```

Per-command subprocess timeout: `30` (hardcoded at `_run_cmd` call site).
Source-age cliff: `_SOURCE_AGE_CLIFF_S = 200` (env override).

## 3. Candidate mechanism: inter-attempt source-age gate

### Mechanism (one sentence)

Between retry attempts in the content-fetch retry loop, check whether the
source's current ready-age plus the per-command timeout (30s) would cross
the source-age cliff (200s); if yes, exit the retry loop immediately with
`source_age_cliff` instead of launching another doomed 30s command.

### Why this is narrower than the existing projection/retry guard path

| Dimension | Existing projection guard | This mechanism |
|---|---|---|
| When | Once, before attempt 1 | Between every retry attempt |
| What it needs | A projection constant (60s) + margin (20s) — both env knobs | Only `_SOURCE_AGE_CLIFF_S` (already exists) + per-command timeout (already hardcoded at 30s) |
| What it catches | Sources already old enough at fetch-start to project past the cliff | Sources that age past the cliff DURING the retry loop (the observed failure mode) |
| Default behavior | OFF by default (projection=0) | Always active — no env knob needed |

The existing projection guard is a pre-fetch gate. This is an inter-attempt
gate. They are complementary: the projection guard catches old sources at
entry; the inter-attempt gate catches sources that age out during retries.
Neither subsumes the other.

### Code change surface (per design packet constraint)

- `csf/nlm_batch.py`: insert a source-age check between the retry-sleep and
  the next `_run_cmd` call inside the `while True` loop at line ~5078 (after
  the retry-sleep delay, before `attempt += 1` for the next iteration).
- `tests/test_nlm_batch.py`: new tests under the existing test suite.
- No other files touched.

### Falsifiable test coverage (per design packet's "Exact Proposed Test Coverage")

1. **Inter-attempt cliff exit:** a source at 180s ready-age starts the retry
   loop, fails attempt 1 (30s), and by the time attempt 2 would start, the
   source is at 210s. The gate must exit the loop with `source_age_cliff`
   before launching attempt 2's subprocess.

2. **Non-interference with clean sources:** a source at 30s ready-age that
   fails attempt 1 (30s) reaches 60s — well below the cliff. The gate must
   NOT exit; the loop continues to attempt 2.

3. **Non-interference with the projection guard:** if the projection guard
   already caught the source at entry (status `source_age_cliff`), the
   inter-attempt gate never runs. The two guards are independent.

4. **Timeout budget accounting:** the gate uses the per-command timeout
   constant (30s) for its age projection, not a new constant. The test
   proves the gate's threshold is `cliff - timeout`, not a new knob.

### What this mechanism does NOT do

- Does not change the retry attempt limit (4).
- Does not change the per-command timeout (30s).
- Does not change the source-age cliff (200s).
- Does not change the projection guard or retry-queue guard.
- Does not promote any throughput claim.

## 4. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Tighter projection constant (projection=30 instead of 60) | Same guard, different constant — the design packet calls this a "same-shape" change; it catches sources at entry but not during retries |
| Per-command timeout reduction (30s → 15s) | Bounds each command but doesn't check source age; a young source with a slow command gets cut short unnecessarily |
| Content-fetch dispatch ordering (youngest first) | Changes dispatch order, not retry semantics; adds complexity without directly addressing the inter-attempt aging gap |
| Notebook rotation before extract phase | Already implemented as source-age cadence (`YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED`); adding another rotation path duplicates that mechanism |

## 5. Sub-batch tracker research question (resolved by code-reading)

The canary result was `correlation_absent_with_no_observed_failures`. The
cheapest follow-up is code-reading, not another live run. From the code:

- `record_success()` (`csf/nlm_batch.py:2704`) is called after EVERY
  successful content-fetch, resetting `_consecutive_failures = 0`.
- The sub-batch reset (`csf/nlm_batch.py:4048`) also resets
  `_consecutive_failures = 0`.
- Therefore: whenever the last content-fetch in a sub-batch succeeds, the
  tracker is already at 0. The sub-batch reset is redundant.
- The reset is only non-redundant when the last content-fetch(es) FAILED
  (tracker has failures > 0 at the boundary).
- When content-fetches fail, the failures are source-specific (NOT_FOUND,
  command_failed), not account-wide rate limits. The backoff delay would
  slow the next sub-batch without improving outcomes (the failures are
  per-source, not per-account).
- **Conclusion:** the reset is either redundant (clean case, confirmed by
  canary) or actively beneficial (removes spurious backoff after source-
  specific failures). The "harmful masking" case requires account-wide
  throttling, for which no evidence exists in any observed run. The
  research question is closed as a non-issue without needing another live run.

## 6. CORRECTION — mechanism already exists in the codebase

**Date:** 2026-08-12 (implementation attempt).
**Finding:** the proposed inter-attempt source-age gate (§3) **already exists**
at `csf/nlm_batch.py:5225-5238`, inside `_fetch_content_round`'s retry loop:

```python
if ready_reference_epoch:
    projected_local_retry_completion_age_s = round(
        time.time() - ready_reference_epoch + delay_s + attempt_elapsed_s,
        3,
    )
    if projected_local_retry_completion_age_s >= _SOURCE_AGE_CLIFF_S:
        local_retry_skipped_reason = "projected_local_retry_completion_age_cliff"
        retry_exit_reason_value = "local_retry_skipped_age_cliff"
        break
```

This is the exact mechanism §3 proposed: an inter-attempt check that projects
the source's completion age (`current_age + retry_delay + last_attempt_elapsed`)
and breaks the loop if it crosses the cliff. It fires between retry attempts,
after the retry-deadline/budget checks and before the `time.sleep`. Live
evidence confirms it activates (run08: 6 projection activations on Pro
worker-01).

**Where the brief's gap analysis went wrong:** §2-3 read the pre-fetch guard
(line 4840) and the retry-queue guard (line 5188) but missed the inter-attempt
guard at line 5225. The claim that "no inter-attempt source-age check exists"
was factually wrong.

**Implication:** the no-patch design packet's original conclusion — "the
likely lever is already present in code as a projection-based age guard and
retry-queue skip path" — was correct. No code change is justified. The
throughput loop is at end-state 2: the current control is confirmed as the
ceiling with explicit negative evidence on every remaining lever.

## 7. Acceptance criteria for the implementation-ready handoff

- [ ] A mechanism narrower than the projection/retry guard path is named
      (inter-attempt source-age gate — DONE, §3).
- [ ] The mechanism has falsifiable test coverage (DONE, §3).
- [ ] The code change surface is confined to `csf/nlm_batch.py` + tests
      (DONE, §3).
- [ ] No throughput/VPH claim is made (DONE — this is a reliability fix that
      bounds wasted command time, not a throughput optimization).
- [ ] The sub-batch tracker research question is resolved (DONE, §5).
