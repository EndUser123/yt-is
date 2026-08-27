"""E3 step 8 — downstream representation shadow impact (no outcomes).

Sets compared: A0 (stored), A1 (mechanical recompute), B, C — all 319
non-series clusters — plus ARM R, the existing scripts/relabel_topics.py
mechanism applied verbatim to the frozen membership but OUTSIDE the blind
vote / decision enum (prereg v3 reuse disclosure).

D2 reads trend_alerts ONCE (live catalog, read-only) purely as a baseline
identity reference; no production write anywhere.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import numpy as np

import e3lib as L
import evidence as EV

MONTHS = {"january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december",
          "monday", "tuesday", "wednesday", "thursday", "friday",
          "saturday", "sunday", "today", "yesterday", "tomorrow", "tonight",
          "morning", "evening", "close", "closing", "opening", "live",
          "full", "episode", "part", "video", "watch", "new", "top"}
DATE_RE = re.compile(r"^(\d{1,4}([/.-]\d{1,4}){1,2}|20\d\d|x{2,})$")


def junk(term: str) -> bool:
    """Verbatim from scripts/relabel_topics.py."""
    t = term.strip().lower()
    return (not t or len(t) <= 1 or t in MONTHS or DATE_RE.match(t)
            or t.isdigit())


def arm_r_labels(clusters: dict[int, L.FrozenCluster],
                 terms_override: dict[int, list[str]] | None = None,
                 stored_fallback: bool = True) -> dict[int, str]:
    """scripts/relabel_topics.py labeling mechanism, series handling
    excluded (population is is_series=0): junk-filter top_terms, rank by
    cross-cluster document frequency then original order, top-3 Title-case;
    falls back to the ORIGINAL stored label if no terms survive."""
    src = {cid: (terms_override[cid] if terms_override else c.top_terms)
           for cid, c in clusters.items()}
    parsed = {}
    df: Counter = Counter()
    for cid, terms in src.items():
        ts = [t.lower().strip() for t in terms]
        parsed[cid] = ts
        for t in set(ts):
            df[t] += 1
    out = {}
    for cid, terms in src.items():
        kept = [t for t in parsed[cid] if not junk(t)]
        kept.sort(key=lambda t: (df.get(t, 1),
                                 parsed[cid].index(t) if t in parsed[cid] else 99))
        new = " ".join(t.title() for t in kept[:3])
        out[cid] = new or (clusters[cid].label if stored_fallback else "")
    return out


def load_sets() -> tuple[dict[int, L.FrozenCluster], dict[str, dict[int, str]]]:
    clusters = L.load_freeze()
    rows = {}
    for line in (L.EF_DATA / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] == "t0":
                rows[r["cluster_id"]] = r
    missing = [i for i in clusters if i not in rows]
    if missing:
        print(f"missing t0 labels for {len(missing)} clusters")
        sys.exit(1)
    sets = {
        "A0": {cid: c.label for cid, c in clusters.items()},
        "A1": {cid: rows[cid]["A"]["label"] for cid in clusters},
        "B": {cid: rows[cid]["B"]["label"] for cid in clusters},
        "C": {cid: rows[cid]["C"]["label"] for cid in clusters},
        "R": arm_r_labels(clusters),
    }
    return clusters, sets


def d1_dup_pairs(labels: dict[int, str]) -> int:
    cnt = Counter(v.strip().casefold() for v in labels.values() if v.strip())
    return sum(n * (n - 1) // 2 for n in cnt.values() if n > 1)


def d2_trend_collision(labels: dict[int, str], topics_cf: set[str]):
    hits = [(cid, lab) for cid, lab in labels.items()
            if lab.strip().casefold() in topics_cf]
    return len(hits), len({lab.casefold() for _, lab in hits})


def embed_texts(texts: list[str]) -> np.ndarray:
    uniq = sorted(set(texts))
    dense = EV.get_embed_server().encode_dense(uniq)
    pos = {t: i for i, t in enumerate(uniq)}
    return np.stack([dense[pos[t]] if t in pos else np.zeros(dense.shape[1])
                     for t in texts])


def d3_near_dupes(labels: dict[int, str], mat: np.ndarray) -> int:
    ids = list(labels.keys())
    sims = mat @ mat.T
    cnt = 0
    for i, j in combinations(range(len(ids)), 2):
        a, b = labels[ids[i]], labels[ids[j]]
        if (not a.strip()) or (not b.strip()):
            continue
        ca, cb = a.strip().casefold(), b.strip().casefold()
        if ca == cb:
            continue          # already counted by D1
        if sims[i, j] >= 0.95:
            cnt += 1
    return cnt


def d4_searchability(clusters, sets, sample_ids):
    """Preregistered proxy: holdout docs = pool ranks beyond the 24 display
    picks (up to 8) must retrieve their OWN cluster's label from the set's
    label embedding space. hit@1 / hit@3 shares."""
    store = EV.VectorStore()
    label_mats = {}
    for k, v in sets.items():
        ids_sorted = sorted(v)
        m = embed_texts([v[i] for i in ids_sorted])
        label_mats[k] = (ids_sorted, m)
    hits = {k: {"n": 0, "h1": 0, "h3": 0} for k in sets}
    for cid in sample_ids:
        ev = EV.pool_evidence(clusters[cid], set(), store)
        vids = ev["pool_vids"]
        holdout = [v for v in vids[EV.DISPLAY_N: EV.DISPLAY_N + 8]]
        if not holdout:
            continue
        pos = {v: i for i, v in enumerate(vids)}
        try:
            docvecs = ev["pool_vecs"][[pos[v] for v in holdout]]
        except KeyError:
            continue
        for k, (ids_sorted, m) in label_mats.items():
            sims = docvecs @ m.T
            own = ids_sorted.index(cid)
            for srow in sims:
                order = np.argsort(-srow)
                hits[k]["n"] += 1
                hits[k]["h1"] += int(order[0] == own)
                hits[k]["h3"] += int(own in order[:3])
    return {k: {"holdout_docs": v["n"],
                "hit_at_1": round(v["h1"] / max(v["n"], 1), 4),
                "hit_at_3": round(v["h3"] / max(v["n"], 1), 4)}
            for k, v in hits.items()}


def d5_packets(clusters, sets):
    """Evidence-clusters convention: non-series, member_count_chunks>=40,
    top 40 by distinct-channel breadth — replicated mechanically from the
    freeze (no production write, no live DB)."""
    def breadth(c):
        chans = set()
        for v in c.videos:
            idn = f"guild:{v.channel_title}" if v.source == "discord" else v.channel_id
            if not idn:
                idn = "unknown"
            chans.add(idn)
        return len(chans)

    eligible = [c for c in clusters.values() if c.member_count_chunks >= 40]
    eligible.sort(key=lambda c: (-breadth(c), c.cluster_id))
    eligible = eligible[:40]
    res = {}
    for name, labs in sets.items():
        changed = [cid for cid in (c.cluster_id for c in eligible)
                   if labs[cid].strip().casefold() != sets["A0"][cid].strip().casefold()]
        toks = []
        for cid in changed:
            toks.append(len(set(L._label_tokens(labs[cid]))
                            ^ set(L._label_tokens(sets["A0"][cid]))))
        res[name] = {
            "eligible_packets": len(eligible),
            "changed_fraction": round(len(changed) / max(len(eligible), 1), 4),
            "mean_token_delta_when_changed": round(float(np.mean(toks)), 2) if toks else 0.0,
        }
    return res


def main() -> int:
    clusters, sets = load_sets()

    # trend topics baseline (single read-only query)
    conn = sqlite3.connect("file:P:/.data/yt-is/ef/catalog.sqlite?mode=ro",
                           uri=True, timeout=30)
    topics = [t for (t,) in conn.execute("SELECT DISTINCT topic FROM trend_alerts")]
    conn.close()
    topics_cf = {t.strip().casefold() for t in topics}

    out = {"set_sizes": {k: len(v) for k, v in sets.items()}}

    # D0: regeneration-only baseline drift
    match = sum(1 for cid in clusters if sets["A1"][cid] == sets["A0"][cid])
    out["D0_A1_equals_stored_rate"] = round(match / len(clusters), 4)

    # D1 + D2
    out["D1_casefold_dup_pairs"] = {k: d1_dup_pairs(v) for k, v in sets.items()}
    out["D2_trend_topic_collisions"] = {}
    for k, v in sets.items():
        n_rows, n_uniq = d2_trend_collision(v, topics_cf)
        out["D2_trend_topic_collisions"][k] = {
            "cluster_rows": n_rows, "distinct_labels": n_uniq}
    out["D2_trend_topics_baseline_count"] = len(topics_cf)

    # D3 embeddings per set
    mats = {}
    for k, v in sets.items():
        labs = [v[i] for i in sorted(v)]
        mats[k] = embed_texts(labs)
    out["D3_near_dup_pairs_cos95"] = {
        k: d3_near_dupes(sets[k], mats[k]) for k in sets}

    # D4 searchability proxy on the eval sample
    import json as _json
    sample = _json.loads((L.EF_DATA / "SAMPLE.json").read_text(encoding="utf-8"))
    sample_ids = [c for b in ("large", "medium", "small")
                  for c in sample["selection"][b]]
    # Arm R participates in mechanical metrics only
    out["D4_searchability_holdout"] = d4_searchability(clusters, sets, sample_ids)

    # D5 interest-inference packet replication
    out["D5_interest_packet_text_changes"] = d5_packets(clusters, sets)

    # Arm R secondary disclosures (outside decision enum)
    r_terms_pert = {}
    drops = {i: EV.perturbation_drop(c) for i, c in clusters.items()}
    for cid, c in clusters.items():
        r_terms_pert[cid] = EV.arm_a_terms(EV.chunk_weighted_titles(c, drops[cid]))
    sets["Rpert"] = arm_r_labels(clusters, terms_override=r_terms_pert)
    mats["Rpert"] = embed_texts([sets["Rpert"][i] for i in sorted(sets["Rpert"])])
    rl = arm_r_labels(clusters)     # stable dict for iteration order
    ids_sorted = sorted(rl)
    m0 = mats["A0"]                 # aligned to sorted(sets['A0']) == ids_sorted
    mr = mats["R"]
    cos = [float(a @ b) for a, b in zip(m0, mr)]
    out["R_vs_stored_mean_cosine"] = round(float(np.mean(cos)), 4)
    r_match = sum(1 for i in ids_sorted if sets["R"][i] == sets["A0"][i])
    out["R_label_differs_from_stored_count"] = len(ids_sorted) - r_match
    rpert_stab = []
    mp = mats["Rpert"]
    for n, i in enumerate(ids_sorted):
        a, b = mr[n], mp[n]
        rpert_stab.append(float(a @ b))
    out["R_perturbation_mean_cosine"] = round(float(np.mean(rpert_stab)), 4)

    (L.EF_DATA / "SHADOW-IMPACT.json").write_text(json.dumps(out, indent=2),
                                                  encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
