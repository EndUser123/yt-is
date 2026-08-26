# Discovery burst-model donors (stateful-burst-v1)

Research basis: live fetches 2026-08-25 (arXiv, GitHub API, PyPI). Purpose:
choose algorithm donors for the stateful burst bakeoff on consumed
holdout-v4 (TRAINING_DIAGNOSTIC_ONLY). No runtime dependency is adopted
without justification.

## Adams & MacKay BOCD (arXiv 0710.3742)

- Algorithm: exact online run-length posterior; predictive probability
  under a conjugate model, growth step, hazard, normalize. O(t)/step.
- Assumptions: within-segment iid (exchangeable); models change in a
  per-step value stream, not inter-arrival structure.
- Input shape: scalar value sequence; timestamps not modeled; count
  streams require caller-supplied conjugate model plus binning.
- Sparse fit (2-20 events over months): poor — run-length posterior
  barely forms; zero-inflated filler bins dominate.
- Decision: REJECT as the primary challenger; the packet's
  Gamma-Poisson window comparison is a closer fit to sparse counts and
  admits an exact deterministic calculation. Documented as considered.

## Kleinberg, "Bursty and Hierarchical Structure in Streams" (KDD 2002)

- Algorithm: infinite-state automaton; state rate ladder r_q = r0*s^q;
  inter-arrival/gap cost under state q; transition cost gamma*ln(rate
  ratio); hierarchical reachability; Viterbi optimal state sequence.
- Input shape: sorted event timestamps — native fit for sparse streams.
- Sparse fit: structurally right; (s, gamma) dominate at low event
  counts, hence the preregistered parameter sweep.
- Decision: ADAPT ALGORITHM (local implementation).

## gwgundersen/bocd (GitHub; BSD-3-Clause)

- Implements BOCD, single normal-inverse-gamma Gaussian model; log-space.
- Last commit 2023-09-03, 12 commits, educational reference code.
- Dependencies: numpy, scipy, plus unconditional top-level
  `import matplotlib.pyplot`.
- Decision: REJECT for this experiment (same sparse-fit reason as BOCD;
  matplotlib import makes any reuse unattractive).

## hitalex/pybursts (GitHub/PyPI; MIT)

- Implements Kleinberg (port of R `bursts`); API `offsets, s, gamma` →
  hierarchical burst intervals.
- Last commit 2014-12-08 — dormant >10 years; numpy only; no input
  validation; expects small non-negative offsets.
- Decision: ADAPT ALGORITHM — local port of its Kleinberg formulation
  with MIT attribution preserved in the calibration script header; no
  PyPI dependency (dead since 2014).

## Alternatives checked

- romain-fontugne/pybursts fork: dormant (2017). No advantage.
- nmarinsek/burst_detection (PyPI 0.1.3, 2018): two-state batched
  Kleinberg; license "free for non-commercial use" — license problem;
  rejected.
- hildensia/bayesian_changepoint_detection: most maintained BOCD-adjacent
  (2025) but heavier value-stream package; unsuited to sparse streams.

## Reuse decision summary

- Kleinberg: local port of pybursts (MIT) formulation, adapted to 14-day
  bins of distinct-EU occurrence (offsets = bin indices of EU
  occurrences), states truncated per pybursts logic.
- Gamma-Poisson Bayesian rate change: exact conjugate posteriors +
  deterministic Gauss-Legendre quadrature for P(lambda_recent >
  multiplier * lambda_baseline); no donor code needed.
- No new runtime dependencies; numpy/scipy already present.
