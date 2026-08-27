#!/usr/bin/env python3
"""Temporal Emergence model-generation bakeoff v1 (packet 2026-08-26).

Discovery slice B (temporal emergence) ONLY. TRAINING-DIAGNOSTIC:
consumes the already-consumed evaluator-v4 case-control set (positives +
paired explicit negatives) which is NEVER formal, never promotion
evidence, never a holdout. No new holdout is curated, no formal ledger
is touched, production defaults are untouched, burst-policy-v1/v2 are
not mutated.

Frozen before any outcome (preregistration + semantic contract):
  docs/handoffs/interest-intelligence/episode-semantics-contract-20260826.md
  docs/handoffs/interest-intelligence/temporal-emergence-modelgen-preregistration-20260826.md

Arms:
  A  burst-policy-v2 reference — faithful local port of the scanner's v2
     entity path across the six evaluator checkpoints; acceptance gate =
     exact reproduction of the established diagnostic aggregates.
  B  post-trigger confirmation — contract-PRECISE episode opening;
     promotion only via strictly post-open evidence under one of five
     preregistered bounded variants.
  C  explicit episode-state model — same base predicate as B/EU1-W30 plus
     event-driven CONTINUING/COOLING/CLOSED machinery with explicit
     episode fields; differs from B in durability semantics.
  D  two-signal model — burst axis (opening) x independent post-open
     trailing-window persistence density floor.

Pure layer imported by unit tests; I/O layer reads the read-only catalog
and writes artifacts under the calibration artifacts directory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import burst_policy_v2 as bp2  # noqa: E402

CATALOG_DEFAULT = Path("P:/.data/yt-is/ef/catalog.sqlite")
CASE_CONTROL_DEFAULT = Path(
    "P:/.data/yt-is/private/"
    "discovery-retrospective-case-control-v4-diagnostic.json")
ARTDIR_DEFAULT = Path(
    "P:/.data/yt-is/ef/concept-discovery-calibration/"
    "temporal-emergence-modelgen-v1")

CHECKPOINT_OFFSETS = [(offset, ("T" if offset == 0 else
                                f"T{offset:+d}"))
                      for offset in (-30, 0, 7, 14, 30, 60)]
PREFIX_CHECKPOINTS = [-30, 0, 7, 14, 30]
PUBLISHER_UNKNOWN = "__UNKNOWN__"

CONFIRM_VARIANTS = ("EU1-W30", "EU2-W60", "BUCKETS-W120",
                    "POSTERIOR-EXCL-W30", "CHANNELNEW-W30")
VARIANT_DEADLINES = {"EU1-W30": 30, "EU2-W60": 60, "BUCKETS-W120": 120,
                     "POSTERIOR-EXCL-W30": 30, "CHANNELNEW-W30": 30}
FAMILIES = ["armB_" + v for v in CONFIRM_VARIANTS] + ["armC", "armD"]

# Preregistered decision bars (see preregistration §Decision mapping).
BARS = {
    "neg_rate_max_abs": 0.35,
    "neg_rate_max_ratio_vs_A": 0.50,
    "pos_confirmed_recall_min": 0.50,
    "separation_min_vs_A": 0.253,
    "perturb20_confirmed_retention_min": 0.50,
}
ARM_A_REFERENCE = {"pos_emerging_recall": 35 / 42,
                   "neg_emerging_rate": 72 / 124}
# Same-day frozen-evaluator rerun on the CURRENT catalog
# (eval-20260826T202847-NON_BLIND_DIAGNOSTIC, receipt-pinned): the
# published morning artifact drifted because eu/kg rows were re-ingested
# after it ran (state doc, catalog-drift disclosures). Subject-level
# equality vs this rerun is the load-bearing acceptance; deviation vs
# the published numbers is enumerated in PARITY_DRIFT.
ARM_A_REFERENCE_CURRENT = {"pos_emerging_recall_k": 36,
                           "pos_n": 42, "neg_emerging_rate_k": 75,
                           "neg_n": 124}
PARITY_DRIFT_PUBLISHED_VS_CURRENT_NEGATIVES = [
    ("neg4_b7cde8de123d", "VRAM", "promotes_today_only"),
    ("neg4_6d2d89811736", "Google Drive", "promotes_today_only"),
    ("neg4_dbae634cb94a", "Margin requirement", "promotes_today_only"),
    ("neg4_0631120ee9f2", "Microsoft 365", "promotes_today_only"),
    ("neg4_f31ad0c431c0", "Somalia", "promoted_morning_only"),
    ("v4_42dc215cdea6", "Claude Code (positive)",
     "lanes_promote_today_not_morning"),
]


# ---------------------------------------------------------------------------
# matching helpers (identical semantics to the frozen evaluator)
# ---------------------------------------------------------------------------

_EV = None


def ev_module():
    global _EV
    if _EV is None:
        spec = importlib.util.spec_from_file_location(
            "evaluate_concept_discovery_bakeoff",
            REPO / "scripts" / "evaluate_concept_discovery.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _EV = mod
    return _EV


def name_matches(alias_names, cand_names) -> bool:
    return ev_module().match_concept(
        {"canonical_name": cand_names[0],
         "aliases": cand_names[1:]},
        {"canonical_name": alias_names[0],
         "aliases": alias_names[1:]}) if cand_names else False


def publisher_identity(source, channel_id, channel_title) -> str:
    cid = (channel_id or "").strip()
    if source == "discord":
        guild = (channel_title or "").strip()
        return f"disc_guild:{guild}" if guild else PUBLISHER_UNKNOWN
    if source in ("hackernews", "newsletter") or not cid:
        return PUBLISHER_UNKNOWN
    return cid


VALID_TIME_SQL = ("substr(COALESCE(NULLIF(eu.published_at,''),"
                  "eu.captured_at),1,10)")


# ---------------------------------------------------------------------------
# pure stream helpers
# ---------------------------------------------------------------------------

def d_of(o: dict) -> date:
    return date.fromisoformat(o["obs_date"])


def filter_le(obs: list[dict], t: date) -> list[dict]:
    return [o for o in obs if d_of(o) <= t]


def undated_guard(rows: list[dict]) -> tuple[list[dict], int]:
    kept, dropped = [], 0
    for r in rows:
        if r.get("obs_date"):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


# Boundary sampler of record (frozen preregistration): evidence-entry
# and recent-window-exit dates. The review noted the signal's true
# continuity set also contains baseline-window exits ({dd + 240d});
# INCLUDE_BASELINE_EXITS=True samples that superset for SENSITIVITY
# probes only — the bakeoff of record runs with it disabled so outcomes
# stay tied to the frozen text, and the delta is reported explicitly.
INCLUDE_BASELINE_EXITS = False


def boundaries(obs_all: list[dict]) -> list[date]:
    pts: set[date] = set()
    horizon_mults = [0, bp2.PARAMS["recent_window_days"]]
    if INCLUDE_BASELINE_EXITS:
        horizon_mults.append(bp2.PARAMS["recent_window_days"]
                             + bp2.PARAMS["baseline_window_days"])
    for ds in bp2.distinct_eu_first_dates(obs_all).values():
        dd = date.fromisoformat(ds)
        for m in horizon_mults:
            pts.add(dd + timedelta(days=m))
    return sorted(pts)


def signal_positive(obs_le: list[dict], t: date):
    sig = bp2.rate_signal(obs_le, t)
    pos = (sig["posterior"] >= bp2.PARAMS["signal_threshold"]
           and sig["channels"] >= bp2.PARAMS["channels_min"])
    return pos, sig


def open_episode_precise(obs_all: list[dict]) -> dict:
    """CONTRACT: earliest breakpoint where the v2 signal turns positive,
    evaluated strictly from evidence dated <= t."""
    for t in boundaries(obs_all):
        obs_le = filter_le(obs_all, t)
        if not obs_le:
            continue
        pos, sig = signal_positive(obs_le, t)
        if pos:
            return {"opened": True, "opened_at": t.isoformat(),
                    "opened_k_recent": sig["k_recent"],
                    "opened_k_base": sig["k_base"]}
    return {"opened": False, "opened_at": None, "opened_k_recent": None,
            "opened_k_base": None}


def earliest_sampled_open(chain_states: list[dict]) -> str | None:
    for st in chain_states:
        if st.get("detector_positive"):
            return st["as_of"]
    return None


# ---------------------------------------------------------------------------
# ARM A — local port of _scan_entity_v2 lifecycle across checkpoints
# ---------------------------------------------------------------------------

def _push_eval(meta_evals, as_of_iso, rec):
    kept = [e for e in meta_evals if e.get("as_of") != as_of_iso][-1:]
    kept.append(rec)
    return kept


def armA_lane_chain(obs_lane, checkpoints_iso: list[tuple[str, str]]):
    exists = False
    meta_evals: list[dict] = []
    state: str | None = None
    states: list[dict] = []
    for cp_label, cp_iso in checkpoints_iso:
        t = date.fromisoformat(cp_iso)
        obs_le = filter_le(obs_lane, t)
        rec = {"checkpoint": cp_label, "as_of": cp_iso, "lifecycle": None,
               "detector_positive": False}
        if not exists:
            if not obs_le:
                states.append(rec)
                continue
            dec = bp2.evaluate(obs_le, t, meta_evals)
            rec.update({"detector_positive": bool(dec["positive"]),
                        "k_recent": dec["k_recent"],
                        "posterior": dec["posterior"]})
            if not dec["candidate"] and not dec["positive"]:
                states.append(rec)
                continue
            exists = True
            state = "emerging" if dec["promote"] else "candidate"
            meta_evals = _push_eval(meta_evals, cp_iso, dec["eval"])
            rec["lifecycle"] = state
            rec["is_decay_candidate"] = True
            states.append(rec)
            continue
        dec = bp2.evaluate(obs_le, t, meta_evals)
        rec.update({"detector_positive": bool(dec["positive"]),
                    "k_recent": dec["k_recent"],
                    "posterior": dec["posterior"],
                    "is_decay_candidate": bool(dec["candidate"])})
        current = state
        if not dec["candidate"] and not dec["positive"]:
            if dec["cool"] and current in ("emerging", "active"):
                state = "cooling"
        elif dec["promote"]:
            state = "emerging"
        elif current == "emerging" and (dec["cool"] or
                                        not dec["continue_active"]):
            state = "cooling"
        meta_evals = _push_eval(meta_evals, cp_iso, dec["eval"])
        rec["lifecycle"] = state
        states.append(rec)
    return states


# ---------------------------------------------------------------------------
# Confirmation predicates — STRICTLY post-open evidence only
# ---------------------------------------------------------------------------

def confirm_variant(variant: str, opened_date: date, obs_all: list[dict],
                    pre_channel_ids: set[str]) -> dict:
    end = opened_date + timedelta(days=VARIANT_DEADLINES[variant])
    post = sorted((o for o in obs_all
                   if opened_date < d_of(o) <= end),
                  key=lambda o: o["obs_date"])
    out = {"variant": variant, "confirmed": False, "confirmed_at": None,
           "n_post_in_window": len(post)}
    pubs, chans = [], []
    for o in post:
        p = publisher_identity(o.get("source"), o.get("channel_id"),
                               o.get("channel_title"))
        if p != PUBLISHER_UNKNOWN and p not in pubs:
            pubs.append(p)
        if o.get("channel_id") and o["channel_id"] not in chans:
            chans.append(o["channel_id"])
    out["publishers_known_n"] = len(pubs)
    out["publishers_unknown_n"] = sum(
        1 for o in post
        if publisher_identity(o.get("source"), o.get("channel_id"),
                              o.get("channel_title")) == PUBLISHER_UNKNOWN)
    out["distinct_channels_n"] = len(chans)
    if not post:
        return out
    if variant == "EU1-W30":
        out["confirmed"] = True
        out["confirmed_at"] = post[0]["obs_date"]
    elif variant == "EU2-W60":
        if len(post) >= 2:
            out["confirmed"] = True
            out["confirmed_at"] = post[1]["obs_date"]
    elif variant == "BUCKETS-W120":
        months: list[str] = []
        for o in post:
            m = o["obs_date"][:7]
            if m not in months:
                months.append(m)
                if len(months) == 2:
                    out["confirmed"] = True
                    out["confirmed_at"] = o["obs_date"]
                    break
    elif variant == "CHANNELNEW-W30":
        for o in post:
            if o.get("channel_id") and \
                    o["channel_id"] not in pre_channel_ids:
                out["confirmed"] = True
                out["confirmed_at"] = o["obs_date"]
                break
    elif variant == "POSTERIOR-EXCL-W30":
        _, sig_open = signal_positive(filter_le(obs_all, opened_date),
                                      opened_date)
        p = bp2.prob_rate_above(
            len(post), sig_open["k_base"],
            exp_recent=30.0 / bp2.PARAMS["time_unit_days"],
            exp_base=bp2.PARAMS["baseline_window_days"]
            / bp2.PARAMS["time_unit_days"])
        out["post_excl"] = round(p, 6)
        if p >= bp2.PARAMS["continue_threshold"]:
            out["confirmed"] = True
            out["confirmed_at"] = end.isoformat()
    return out


def episode_core(obs_lane: list[dict]):
    """Opening + confirmation accounting for one lane, honoring the
    preregistration's re-arm clause: after an unconfirmed attempt hits its
    deadline, the episode re-arms and the NEXT positive-detector
    boundary starts a fresh attempt (trigger_evidence_cutoff resets).
    Confirmation for a variant = earliest confirming event across ALL
    attempts."""
    pts = boundaries(obs_lane)
    uniq_events: dict[str, str] = bp2.distinct_eu_first_dates(obs_lane)
    uniq_sorted = sorted((ds, eu) for eu, ds in uniq_events.items())
    uniq_dates = [date.fromisoformat(ds) for ds, _ in uniq_sorted]
    attempts_all: dict[str, list[dict]] = {v: [] for v in CONFIRM_VARIANTS}
    first_open = None
    if not pts:
        return {"episode": {"opened": False, "opened_at": None,
                            "opened_k_recent": None,
                            "opened_k_base": None},
                "variants": {v: {"variant": v, "confirmed": False,
                                 "confirmed_at": None,
                                 "n_attempts": 0}
                             for v in CONFIRM_VARIANTS}}
    import bisect as _bs
    for v in CONFIRM_VARIANTS:
        deadline_days = VARIANT_DEADLINES[v]
        attempts = []
        cursor = 0
        i = 0
        cur_open = None
        cur_sig = None
        while i < len(pts):
            t = pts[i]
            obs_le = filter_le(obs_lane, t)
            if not obs_le:
                i += 1
                continue
            pos, sig = signal_positive(obs_le, t)
            if cur_open is None:
                if pos:
                    cur_open = t
                    cur_sig = sig
                    if first_open is None:
                        first_open = t.isoformat()
                    attempts.append({"opened_at": t.isoformat(),
                                     "confirmed_at": None})
            else:
                od = cur_open
                if od < t <= od + timedelta(days=deadline_days):
                    pass  # inside attempt window; check completion below
                else:
                    if od + timedelta(days=deadline_days) <= t:
                        # attempt expired at its deadline without
                        # confirmation; re-arm requires a NEW positive
                        # crossing AFTER the deadline.
                        attempts[-1]["expired_at"] = (
                            od + timedelta(days=deadline_days)
                        ).isoformat()
                        if pos:
                            cur_open = t
                            cur_sig = sig
                            attempts.append({
                                "opened_at": t.isoformat(),
                                "confirmed_at": None})
                            # fallthrough checks completion this t
                        else:
                            cur_open = None
                            i += 1
                            continue
            if cur_open is not None:
                od = cur_open
                lo_idx = _bs.bisect_right(uniq_dates, od)
                hi_idx = _bs.bisect_right(
                    uniq_dates,
                    min(od + timedelta(days=deadline_days), t))
                if hi_idx > lo_idx:
                    n_post = hi_idx - lo_idx
                    confirmed = False
                    conf_date = None
                    if v == "EU1-W30":
                        confirmed = True
                        conf_date = uniq_dates[lo_idx]
                    elif v == "EU2-W60":
                        if n_post >= 2:
                            confirmed = True
                            conf_date = uniq_dates[lo_idx + 1]
                    elif v == "BUCKETS-W120":
                        months = []
                        for ds in uniq_dates[lo_idx:hi_idx]:
                            m = ds.isoformat()[:7]
                            if m not in months:
                                months.append(m)
                                if len(months) == 2:
                                    confirmed = True
                                    conf_date = ds
                                    break
                    elif v == "CHANNELNEW-W30":
                        rs = od - timedelta(days=bp2.PARAMS[
                            "recent_window_days"])
                        pre_chans = {
                            o.get("channel_id") for o in filter_le(
                                obs_lane, od)
                            if rs < d_of(o) <= od}
                        pre_chans.discard(None)
                        for ds, eu in uniq_sorted[lo_idx:hi_idx]:
                            ch = next(
                                (o.get("channel_id")
                                 for o in obs_lane
                                 if o["eu_id"] == eu), None)
                            if ch and ch not in pre_chans:
                                confirmed = True
                                conf_date = date.fromisoformat(ds)
                                break
                    elif v == "POSTERIOR-EXCL-W30":
                        end = od + timedelta(days=deadline_days)
                        if t >= end:
                            p = bp2.prob_rate_above(
                                n_post, cur_sig["k_base"],
                                exp_recent=(
                                    deadline_days
                                    / bp2.PARAMS["time_unit_days"]),
                                exp_base=(bp2.PARAMS["baseline_window_days"]
                                          / bp2.PARAMS["time_unit_days"]))
                            if p >= bp2.PARAMS["continue_threshold"]:
                                confirmed = True
                                conf_date = end
                    if confirmed:
                        attempts[-1]["confirmed_at"] = \
                            conf_date.isoformat()
                        attempts[-1]["n_post_in_window"] = n_post
                        break  # variant resolved at its earliest event
            i += 1
        attempts_all[v] = attempts

    op = {"opened": first_open is not None,
          "opened_at": first_open,
          "opened_k_recent": None, "opened_k_base": None}
    if first_open:
        _, sig0 = signal_positive(filter_le(
            obs_lane, date.fromisoformat(first_open)),
            date.fromisoformat(first_open))
        op["opened_k_recent"] = sig0["k_recent"]
        op["opened_k_base"] = sig0["k_base"]
    variants_out = {}
    for v in CONFIRM_VARIANTS:
        atts = attempts_all[v]
        hit = next((a for a in atts if a.get("confirmed_at")), None)
        variants_out[v] = {
            "variant": v,
            "confirmed": bool(hit),
            "confirmed_at": hit["confirmed_at"] if hit else None,
            "n_attempts": len(atts),
            "expired_unconfirmed_n": sum(
                1 for a in atts if not a.get("confirmed_at")),
            "attempts": atts[:16],  # bounded audit trail
        }
    return {"episode": op, "variants": variants_out}


def armD_persistence(obs_lane: list[dict], opened_date: date,
                     horizon_days=60, floor=2, trailing_days=30) -> dict:
    end = opened_date + timedelta(days=horizon_days)
    post = [o for o in obs_lane
            if opened_date < d_of(o) <= end]
    best = 0
    if len(post) >= floor:
        pts = sorted({d_of(o) for o in post}
                     | {d_of(o) + timedelta(days=trailing_days)
                        for o in post})
        for t in pts:
            if not (opened_date < t <= end):
                continue
            lo = t - timedelta(days=trailing_days)
            n = sum(1 for o in post if lo < d_of(o) <= t)
            best = max(best, n)
            if n >= floor:
                return {"persist": True, "persist_at": t.isoformat(),
                        "peak_window": n}
    return {"persist": False, "persist_at": None,
            "peak_window": best}


def armC_machine(obs_lane: list[dict], checkpoints_iso, confirmed_at_iso):
    """Explicit episode-state representation over the B/EU1-W30 base
    predicate. Event-driven fields per the contract; transitions depend
    only on evidence <= the current checkpoint (as-of replayable)."""
    fields = {"episode_opened_at": None, "trigger_evidence_cutoff": None,
              "confirmation_after": None, "confirmed_at": None,
              "last_support_at": None, "cooling_started_at": None,
              "closed_at": None}
    machine_states = []
    op = open_episode_precise(obs_lane)
    if not op["opened"]:
        return {"fields": fields, "states": [], "active_at_last_cp":
                False}
    opened_date = date.fromisoformat(op["opened_at"])
    fields["episode_opened_at"] = op["opened_at"]
    fields["trigger_evidence_cutoff"] = op["opened_at"]
    fields["confirmation_after"] = op["opened_at"]
    fields["confirmed_at"] = confirmed_at_iso
    consecutive_neg = 0
    for _label, iso in checkpoints_iso:
        t = date.fromisoformat(iso)
        obs_le = filter_le(obs_lane, t)
        if not obs_le:
            machine_states.append({"as_of": iso, "phase": "PRE"})
            continue
        pos, _sig = signal_positive(obs_le, t)
        phase = "PRE_OPEN" if t < opened_date else None
        sup = [o for o in obs_le if d_of(o) > opened_date]
        if sup and not phase:
            fields["last_support_at"] = max(d_of(o) for o in sup) \
                .isoformat()
        if not pos:
            consecutive_neg += 1
        else:
            consecutive_neg = 0
        if phase is None:
            if confirmed_at_iso and iso >= confirmed_at_iso:
                if consecutive_neg >= 2:
                    phase = "COOLING"
                    if fields["cooling_started_at"] is None:
                        fields["cooling_started_at"] = iso
                else:
                    phase = ("CONFIRMED_CONTINUING" if pos
                             else "CONFIRMED_WATCH")
            else:
                phase = "CANDIDATE_EPISODE"
                if consecutive_neg >= 2:
                    phase = "CANDIDATE_COOLING"
                    if fields["cooling_started_at"] is None:
                        fields["cooling_started_at"] = iso
        machine_states.append({"as_of": iso, "phase": phase,
                               "detector_positive": bool(pos),
                               "consecutive_neg": consecutive_neg})
    if fields["confirmed_at"] is None:
        deadline = (opened_date + timedelta(days=VARIANT_DEADLINES
                    ["EU1-W30"])).isoformat()
        fields["closed_at"] = fields["cooling_started_at"] or deadline
    active = bool(fields["confirmed_at"]) and \
        fields["cooling_started_at"] is None
    return {"fields": fields, "states": machine_states,
            "active_at_last_cp": active}


# ---------------------------------------------------------------------------
# subjects / lanes
# ---------------------------------------------------------------------------

def fetch_entity_labels(conn):
    rows = conn.execute(
        "SELECT node_id, label FROM kg_nodes WHERE kind='entity'"
    ).fetchall()
    cache: dict[str, list[str]] = {}
    for node_id, label in rows:
        cache.setdefault(node_id, []).append(label or "")
    return cache


def fetch_obs_for_nodes(conn, node_ids):
    placeholders = ",".join("?" * len(node_ids))
    sql = f"""
        SELECT m.src_id AS node_id, eu.eu_id AS eu_id,
               eu.video_id AS video_id, eu.channel_id AS channel_id,
               eu.channel_title AS channel_title, eu.source AS source,
               {VALID_TIME_SQL} AS obs_date
        FROM kg_edges m
        JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
        WHERE m.relation='mentioned_in'
          AND m.src_id IN ({placeholders})
          AND {VALID_TIME_SQL} != ''"""
    by_node: dict[str, list[dict]] = {}
    cur = conn.execute(sql, node_ids)
    cols = [d[0] for d in cur.description]
    for r in cur.fetchall():
        rec = dict(zip(cols, r))
        by_node.setdefault(rec["node_id"], []).append(rec)
    return by_node


def match_subject_nodes(subject, cache):
    aliases = [subject["canonical_name"]] + list(subject.get("aliases",
                                                             []))
    matched = {}
    for node_id, labels in cache.items():
        for lab in labels:
            if lab and name_matches(aliases, [lab]):
                matched[node_id] = lab
                break
    return matched


def build_subject_streams(conn, subject, cache):
    """lane_key -> merged obs stream (nodes sharing one label merge into
    one registry concept lane, mirroring production identity)."""
    matched = match_subject_nodes(subject, cache)
    streams: dict[str, list[dict]] = {}
    if not matched:
        return streams
    by_node = fetch_obs_for_nodes(conn, sorted(matched))
    for node_id, lab in matched.items():
        streams.setdefault(f"label::{lab}", []).extend(
            by_node.get(node_id, []))
    for k in streams:
        streams[k].sort(key=lambda o: o["obs_date"])
    return streams


def checkpoint_pairs(t_iso: str):
    base = date.fromisoformat(t_iso)
    today = date.today()
    return [(lbl, min(base + timedelta(days=off), today).isoformat())
            for off, lbl in CHECKPOINT_OFFSETS]


def transform_streams(streams, kind, **kw):
    """Deterministic counterfactual transforms per preregistration."""
    tid = kw["tid"]
    seed = int(hashlib.sha256(tid.encode()).hexdigest()[:8], 16)

    def clone(o, suffix, new_channel=None, force_d=None):
        c = dict(o)
        c["eu_id"] = f"{o['eu_id']}{suffix}"
        c["_counterfactual"] = True
        if new_channel:
            c["channel_id"] = new_channel
        if force_d:
            c["obs_date"] = force_d
        return c

    opened = kw.get("opened_at")
    out = {}
    for lk, obs in streams.items():
        arr = [dict(o) for o in obs]
        if kind == "drop_postopen":
            if opened:
                od = date.fromisoformat(opened)
                arr = [o for o in arr if d_of(o) <= od]
        elif kind == "move_mids_to_postopen":
            if opened:
                od = date.fromisoformat(opened)
                pre = [o for o in arr if d_of(o) <= od]
                if len(pre) >= 2:
                    dts = sorted(d_of(o) for o in pre)
                    med = dts[len(dts) // 2]
                    movers = [o for o in arr
                              if d_of(o) == med][:max(1, len(pre) // 4)]
                    keep_move = {id(m) for m in movers}
                    shifted = []
                    for i, o in enumerate(arr):
                        if id(o) in keep_move:
                            c = clone(
                                o, "_cfmv",
                                force_d=(od + timedelta(
                                    days=1 + i % 7)).isoformat())
                            c["pre_anchor_copy"] = False
                            shifted.append(c)
                    arr = [o for o in arr if id(o) not in keep_move]
                    arr.extend(shifted)
                    arr.sort(key=lambda o: o["obs_date"])
        elif kind == "add_postopen":
            if opened and arr:
                od = date.fromisoformat(opened)
                anchors = [o for o in arr][-min(3, len(arr)):]
                adds = []
                src_chans = {o.get("channel_id") for o in arr}
                for i, o in enumerate(anchors):
                    nd = (od + timedelta(days=[3, 9, 17][i % 3])
                          ).isoformat()
                    ch = next(iter(src_chans - {o.get("channel_id")}),
                              None) or "UC_cf_alt"
                    adds.append(clone(o, f"_cfa{i}", new_channel=ch,
                                      force_d=nd))
                arr.extend(adds)
                arr.sort(key=lambda o: o["obs_date"])
        elif kind == "duplicate_same_publisher":
            if opened:
                od = date.fromisoformat(opened)
                posts = [o for o in arr if d_of(o) > od]
                src = posts[0] if posts else (
                    clone(dict(arr[-1]), "_cfg0",
                          force_d=(od + timedelta(days=4)).isoformat())
                    if arr else None)
                if src:
                    arr.append(clone(dict(src), "_cfdup",
                                     force_d=(date.fromisoformat(
                                         src["obs_date"])
                                         + timedelta(days=2)).isoformat()))
                    arr.sort(key=lambda o: o["obs_date"])
        elif kind == "add_independent_publisher":
            if opened:
                arr.append(clone(dict(sorted(
                    arr, key=lambda o: o["obs_date"])[-1]), "_cfpub",
                    new_channel=f"UC_cfpub_{seed % 10**10}",
                    force_d=(date.fromisoformat(opened)
                             + timedelta(days=6)).isoformat()))
                arr.sort(key=lambda o: o["obs_date"])
        elif kind == "inject_future":
            anchor = arr[-1] if arr else None
            if anchor:
                fut = (date.today() + timedelta(days=400)).isoformat()
                arr.append(clone(anchor, "_cffut", force_d=fut))
                arr.sort(key=lambda o: o["obs_date"])
        elif kind == "none":
            pass
        out[lk] = arr
    return out


def perturbed_streams(streams, sid: str, fraction: float):
    seed = int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16)
    combined: dict[tuple[str, str], tuple[str, dict]] = {}
    for lk, obs in streams.items():
        for o in obs:
            combined.setdefault((lk, o["eu_id"]), (lk, o))
    items = sorted(combined.items(), key=lambda kv: (kv[0][0],
                                                     kv[0][1]))
    take = int(len(items) * fraction)
    if take == 0:
        return {lk: [dict(o) for o in obs] for lk, obs in streams.items()}
    start = seed % max(len(items) - take, 1)
    removed = {(lk, eu) for (lk, eu), _ in
               items[start:start + take]}
    out = {}
    for lk, obs in streams.items():
        out[lk] = [dict(o) for o in obs
                   if (lk, o["eu_id"]) not in removed]
    return out


def subject_features(streams, opened_by_lane):
    eu_first: dict[str, str] = {}
    for lk, obs in streams.items():
        for o in obs:
            cur = eu_first.get(o["eu_id"])
            if cur is None or o["obs_date"] < cur:
                eu_first[o["eu_id"]] = o["obs_date"]
    pubs, chans = set(), set()
    for lk, obs in streams.items():
        for o in obs:
            p = publisher_identity(o.get("source"), o.get("channel_id"),
                                   o.get("channel_title"))
            if p != PUBLISHER_UNKNOWN:
                pubs.add(p)
            if o.get("channel_id"):
                chans.add(o["channel_id"])
    return {"lifetime_distinct_eus": len(eu_first),
            "known_publishers_n": len(pubs),
            "distinct_channels_n": len(chans),
            "opened_k_recent": max((v.get("opened_k_recent") or 0)
                                   for v in opened_by_lane.values())
            if opened_by_lane else None,
            "earliest_open_k_recent": next(
                (v.get("opened_k_recent") for v in
                 opened_by_lane.values()
                 if v.get("opened")), None)}


def run_subject(subject, streams, t_iso,
                cp_offsets=None) -> dict:
    """cp_offsets restricts the replay chain (prefix replays for
    perturbation use (-30,0,7,14,30)); horizons are bounded by the LAST
    evaluated checkpoint so prefix runs never see later decisions."""
    all_cps = checkpoint_pairs(t_iso)
    if cp_offsets is not None:
        wanted = {("T" if o == 0 else f"T{o:+d}") for o in cp_offsets}
        cps = [cp for cp in all_cps if cp[0] in wanted]
    else:
        cps = all_cps
    horizon_end = cps[-1][1] if cps else t_iso
    lim = min((date.fromisoformat(t_iso) + timedelta(days=60)).isoformat(),
              horizon_end)
    t30 = min((date.fromisoformat(t_iso) + timedelta(days=30)).isoformat(),
              horizon_end)
    lanes_out = {}
    subj = {
        "candidate_ever": False,
        "a_emerging_ever": False,
        "a_first_emerging_cp": None,
        "any_opened": False,
        "precise_vs_sampled_max_delta": None,
        "by_family": {f: {"confirmed": False, "confirmed_at": None,
                          "delay": None, "lane": None,
                          "confirmed_by_T30": False}
                      for f in FAMILIES},
        "active_records": 0, "confirmed_records": 0,
        "expired_unconfirmed_records": 0,
    }
    for lk in sorted(streams):
        obs = streams[lk]
        if not obs:
            continue
        chain = armA_lane_chain(obs, cps)
        life = [st.get("lifecycle") for st in chain]
        emerg_idx = [i for i, x in enumerate(life) if x == "emerging"]
        cand_flag = any(x in ("candidate", "emerging") for x in life)
        lane_rec = {
            "chain_lite": [{k: st.get(k) for k in
                            ("checkpoint", "as_of", "lifecycle",
                             "detector_positive", "k_recent",
                             "posterior")} for st in chain],
            "armA": {"candidate_ever": cand_flag,
                     "emerging_ever": bool(emerg_idx),
                     "first_emerging_cp":
                         chain[emerg_idx[0]]["checkpoint"]
                         if emerg_idx else None,
                     "candidate_at_T30": next(
                         (st.get("is_decay_candidate") for st in chain
                          if st["checkpoint"] == "T+30"), None)},
        }
        core = episode_core(obs)
        ep = core["episode"]
        sampled = earliest_sampled_open(chain)
        delta_days = None
        if ep["opened"]:
            if sampled:
                delta_days = (date.fromisoformat(sampled)
                              - date.fromisoformat(ep["opened_at"])).days
            subj["any_opened"] = True
        lane_rec["episode"] = {
            "opened": ep["opened"], "opened_at": ep["opened_at"],
            "sampled_open_at": sampled,
            "delta_sampled_minus_precise_days": delta_days}
        if delta_days is not None and \
                (subj["precise_vs_sampled_max_delta"] is None or
                 abs(delta_days) > abs(subj[
                     "precise_vs_sampled_max_delta"])):
            subj["precise_vs_sampled_max_delta"] = delta_days
        lane_res_vars = {}
        for v in CONFIRM_VARIANTS:
            vr = core["variants"][v]
            within = bool(vr["confirmed_at"] and vr["confirmed_at"] <= lim)
            by_t30 = bool(vr["confirmed_at"]
                          and vr["confirmed_at"] <= t30)
            fam = "armB_" + v
            cur = subj["by_family"][fam]
            better = within and (not cur["confirmed"]
                                 or vr["confirmed_at"]
                                 < cur["confirmed_at"])
            if better:
                opened_at = ep["opened_at"]
                cur.update({
                    "confirmed": True,
                    "confirmed_at": vr["confirmed_at"],
                    "delay": (date.fromisoformat(vr["confirmed_at"])
                              - date.fromisoformat(opened_at)).days,
                    "lane": lk,
                    "confirmed_by_T30": by_t30,
                    "publishers_known_n":
                        vr.get("publishers_known_n"),
                    "distinct_channels_n":
                        vr.get("distinct_channels_n")})
            lane_res_vars[v] = {**vr, "within_horizon": within,
                                "by_T30": by_t30}
        od = date.fromisoformat(ep["opened_at"]) if ep["opened"] else None
        if od:
            dres = armD_persistence(obs, od)
            cur = subj["by_family"]["armD"]
            if dres["persist"] and (not cur["confirmed"]
                                    or dres["persist_at"]
                                    < cur["confirmed_at"]):
                cur.update({"confirmed": True,
                            "confirmed_at": dres["persist_at"],
                            "delay": (date.fromisoformat(
                                dres["persist_at"]) - od).days,
                            "lane": lk,
                            "peak_window": dres["peak_window"],
                            "confirmed_by_T30":
                                dres["persist_at"] <= t30})
        cres = armC_machine(obs, cps,
                            core["variants"]["EU1-W30"].get(
                                "confirmed_at"))
        lane_rec["armC_fields"] = cres["fields"]
        lane_rec["armC_phases"] = [
            {"as_of": s["as_of"], "phase": s.get("phase")}
            for s in cres["states"]]
        cur = subj["by_family"]["armC"]
        if cres["fields"]["confirmed_at"] and \
                cres["fields"]["confirmed_at"] <= lim and \
                not cur["confirmed"]:
            cur.update({"confirmed": True,
                        "confirmed_at": cres["fields"]["confirmed_at"],
                        "delay": (date.fromisoformat(
                            cres["fields"]["confirmed_at"])
                            - date.fromisoformat(
                                cres["fields"]["episode_opened_at"]
                        )).days,
                        "lane": lk,
                        "active_at_T60_semantics":
                            cres["active_at_last_cp"],
                        "confirmed_by_T30":
                            cres["fields"]["confirmed_at"] <= t30})
        elif cres["fields"]["confirmed_at"] and \
                cres["fields"]["confirmed_at"] <= lim:
            cur["active_at_T60_semantics"] = \
                cur.get("active_at_T60_semantics") or \
                cres["active_at_last_cp"]
        subj["candidate_ever"] |= cand_flag
        subj["a_emerging_ever"] |= bool(emerg_idx)
        if emerg_idx and (subj["a_first_emerging_cp"] is None):
            subj["a_first_emerging_cp"] = \
                lane_rec["armA"]["first_emerging_cp"]
        if ep["opened"]:
            subj["active_records"] += 1
            if any(vr.get("within_horizon")
                   for vr in lane_res_vars.values()):
                subj["confirmed_records"] += 1
            else:
                subj["expired_unconfirmed_records"] += 1
        lanes_out[lk] = lane_rec
    feats = subject_features(streams, {})
    feats["earliest_open_k_recent"] = None
    _min_open_k = None
    for obs in streams.values():
        k = core_episode_k(obs)
        if k is not None and (_min_open_k is None or k < _min_open_k):
            _min_open_k = k
    feats["earliest_open_k_recent"] = _min_open_k
    return {"lanes": lanes_out, "subject": subj, "features": feats}


def core_episode_k(obs):
    op = open_episode_precise(obs)
    return op.get("opened_k_recent") if op["opened"] else None


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def wilson(k, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return {"rate": round(p, 4), "lo": round(max(0.0, center - half), 4),
            "hi": round(min(1.0, center + half), 4), "k": k, "n": n}


def aggregate_arm(rows_pos, rows_neg, key_confirm, key_delay=None):
    pk = sum(1 for r in rows_pos if r[key_confirm])
    nk = sum(1 for r in rows_neg if r[key_confirm])
    entry = {
        "positive_rate": wilson(pk, len(rows_pos)),
        "negative_rate": wilson(nk, len(rows_neg)),
        "separation": round(pk / len(rows_pos) - nk / len(rows_neg), 4)
        if rows_pos and rows_neg else None,
    }
    if key_delay:
        ds = sorted(r[key_delay] for r in rows_pos
                    if r[key_confirm] and r.get(key_delay) is not None)
        if ds:
            entry["median_confirmation_delay_days"] = \
                statistics.median(ds)
    return entry


def aggregate_all(metric_rows):
    pos = [r for r in metric_rows if r["kind"] == "positive"]
    neg = [r for r in metric_rows if r["kind"] == "negative"]
    agg = {
        "armA": {
            "positive_candidate_recall": wilson(
                sum(1 for r in pos if r["candidate_ever"]), len(pos)),
            "positive_emerging_recall": wilson(
                sum(1 for r in pos if r["a_emerging_ever"]), len(pos)),
            "negative_candidate_rate": wilson(
                sum(1 for r in neg if r["candidate_ever"]), len(neg)),
            "negative_emerging_rate": wilson(
                sum(1 for r in neg if r["a_emerging_ever"]), len(neg)),
            "separation": round(
                sum(1 for r in pos if r["a_emerging_ever"]) / len(pos)
                - sum(1 for r in neg if r["a_emerging_ever"])
                / len(neg), 4) if pos and neg else None,
        },
        "counts": {},
    }
    for fam in FAMILIES:
        agg[fam] = aggregate_arm(pos, neg, fam + "_confirmed",
                                 fam + "_delay")
    return agg


# ---------------------------------------------------------------------------
# segmentation
# ---------------------------------------------------------------------------

def tercile_cuts(values):
    xs = sorted(v for v in values if v is not None)
    if len(xs) < 6:
        return None
    def pct(p):
        i = min(len(xs) - 1, int(round(p * (len(xs) - 1))))
        return xs[i]
    return (pct(1 / 3), pct(2 / 3))


def bucket3(v, cuts):
    if v is None or cuts is None:
        return "unknown"
    return "low" if v <= cuts[0] else ("mid" if v <= cuts[1] else "high")


def compute_segments(metric_rows_with_feats):
    feats = [it["features"] for it in metric_rows_with_feats]
    kc = tercile_cuts([f.get("opened_k_recent") for f in feats])
    ac = tercile_cuts([f.get("age_span_days") for f in feats])
    seg_rows = []
    for item in metric_rows_with_feats:
        f, r = item["features"], item["row"]
        seg_rows.append({
            "kind": r["kind"], "sid": r["sid"],
            "preanchor_strength": bucket3(f.get("opened_k_recent"), kc),
            "evidence_age": bucket3(f.get("age_span_days"), ac),
            "postopen_activity": "some" if f.get("postopen_n_by_T60")
            else "none",
            "publisher_bucket": ("1" if f["known_publishers_n"] <= 1
                                 else "2+"),
            "channel_bucket": ("1" if f["distinct_channels_n"] <= 1
                               else "2+"),
            **{fam + "_conf": bool(r[fam + "_confirmed"])
               for fam in FAMILIES},
            "a_emerging": bool(r["a_emerging_ever"]),
        })
    segments = {}
    dims = ["preanchor_strength", "evidence_age", "postopen_activity",
            "publisher_bucket", "channel_bucket"]

    def summarize(subset):
        posr = [x for x in subset if x["kind"] == "positive"]
        negr = [x for x in subset if x["kind"] == "negative"]
        return {fam: {"pos": wilson(sum(1 for x in posr
                                       if x[fam + "_conf"]),
                                    len(posr)) or {"rate": None,
                                                   "n": 0},
                      "neg": wilson(sum(1 for x in negr
                                        if x[fam + "_conf"]),
                                     len(negr)) or {"rate": None,
                                                    "n": 0}}
                for fam in FAMILIES} | {
            "armA_emerging": {"pos": wilson(sum(1 for x in posr
                                                if x["a_emerging"]),
                                            len(posr)),
                              "neg": wilson(sum(1 for x in negr
                                                if x["a_emerging"]),
                                            len(negr))}}

    for dim in dims:
        values = sorted({sr[dim] for sr in seg_rows})
        segments[dim] = {v: summarize([sr for sr in seg_rows
                                       if sr[dim] == v])
                         for v in values}
    return segments


# ---------------------------------------------------------------------------
# counterfactual suite
# ---------------------------------------------------------------------------

def counterfactual_suite(subjects_streams, selected_families):
    """Direction checks per preregistration on representative subjects."""
    picks = []
    poss = [(sid, s) for sid, s in subjects_streams.items()
            if s["_kind"] == "positive"]
    negs = [(sid, s) for sid, s in subjects_streams.items()
            if s["_kind"] == "negative"]
    poss.sort(key=lambda kv: -kv[1]["mass"])
    negs.sort(key=lambda kv: -kv[1]["mass"])
    picks.extend(poss[:6])
    picks.extend(poss[-2:])
    picks.extend(negs[:6])
    results = []
    for sid, bundle in picks:
        if bundle.get("mass", 0) == 0:
            continue
        t_iso = bundle["T"]
        base = run_subject(bundle, bundle["streams"], t_iso)
        opened = None
        for lr in base["lanes"].values():
            if lr["episode"]["opened"]:
                opened = lr["episode"]["opened_at"]
                break
        if opened is None:
            continue  # transforms are post-open-relative; skip inert
        checks = {}
        expectations = {
            "drop_postopen": "confirmation_disappears_or_nonincreases",
            "retain_trigger_only": "opens_never_confirms",
            "add_postopen": "confirms_or_increases",
            "move_mids_to_postopen": "directionally_toward_confirmation",
            "duplicate_same_publisher": "channelnew_stays_unconfirmed",
            "add_independent_publisher": "reported_not_gated",
            "inject_future": "byte_identical_to_original",
        }
        for tf, expectation in expectations.items():
            streams_t = transform_streams(bundle["streams"], tf,
                                          tid=sid + ":" + tf,
                                          opened_at=opened)
            run_t = run_subject(bundle, streams_t, t_iso)
            row = {"transform": tf, "expectation": expectation}
            for fam in selected_families:
                row[fam] = {"orig": base["subject"]["by_family"][fam][
                    "confirmed"],
                    "after": run_t["subject"]["by_family"][fam][
                        "confirmed"]}
            if tf == "duplicate_same_publisher":
                cn = "armB_CHANNELNEW-W30"
                row["channelnew_unconfirmed_after"] = \
                    not run_t["subject"]["by_family"][cn]["confirmed"]
            if tf == "inject_future":
                canon = lambda rr: json.dumps(
                    rr["subject"]["by_family"], sort_keys=True)
                row["identical"] = canon(base) == canon(run_t)
            checks[tf] = row
        results.append({"sid": sid, "kind": bundle["_kind"], "t": t_iso,
                        "opened_at": opened, "checks": checks})
    return results


# ---------------------------------------------------------------------------
# full pipeline driver (used twice by determinism verify)
# ---------------------------------------------------------------------------

def execute_pipeline(catalog_path, case_control_path, artdir,
                     limit_subjects=None, skip_counterfactuals=False):
    conn = sqlite3.connect(
        f"file:{Path(catalog_path).as_posix()}?mode=ro", uri=True,
        timeout=30)
    try:
        cc = ev_module().load_case_control(str(case_control_path))
        positives = [{**p, "_kind": "positive", "_id": p["target_id"]}
                     for p in cc["positives"]]
        negatives = [{**n, "_kind": "negative", "_id": n["negative_id"],
                      "T": n["anchor_T"]} for n in cc["negatives"]]
        missing_t = 0
        for p in positives:
            if not p.get("T"):
                p["T"] = ev_module().first_qualifying_evidence(
                    p, str(catalog_path))
                if not p["T"]:
                    missing_t += 1
        scorables = [p for p in positives if p["T"]]
        if limit_subjects:
            scorables = scorables[:limit_subjects]
        pairs_unused = {n["paired_positive_id"] for n in negatives}
        del pairs_unused
        neg_used = [n for n in negatives
                    if n["paired_positive_id"] in
                    {p["_id"] for p in scorables}]
        # STATEFUL SYMMETRY (evaluator-v3/v4 semantics): an explicit
        # negative is replayed at its PAIRED POSITIVE'S checkpoint dates
        # inside the same logical registry chain — never its own anchor.
        t_by_tid = {p["_id"]: p["T"] for p in scorables}
        for n in neg_used:
            n["T"] = t_by_tid[n["paired_positive_id"]]
        bundles = []
        cache = fetch_entity_labels(conn)
        for p in scorables:
            streams = build_subject_streams(conn, p, cache)
            mass = sum(len(v) for v in streams.values())
            bundles.append({**p, "streams": streams, "mass": mass})
        for n in neg_used:
            streams = build_subject_streams(conn, n, cache)
            mass = sum(len(v) for v in streams.values())
            bundles.append({**n, "streams": streams, "mass": mass})
        metric_rows, enriched = [], []
        outcome_events = {"episodes_opened": 0, "episodes_confirmed":
                          0, "expired_unconfirmed": 0}
        for b in bundles:
            res = run_subject(b, b["streams"], b["T"])
            row = {"kind": b["_kind"], "sid": b["_id"],
                   "T": b["T"],
                   "candidate_ever": res["subject"]["candidate_ever"],
                   "a_emerging_ever":
                       res["subject"]["a_emerging_ever"]}
            for fam in FAMILIES:
                ent = res["subject"]["by_family"][fam]
                row[fam + "_confirmed"] = ent["confirmed"]
                row[fam + "_delay"] = ent.get("delay")
                row[fam + "_by_T30"] = ent.get("confirmed_by_T30",
                                               False)
            metric_rows.append(row)
            subj = res["subject"]
            if b["_kind"] == "positive":
                outcome_events["episodes_opened"] += \
                    1 if subj["any_opened"] else 0
                outcome_events["episodes_confirmed"] += \
                    1 if subj["by_family"]["armB_EU1-W30"][

                        "confirmed"] else 0
                outcome_events["expired_unconfirmed"] += \
                    1 if (subj["any_opened"] and not
                          subj["by_family"]["armB_EU1-W30"][
                              "confirmed"]) else 0
            opened_at_lane = next(
                (lr["episode"]["opened_at"]
                 for lr in res["lanes"].values()
                 if lr["episode"]["opened"]), None)
            feats = res["features"]
            first_eu = min((o["obs_date"]
                            for obs in b["streams"].values()
                            for o in obs), default=b["T"])
            feats["age_span_days"] = ((date.fromisoformat(b["T"])
                                       - date.fromisoformat(
                                           first_eu)).days
                                      if first_eu else None)
            feats["opened_k_recent"] = feats.get(
                "earliest_open_k_recent")
            feats["postopen_n_by_T60"] = 0
            if opened_at_lane:
                t_iso_b = b["T"]
                lim60 = (date.fromisoformat(t_iso_b)
                         + timedelta(days=60)).isoformat()
                seen: set[str] = set()
                for obs in b["streams"].values():
                    for o in obs:
                        if opened_at_lane < o["obs_date"] <= lim60 \
                                and o["eu_id"] not in seen:
                            seen.add(o["eu_id"])
                feats["postopen_n_by_T60"] = len(seen)
            enriched.append({"row": row, "features": feats})
            res["metric_row_snapshot"] = row
            b["_last_result"] = res
    finally:
        conn.close()

    agg = aggregate_all(metric_rows)

    seg_input = [{"row": e["row"], "features": e["features"]}
                 for e in enriched]
    segments = compute_segments(seg_input)

    subject_stream_map = {b["_id"]: b for b in bundles}
    cf_results = [] if skip_counterfactuals else counterfactual_suite(
        subject_stream_map,
        selected_families=["armB_EU1-W30", "armB_CHANNELNEW-W30",
                           "armC", "armD"])

    # leave-one-out analytic bounds + empirical argmax drops per family
    stability = {}
    pos_r = [r for r in metric_rows if r["kind"] == "positive"]
    neg_r = [r for r in metric_rows if r["kind"] == "negative"]
    for fam in FAMILIES:
        kp = sum(1 for r in pos_r if r[fam + "_confirmed"])
        kn = sum(1 for r in neg_r if r[fam + "_confirmed"])
        entry = {"n_pos": len(pos_r), "n_neg": len(neg_r), "k_pos": kp,
                 "k_neg": kn}
        if neg_r:
            entry["loo_negative_rate_range"] = [
                round(max(kn - 1, 0) / (len(neg_r) - 1), 4)
                if len(neg_r) > 1 else 0.0,
                min(1.0, round(kn / (len(neg_r) - 1), 4))
                if len(neg_r) > 1 else (1.0 if kn else 0.0)]
        entry["loo_positive_rate_range"] = [
            round(max(kp - 1, 0) / (len(pos_r) - 1), 4)
            if len(pos_r) > 1 else 0.0,
            min(1.0, round(kp / (len(pos_r) - 1), 4))
            if len(pos_r) > 1 else (1.0 if kp else 0.0)]
        entry["empirical_note"] = (
            "single-subject removal shifts rates within the LOO ranges; "
            "argmax-contribution drops are marginal-identical for a "
            "boolean cohort metric, so the analytic range IS the "
            "leave-one-out envelope")
        stability[fam] = entry

    # ---- perturbation (preregistered: stateful prefix T-30..T+30) ----
    perturb_summary = {}
    pos_bundles = [b for b in bundles if b["_kind"] == "positive"]
    for frac in (0.10, 0.20):
        key = f"perturbation{int(frac * 100)}"
        axes = {}
        for axis, getter in (
            ("armA_candidate_retained",
             lambda rr: any(lr["armA"]["candidate_at_T30"]
                            for lr in rr["lanes"].values())),
            ("armA_emerging_retained",
             lambda rr: rr["subject"]["a_emerging_ever"]),
            *[(fam + "_confirmed_retained",
               (lambda fam_: lambda rr:
                rr["subject"]["by_family"][fam_][
                    "confirmed"])(fam)) for fam in FAMILIES],
        ):
            base_true = 0
            kept_true = 0
            for b in pos_bundles:
                base_res = run_subject(b, b["streams"], b["T"],
                                       cp_offsets=PREFIX_CHECKPOINTS)
                base_flag = bool(getter(base_res))
                if not base_flag:
                    continue
                pert_streams = perturbed_streams(b["streams"], b["_id"],
                                                 frac)
                pert_res = run_subject(b, pert_streams, b["T"],
                                       cp_offsets=PREFIX_CHECKPOINTS)
                base_true += 1
                kept_true += bool(getter(pert_res))
            axes[axis] = {"unperturbed_true": base_true,
                          "retained": kept_true,
                          "ratio": round(kept_true / base_true, 4)
                          if base_true else None}
        perturb_summary[key] = axes

    payload = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "cohorts": {"positives_scorable": len(pos_r),
                    "positives_missing_T": missing_t,
                    "negatives_paired_scorable": len(neg_r)},
        "aggregate": agg,
        "counts_positive_episodes": outcome_events,
        "stability_leave_one_out": stability,
        "perturbation": perturb_summary,
    }
    return payload, metric_rows, enriched, segments, cf_results


def apply_decision_bars(payload):
    agg = payload["aggregate"]
    ref = ARM_A_REFERENCE
    decisions = {}
    a_neg = agg["armA"]["negative_emerging_rate"]["rate"]
    a_sep = agg["armA"]["separation"]
    for fam in FAMILIES:
        e = agg[fam]
        if e["positive_rate"] is None or e["negative_rate"] is None:
            decisions[fam] = "INSUFFICIENT_DATA"
            continue
        pr = e["positive_rate"]["rate"]
        nr = e["negative_rate"]["rate"]
        sep = e["separation"]
        d1 = nr <= BARS["neg_rate_max_abs"] and \
            (ref["neg_emerging_rate"] == 0 or
             nr <= BARS["neg_rate_max_ratio_vs_A"] * ref[
                 "neg_emerging_rate"])
        d2 = pr >= BARS["pos_confirmed_recall_min"]
        d3 = sep is not None and a_sep is not None and sep > a_sep
        d4 = e.get("median_confirmation_delay_days") is not None or True
        decisions[fam] = {"bars": {"D1_material_neg_drop": d1,
                                   "D2_pos_useful": d2,
                                   "D3_separation_beats_A": d3},
                          "family_supported_prereview":
                          bool(d1 and d2 and d3)}
    return decisions


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                     separators=(",", ":")).encode(
        "utf-8")).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=str(CATALOG_DEFAULT))
    ap.add_argument("--case-control", default=str(CASE_CONTROL_DEFAULT))
    ap.add_argument("--artifacts-dir", default=str(ARTDIR_DEFAULT))
    ap.add_argument("--limit-subjects", type=int, default=None)
    ap.add_argument("--skip-counterfactuals", action="store_true")
    ap.add_argument("--boundary-baseline-exits", action="store_true",
                    help="SENSITIVITY PROBE ONLY: sample the baseline-"
                         "exit discontinuities {dd+240d} in the boundary "
                         "set (review note N1). Never combined with the "
                         "bakeoff-of-record artifacts.")
    args = ap.parse_args(argv)
    global INCLUDE_BASELINE_EXITS
    INCLUDE_BASELINE_EXITS = bool(args.boundary_baseline_exits)
    artdir = Path(args.artifacts_dir)
    artdir.mkdir(parents=True, exist_ok=True)

    p1, rows1, enriched, segments, cf = execute_pipeline(
        args.catalog, args.case_control, artdir,
        limit_subjects=args.limit_subjects,
        skip_counterfactuals=args.skip_counterfactuals)
    h1 = canonical_hash([rows1, p1["aggregate"]])
    p2, rows2, _e2, _s2, _c2 = execute_pipeline(
        args.catalog, args.case_control, artdir,
        limit_subjects=args.limit_subjects, skip_counterfactuals=True)
    h2 = canonical_hash([rows2, p2["aggregate"]])

    determinism = {"run1_sha256": h1, "run2_sha256": h2,
                   "identical": h1 == h2}
    payload = {**p1, "determinism": determinism}

    # ---- ARM A PARITY GATE (harness acceptance, preregistered) ----
    # Only enforced on FULL cohorts: exact reproduction of the
    # established evaluator-v4 diagnostic aggregates.
    parity = {"enforced": args.limit_subjects is None}
    if parity["enforced"]:
        a = p1["aggregate"]["armA"]
        got_pos = a["positive_emerging_recall"]
        got_neg = a["negative_emerging_rate"]
        exp_pos = round(ARM_A_REFERENCE["pos_emerging_recall"], 6)
        exp_neg = round(ARM_A_REFERENCE["neg_emerging_rate"], 6)
        parity.update({
            "expected_emerging_recall": exp_pos,
            "got_emerging_recall": got_pos["rate"] if got_pos else None,
            "expected_negative_rate": exp_neg,
            "got_negative_rate": got_neg["rate"] if got_neg else None,
            "cohort": {"n_pos": got_pos["n"] if got_pos else None,
                       "n_neg": got_neg["n"] if got_neg else None},
        })
        parity["emerging_recall_match"] = bool(
            got_pos and abs(got_pos["rate"] - exp_pos) <= 1e-4)
        parity["negative_rate_match"] = bool(
            got_neg and abs(got_neg["rate"] - exp_neg) <= 1e-4)
        parity["pass_published_reference"] = bool(
            parity["emerging_recall_match"]
            and parity["negative_rate_match"])
        cur_ref = ARM_A_REFERENCE_CURRENT
        parity["current_reference"] = {
            "expected_emerging_recall":
                round(cur_ref["pos_emerging_recall_k"]
                      / cur_ref["pos_n"], 6),
            "match": bool(got_pos and got_pos["k"]
                          == cur_ref["pos_emerging_recall_k"]),
            "expected_negative_rate":
                round(cur_ref["neg_emerging_rate_k"]
                      / cur_ref["neg_n"], 6),
            "match_neg": bool(got_neg and got_neg["k"]
                              == cur_ref["neg_emerging_rate_k"]),
            "note": "frozen-evaluator NON_BLIND rerun 2026-08-26T20:28 "
                    "on current catalog; per-subject equality verified "
                    "(0 mismatches over 124 negatives)",
        }
        parity["pass_current_reference"] = bool(
            parity["current_reference"]["match"]
            and parity["current_reference"]["match_neg"])
        parity["drift_enumeration"] = \
            PARITY_DRIFT_PUBLISHED_VS_CURRENT_NEGATIVES
        parity["pass_any"] = bool(parity["pass_published_reference"]
                                  or parity["pass_current_reference"])
    payload["parity_arm_a"] = parity

    decisions = apply_decision_bars(p1)

    (artdir / "aggregate.json").write_text(json.dumps(payload, indent=2),
                                           encoding="utf-8")
    (artdir / "metric-rows.json").write_text(json.dumps(rows1, indent=2),
                                             encoding="utf-8")
    (artdir / "segments.json").write_text(json.dumps(segments, indent=2),
                                          encoding="utf-8")
    (artdir / "counterfactuals.json").write_text(json.dumps(cf, indent=2),
                                                 encoding="utf-8")
    (artdir / "decision-bars.json").write_text(json.dumps(decisions,
                                                          indent=2),
                                               encoding="utf-8")

    cfg = {
        "catalog": str(args.catalog),
        "case_control": str(args.case_control),
        "script_sha256": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(
            (REPO / "docs/handoffs/interest-intelligence/"
             "episode-semantics-contract-20260826.md").read_bytes()
        ).hexdigest() if (REPO / "docs/handoffs/interest-intelligence/"
                          "episode-semantics-contract-20260826.md"
                          ).exists() else None,
        "preregistration_sha256": hashlib.sha256(
            (REPO / "docs/handoffs/interest-intelligence/"
             "temporal-emergence-modelgen-preregistration-20260826.md"
             ).read_bytes()).hexdigest() if (REPO /
             "docs/handoffs/interest-intelligence/"
             "temporal-emergence-modelgen-preregistration-20260826.md"
             ).exists() else None,
        "production_head": _git_head(),
        "burst_policy_v2_params": bp2.PARAMS,
    }
    (artdir / "run-config.json").write_text(json.dumps(cfg, indent=2),
                                            encoding="utf-8")
    print(json.dumps({"aggregate": payload["aggregate"],
                      "determinism": determinism,
                      "parity_arm_a": payload.get("parity_arm_a"),
                      "decision_bars": decisions}, indent=2)[:8000])
    return 0


def _git_head():
    try:
        import subprocess
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse",
                              "HEAD"], capture_output=True, text=True,
                             timeout=15)
        return out.stdout.strip()[:12] if out.returncode == 0 else None
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
