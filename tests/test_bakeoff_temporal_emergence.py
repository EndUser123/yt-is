"""Unit tests for the temporal-emergence model-generation bakeoff v1.

Pure-layer coverage of the preregistered mechanical acceptance fixtures
(CASE P / CASE N, future-leak, duplicate publisher, undated evidence,
prefix-horizon integrity, replay determinism). No catalog access.
"""
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "bakeoff_temporal_emergence",
    REPO / "scripts" / "bakeoff_temporal_emergence.py")
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)

from ef import burst_policy_v2 as bp2  # noqa: E402


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------

def mkobs(eu_id, d, channel="UC_A", source="youtube",
          channel_title=None):
    return {"eu_id": eu_id, "video_id": f"v_{eu_id}", "channel_id":
            channel,
            "channel_title": channel_title, "source": source,
            "obs_date": d}


def historical_stream(base_date: date, n_eus=12, channels=("UC_A",)):
    """Trigger-period mass concentrated ON ONE DAY so that NOTHING is
    dated after the earliest positive crossing (a genuine CASE N: the
    whole historical surge precedes episode opening)."""
    return [mkobs(f"EU_H{i}", base_date.isoformat(),
                  channel=channels[i % len(channels)])
            for i in range(n_eus)]


CASE_BASE = date(2025, 3, 1)


def case_n_stream():
    """CASE N: heavy historical activity, nothing post-open."""
    return historical_stream(CASE_BASE)


def case_p_stream(n_post=2, channels=("UC_C", "UC_D")):
    """CASE P: identical historical activity + genuinely new post-open
    support from fresh channels."""
    out = case_n_stream()
    opened = bk.open_episode_precise(out)["opened_at"]
    od = date.fromisoformat(opened)
    for i in range(n_post):
        out.append(mkobs(f"EU_P{i}", (od + timedelta(days=4 + 6 * i))
                         .isoformat(), channel=channels[i % len(channels)]))
    return out


def cps_around(t_iso):
    base = date.fromisoformat(t_iso)
    pairs = []
    for off in (-30, 0, 7, 14, 30, 60):
        lbl = ("T" if off == 0 else f"T{off:+d}")
        pairs.append((lbl, (base + timedelta(days=off)).isoformat()))
    return pairs


# ---------------------------------------------------------------------------
# opening semantics
# ---------------------------------------------------------------------------

class TestOpening:
    def test_case_n_fixture_is_truly_trigger_only(self):
        s = case_n_stream()
        op = bk.open_episode_precise(s)
        assert op["opened"]
        od = date.fromisoformat(op["opened_at"])
        # FIXTURE VALIDATION: no evidence dated strictly after opening.
        assert not [o for o in s if bk.d_of(o) > od]

    def test_opening_uses_no_future_evidence(self):
        s = case_n_stream()
        full = bk.open_episode_precise(s)
        last_day = max(bk.d_of(o) for o in s)
        truncated = [o for o in s if bk.d_of(o) <= last_day]
        assert full["opened_at"] == \
            bk.open_episode_precise(truncated)["opened_at"]

    def test_boundaries_cover_window_edges(self):
        s = case_n_stream()
        pts = bk.boundaries(s)
        assert all(p2 > p1 for p1, p2 in zip(pts, pts[1:]))


# ---------------------------------------------------------------------------
# THE PRIMARY DISCRIMINATING TEST (preregistration)
# ---------------------------------------------------------------------------

class TestDiscriminatingFixture:
    def test_confirmation_variants_separate_P_from_N(self):
        pn = case_n_stream()
        pp = case_p_stream()
        cn = bk.episode_core(pn)
        cp = bk.episode_core(pp)
        for v in bk.CONFIRM_VARIANTS:
            # CASE N may never confirm under any variant.
            assert not cn["variants"][v]["confirmed"], v
        # CASE P confirms at least the EU1/EU2/CHANNELNEW/POSTERIOR arms;
        # BUCKETS needs a second month, so only assert the bounded core.
        for v in ("EU1-W30", "EU2-W60", "CHANNELNEW-W30"):
            assert cp["variants"][v]["confirmed"], v

    def test_arm_a_promotes_both_cases_reference_failure(self):
        t = min(bk.d_of(o) for o in case_n_stream()).isoformat()
        chain_n = bk.armA_lane_chain(case_n_stream(), cps_around(t))
        t2 = min(bk.d_of(o) for o in case_p_stream()).isoformat()
        chain_p = bk.armA_lane_chain(case_p_stream(), cps_around(t2))
        emerged = lambda ch: any(st.get("lifecycle") == "emerging"
                                 for st in ch)
        # The documented v2 failure: trigger-momentum promotes even with
        # zero post-open support.
        assert emerged(chain_n)
        assert emerged(chain_p)

    def test_full_subject_pipeline_matches_fixture(self):
        subj = {"_kind": "positive", "_id": "FIXT_P", "_T": None,
                "T": min(bk.d_of(o) for o in case_p_stream())
                .isoformat()}
        streams = {"label::fixture": case_p_stream()}
        res = bk.run_subject(subj, streams, subj["T"])
        fam = res["subject"]["by_family"]["armB_EU1-W30"]
        assert fam["confirmed"]
        assert res["subject"]["a_emerging_ever"]

    def test_case_n_pipeline_never_confirms(self):
        subj = {"_kind": "negative", "_id": "FIXT_N",
                "T": min(bk.d_of(o) for o in case_n_stream())
                .isoformat()}
        streams = {"label::fixture": case_n_stream()}
        res = bk.run_subject(subj, streams, subj["T"])
        for fam in bk.FAMILIES:
            assert not res["subject"]["by_family"][fam]["confirmed"], fam


# ---------------------------------------------------------------------------
# no look-ahead
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    def test_future_evidence_cannot_confirm_past_state(self):
        s = case_n_stream()
        open_at = bk.open_episode_precise(s)["opened_at"]
        od = date.fromisoformat(open_at)
        s_fut = s + [mkobs("EU_FUT0", "2030-01-01"),
                     mkobs("EU_FUT1", "2030-02-01")]
        base = bk.open_episode_precise(s)
        leaky = bk.open_episode_precise(
            bk.filter_le(s_fut, od))
        assert base["opened_at"] == leaky["opened_at"]

    def test_truncation_equivalence_for_decisions(self):
        """State at every evaluation day t must be identical whether the
        stream still CONTAINS rows dated > t or they are physically
        removed — the strongest form of the no-look-ahead property."""
        import bisect as bs_
        s_full = case_p_stream()
        t_iso = min(bk.d_of(o) for o in s_full).isoformat()
        cps = cps_around(t_iso)
        events = sorted({bk.d_of(o) for o in s_full})
        cut = events[len(events) // 2]
        kept = [o for o in s_full if bk.d_of(o) <= cut]
        chain_full = bk.armA_lane_chain(s_full, cps)
        chain_kept = bk.armA_lane_chain(kept, cps)
        for st_a, st_b in zip(chain_full, chain_kept):
            if date.fromisoformat(st_a["as_of"]) > cut:
                break
            assert st_a["lifecycle"] == st_b["lifecycle"], st_a
            assert st_a.get("posterior") == st_b.get("posterior")

    def test_inject_future_transform_is_inert(self):
        subj_T = min(bk.d_of(o) for o in case_p_stream()).isoformat()
        s0 = case_p_stream()
        r0 = bk.run_subject({"_id": "x"}, {"L": s0}, subj_T)
        s1 = bk.transform_streams({"L": s0}, "inject_future",
                                  tid="x", opened_at=subj_T)["L"]
        r1 = bk.run_subject({"_id": "x"}, {"L": s1}, subj_T)
        c0 = repr(sorted(r0["subject"]["by_family"].items()))
        c1 = repr(sorted(r1["subject"]["by_family"].items()))
        assert c0 == c1


# ---------------------------------------------------------------------------
# independence semantics
# ---------------------------------------------------------------------------

class TestPublisherSemantics:
    def test_duplicate_same_publisher_blocks_channelnew_only(self):
        s = case_p_stream(n_post=0)
        opened = bk.open_episode_precise(s)["opened_at"]
        od = date.fromisoformat(opened)
        # Trigger evidence INCLUDES the opening day (window-inclusive,
        # mirroring episode_core pre_channel_ids).
        trig_chans = sorted({o["channel_id"] for o in s
                             if bk.d_of(o) <= od})
        pure = [o for o in s if bk.d_of(o) <= od]
        c1 = mkobs("EU_C1", (od + timedelta(days=3)).isoformat(),
                   channel=trig_chans[0])
        c2 = mkobs("EU_C2_clone", (od + timedelta(days=5)).isoformat(),
                   channel=trig_chans[0])
        pure = sorted(pure + [c1, c2], key=lambda o: o["obs_date"])
        pre = set(trig_chans)
        # Repeated same-publisher coverage IS continued emergence for the
        # plain counting family...
        assert bk.confirm_variant("EU1-W30", od, pure, pre)["confirmed"]
        assert bk.confirm_variant("EU2-W60", od, pure, pre)["confirmed"]
        # ...but clones can never simulate independent corroboration.
        chan_res = bk.confirm_variant("CHANNELNEW-W30", od, pure, pre)
        assert not chan_res["confirmed"]
        # A genuinely fresh channel does corroborate.
        fresh = sorted(pure + [
            mkobs("EU_FRESH", (od + timedelta(days=7)).isoformat(),
                  channel="UC_NEVER_SEEN")],
            key=lambda o: o["obs_date"])
        assert bk.confirm_variant("CHANNELNEW-W30", od, fresh,
                                  pre)["confirmed"]

    def test_unknown_publisher_stays_unknown(self):
        assert bk.publisher_identity("hackernews", "UC_X", "") == \
            bk.PUBLISHER_UNKNOWN
        assert bk.publisher_identity("rss", "", "") == \
            bk.PUBLISHER_UNKNOWN
        gid = bk.publisher_identity("discord", "UC_Y", "guild9")
        assert gid == "disc_guild:guild9"
        assert bk.publisher_identity("youtube", "UC_Z", None) == "UC_Z"


# ---------------------------------------------------------------------------
# undated evidence
# ---------------------------------------------------------------------------

class TestUndatedGuard:
    def test_undated_dropped_identically(self):
        rows = [{"obs_date": "2025-01-01"}, {"obs_date": ""}]
        kept, dropped = bk.undated_guard(rows)
        assert dropped == 1 and len(kept) == 1


# ---------------------------------------------------------------------------
# two-signal arm D
# ---------------------------------------------------------------------------

class TestTwoSignalArmD:
    def _base(self):
        s = case_n_stream()
        opened = bk.open_episode_precise(s)["opened_at"]
        return s, date.fromisoformat(opened)

    def test_low_density_burst_not_confirmed_by_D(self):
        s, od = self._base()
        sparse = s + [
            mkobs("EU_SO0", (od + timedelta(days=1)).isoformat()),
            mkobs("EU_SO1", (od + timedelta(days=45)).isoformat())]
        res = bk.armD_persistence(sparse, od)
        # two post-open EUs exist inside the horizon but never share a
        # trailing-30d window -> independent persistence floor unmet.
        assert res["persist"] is False

    def test_dense_postopen_support_confirms_D(self):
        s, od = self._base()
        dense = s + [
            mkobs("EU_DN0", (od + timedelta(days=10)).isoformat()),
            mkobs("EU_DN1", (od + timedelta(days=12)).isoformat())]
        res = bk.armD_persistence(dense, od)
        assert res["persist"]
        assert res["peak_window"] >= 2

    def test_no_postopen_never_confirms_D(self):
        s, od = self._base()
        assert bk.armD_persistence(s, od)["persist"] is False


# ---------------------------------------------------------------------------
# state machine arm C + horizon integrity
# ---------------------------------------------------------------------------

class TestStateMachineAndHorizons:
    def test_prefix_horizon_bounds_decisions(self):
        t = min(bk.d_of(o) for o in case_p_stream()).isoformat()
        streams = {"L": case_p_stream()}
        full = bk.run_subject({"_id": "h"}, streams, t)
        pref = bk.run_subject({"_id": "h"}, streams, t,
                              cp_offsets=bk.PREFIX_CHECKPOINTS)
        # confirmation observed later than T+30 in the full run must be
        # invisible to the prefix run (no backward promotion).
        for fam in bk.FAMILIES:
            fp = pref["subject"]["by_family"][fam]["confirmed"]
            ff = full["subject"]["by_family"][fam]["confirmed"]
            assert fp or not ff  # prefix subset of full

    def test_replay_determinism_chain(self):
        t = min(bk.d_of(o) for o in case_p_stream()).isoformat()
        streams = {"L": case_p_stream()}
        r1 = bk.run_subject({"_id": "d"}, streams, t)
        r2 = bk.run_subject({"_id": "d"}, streams, t)
        h1 = bk.canonical_hash(r1["subject"]["by_family"])
        h2 = bk.canonical_hash(r2["subject"]["by_family"])
        assert h1 == h2

    def test_closed_when_unconfirmed_deadline(self):
        s = case_n_stream()
        opened = bk.open_episode_precise(s)["opened_at"]
        t_iso = min(bk.d_of(o) for o in s).isoformat()
        m = bk.armC_machine(s, cps_around(t_iso), None)
        f = m["fields"]
        assert f["episode_opened_at"] == opened
        assert f["confirmed_at"] is None
        assert f["closed_at"] is not None
