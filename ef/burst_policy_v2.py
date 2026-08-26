"""burst-policy-v2 — calibrated Gamma-Poisson stateful burst policy.

SHADOW policy: implemented and explicitly selectable, but NOT the
production default until it passes a completely new unseen formal
holdout (architect decision 2026-08-25).

Calibration lineage: consumed holdout-v4 (TRAINING_DIAGNOSTIC_ONLY),
preregistered plan sha256 a04ee19897ef318daa658caf8d27995a1caa11a556e
0def050f340c52c24b957, conclusion BAYESIAN_EPISODES_SUPPORTED,
publication da53eba76fc2c2468b33fe084dfc442a06f01dcb.

Pure algorithm module: no I/O, no registry access. Orchestration and
persistence live in ef/concept_discovery.py; the registry's existing
trend_episodes table is the durable episode store.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
from scipy.special import betainc

POLICY_VERSION = "burst-policy-v2"

# Calibrated parameters (stateful-burst-v1 preregistration; DO NOT tune
# without a new architect packet).
PARAMS = {
    "candidate_half_life_days": 30,
    "candidate_support_min": 1.5,
    "candidate_lifetime_min": 2,
    "recent_window_days": 60,
    "baseline_window_days": 180,
    "prior_alpha": 0.5,
    "prior_beta": 0.5,
    "rate_multiplier": 1.5,
    "signal_threshold": 0.80,
    "continue_threshold": 0.70,
    "strong_threshold": 0.99,
    "strong_channels_min": 2,
    "channels_min": 1,
    "consecutive_gap_max_days": 30,
    "time_unit_days": 30,
}

NUMERICAL_METHOD = "beta-incomplete-closed-form"  # validated vs GL-256

_GL_NODES = 256
_GL_X, _GL_W = np.polynomial.legendre.leggauss(_GL_NODES)


def prob_rate_above_gl(k_recent, k_base, exp_recent=2.0, exp_base=6.0,
                       mult=None, alpha=None, beta=None):
    """Calibrated reference: deterministic 256-node Gauss-Legendre
    quadrature of P(lambda_recent > mult * lambda_baseline) with
    y = t^2 substitution (removes the Gamma(0.5) density singularity
    at zero)."""
    mult = mult if mult is not None else PARAMS["rate_multiplier"]
    alpha = alpha if alpha is not None else PARAMS["prior_alpha"]
    beta = beta if beta is not None else PARAMS["prior_beta"]
    from scipy.stats import gamma as gamma_dist
    a1 = alpha + k_recent
    b1 = beta + exp_recent
    a2 = alpha + k_base
    b2 = beta + exp_base
    y_max = float(gamma_dist.ppf(1 - 1e-13, a2, scale=1.0 / b2))
    if y_max <= 0:
        return 0.0
    t_max = math.sqrt(y_max)
    ts = 0.5 * t_max * (_GL_X + 1.0)
    ws = 0.5 * t_max * _GL_W
    ys = ts * ts
    vals = gamma_dist.sf(mult * ys, a1, scale=1.0 / b1) * \
        gamma_dist.pdf(ys, a2, scale=1.0 / b2) * 2.0 * ts
    return float(np.clip(np.dot(ws, vals), 0.0, 1.0))


def prob_rate_above_beta(k_recent, k_base, exp_recent=2.0, exp_base=6.0,
                         mult=None, alpha=None, beta=None):
    """Exact closed form via the beta-prime relationship. With
    U = b_r * lambda_r ~ Gamma(a_r, 1) and W = lambda_b-related
    unit-rate Gamma, U/(cW) is beta-prime(a_r, a_b) with
    c = mult * b_r / b_b, so
    P(lambda_r > mult * lambda_b) = 1 - I_{c/(1+c)}(a_r, a_b)."""
    mult = mult if mult is not None else PARAMS["rate_multiplier"]
    alpha = alpha if alpha is not None else PARAMS["prior_alpha"]
    beta = beta if beta is not None else PARAMS["prior_beta"]
    a_r = alpha + k_recent
    b_r = beta + exp_recent
    a_b = alpha + k_base
    b_b = beta + exp_base
    c = mult * b_r / b_b
    x = c / (1.0 + c)
    return float(1.0 - betainc(a_r, a_b, x))


def prob_rate_above(k_recent, k_base, **kw):
    """Production posterior. NUMERICAL_METHOD selects the implementation;
    both are validated equivalent to <=1e-10 with zero decision
    differences on all v4 training points and parameter combinations."""
    if NUMERICAL_METHOD == "beta-incomplete-closed-form":
        return prob_rate_above_beta(k_recent, k_base, **kw)
    return prob_rate_above_gl(k_recent, k_base, **kw)


def distinct_eu_first_dates(obs):
    """obs: iterable of mappings with eu_id and obs_date. Returns
    {eu_id: first_date_str} — duplicates never increase support."""
    first = {}
    for o in obs:
        eu = o["eu_id"]
        d = o["obs_date"]
        if eu not in first or d < first[eu]:
            first[eu] = d
    return first


def candidate_support(obs, as_of_d: date, half_life=None) -> float:
    half_life = half_life or PARAMS["candidate_half_life_days"]
    h = float(half_life)
    total = 0.0
    for d in distinct_eu_first_dates(obs).values():
        total += 2.0 ** (-((as_of_d - date.fromisoformat(d)).days) / h)
    return total


def is_candidate(obs, as_of_d: date) -> bool:
    if len(distinct_eu_first_dates(obs)) < PARAMS["candidate_lifetime_min"]:
        return False
    return candidate_support(obs, as_of_d) >= \
        PARAMS["candidate_support_min"]


def rate_signal(obs, as_of_d: date):
    """Distinct-EU counts, channels and posterior for the v2 windows.
    Returns dict with k_recent, k_base, channels, posterior."""
    first = distinct_eu_first_dates(obs)
    rs = as_of_d - timedelta(days=PARAMS["recent_window_days"])
    bs = rs - timedelta(days=PARAMS["baseline_window_days"])
    rec_eus = []
    k_base = 0
    for eu, d in first.items():
        dd = date.fromisoformat(d)
        if rs < dd <= as_of_d:
            rec_eus.append(eu)
        elif bs < dd <= rs:
            k_base += 1
    channels = len({o["channel_id"] for o in obs if o["eu_id"] in
                    set(rec_eus)})
    exp_r = PARAMS["recent_window_days"] / PARAMS["time_unit_days"]
    exp_b = PARAMS["baseline_window_days"] / PARAMS["time_unit_days"]
    post = prob_rate_above(len(rec_eus), k_base, exp_recent=exp_r,
                           exp_base=exp_b)
    return {"k_recent": len(rec_eus), "k_base": k_base,
            "channels": channels, "posterior": round(post, 6)}


def evaluate(obs, as_of_d: date, prev_evals=None):
    """One policy evaluation of an entity.

    prev_evals: list of up to two prior evaluation records
    [{"as_of", "positive"}, ...] in chronological order (most recent
    last), from the concept's stored metadata. Used for the two-
    consecutive-evaluation promotion/cooling logic. A prior record with
    the SAME as_of is treated as a duplicate re-run (idempotence): it is
    ignored for transition purposes and replaced.

    Returns the full decision record for persistence + transition:
    {
      candidate, positive, posterior, promote, continue_active, cool,
      k_recent, k_base, channels, support, lifetime, eval
    }
    """
    prev = [e for e in (prev_evals or [])
            if e.get("as_of") != as_of_d.isoformat()][-2:]
    support = candidate_support(obs, as_of_d)
    lifetime = len(distinct_eu_first_dates(obs))
    candidate = (lifetime >= PARAMS["candidate_lifetime_min"]
                 and support >= PARAMS["candidate_support_min"])
    # The Bayesian signal is computed UNGATED by the candidate rule (the
    # episode machine opens on the first positive signal; the candidate
    # gate controls concept/monitoring persistence, not the signal).
    sig = rate_signal(obs, as_of_d)
    positive = sig["posterior"] >= PARAMS["signal_threshold"] and \
        sig["channels"] >= PARAMS["channels_min"]
    promote = False
    if positive and prev:
        last = prev[-1]
        gap = (as_of_d - date.fromisoformat(last["as_of"])).days
        if last.get("positive") and 0 < gap <= \
                PARAMS["consecutive_gap_max_days"]:
            promote = True
    if sig["posterior"] >= PARAMS["strong_threshold"] and \
            sig["channels"] >= PARAMS["strong_channels_min"]:
        promote = True
    continue_active = positive or \
        sig["posterior"] >= PARAMS["continue_threshold"]
    cool = (not positive) and bool(prev) and not prev[-1].get("positive")
    return {
        "candidate": candidate, "positive": positive,
        "posterior": sig["posterior"], "promote": promote,
        "continue_active": continue_active, "cool": cool,
        "k_recent": sig["k_recent"], "k_base": sig["k_base"],
        "channels": sig["channels"],
        "support": round(support, 4), "lifetime": lifetime,
        "recent_count_30d": sum(
            1 for d in distinct_eu_first_dates(obs).values()
            if as_of_d - timedelta(days=30) <
            date.fromisoformat(d) <= as_of_d),
        "first_seen": min(distinct_eu_first_dates(obs).values(),
                          default=as_of_d.isoformat()),
        "eval": {"as_of": as_of_d.isoformat(), "positive": positive},
    }
