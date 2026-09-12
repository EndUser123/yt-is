# Default NotebookLM Chrome Profile Incident Runbook

## Purpose

Define a deterministic response for the recurring incident where the shared default NotebookLM Chrome profile is still present and blocks a hotel or home benchmark run before smoke can start.

This runbook is intentionally narrow:
- it covers the shared default NotebookLM Chrome profile only
- it does not change the ownership rules for unrelated Chrome processes
- it does not relax the benchmark universe boundaries
- it does not replace the existing benchmark gates; it explains how they should behave when the incident occurs

## Problem Statement

The benchmark sequence can fail before smoke because the default NotebookLM Chrome profile is still running. This creates a repeated manual judgment call:
- should the process be observed longer
- should it be reaped automatically
- should the current run be invalidated and a fresh rerun started
- or should we stop and ask for human action

Without a codified flow, the same incident gets re-decided turn by turn.

## Goals

1. Keep benchmark runs self-healing when the shared default NotebookLM browser session is clearly recoverable.
2. Preserve the safety contract for non-NotebookLM Chrome processes.
3. Make the next action explicit when the benchmark cannot safely continue.
4. Keep the decision tree stable enough that future reruns do not need fresh interpretation.

## Non-Goals

- Rewriting auth-family mapping.
- Replacing lane-specific browser roots.
- Broadening process termination to arbitrary Chrome processes.
- Changing benchmark geometry, cohort selection, or run-environment labels.
- Adding a new benchmark shape.

## Incident Definition

The incident is active when all of the following are true:
- the benchmark sequence is in pre-run browser health or lane-start browser health
- the default NotebookLM `chrome-profile` is present
- the benchmark cannot enter smoke until the profile is cleared

The incident is not active when:
- the only Chrome processes are lane-owned browser roots under `P:\.data\yt-is\browser\...`
- the default profile is absent
- the run is already in smoke or soak and the browser-health gate has passed

## Decision Tree

### 1. Detect

Record the incident in `browser_health.json` or the equivalent gate artifact with:
- detected default-profile PIDs
- reaped default-profile PIDs
- remaining default-profile PIDs
- allowed lane browser roots
- the gate outcome

### 2. Reap if clearly safe

Reap the default profile automatically only when the process is clearly the shared NotebookLM session and not a generic Chrome instance.

Criteria:
- the command line matches the default NotebookLM user-data-dir for the shared `chrome-profile`
- and the command line or browser session carries a NotebookLM-owned identity marker, such as the NotebookLM origin, the NotebookLM account marker, or the equivalent gate-owned session discriminator
- and the process is not part of an allowed lane browser root

If reaping succeeds and the default profile is gone, mark the health state as recovered-clean and continue the run.

### 3. Retry observation once

If the default profile is still present after the first reap attempt, keep sampling through the configured settle window and, on each sample, reap again only if the same safe predicate still holds.

This avoids racing against short-lived shutdown behavior while keeping the decision ownership-aware and profile-specific. The settle window is not a blind sleep; it is repeated observation with the same ownership check.

### 4. Fail closed when persistent

If the default profile is still present after the settle window:
- mark the run unhealthy before smoke
- do not launch smoke or soak
- preserve the run artifact as invalidated for that universe

### 5. Ask for human action only after the gate has done its job

If the default profile cannot be confidently tied to the NotebookLM session, or if the gate still cannot clear it after the settle/reap cycle, stop and ask for human action rather than widening the kill criteria.

## Operational Contract

- Pre-run browser health may reap the default profile once.
- Lane-start browser health may reap it again if it appears after preflight.
- Post-run hygiene may reap a lingering default profile after soak.
- Any reap decision must remain ownership-aware and profile-specific.
- If a reap fails, the gate must say so explicitly in the artifact.

## Required Artifact Fields

The browser-health artifact should expose:
- `status`
- `initial_default_profile_detected_count`
- `initial_default_profile_reaped_count`
- `default_profile_detected_count`
- `default_profile_reaped_count`
- `default_profile_remaining_count`
- `allowed_browser_roots`
- `issues`
- `warnings`

These fields are the source of truth for whether the incident was recovered or remained blocking.

## Expected Outcomes

When the incident is recoverable:
- the run proceeds into smoke
- the browser-health artifact shows recovered-clean
- the default profile is no longer present

When the incident is not recoverable:
- the run stops before smoke
- the browser-health artifact shows unhealthy
- the rerun universe is not used as throughput evidence

## Testing Notes

The runbook should be supported by tests that prove:
- the gate reaps a clearly NotebookLM-owned default profile
- the gate refuses an unowned or ambiguous Chrome process
- the gate records recovered-clean only when the default profile is gone
- the gate remains failure-closed if the default profile persists after the settle window
- the gate keeps sampling through the settle window rather than relying on a single resample
- the gate re-applies the same safe predicate on each settle-window sample instead of broadening the kill criteria

## Review Checklist

- Is the scope narrow enough to stay specific to the default-profile incident?
- Are the decision branches deterministic?
- Does the flow preserve the safety contract for unrelated Chrome?
- Is the difference between recoverable and persistent clearly stated?
- Would a future reader know whether to wait, reap, invalidate, or stop?
