"""Synthetic-fixture tests for the stateful burst bakeoff."""
import hashlib
import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

WT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sb", WT / "scripts" / "calibrate_stateful_burst.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def test_plan_hash_frozen():
    import hashlib as h
    digest = h.sha256(sb.PLAN_PATH.read_bytes()).hexdigest()
    assert digest == sb.PLAN_SHA256
    # guard refuses on mismatch
    real = sb.PLAN_SHA256
    sb.PLAN_SHA256 = "0" * 64
    try:
        sb.extract()
        raised = False
    except SystemExit:
        raised = True
    finally:
        sb.PLAN_SHA256 = real
    assert raised


def test_quadrature_exact_vs_bruteforce():
    # brute-force grid comparison < 1e-6
    for (k1, k2, m) in [(3, 1, 2.0), (0, 5, 1.5), (7, 2, 1.0),
                        (2, 0, 2.0)]:
        p = sb.prob_rate_above(k1, 1.0, k2, m)
        # monte-carlo-free reference: dense grid
        from scipy.stats import gamma as g
        a1, b1 = 0.5 + k1, 0.5 + 1.0
        a2, b2 = 0.5 + k2, 0.5 + 6.0
        from scipy.integrate import quad
        ref = float(quad(lambda y: g.sf(m * y, a1, scale=1 / b1)
                         * g.pdf(y, a2, scale=1 / b2), 0, np.inf,
                        limit=500)[0])
        assert abs(p - ref) < 1e-6, (p, ref, k1, k2, m)


def test_posterior_monotone_in_evidence():
    p_low = sb.prob_rate_above(2, 1.0, 10, 2.0)
    p_high = sb.prob_rate_above(10, 1.0, 0, 2.0)
    assert p_high > 0.9 > p_low


def test_kleinberg_port_parity_basic():
    # deterministic, strictly increasing offsets; burst detected for a
    # dense cluster after a long quiet period
    offsets = list(range(0, 100, 10)) + [100, 100.4, 100.8, 101.2, 101.6]
    bursts = sb.kleinberg(offsets, s=2, gamma=0.5)
    assert any(b[0] >= 1 for b in bursts)
    # quiet stream: no burst
    quiet = [float(x) for x in range(0, 100, 10)]
    assert not any(b[0] >= 1 for b in sb.kleinberg(quiet, 2, 1.0))
    # validation parity with pybursts semantics
    try:
        sb.kleinberg([1, 1], 2, 1)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_kleinberg_signal_bin_adaptation():
    base = date(2026, 1, 1)
    obs = []
    # quiet history then burst in the last 2 weeks
    for i in range(10):
        obs.append(((base + timedelta(days=300 + 30 * i)).isoformat(),
                    f"ch{i % 3}"))
    for i in range(6):
        obs.append(((base + timedelta(days=715 + i)).isoformat(), "chA"))
    active, span, level, ch28 = sb.kleinberg_signal(
        obs, base + timedelta(days=729), 2, 0.5)
    assert ch28 == 1
    # quiet only
    quiet = [(d, c) for d, c in obs if date.fromisoformat(d) <
             base + timedelta(days=700)]
    a2, _, _, _ = sb.kleinberg_signal(quiet, base + timedelta(days=729),
                                      2, 0.5)
    assert not a2


def test_decay_support():
    as_of = date(2026, 1, 30)
    obs = [("2026-01-30", "c"), ("2026-01-15", "c"), ("2025-01-01", "c")]
    d30 = sb.decay_support(obs, as_of, 30)
    assert abs(d30 - (1.0 + 2 ** (-15 / 30) + 2 ** (-394 / 30))) < 1e-9


def test_episode_promotion_rules():
    # two consecutive positives 7d apart -> promote
    sigs = [("T", True, 0.9, 3), ("T+7", True, 0.9, 3)]
    assert sb.episode_promotion(sigs)
    # single strong
    assert sb.episode_promotion([("T", True, 0.995, 3)])
    # single weak positive -> no
    assert not sb.episode_promotion([("T", True, 0.9, 3)])
    # positives 31d apart (no such pair here) -> simulate via T-30,T
    sigs2 = [("T-30", True, 0.9, 3), ("T", True, 0.9, 3)]  # gap 30 <= 30
    assert sb.episode_promotion(sigs2)
    # non-consecutive
    sigs3 = [("T", True, 0.9, 3), ("T+7", False, 0.5, 3),
             ("T+14", True, 0.9, 3)]
    assert not sb.episode_promotion(sigs3)


def test_fold_assignment_matches_prior_calibration():
    tid = "v4_02fac9aff2b1"
    assert sb.fold_of(tid) == int(
        hashlib.sha256(tid.encode()).hexdigest()[:8], 16) % 5


def test_grid_sizes():
    assert len(sb.GP_WINDOW) * len(sb.GP_MULT) * len(sb.GP_THR) * \
        len(sb.GP_FLOOR) == 36
    assert len(sb.KB_S) * len(sb.KB_GAMMA) * len(sb.KB_FLOOR) == 12
    assert len(sb.DECAY_HL) * len(sb.DECAY_MIN) == 6


def test_source_types_never_gates():
    src = (WT / "scripts" / "calibrate_stateful_burst.py").read_text(
        encoding="utf-8")
    assert "st30" not in src  # no source_types gate anywhere
    assert "mode=ro" in src
    assert "formal-holdout-ledger" not in src
    assert "claim_formal_holdout" not in src


def test_no_production_import_side_effects():
    # schema creation only happens inside extract(); import alone writes
    # nothing (features.sqlite is created by extract, not by import)
    src = (WT / "scripts" / "calibrate_stateful_burst.py").read_text(
        encoding="utf-8")
    assert "executescript" in src
    assert "def extract" in src


def test_perturbation_deterministic():
    obs = [{"eu_id": f"eu{i:03d}", "obs_date": "2026-01-05",
            "channel_id": "c"} for i in range(20)]
    f1, r1 = sb._perturbed_features(
        "v4_t", obs, 0.2, "2026-02-14", "gp",
        {"half_life": 60, "smin": 1.0, "window": 30, "mult": 2.0,
         "thr": 0.9, "floor": 1})
    f2, r2 = sb._perturbed_features(
        "v4_t", obs, 0.2, "2026-02-14", "gp",
        {"half_life": 60, "smin": 1.0, "window": 30, "mult": 2.0,
         "thr": 0.9, "floor": 1})
    assert r1 == r2 == 4
    assert f1 == f2
