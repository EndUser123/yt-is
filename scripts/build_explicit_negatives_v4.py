"""Explicit-negative curation for the v4 training diagnostic
(preregistered policy explicit-negative-v1, sha256 1e454421...).

Constructs paired explicit negatives per consumed-holdout-v4 positive
using ONLY pre-T-30 evidence for matching; negatives must fail the
Tier-C persistence conjunction over [T, T+120] and satisfy the
hard-negative activity requirement. Writes the PRIVATE case-control
diagnostic artifact. Never touches the formal ledger; never FORMAL.
"""
import hashlib
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "scripts"))

import evaluate_concept_discovery as ev  # noqa: E402
from ef import burst_policy_v2 as bp  # noqa: E402
from ef import concept_discovery as cd  # noqa: E402
from ef import concept_registry as cr  # noqa: E402

POLICY_PATH = Path("P:/.data/yt-is/ef/concept-discovery-eval/"
                   "explicit-negative-v1/preregistered-policy.json")
POLICY_SHA256 = ("1e454421c635f950acfea27e22602f945288a1ad"
                 "feca404ebfc302f0b7fdfc9a")
HOLDOUT = "P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json"
HOLDOUT_V2 = "P:/.data/yt-is/private/discovery-retrospective-holdout-v2.json"
HOLDOUT_V3 = "P:/.data/yt-is/private/discovery-retrospective-holdout-v3.json"
OUT = Path("P:/.data/yt-is/private/"
           "discovery-retrospective-case-control-v4-diagnostic.json")
ART = Path("P:/.data/yt-is/ef/concept-discovery-eval/"
           "eval-20260825T114338-FORMAL")
CATALOG = "P:/.data/yt-is/ef/catalog.sqlite"
TODAY = date.today()


def load_names(path):
    """Local loader (the evaluator's load_targets was replaced by
    load_case_control in evaluator-v4; curation must not depend on it)."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    targets = payload.get("targets") or payload.get("positive_targets")         or []
    return [(t.get("canonical_name"), t.get("aliases", []))
            for t in targets if t.get("canonical_name")]


def load_positives(path):
    """Load the positive side from a legacy targets-only holdout file
    (holdout-v4 format). Case-control files are not curation input."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for t in payload.get("targets", []):
        for field in ("target_id", "canonical_name"):
            if not t.get(field):
                raise SystemExit(f"positive target missing {field}")
        t.setdefault("aliases", [])
        out.append(t)
    return out


def matches_any(label, names):
    ln = ev._norm(label)
    if not ln:
        return False
    for cn, aliases in names:
        for n in [cn] + list(aliases):
            tn = ev._norm(n)
            if tn and (ln == tn or ev._word_boundary_contains(ln, tn)
                       or ev._word_boundary_contains(tn, ln)):
                return True
    return False


def main():
    digest = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    if digest != POLICY_SHA256:
        sys.exit(f"policy hash mismatch: {digest}; refusing")
    positives = load_positives(HOLDOUT)
    scor = {s["target_id"]: s["T"] for s in
            json.loads((ART / "target-scorability.json").read_text())}
    excluded_names = (load_names(HOLDOUT) + load_names(HOLDOUT_V2)
                      + load_names(HOLDOUT_V3))

    cat = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    cat.row_factory = sqlite3.Row
    eo = cd._entity_observations(cat, TODAY.isoformat())
    # per-entity: cid, label, sorted (date, eu, channel) list
    entities = []
    for nid, obs in eo.items():
        label = obs[0]["label"]
        first = bp.distinct_eu_first_dates(obs)
        ch_by_eu = {}
        for o in obs:
            if o["eu_id"] not in ch_by_eu or o["obs_date"] < \
                    ch_by_eu[o["eu_id"]][0]:
                ch_by_eu[o["eu_id"]] = (o["obs_date"], o["channel_id"])
        events = sorted((d, eu, ch_by_eu[eu][1])
                        for eu, d in first.items())
        entities.append({"cid": cr.concept_identity_id("entity", label),
                         "label": label,
                         "first": events[0][0] if events else None,
                         "events": events})
    cat.close()

    def pre_features(ent, d0):
        """(eu_count, channel_count, age_days) over evidence <= d0."""
        evs = [e for e in ent["events"] if e[0] <= d0.isoformat()]
        return (len(evs), len({e[2] for e in evs}),
                (d0 - date.fromisoformat(ent["first"])).days
                if ent["first"] else None)

    def tierc_positive(ent, t_date):
        end = (t_date + timedelta(days=120)).isoformat()
        late = (t_date + timedelta(days=30)).isoformat()
        win = [e for e in ent["events"]
               if t_date.isoformat() < e[0] <= end]
        eus = {e[1] for e in win}
        chs = {e[2] for e in win}
        after30 = [e for e in win if e[0] > late]
        return len(eus) >= 3 and len(chs) >= 2 and len(after30) >= 1

    def hard_negative(ent, t_date):
        lo = (t_date - timedelta(days=30)).isoformat()
        hi = (t_date + timedelta(days=30)).isoformat()
        return any(lo <= e[0] <= hi for e in ent["events"])

    negatives = []
    insufficient = []
    used_cids = set()
    for t in positives:
        tid = t["target_id"]
        t_date = date.fromisoformat(scor[tid])
        tm30 = t_date - timedelta(days=30)
        # paired positive pre-T-30 features: best-evidence name-matched
        pnames = [(t["canonical_name"], t.get("aliases", []))]
        anchor = None
        for ent in entities:
            if matches_any(ent["label"], pnames):
                f = pre_features(ent, tm30)
                if anchor is None or f[0] > anchor[0]:
                    anchor = f
        if anchor is None:
            anchor = (0, 0, 0)
        # eligible negatives
        cands = []
        for ent in entities:
            if ent["cid"] in used_cids:
                continue
            if matches_any(ent["label"], excluded_names):
                continue
            if t_date + timedelta(days=120) > TODAY:
                continue  # horizon unavailable
            f = pre_features(ent, tm30)
            if f[0] < 1:
                continue  # not present at T-30
            if tierc_positive(ent, t_date):
                continue  # positive-like future evidence
            if not hard_negative(ent, t_date):
                continue
            dist = (abs(f[0] - anchor[0]), abs(f[1] - anchor[1]),
                    abs((f[2] or 0) - (anchor[2] or 0)))
            tie = hashlib.sha256(
                (ent["cid"] + tid).encode()).hexdigest()
            cands.append((dist, tie, ent))
        cands.sort(key=lambda c: (c[0], c[1]))
        picked, seen_ids = [], set()
        for c in cands:
            if c[2]["cid"] in seen_ids:
                continue  # case-variant labels share concept identity
            seen_ids.add(c[2]["cid"])
            picked.append(c)
            if len(picked) == 3:
                break
        if len(picked) < 2:
            insufficient.append(tid)
        for dist, tie, ent in picked:
            used_cids.add(ent["cid"])
            negatives.append({
                "negative_id": f"neg4_{ent['cid'][-12:]}",
                "canonical_name": ent["label"],
                "aliases": [],
                "domain": "unlabeled",
                "paired_positive_id": tid,
                "anchor_T": scor[tid],
            })
    payload = {
        "status": "TRAINING_DIAGNOSTIC_ONLY — NEVER FORMAL — NEVER "
                  "PROMOTION EVIDENCE",
        "policy": "explicit-negative-v1",
        "policy_sha256": POLICY_SHA256,
        "positive_targets": [
            {"target_id": t["target_id"],
             "canonical_name": t["canonical_name"],
             "aliases": t.get("aliases", []), "domain": t.get("domain")}
            for t in positives],
        "negative_targets": negatives,
        "negative_control_insufficient_targets": insufficient,
    }
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    from collections import Counter
    print(json.dumps({
        "positives": len(positives),
        "negatives_selected": len(negatives),
        "neg_per_positive": dict(Counter(
            n["paired_positive_id"] for n in negatives)),
        "insufficient_targets": insufficient,
        "out": str(OUT)}))


if __name__ == "__main__":
    main()
