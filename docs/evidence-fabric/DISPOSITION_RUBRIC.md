# N/O-gate disposition rubric — committed BEFORE any candidate is judged

Frozen 2026-08-18, prior to the blinded 35-claim review. The candidate
data for review is generated blinded: rank/score stripped, candidate
order shuffled per claim. The unblinded mapping is committed separately
(`n_gate_disposition_key.json`) and must only be joined AFTER all
dispositions are recorded.

## Candidate-level dispositions (judge reopened evidence only)

| Disposition | Meaning |
|---|---|
| supports | the reopened evidence materially substantiates the claim |
| qualifies | material boundary/exception/condition on the claim |
| contradicts | credible direct conflict with the claim |
| irrelevant_or_insufficient | off-topic, too thin, or wrong scope |

Judgment inputs: evidence text, source identity, channel, publication
date. NOT retrieval rank or score (hidden). A high-position candidate
may be irrelevant; a late one may support.

## Claim-level actions (one per claim)

| Action | Meaning |
|---|---|
| no_change_confirmed | evidence reviewed; claim stands as-is |
| strengthen_evidence | add the new source(s) to the claim's receipts |
| qualify_claim | add boundary/condition to claim text |
| revise_claim | change the claim's substance |
| mark_for_reverification | page needs a fresh verification pass |
| remove_or_deprecate | claim retired |
| defer_insufficient_evidence | neither confirm nor change; note gap |

## Review-reason taxonomy (per claim, from the two-clock model)

| Reason | Source |
|---|---|
| genuinely_newer | published_at > last_verified (0 claims in this sample) |
| newly_available | captured_at > last_verified, published_at older (22) |
| unknown_last_verified | page lacks last_verified (11) |
| ordinary_discovery | evidence/contradiction mode irrespective of staleness |

## Aggregates to compute after dispositions

- candidate useful rate; candidate irrelevant/noise rate
- claims with new useful support / material qualification / credible
  contradiction / confirmed unchanged / actual wiki modification /
  insufficient evidence
- newly-available subset: material-consequence rate, no-action
  confirmation rate
- unknown-last_verified subset: pages worth immediate metadata fix
- mode-level utility: evidence, contradiction, staleness/refresh

Primary question: of the 22 newly-available claims, how many yielded a
material maintenance consequence?

agent: zcode · host: both
