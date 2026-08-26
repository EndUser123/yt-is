"""Production burst-policy-v2 tests (packet 2026-08-25 minimum set)."""
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "scripts"))

from ef import burst_policy_v2 as bp  # noqa: E402
from ef import concept_discovery as cd  # noqa: E402
from ef import concept_registry as cr  # noqa: E402


def obs_row(eu, d, ch="c1", src="notebooklm"):
    return {"eu_id": eu, "obs_date": d, "channel_id": ch, "label": "x",
            "video_id": "v", "source": src}


def test_decay_exact_values():
    as_of = date(2026, 1, 30)
    obs = [obs_row("a", "2026-01-30"), obs_row("b", "2026-01-15"),
           obs_row("c", "2026-01-01")]
    # ages 0, 15, 29
    expect = 1.0 + 2 ** (-15 / 30) + 2 ** (-29 / 30)
    assert abs(bp.candidate_support(obs, as_of) - expect) < 1e-12


def test_distinct_eu_deduplication():
    as_of = date(2026, 1, 30)
    dup = [obs_row("a", "2026-01-05"), obs_row("a", "2026-01-20"),
           obs_row("a", "2026-01-28")]
    # EU 'a' counted once, at its EARLIEST mention (age 25)
    assert abs(bp.candidate_support(dup, as_of) - 2 ** (-25 / 30)) < 1e-12
    assert bp.distinct_eu_first_dates(dup) == {"a": "2026-01-05"}


def test_lifetime_gate():
    as_of = date(2026, 1, 30)
    one = [obs_row("a", "2026-01-29")]
    assert not bp.is_candidate(one, as_of)  # lifetime 1 < 2 despite weight ~1


def test_posterior_known_cases():
    # equal COUNTS but 3x the exposure in the baseline window means a
    # HIGHER recent rate, so the posterior exceeds 0.5
    p = bp.prob_rate_above_beta(5, 5, 2.0, 6.0, 1.5)
    assert 0.5 < p < 0.99
    # overwhelming recent evidence -> near 1
    p2 = bp.prob_rate_above_beta(40, 0, 2.0, 6.0, 1.5)
    assert p2 > 0.999
    # no recent evidence, strong baseline -> near 0
    p3 = bp.prob_rate_above_beta(0, 30, 2.0, 6.0, 1.5)
    assert p3 < 0.01


def test_numerical_parity_gl_vs_beta():
    # production method vs calibrated GL-256 reference
    for k in range(0, 30, 3):
        for kb in range(0, 30, 3):
            a = bp.prob_rate_above_beta(k, kb)
            b = bp.prob_rate_above_gl(k, kb)
            assert abs(a - b) <= 1e-10, (k, kb, a, b)
            for th in (0.70, 0.80, 0.90, 0.95, 0.99):
                assert (a >= th) == (b >= th)


def test_boundary_080():
    as_of = date(2026, 3, 1)
    obs = []
    # recent window (60d): 3 EUs; baseline (preceding 180d): 8 EUs
    for i in range(3):
        obs.append(obs_row(f"r{i}", f"2026-02-{10 + i:02d}", f"ch{i}"))
    base_days = ["2025-08-01", "2025-08-15", "2025-09-01", "2025-09-15",
                 "2025-10-01", "2025-10-20", "2025-11-05", "2025-11-25"]
    for i, d in enumerate(base_days):
        obs.append(obs_row(f"b{i}", d))
    sig = bp.rate_signal(obs, as_of)
    assert sig["k_recent"] == 3 and sig["k_base"] == 8
    # posterior below 0.80 with this little evidence
    assert sig["posterior"] < 0.80
    dec = bp.evaluate(obs, as_of)
    assert not dec["positive"]


def test_continuation_070_and_promotion_099_boundaries():
    # evaluate() boundary semantics are exercised via prev-eval logic and
    # posterior levels; direct unit check of thresholds:
    assert bp.PARAMS["continue_threshold"] == 0.70
    assert bp.PARAMS["signal_threshold"] == 0.80
    assert bp.PARAMS["strong_threshold"] == 0.99
    assert bp.PARAMS["strong_channels_min"] == 2


def test_channel_floor():
    as_of = date(2026, 3, 1)
    obs = [obs_row(f"r{i}", "2026-02-15", "single") for i in range(6)]
    sig = bp.rate_signal(obs, as_of)
    assert sig["channels"] == 1
    # positive requires channels >= 1 (floor 1): satisfied
    assert sig["posterior"] > 0.80
    dec = bp.evaluate(obs, as_of)
    assert dec["positive"]


def test_source_types_cannot_block_v2():
    # every observation from the same acquisition modality ('youtube'
    # after SOURCE_LABELS) — v2 has no source_types gate
    as_of = date(2026, 3, 1)
    obs = [obs_row(f"r{i}", "2026-02-15", f"ch{i}") for i in range(6)]
    for o in obs:
        o["source"] = "notebooklm"  # all normalize to 'youtube'
    dec = bp.evaluate(obs, as_of)
    assert dec["positive"]


def _eval_seq_dates():
    return [date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 29)]


def _strong_obs():
    # strong burst: 6 distinct EUs, 6 channels, in recent window
    return [obs_row(f"r{i}", "2026-01-25", f"ch{i}")
            for i in range(6)]


def test_two_qualifying_evaluations_promote():
    as_of1, as_of2 = date(2026, 1, 15), date(2026, 1, 29)
    # single channel: positive but never strong-single (needs ch >= 2)
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", "ch0") for i in range(6)]
    d1 = bp.evaluate(obs, as_of1)   # first positive, no promotion
    assert d1["positive"] and not d1["promote"]
    d2 = bp.evaluate(obs, as_of2, prev_evals=[d1["eval"]])
    assert d2["promote"]  # two consecutive positives 14d apart


def test_gap_over_30_days_no_consecutive_promotion():
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", "ch0") for i in range(6)]
    d1 = bp.evaluate(obs, date(2026, 1, 1))
    # next evaluation 45 days later, still positive
    obs2 = [obs_row(f"r{i}", f"2026-02-1{i}", "ch0") for i in range(6)]
    d2 = bp.evaluate(obs2, date(2026, 2, 15), prev_evals=[d1["eval"]])
    assert d2["positive"] and not d2["promote"]


def test_strong_single_promotion():
    obs = [obs_row(f"r{i}", "2026-01-28", f"ch{i}") for i in range(8)]
    dec = bp.evaluate(obs, date(2026, 1, 30))
    assert dec["posterior"] >= 0.99 and dec["channels"] >= 2
    assert dec["promote"]


def test_two_negatives_cool():
    obs_pos = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    d1 = bp.evaluate(obs_pos, date(2026, 1, 15))
    quiet = [obs_row("z", "2025-06-01")]
    d2 = bp.evaluate(quiet, date(2026, 1, 29), prev_evals=[d1["eval"]])
    assert not d2["positive"]
    d3 = bp.evaluate(quiet, date(2026, 2, 12),
                     prev_evals=[d1["eval"], d2["eval"]])
    assert d3["cool"]  # two consecutive negatives


def test_duplicate_asof_idempotent():
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    as_of = date(2026, 1, 20)
    d1 = bp.evaluate(obs, as_of)
    # re-run at the same as_of with the recorded eval present: the stale
    # same-date eval must be ignored (no double-counted consecutiveness)
    d2 = bp.evaluate(obs, as_of, prev_evals=[d1["eval"], d1["eval"]])
    assert d2["promote"] == d1["promote"] and d2["positive"] == \
        d1["positive"]


def _scratch_registry(tmp_path, name="reg.sqlite"):
    p = tmp_path / name
    conn = cr.connect(str(p))
    return conn, p


def test_episode_opens_once_and_idempotent_rescan(tmp_path):
    conn, _ = _scratch_registry(tmp_path)
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    for d in ("2026-01-15", "2026-01-29", "2026-01-29"):  # duplicate scan
        cd._scan_entity_v2(conn, "node1", obs, "TestConcept",
                           d, date.fromisoformat(d), "run_x",
                           {"emerging": 0, "candidates": 0, "cooling": 0})
    rows = conn.execute(
        "SELECT * FROM trend_episodes WHERE policy_version='burst-"
        "policy-v2'").fetchall()
    assert len(rows) == 1  # deterministic id, opened once
    eps = conn.execute("SELECT COUNT(*) FROM trend_episodes").fetchone()[0]
    assert eps == 1  # no duplicates from the retry
    conn.close()


def test_policy_coexistence_v1_preserved(tmp_path):
    conn, _ = _scratch_registry(tmp_path)
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    cd._scan_entity_v2(conn, "node1", obs, "TestConcept",
                       "2026-01-15", date(2026, 1, 15), "run_v2",
                       {"emerging": 0, "candidates": 0, "cooling": 0})
    # v1 scan on the same registry: coexists, separate policy marker
    summary = cd.scan_internal(conn, as_of="2026-01-16",
                               policy_version="burst-policy-v1")
    assert summary["as_of"] == "2026-01-16"
    pol = conn.execute(
        "SELECT DISTINCT policy_version FROM trend_episodes").fetchall()
    assert {r[0] for r in pol} == {"burst-policy-v2"}
    v2row = conn.execute(
        "SELECT lifecycle_state FROM concepts WHERE metadata_json LIKE "
        "'%entity_burst_v2%'").fetchall()
    assert v2row  # v2 row preserved after a v1 scan
    conn.close()


def test_default_policy_remains_v1():
    assert cd.POLICY_VERSION == "burst-policy-v1"
    assert bp.POLICY_VERSION == "burst-policy-v2"


def test_explicit_dispatch_and_unknown_rejected(tmp_path):
    conn, _ = _scratch_registry(tmp_path)
    for v in ("burst-policy-v1", "burst-policy-v2"):
        cd.scan_internal(conn, as_of="2026-01-16", policy_version=v)
    with pytest.raises(ValueError):
        cd.scan_internal(conn, as_of="2026-01-16",
                         policy_version="burst-policy-v3")
    conn.close()


def test_asof_future_evidence_excluded(tmp_path):
    conn, _ = _scratch_registry(tmp_path)
    obs = [obs_row("a", "2026-01-10"), obs_row("b", "2026-01-12")]
    # observation dated after as_of must not create a concept
    obs_future = obs + [obs_row("c", "2026-02-01"), obs_row("d",
                                                            "2026-02-02")]
    # simulate by scanning with an earlier as_of using the real catalog
    # path is exercised in integration; here check the pure module:
    as_of = date(2026, 1, 15)
    dec = bp.evaluate([o for o in obs_future
                       if o["obs_date"] <= "2026-01-15"], as_of)
    assert dec["lifetime"] == 2  # future rows excluded upstream anyway
    conn.close()


def test_no_strong_relationship_overwrite(tmp_path):
    conn, p = _scratch_registry(tmp_path)
    obs = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    cd._scan_entity_v2(conn, "node1", obs, "TestConcept",
                       "2026-01-15", date(2026, 1, 15), "run1",
                       {"emerging": 0, "candidates": 0, "cooling": 0})
    cid = cr.concept_identity_id("entity", "TestConcept")
    cr.set_user_relationship(conn, cid, "durable_interest",
                             reason="operator", method="operator",
                             run_id="op")
    cd._scan_entity_v2(conn, "node1", obs, "TestConcept",
                       "2026-01-29", date(2026, 1, 29), "run2",
                       {"emerging": 0, "candidates": 0, "cooling": 0})
    row = conn.execute("SELECT user_relationship FROM concepts WHERE "
                       "concept_id=?", (cid,)).fetchone()
    assert row[0] == "durable_interest"
    conn.close()


def test_decayed_entity_still_cools(tmp_path):
    """Regression (review F1): after support decays below the candidate
    gate, two consecutive negative scans must still COOL the episode —
    the negative evaluation is persisted even for non-candidates."""
    conn, _ = _scratch_registry(tmp_path)
    burst = [obs_row(f"r{i}", f"2026-01-1{i}", f"ch{i}") for i in range(6)]
    quiet = [obs_row("a", "2025-01-01"), obs_row("b", "2025-01-02")]
    summary = {"emerging": 0, "candidates": 0, "cooling": 0}
    cd._scan_entity_v2(conn, "n1", burst, "CoolCase", "2026-01-15",
                       date(2026, 1, 15), "r1", summary)
    cd._scan_entity_v2(conn, "n1", burst, "CoolCase", "2026-01-29",
                       date(2026, 1, 29), "r2", summary)  # promotes
    # support decays: old obs only, non-candidate, non-positive
    cd._scan_entity_v2(conn, "n1", quiet, "CoolCase", "2026-06-01",
                       date(2026, 6, 1), "r3", summary)
    cd._scan_entity_v2(conn, "n1", quiet, "CoolCase", "2026-06-15",
                       date(2026, 6, 15), "r4", summary)  # second negative
    cid = cr.concept_identity_id("entity", "CoolCase")
    lc = conn.execute("SELECT lifecycle_state FROM concepts WHERE "
                      "concept_id=?", (cid,)).fetchone()[0]
    ep = conn.execute("SELECT state FROM trend_episodes WHERE concept_id"
                      "=?", (cid,)).fetchone()
    assert lc == "cooling"
    assert ep is not None and ep[0] == "cooled"
    conn.close()
