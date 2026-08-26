"""Stateful burst bakeoff on consumed holdout-v4 (packet 2026-08-25).

TRAINING/DIAGNOSTIC ONLY. Not imported by production; never modifies
burst-policy-v1, evaluator-v2, the formal ledger, or holdout files.

Preregistered plan (frozen + hashed before any model result):
  P:/.data/yt-is/ef/concept-discovery-calibration/stateful-burst-v1/
  preregistered-plan.json

Kleinberg implementation: local port of hitalex/pybursts (MIT, 2014),
algorithm preserved; adaptation disclosed under the plan's
incompatibility clause: pybursts requires strictly increasing offsets,
so 14-day bin indices are made strictly increasing by adding a
sequential intra-bin fraction (preserving event multiplicity and bin
semantics).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy.stats import gamma as gamma_dist

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "scripts"))

import evaluate_concept_discovery as ev  # noqa: E402
from ef import concept_discovery as cd  # noqa: E402
from ef import concept_registry as cr  # noqa: E402

PLAN_PATH = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
                 "stateful-burst-v1/preregistered-plan.json")
PLAN_SHA256 = "a04ee19897ef318daa658caf8d27995a1caa11a556e0def050f340c52c24b957"
ART = Path("P:/.data/yt-is/ef/concept-discovery-eval/"
           "eval-20260825T114338-FORMAL")
CAL = Path("P:/.data/yt-is/ef/concept-discovery-calibration/"
           "stateful-burst-v1")
CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
HOLDOUT = "P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json"
CHECKPOINTS = ["T-30", "T", "T+7", "T+14", "T+30", "T+60"]
CP_OFFSETS = {"T-30": -30, "T": 0, "T+7": 7, "T+14": 14,
              "T+30": 30, "T+60": 60}

DECAY_HL = [30, 60, 90]
DECAY_MIN = [1.0, 1.5]
GP_WINDOW = [30, 60]
GP_MULT = [1.0, 1.5, 2.0]
GP_THR = [0.80, 0.90, 0.95]
GP_FLOOR = [1, 2]
GP_ALPHA, GP_BETA = 0.5, 0.5
KB_S = [2, 3]
KB_GAMMA = [0.5, 1.0, 2.0]
KB_FLOOR = [1, 2]
KB_BIN = 14
KB_LOOKBACK = 730
GL_NODES = 256
_gl_x, _gl_w = np.polynomial.legendre.leggauss(GL_NODES)


# ---------------------------------------------------------------------------
# posterior probability P(lambda_recent > mult * lambda_baseline)
# ---------------------------------------------------------------------------

def prob_rate_above(k_recent, exp_recent, k_base, mult):
    """Exact deterministic Gauss-Legendre quadrature of
    E_{lb~Ga(a2,b2)}[ sf_{Ga(a1,b1)}(mult*lb) ]. Exposures in 30-day
    units."""
    a1 = GP_ALPHA + k_recent
    b1 = GP_BETA + exp_recent
    a2 = GP_ALPHA + k_base
    b2 = GP_BETA + 6.0  # 180d baseline in 30-day units
    y_max = float(gamma_dist.ppf(1 - 1e-13, a2, scale=1.0 / b2))
    if y_max <= 0:
        return 0.0
    # y = t^2 substitution removes the y^-(1-a2) singularity at 0 for
    # small a2 (k_base=0 gives a2=0.5); GL then runs over t in
    # (0, sqrt(y_max)) with dy = 2t dt.
    t_max = math.sqrt(y_max)
    ts = 0.5 * t_max * (_gl_x + 1.0)
    ws = 0.5 * t_max * _gl_w
    ys = ts * ts
    vals = gamma_dist.sf(mult * ys, a1, scale=1.0 / b1) * \
        gamma_dist.pdf(ys, a2, scale=1.0 / b2) * 2.0 * ts
    return float(np.clip(np.dot(ws, vals), 0.0, 1.0))


# ---------------------------------------------------------------------------
# Kleinberg (port of hitalex/pybursts kleinberg(), MIT)
# ---------------------------------------------------------------------------

def kleinberg(offsets, s=2, gamma=1.0):
    """Faithful port of pybursts.kleinberg. offsets must be strictly
    increasing. Returns list of [level, start, end]."""
    if s <= 1 or gamma <= 0:
        raise ValueError("bad s/gamma")
    offsets = [float(o) for o in offsets]
    n = len(offsets)
    if n == 0:
        return []
    if n == 1:
        return [[0, offsets[0], offsets[0]]]
    gaps = [offsets[i + 1] - offsets[i] for i in range(n - 1)]
    if any(g <= 0 for g in gaps):
        raise ValueError("zero gap")
    T = sum(gaps)
    if T <= 0:
        return []
    ng = len(gaps)
    g_hat = T / ng
    min_gap = min(gaps)
    k = int(math.ceil(1 + math.log(T, s) + math.log(1 / min_gap, s)))
    k = max(k, 1)
    gamma_log_n = gamma * math.log(ng)
    alpha = [s ** x / g_hat for x in range(k)]
    C = [float("inf")] * k
    C[0] = 0.0
    q = []
    for t in range(ng):
        Cp = [float("inf")] * k
        qp = [[None] * (t + 1) for _ in range(k)]
        for j in range(k):
            best, el = float("inf"), 0
            for x in range(k):
                tau = 0.0 if x >= j else (j - x) * gamma_log_n
                c = C[x] + tau
                if c < best:
                    best, el = c, x
            fj = alpha[j] * math.exp(-alpha[j] * gaps[t])
            if fj > 0:
                Cp[j] = best - math.log(fj)
            if t > 0:
                qp[j][:t] = q[el][:]
            qp[j][t] = j + 1
        C = Cp
        q = qp
    j = min(range(k), key=lambda i: C[i])
    states = q[j]
    bursts = []
    stack = []
    prev = 0
    for t in range(ng):
        st = states[t]
        if st > prev:
            for i in range(st - prev):
                bursts.append([prev + i, offsets[t], None])
                stack.append(len(bursts) - 1)
        elif st < prev:
            for _ in range(prev - st):
                if stack:
                    bursts[stack.pop()][2] = offsets[t + 1]
        prev = st
    while stack:
        bursts[stack.pop()][2] = offsets[-1]
    return bursts


def kleinberg_signal(obs, as_of_d, s, gamma):
    """obs: list of (obs_date_iso, channel_id) for distinct EUs (one row
    per distinct EU, date = its earliest mention). Returns
    (active, span_bins, top_level, ch28)."""
    lookback_start = as_of_d - timedelta(days=KB_LOOKBACK)
    pts = sorted(date.fromisoformat(d) for d, _ in obs
                 if date.fromisoformat(d) > lookback_start)
    if len(pts) < 2:
        return False, 0, 0, _ch28(obs, as_of_d)
    # fractional bin offsets: strictly increasing, multiplicity kept
    offsets = []
    last_bin, rank = None, 0
    for d in pts:
        b = (d - (as_of_d - timedelta(days=KB_LOOKBACK))).days / KB_BIN
        if b == last_bin:
            rank += 1
        else:
            rank = 0
            last_bin = b
        offsets.append(b + rank / (rank + 2.0))
    try:
        bursts = kleinberg(offsets, s, gamma)
    except ValueError:
        return False, 0, 0, _ch28(obs, as_of_d)
    last_off = offsets[-1]
    last_bin_f = math.floor(last_off)
    total_bins = math.ceil(KB_LOOKBACK / KB_BIN)
    top = 0
    for level, st, en in bursts:
        if en is None:
            continue
        if level < 1:
            continue
        top = max(top, level)
    # active burst at as_of: interval ending at the last event whose end
    # bin is within the final 2 bins of the lookback window
    for level, st, en in bursts:
        if level < 1 or en is None:
            continue
        if en >= last_off - 1e-9 and math.floor(en) >= total_bins - 2:
            span = math.floor(en) - math.floor(st)
            return True, span, top, _ch28(obs, as_of_d)
    return False, 0, top, _ch28(obs, as_of_d)


def _ch28(obs, as_of_d):
    recent = as_of_d - timedelta(days=28)
    return len({ch for d, ch in obs if date.fromisoformat(d) > recent})


# ---------------------------------------------------------------------------
# decay / candidate
# ---------------------------------------------------------------------------

def decay_support(obs, as_of_d, half_life):
    """obs rows (date, channel); distinct EU handled upstream by
    deduplication to one row per EU."""
    h = float(half_life)
    return sum(2.0 ** (-((as_of_d - date.fromisoformat(d)).days) / h)
               for d, _ in obs)


def is_candidate(dsup_by_h, lifetime, half_life, smin):
    return dsup_by_h[half_life] >= smin and lifetime >= 2


# ---------------------------------------------------------------------------
# per-checkpoint aggregate computation from obs rows
# ---------------------------------------------------------------------------

def aggregate(obs_rows, as_of_d):
    """obs_rows: list of dicts (eu_id, obs_date, channel_id). Returns
    aggregates used by all model families (distinct-EU based)."""
    first_by_eu = {}
    ch_by_eu = {}
    raw_mentions = 0
    for r in obs_rows:
        raw_mentions += 1
        eu = r["eu_id"]
        if eu not in first_by_eu or r["obs_date"] < first_by_eu[eu]:
            first_by_eu[eu] = r["obs_date"]
            ch_by_eu[eu] = r["channel_id"]
    eu_pairs = [(d, ch_by_eu[eu]) for eu, d in first_by_eu.items()]
    out = {"lifetime": len(first_by_eu), "raw": raw_mentions}
    for h in DECAY_HL:
        out[f"d{h}"] = round(decay_support(eu_pairs, as_of_d, h), 4)
    for w in GP_WINDOW:
        rs = as_of_d - timedelta(days=w)
        bs = rs - timedelta(days=180)
        rec = [(d, c) for d, c in eu_pairs
               if rs < date.fromisoformat(d) <= as_of_d]
        base = [d for d, _ in eu_pairs
                if bs < date.fromisoformat(d) <= rs]
        out[f"k{w}"] = len(rec)
        out[f"ch{w}"] = len({c for _, c in rec})
        out[f"b180_{w}"] = len(base)
    out["obs"] = eu_pairs
    return out


# ---------------------------------------------------------------------------
# episode state machine (frozen semantics)
# ---------------------------------------------------------------------------

def episode_promotion(signals):
    """signals: list of (cp_label, positive, posterior, channels) in
    chronological order. Returns promoted (bool)."""
    promoted = False
    prev_cp, prev_pos = None, False
    for cp, pos, post, ch in signals:
        if pos and prev_pos and prev_cp is not None:
            gap = abs(CP_OFFSETS[cp] - CP_OFFSETS[prev_cp])
            if gap <= 30:
                promoted = True
        if post is not None and post >= 0.99 and ch >= 2:
            promoted = True
        prev_cp, prev_pos = cp, pos
    return promoted


def episode_promotion_no_persistence(signals):
    return any(pos for _, pos, _, _ in signals)


# ---------------------------------------------------------------------------
# phase 1: extraction into features.sqlite
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE meta(k, v);
CREATE TABLE tinfo(t_key TEXT, cp TEXT, as_of TEXT);
CREATE TABLE entities(
  t_key TEXT, cp TEXT, cid TEXT,
  k30 INT, k60 INT, b180_30 INT, b180_60 INT, ch30 INT, ch60 INT,
  d30 REAL, d60 REAL, d90 REAL, lifetime INT);
CREATE TABLE obs(t_key TEXT, cp TEXT, cid TEXT, eu_id TEXT,
  obs_date TEXT, channel_id TEXT);
CREATE TABLE klein(t_key TEXT, cp TEXT, cid TEXT, s REAL, gamma REAL,
  active INT, span INT, level INT, ch28 INT);
CREATE INDEX ix_ent ON entities(t_key, cp);
CREATE INDEX ix_obs ON obs(t_key, cp);
CREATE INDEX ix_kb ON klein(t_key, cp);
"""


def _matches(label, names_norm):
    ln = ev._norm(label)
    if not ln:
        return False
    for tn in names_norm:
        if tn and (ln == tn or ev._word_boundary_contains(ln, tn)
                   or ev._word_boundary_contains(tn, ln)):
            return True
    return False


def extract() -> None:
    digest = hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    if digest != PLAN_SHA256:
        sys.exit(f"plan hash mismatch: {digest}; refusing")
    fdb = CAL / "features.sqlite"
    if fdb.exists():
        fdb.unlink()
    conn = sqlite3.connect(str(fdb))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO meta VALUES('plan_sha256', ?)", (digest,))
    targets = ev.load_targets(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    negs = json.loads((ART / "negative-controls.json").read_text())
    controls_by_target = {}
    for n in negs:
        controls_by_target.setdefault(n["target_id"], []).append(n["control_id"])

    cat = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    cat.row_factory = sqlite3.Row
    t0 = time.time()
    try:
        for i, t in enumerate(targets):
            tid = t["target_id"]
            names_norm = [ev._norm(n) for n in
                          [t["canonical_name"]] + list(t["aliases"])]
            my_controls = set(controls_by_target.get(tid, []))
            t_date = date.fromisoformat(scor[tid])
            for cp in CHECKPOINTS:
                d = min(t_date + timedelta(days=CP_OFFSETS[cp]),
                        date.today()).isoformat()
                as_of_d = date.fromisoformat(d)
                conn.execute("INSERT INTO tinfo VALUES(?,?,?)", (tid, cp, d))
                entity_obs = cd._entity_observations(cat, d)
                ent_rows, obs_rows, kb_rows = [], [], []
                for node_id, obs in entity_obs.items():
                    label = obs[0]["label"]
                    cid = cr.concept_identity_id("entity", label)
                    agg = aggregate(obs, as_of_d)
                    ent_rows.append((tid, cp, cid, agg["k30"], agg["k60"],
                                     agg["b180_30"], agg["b180_60"],
                                     agg["ch30"], agg["ch60"], agg["d30"],
                                     agg["d60"], agg["d90"], agg["lifetime"]))
                    keep_full = cid in my_controls or \
                        _matches(label, names_norm)
                    if keep_full:
                        for r in obs:
                            obs_rows.append((tid, cp, cid, r["eu_id"],
                                             r["obs_date"], r["channel_id"]))
                        for s in KB_S:
                            for g in KB_GAMMA:
                                a, sp, lv, c28 = kleinberg_signal(
                                    agg["obs"], as_of_d, s, g)
                                kb_rows.append((tid, cp, cid, s, g,
                                                int(a), sp, lv, c28))
                conn.executemany("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,"
                                 "?,?,?,?,?)", ent_rows)
                conn.executemany("INSERT INTO obs VALUES(?,?,?,?,?,?)",
                                 obs_rows)
                conn.executemany("INSERT INTO klein VALUES(?,?,?,?,?,?,?,?,?)",
                                 kb_rows)
            conn.commit()
            print(f"[extract] {i + 1}/42 {tid} ({time.time() - t0:.0f}s)",
                  flush=True)
    finally:
        cat.close()
        conn.close()
    print(f"[extract] wrote {fdb}")


# ---------------------------------------------------------------------------
# phase 2: evaluation
# ---------------------------------------------------------------------------

def fold_of(tid):
    return int(hashlib.sha256(tid.encode()).hexdigest()[:8], 16) % 5


def load_eval_data():
    """Load aggregates into memory keyed (t_key, cp) -> {cid: agg} plus
    kleinberg table and obs for matched/control entities."""
    fdb = CAL / "features.sqlite"
    conn = sqlite3.connect(f"file:{fdb}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ents, kleins, obsmap, asof, matched = {}, {}, {}, {}, {}
    for r in conn.execute("SELECT * FROM tinfo"):
        asof[(r["t_key"], r["cp"])] = r["as_of"]
    for r in conn.execute("SELECT * FROM entities"):
        ents.setdefault((r["t_key"], r["cp"]), {})[r["cid"]] = {
            "k30": r["k30"], "k60": r["k60"], "b180_30": r["b180_30"],
            "b180_60": r["b180_60"], "ch30": r["ch30"], "ch60": r["ch60"],
            "d30": r["d30"], "d60": r["d60"], "d90": r["d90"],
            "lifetime": r["lifetime"]}
    for r in conn.execute("SELECT * FROM klein"):
        kleins.setdefault((r["t_key"], r["cp"]), {})\
            .setdefault(r["cid"], {})[(r["s"], r["gamma"])] = {
                "active": r["active"], "span": r["span"],
                "level": r["level"], "ch28": r["ch28"]}
    for r in conn.execute("SELECT * FROM obs"):
        obsmap.setdefault((r["t_key"], r["cp"]), {})\
            .setdefault(r["cid"], []).append(
                {"eu_id": r["eu_id"], "obs_date": r["obs_date"],
                 "channel_id": r["channel_id"]})
    conn.close()
    negs = json.loads((ART / "negative-controls.json").read_text())
    ctl = {}
    for n in negs:
        ctl.setdefault(n["target_id"], []).append(n["control_id"])
    # matched entity per (t, cp): the cid with obs rows
    for (tk, cp), bycid in obsmap.items():
        matched.setdefault(tk, set()).update(bycid.keys())
    return {"ents": ents, "klein": kleins, "obs": obsmap, "asof": asof,
            "controls": ctl, "matched": matched,
            "target_ids": sorted(ents and {k[0] for k in ents} or
                                 set(ctl.keys()))}


def gp_posterior(agg, window, mult):
    exp_r = window / 30.0
    k = agg[f"k{window}"]
    kb = agg[f"b180_{window}"]
    return prob_rate_above(k, exp_r, kb, mult)


def eval_decay(data, tids, half_life, smin):
    hits, counts = 0, []
    for tk in tids:
        t_hit = False
        for cp in CHECKPOINTS:
            e = data["ents"].get((tk, cp), {})
            cnt = 0
            for agg in e.values():
                if is_candidate({half_life: agg[f"d{half_life}"]},
                                agg["lifetime"], half_life, smin):
                    cnt += 1
            counts.append(cnt)
            for cid in data["matched"].get(tk, set()):
                agg = e.get(cid)
                if agg and is_candidate(
                        {half_life: agg[f"d{half_life}"]},
                        agg["lifetime"], half_life, smin):
                    t_hit = True
        hits += t_hit
    n = len(tids) or 1
    return {"candidate_recall": round(hits / n, 4),
            "mean_candidates": round(sum(counts) / len(counts), 1) if
            counts else 0.0}


def select_decay_gate(data, tids):
    rows = [eval_decay(data, tids, h, m) |
            {"half_life": h, "smin": m}
            for h in DECAY_HL for m in DECAY_MIN]
    ok = [r for r in rows if r["candidate_recall"] >= 0.70]
    if ok:
        ok.sort(key=lambda r: (r["mean_candidates"], -r["half_life"],
                               r["smin"]))
        return ok[0], rows
    rows.sort(key=lambda r: (-r["candidate_recall"], r["mean_candidates"]))
    return rows[0], rows


def eval_gp(data, tids, gate, window, mult, thr, floor,
            persistence=True):
    h, smin = gate["half_life"], gate["smin"]
    em_hits = ctl_hits = cand_hits = 0
    ctl_den = 0
    em_counts = []
    for tk in tids:
        t_cand = t_em = False
        sigs = []
        for cp in CHECKPOINTS:
            key = (tk, cp)
            aggs = data["ents"].get(key, {})
            em = 0
            for cid, agg in aggs.items():
                post = gp_posterior(agg, window, mult)
                pos = post >= thr and agg[f"ch{window}"] >= floor
                if pos:
                    em += 1
                if cid in data["matched"].get(tk, set()):
                    if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"],
                                    h, smin):
                        t_cand = True
                    sigs.append((cp, pos, post, agg[f"ch{window}"]))
            em_counts.append(em)
        t_em = (episode_promotion(sigs) if persistence
                else episode_promotion_no_persistence(sigs)) and t_cand
        em_hits += t_em
        cand_hits += t_cand
        for cid in data["controls"].get(tk, []):
            ctl_den += 1
            sigs = []
            g_cand = False
            for cp in ("T-30", "T+30"):
                agg = data["ents"].get((tk, cp), {}).get(cid)
                if not agg:
                    sigs.append((cp, False, None, 0))
                    continue
                post = gp_posterior(agg, window, mult)
                pos = post >= thr and agg[f"ch{window}"] >= floor
                sigs.append((cp, pos, post, agg[f"ch{window}"]))
                if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"], h,
                                smin):
                    g_cand = True
            if g_cand and (episode_promotion(sigs) if persistence else
                           episode_promotion_no_persistence(sigs)):
                ctl_hits += 1
    n = len(tids) or 1
    return {"candidate_recall": round(cand_hits / n, 4),
            "emerging_recall": round(em_hits / n, 4),
            "control_emerging_rate": round(ctl_hits / ctl_den, 4) if
            ctl_den else 0.0,
            "separation": round(em_hits / n - (ctl_hits / ctl_den if
                                               ctl_den else 0), 4),
            "mean_emerging": round(sum(em_counts) / len(em_counts), 2) if
            em_counts else 0.0}


def eval_kb(data, tids, gate, s, gamma, floor):
    h, smin = gate["half_life"], gate["smin"]
    em_hits = ctl_hits = cand_hits = 0
    ctl_den = 0
    for tk in tids:
        t_cand = False
        pos_seq = []
        for cp in CHECKPOINTS:
            key = (tk, cp)
            aggs = data["ents"].get(key, {})
            kb = data["klein"].get(key, {})
            for cid, agg in aggs.items():
                if cid in data["matched"].get(tk, set()):
                    if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"],
                                    h, smin):
                        t_cand = True
                    k = kb.get(cid, {}).get((s, gamma))
                    if k:
                        ok = k["active"] and (k["span"] >= 1 or
                                              k["level"] >= 2) and \
                            k["ch28"] >= floor
                        pos_seq.append(ok)
        t_em = t_cand and _kb_promoted(pos_seq)
        em_hits += t_em
        cand_hits += t_cand
        for cid in data["controls"].get(tk, []):
            ctl_den += 1
            seq = []
            g_cand = False
            for cp in ("T-30", "T+30"):
                agg = data["ents"].get((tk, cp), {}).get(cid)
                if not agg:
                    seq.append(False)
                    continue
                if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"], h,
                                smin):
                    g_cand = True
                k = data["klein"].get((tk, cp), {}).get(cid, {})\
                    .get((s, gamma))
                seq.append(bool(k and k["active"] and
                                (k["span"] >= 1 or k["level"] >= 2) and
                                k["ch28"] >= floor))
            if g_cand and _kb_promoted(seq):
                ctl_hits += 1
    n = len(tids) or 1
    return {"candidate_recall": round(cand_hits / n, 4),
            "emerging_recall": round(em_hits / n, 4),
            "control_emerging_rate": round(ctl_hits / ctl_den, 4) if
            ctl_den else 0.0,
            "separation": round(em_hits / n - (ctl_hits / ctl_den if
                                               ctl_den else 0), 4)}


def _kb_promoted(seq):
    """two consecutive positives (Kleinberg promotion semantics)."""
    return any(seq[i] and seq[i + 1] for i in range(len(seq) - 1))


# ---------------------------------------------------------------------------
# perturbation
# ---------------------------------------------------------------------------

def perturb_eval(data, tids, gate, kind, params):
    h, smin = gate["half_life"], gate["smin"]
    ret = {10: {"cand": 0, "em": 0, "den": 0}, 20: {"cand": 0, "em": 0,
                                                    "den": 0}}
    for tk in tids:
        universe = data["obs"].get((tk, "T+30"), {})
        cids = [c for c in universe if c in data["matched"].get(tk, set())]
        cids = sorted(cids)
        for pct in (10, 20):
            frac = pct / 100.0
            ret[pct]["den"] += 1
            if not cids:
                continue
            obs = universe[cids[0]]
            f, _ = _perturbed_features(tk, obs, frac,
                                       data["asof"][(tk, "T+30")], kind,
                                       params)
            if f is None:
                continue
            if f["cand"]:
                ret[pct]["cand"] += 1
                if f["em"]:
                    ret[pct]["em"] += 1
    return ret


def _perturbed_features(tk, obs, frac, as_of30, kind, params):
    rnd = int(hashlib.sha256(tk.encode()).hexdigest()[:8], 16)
    eu_first = {}
    for r in obs:
        eu = r["eu_id"]
        if eu not in eu_first or r["obs_date"] < eu_first[eu]["obs_date"]:
            eu_first[eu] = r
    eus = sorted(eu_first)
    take = int(len(eus) * frac)
    start = rnd % max(len(eus) - take, 1) if take else 0
    removed = set(eus[start:start + take])
    kept = [r for r in obs if r["eu_id"] not in removed]
    sigs = []
    cand_any = False
    for cp in CHECKPOINTS:
        d = date.fromisoformat(as_of30) - timedelta(
            days=CP_OFFSETS["T+30"] - CP_OFFSETS[cp])
        sub = [r for r in kept if date.fromisoformat(r["obs_date"]) <= d]
        agg = aggregate(sub, d)
        if is_candidate({h_: agg[f"d{h_}"] for h_ in DECAY_HL},
                        agg["lifetime"], params["half_life"],
                        params["smin"]):
            cand_any = True
        if kind == "gp":
            post = gp_posterior(agg, params["window"], params["mult"])
            pos = post >= params["thr"] and \
                agg[f"ch{params['window']}"] >= params["floor"]
            sigs.append((cp, pos, post, agg[f"ch{params['window']}"]))
    em = False
    if kind == "gp":
        em = episode_promotion(sigs) and cand_any
    return {"cand": cand_any, "em": em}, len(removed)


# ---------------------------------------------------------------------------
# selection + driver
# ---------------------------------------------------------------------------

def select_variant(rows):
    ok = [r for r in rows if r["control_emerging_rate"] < 0.5]
    qual = [r for r in ok
            if r["candidate_recall"] >= 0.70
            and r["emerging_recall"] >= 0.50
            and r["control_emerging_rate"] <= 0.20
            and r["perturb20"] >= 0.50
            and r["separation"] > 0]
    if qual:
        qual.sort(key=lambda r: (r["control_emerging_rate"],
                                 -r["emerging_recall"], -r["perturb20"],
                                 -r["candidate_recall"],
                                 r.get("mean_emerging", 0),
                                 r.get("params_count", 0)))
        return {"selected": _vid(qual[0]), "n_qualified": len(qual)}
    return {"selected": None, "pareto": _pareto(ok)}


def _vid(r):
    return {k: r[k] for k in ("kind", "window", "mult", "thr", "floor",
                              "s", "gamma") if k in r}


def _pareto(rows):
    front = []
    for r in rows:
        dominated = any(
            o is not r and o["emerging_recall"] >= r["emerging_recall"]
            and o["control_emerging_rate"] <= r["control_emerging_rate"]
            and (o["emerging_recall"] > r["emerging_recall"]
                 or o["control_emerging_rate"] < r["control_emerging_rate"])
            for o in rows)
        if not dominated:
            front.append(_vid(r))
    return front[:20]


def gp_variants(data, tids, gate, with_perturb=True):
    rows = []
    for w in GP_WINDOW:
        for m in GP_MULT:
            for th in GP_THR:
                for fl in GP_FLOOR:
                    r = eval_gp(data, tids, gate, w, m, th, fl)
                    r.update({"kind": "gp", "window": w, "mult": m,
                              "thr": th, "floor": fl, "params_count":
                              (1 if fl > 1 else 0) +
                              (0 if m == 1.0 else 1)})
                    rows.append(r)
    if with_perturb:
        for r in rows:
            p = perturb_eval(data, tids, gate, "gp",
                             {"half_life": gate["half_life"],
                              "smin": gate["smin"], "window": r["window"],
                              "mult": r["mult"], "thr": r["thr"],
                              "floor": r["floor"]})
            r["perturb20"] = round(p[20]["cand"] / p[20]["den"], 4) if \
                p[20]["den"] else 0.0
            r["perturb20_emerging"] = round(p[20]["em"] / p[20]["den"],
                                            4) if p[20]["den"] else 0.0
    else:
        for r in rows:
            r["perturb20"] = 1.0
    return rows


def kb_variants(data, tids, gate, with_perturb=True):
    rows = []
    for s in KB_S:
        for g in KB_GAMMA:
            for fl in KB_FLOOR:
                r = eval_kb(data, tids, gate, s, g, fl)
                r.update({"kind": "kb", "s": s, "gamma": g, "floor": fl,
                          "params_count": 1 + (0 if g == 1.0 else 1)})
                rows.append(r)
    if with_perturb:
        for r in rows:
            p = perturb_eval(data, tids, gate, "kb",
                             {"half_life": gate["half_life"],
                              "smin": gate["smin"]})
            r["perturb20"] = round(p[20]["cand"] / p[20]["den"], 4) if \
                p[20]["den"] else 0.0
    else:
        for r in rows:
            r["perturb20"] = 1.0
    return rows


def run() -> None:
    digest = hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    if digest != PLAN_SHA256:
        sys.exit(f"plan hash mismatch: {digest}; refusing")
    fdb_digest = sqlite3.connect(
        f"file:{CAL / 'features.sqlite'}?mode=ro", uri=True).execute(
        "SELECT v FROM meta WHERE k='plan_sha256'").fetchone()[0]
    if fdb_digest != PLAN_SHA256:
        sys.exit("features.sqlite plan hash mismatch; refusing")
    data = load_eval_data()
    tids = data["target_ids"]
    gate_full, decay_rows_full = select_decay_gate(data, tids)
    print("[decay gate full]", json.dumps(gate_full))
    gp_full = gp_variants(data, tids, gate_full)
    kb_full = kb_variants(data, tids, gate_full)
    gp_sel = select_variant(gp_full)
    kb_sel = select_variant(kb_full)
    (CAL / "full-results.json").write_text(json.dumps({
        "plan_sha256": PLAN_SHA256,
        "decay_gate": gate_full, "decay_variants": decay_rows_full,
        "gp_selection": gp_sel, "gp_grid": gp_full,
        "kb_selection": kb_sel, "kb_grid": kb_full}, indent=1),
        encoding="utf-8")
    print("[gp full]", json.dumps(gp_sel))
    print("[kb full]", json.dumps(kb_sel))

    folds = {i: {t for t in tids if fold_of(t) == i} for i in range(5)}
    fold_results = []
    oof = {"gp": [], "kb": []}
    for held in range(5):
        train = [t for t in tids if fold_of(t) != held]
        held_ids = [t for t in tids if fold_of(t) == held]
        gate, _ = select_decay_gate(data, train)
        gp_rows = gp_variants(data, train, gate)
        kb_rows = kb_variants(data, train, gate)
        gsel = select_variant(gp_rows)
        ksel = select_variant(kb_rows)
        rec = {"fold": held, "gate": gate, "gp_selected": gsel["selected"],
               "kb_selected": ksel["selected"],
               "gp_n_qualified": gsel.get("n_qualified", 0),
               "kb_n_qualified": ksel.get("n_qualified", 0)}
        if gsel["selected"]:
            ho = eval_gp(data, held_ids, gate,
                         gsel["selected"]["window"],
                         gsel["selected"]["mult"], gsel["selected"]["thr"],
                         gsel["selected"]["floor"])
            rec["gp_held_out"] = ho
            oof["gp"].append(ho)
        if ksel["selected"]:
            ho = eval_kb(data, held_ids, gate, ksel["selected"]["s"],
                         ksel["selected"]["gamma"], ksel["selected"]["floor"])
            rec["kb_held_out"] = ho
            oof["kb"].append(ho)
        fold_results.append(rec)
        print(f"[fold {held}]", json.dumps(rec), flush=True)
    (CAL / "fold-results.json").write_text(json.dumps(fold_results,
                                                      indent=1),
                                           encoding="utf-8")

    def agg_oof(lst):
        if not lst:
            return {"status": "NO_SELECTION_IN_ANY_FOLD"}
        n = len(lst)
        return {k: round(sum(r[k] for r in lst) / n, 4) for k in
                ("candidate_recall", "emerging_recall",
                 "control_emerging_rate", "separation")}
    oof_summary = {"gp": agg_oof(oof["gp"]), "kb": agg_oof(oof["kb"])}
    (CAL / "oof-summary.json").write_text(json.dumps(oof_summary,
                                                     indent=1),
                                          encoding="utf-8")
    print("[oof]", json.dumps(oof_summary))
    ablations(data, tids, gate_full, gp_sel, kb_sel)


def ablations(data, tids, gate, gp_sel, kb_sel):
    out = {"decay_gate": gate}
    gsel = gp_sel["selected"]
    if gsel:
        base = eval_gp(data, tids, gate, gsel["window"], gsel["mult"],
                       gsel["thr"], gsel["floor"])
        out["persistence_on"] = base
        out["persistence_off"] = eval_gp(data, tids, gate, gsel["window"],
                                         gsel["mult"], gsel["thr"],
                                         gsel["floor"],
                                         persistence=False)
        out["floor_off"] = eval_gp(data, tids, gate, gsel["window"],
                                   gsel["mult"], gsel["thr"], 1)
    # candidate hard-window comparator (C3: 30d, recent>=1, lifetime>=2)
    out["candidate_hard_window_C3"] = eval_decay_hard_C3(data, tids)
    out["candidate_decay_gate"] = gate
    (CAL / "ablations.json").write_text(json.dumps(out, indent=1),
                                        encoding="utf-8")


def eval_gp_counts(data, tids, gate, window, mult, thr, floor, mode):
    """Ablation: Bayesian counts computed from raw obs rows in `mode`:
    distinct (default), raw (all mention rows), capped (1 per channel
    per day). Applies to targets and controls via their stored obs."""
    h, smin = gate["half_life"], gate["smin"]
    em_hits = ctl_hits = 0
    ctl_den = 0
    for tk in tids:
        sigs = []
        t_cand = False
        for cp in CHECKPOINTS:
            key = (tk, cp)
            aggs = data["ents"].get(key, {})
            obsmap = data["obs"].get(key, {})
            for cid in data["matched"].get(tk, set()):
                agg = aggs.get(cid)
                rows = obsmap.get(cid, [])
                if not agg or not rows:
                    continue
                if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"], h,
                                smin):
                    t_cand = True
                counts = _window_counts(rows, cp_date(data, tk, cp),
                                        window, mode)
                post = prob_rate_above(counts["k"], window / 30.0,
                                       counts["b"], mult)
                pos = post >= thr and counts["ch"] >= floor
                sigs.append((cp, pos, post, counts["ch"]))
        em_hits += (episode_promotion(sigs) and t_cand)
        for cid in data["controls"].get(tk, []):
            ctl_den += 1
            sigs = []
            g_cand = False
            for cp in ("T-30", "T+30"):
                agg = data["ents"].get((tk, cp), {}).get(cid)
                rows = data["obs"].get((tk, cp), {}).get(cid, [])
                if not agg or not rows:
                    sigs.append((cp, False, None, 0))
                    continue
                if is_candidate({h: agg[f"d{h}"]}, agg["lifetime"], h,
                                smin):
                    g_cand = True
                counts = _window_counts(rows, cp_date(data, tk, cp),
                                        window, mode)
                post = prob_rate_above(counts["k"], window / 30.0,
                                       counts["b"], mult)
                sigs.append((cp, post >= thr and counts["ch"] >= floor,
                             post, counts["ch"]))
            if g_cand and episode_promotion(sigs):
                ctl_hits += 1
    n = len(tids) or 1
    return {"emerging_recall": round(em_hits / n, 4),
            "control_emerging_rate": round(ctl_hits / ctl_den, 4) if
            ctl_den else 0.0}


def cp_date(data, tk, cp):
    return date.fromisoformat(data["asof"][(tk, cp)])


def _window_counts(rows, as_of_d, window, mode):
    rs = as_of_d - timedelta(days=window)
    bs = rs - timedelta(days=180)
    if mode == "raw":
        rec = [r for r in rows if rs < date.fromisoformat(r["obs_date"])
               <= as_of_d]
        base = [r for r in rows if bs < date.fromisoformat(r["obs_date"])
                <= rs]
        return {"k": len(rec), "b": len(base),
                "ch": len({r["channel_id"] for r in rec})}
    if mode == "capped":
        def cap(rows_):
            seen = set()
            out = []
            for r in rows_:
                key = (r["channel_id"], r["obs_date"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
            return out
        rec = cap([r for r in rows
                   if rs < date.fromisoformat(r["obs_date"]) <= as_of_d])
        base = cap([r for r in rows
                    if bs < date.fromisoformat(r["obs_date"]) <= rs])
        return {"k": len(rec), "b": len(base),
                "ch": len({r["channel_id"] for r in rec})}
    first_by_eu, ch_by_eu = {}, {}
    for r in rows:
        eu = r["eu_id"]
        if eu not in first_by_eu or r["obs_date"] < first_by_eu[eu]:
            first_by_eu[eu] = r["obs_date"]
            ch_by_eu[eu] = r["channel_id"]
    rec = [(d, ch_by_eu[e]) for e, d in first_by_eu.items()
           if rs < date.fromisoformat(d) <= as_of_d]
    base = [d for d in first_by_eu.values()
            if bs < date.fromisoformat(d) <= rs]
    return {"k": len(rec), "b": len(base),
            "ch": len({c for _, c in rec})}


def eval_decay_hard_C3(data, tids):
    hits, counts = 0, []
    for tk in tids:
        t_hit = False
        for cp in CHECKPOINTS:
            e = data["ents"].get((tk, cp), {})
            cnt = 0
            for agg in e.values():
                if agg["k30"] >= 1 and agg["lifetime"] >= 2:
                    cnt += 1
            counts.append(cnt)
            for cid in data["matched"].get(tk, set()):
                agg = e.get(cid)
                if agg and agg["k30"] >= 1 and agg["lifetime"] >= 2:
                    t_hit = True
        hits += t_hit
    return {"candidate_recall": round(hits / (len(tids) or 1), 4),
            "mean_candidates": round(sum(counts) / len(counts), 1) if
            counts else 0.0}


def _conclusion(oof_summary, gp_sel, kb_sel):
    gp_ok = bool(gp_sel["selected"]) and "status" not in oof_summary["gp"]
    kb_ok = bool(kb_sel["selected"]) and "status" not in oof_summary["kb"]
    if gp_ok and kb_ok:
        return "BOTH_STATEFUL_FAMILIES_SUPPORTED"
    if gp_ok:
        return "BAYESIAN_EPISODES_SUPPORTED"
    if kb_ok:
        return "KLEINBERG_EPISODES_SUPPORTED"
    full = json.loads((CAL / "full-results.json").read_text(encoding="utf-8"))
    best_em = max([r["emerging_recall"] for r in full["gp_grid"]] +
                  [r["emerging_recall"] for r in full["kb_grid"]] + [0])
    if best_em >= 0.40:
        return "PARTIAL_STATEFUL_SUPPORT"
    return "NO_STATEFUL_MODEL_SUPPORTED"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["extract", "run", "all"])
    a = ap.parse_args()
    if a.phase in ("extract", "all"):
        extract()
    if a.phase in ("run", "all"):
        run()
        oof = json.loads((CAL / "oof-summary.json").read_text(
            encoding="utf-8"))
        full = json.loads((CAL / "full-results.json").read_text(
            encoding="utf-8"))
        concl = _conclusion(oof, full["gp_selection"], full["kb_selection"])
        gp_sel_row = None
        if full["gp_selection"]["selected"]:
            sel = full["gp_selection"]["selected"]
            gp_sel_row = next(
                r for r in full["gp_grid"]
                if r["window"] == sel["window"] and r["mult"] == sel["mult"]
                and r["thr"] == sel["thr"] and r["floor"] == sel["floor"])
        rep = {
            "plan_sha256": PLAN_SHA256,
            "training_status": "consumed holdout-v4, TRAINING_DIAGNOSTIC_ONLY",
            "decay_gate": full["decay_gate"],
            "gp_selection": full["gp_selection"],
            "gp_selected_metrics": gp_sel_row,
            "kb_selection": full["kb_selection"],
            "oof": oof,
            "fold_selected_gp": [r["gp_selected"] for r in json.loads(
                (CAL / "fold-results.json").read_text(encoding="utf-8"))],
            "conclusion_class": concl,
            "note": "training diagnostic only; production v2 requires "
                    "separate architect approval; promotion requires a NEW "
                    "unseen holdout after implementation",
        }
        (CAL / "final-report.json").write_text(json.dumps(rep, indent=1),
                                               encoding="utf-8")
        print("[conclusion]", concl)


if __name__ == "__main__":
    main()
