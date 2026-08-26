"""Interest-recovery evaluator (evaluator-v1, preregistered).

Scores inference arms against the PRIVATE known-interest holdout
(P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json).
Never writes target identities to repository-tracked output; detailed
per-target reports go only to the private eval directory.

Subcommands:
  support      deterministic per-target evidence-cluster support counts
  score        alias + blind-LLM-judge matching, metrics for one arm
  perturb      run bootstrap with a preregistered cluster-removal scheme
  verdict      combine arm reports into the preregistered verdict

Policy source: preregistration JSON whose sha256 is frozen in the
private eval directory before any target inspection (see
docs/handoffs/interest-intelligence/project-state-inference.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOLDOUT = Path("P:/.data/yt-is/private/discovery-retrospective-holdout-v4.json")
CASE_CONTROL = Path(
    "P:/.data/yt-is/private/"
    "discovery-retrospective-case-control-v4-diagnostic.json")
EVAL_ROOT = Path("P:/.data/yt-is/ef/interest-inference-eval")
JUDGE_MODEL = "gpt-5.6-luna"
JUDGE_TIMEOUT = 300

JUDGE_PROMPT = """You are a strict relevance judge. Given one inferred
interest extracted from a personal media-corpus inference system, and one
reference target (a known personal interest topic with aliases), decide
whether the inferred interest is substantially ABOUT the reference target
(the same underlying interest/topic, not merely adjacent or co-occurring).

Return ONLY JSON: {{"match": true|false, "confidence": 0.0-1.0}}

INFERED INTEREST:
name: {iname}
summary: {isummary}
evidence clusters: {iclusters}

REFERENCE TARGET:
name: {tname}
aliases: {taliases}
"""


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- support

def cmd_support(args) -> int:
    from ef.evidence_clusters import evidence_cluster_inventory, \
        hydrate_evidence_clusters
    inv = evidence_cluster_inventory()
    clusters = inv["clusters"]
    hydrated = hydrate_evidence_clusters([c["cluster_id"] for c in clusters])
    # searchable text per cluster: label + terms + representative doc text
    texts = {}
    for c in clusters:
        parts = [c["label"] or ""] + list(c.get("terms") or [])
        texts[c["cluster_id"]] = " ".join(parts).lower()
    for h in hydrated:
        # hydrated packets carry representative document text
        s = json.dumps(h, ensure_ascii=False).lower() if not isinstance(
            h, str) else h.lower()
        texts.setdefault(h.get("cluster_id", -1) if isinstance(h, dict)
                         else -1, "")
        if isinstance(h, dict):
            texts[h.get("cluster_id", -1)] = texts.get(
                h.get("cluster_id", -1), "") + " " + s

    targets = _load(HOLDOUT)["targets"]
    out = {"generated": time.strftime("%Y-%m-%dT%H%M%S"),
           "eligible_cluster_ids": [c["cluster_id"] for c in clusters],
           "targets": []}
    for t in targets:
        needles = [t["canonical_name"].lower()] + [
            a.lower() for a in t.get("aliases", []) if len(a) >= 4]
        hits = []
        for cid, text in texts.items():
            if any(n in text for n in needles):
                hits.append(cid)
        out["targets"].append({"target_id": t["target_id"],
                               "canonical_name": t["canonical_name"],
                               "supporting_clusters": sorted(hits)})
    supported = [t for t in out["targets"] if t["supporting_clusters"]]
    out["summary"] = {"eligible_clusters": inv["eligible_count"],
                      "supported_targets": len(supported),
                      "total_targets": len(out["targets"])}
    _write(Path(args.out), out)
    print(json.dumps(out["summary"]))
    return 0


# ----------------------------------------------------------------- judge

def _tokens(names: list[str]) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "about", "using"}
    toks = set()
    for n in names:
        for w in n.lower().replace("/", " ").split():
            if len(w) >= 5 and w not in stop and not w.isdigit():
                toks.add(w)
    return toks


def _alias_match(interest_names: list[str], target) -> bool:
    needles = [target["canonical_name"].lower()] + [
        a.lower() for a in target.get("aliases", []) if len(a) >= 4]
    hay = " | ".join(interest_names).lower()
    for n in needles:
        if len(n) >= 4 and (n in hay):
            return True
    return False


def _summary_of(it: dict) -> str:
    return (it.get("evidence_summary") or it.get("summary")
            or it.get("description") or "")


def judge_pair(iname, isummary, iclusters, target, cache: dict) -> bool:
    key = hashlib.sha256(json.dumps(
        [iname, isummary, target["target_id"]], sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    if key in cache:
        return cache[key]
    import shutil
    prompt = JUDGE_PROMPT.format(
        iname=iname, isummary=(isummary or "")[:400],
        iclusters=str(iclusters)[:300], tname=target["canonical_name"],
        taliases=", ".join(target.get("aliases", [])))
    pf = EVAL_ROOT / "judge-prompt.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(prompt, encoding="utf-8")
    cmd = [shutil.which("codex"), "exec", "--json", "--ephemeral",
           "-s", "read-only", "-m", JUDGE_MODEL,
           "-c", "model_reasoning_effort=low", "-C", "P:/",
           f"Read {pf} and return ONLY the JSON. No prose, no markdown "
           "fences."]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=JUDGE_TIMEOUT, cwd="P:/",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ok = None
    if r.returncode == 0:
        raw = r.stdout.strip()
        # take the last plausible JSON object
        import re
        objs = re.findall(r'\{[^{}]*\}', raw)
        for o in reversed(objs):
            try:
                ok = bool(json.loads(o).get("match"))
                break
            except Exception:
                continue
    if ok is None:
        # judge execution failure: do NOT poison the replay cache
        return False
    cache[key] = ok
    return ok


# ----------------------------------------------------------------- score

def cmd_score(args) -> int:
    support = _load(args.support)
    sup_by_id = {t["target_id"]: t for t in support["targets"]}
    targets = _load(HOLDOUT)["targets"]
    negatives = []
    if Path(args.negatives).exists():
        negatives = _load(args.negatives).get("negative_targets", [])

    result = _load(args.result)
    if "final" in result and isinstance(result["final"], dict):
        result = result["final"]
    interests = result.get("inferred_interests", [])
    goals = result.get("goals", [])
    questions = result.get("questions", [])
    regret = result.get("regret_candidates", [])
    dispositions = result.get("fragment_dispositions", [])

    # provenance: valid cluster refs against the eligible universe
    eligible = set(support.get("eligible_cluster_ids") or [])
    valid_refs = invalid_refs = missing = 0
    for it in interests:
        refs = it.get("evidence_cluster_ids") or it.get("cluster_ids") or []
        if not refs:
            missing += 1
        elif eligible and not set(refs) <= eligible:
            invalid_refs += 1
        else:
            valid_refs += 1
    provenance_valid_frac = (valid_refs / len(interests)) if interests else 0.0

    cache_path = Path(args.cache or (EVAL_ROOT / "judge-cache.json"))
    cache = _load(cache_path) if cache_path.exists() else {}

    # stage 1+2 matching per target
    matched = []
    per_target = []
    for t in targets:
        sup = sup_by_id.get(t["target_id"], {}).get(
            "supporting_clusters", [])
        # candidate interests: alias hit or token overlap
        toks = _tokens([t["canonical_name"]] + t.get("aliases", []))
        cands = []
        for it in interests:
            nm = (it.get("name") or "")
            sm = _summary_of(it)
            hay = (nm + " " + sm).lower()
            if _alias_match([nm, sm], t) or any(w in hay for w in toks):
                cands.append(it)
        is_match = False
        matched_names = []
        for it in cands:
            nm = it.get("name") or ""
            if _alias_match([nm], t) or judge_pair(
                    nm, _summary_of(it), it.get(
                        "evidence_cluster_ids") or [], t, cache):
                is_match = True
                matched_names.append(nm)
        per_target.append({"target_id": t["target_id"], "matched":
                           is_match, "support_count": len(sup),
                           "matched_interests": matched_names})
        if is_match:
            matched.append(t["target_id"])
    _write(cache_path, cache)

    supported = [p for p in per_target if p["support_count"] > 0]
    sup_matched = [p for p in supported if p["matched"]]
    ranks = sorted(supported, key=lambda p: p["support_count"])
    half = max(1, len(ranks) // 2)
    narrow = ranks[:half]
    narrow_matched = [p for p in narrow if p["matched"]]

    # negatives: explicit unsupported-interest rate
    neg_hits = []
    for it in interests:
        nm = (it.get("name") or "").lower()
        sm = _summary_of(it).lower()
        for n in negatives:
            if _alias_match([nm, sm], n):
                neg_hits.append(it.get("name"))
                break

    # duplication among questions/information needs: exact-normalized dups
    def _norm(s):
        return " ".join((s or "").lower().split())
    qtexts = [_norm(q.get("text") or q.get("question") or q.get("name"))
              for q in questions]
    dup_q = len(qtexts) - len(set(qtexts))

    report = {
        "arm": args.arm,
        "result_path": str(args.result),
        "n_interests": len(interests), "n_goals": len(goals),
        "n_questions": len(questions), "n_regret": len(regret),
        "n_dispositions": len(dispositions),
        "recall_all": len(matched) / len(targets) if targets else 0.0,
        "recall_supported": (len(sup_matched) / len(supported)
                             if supported else 0.0),
        "narrow_supported": len(narrow),
        "narrow_recall": (len(narrow_matched) / len(narrow)
                          if narrow else 0.0),
        "matched_target_ids": matched,
        "provenance_valid_frac": provenance_valid_frac,
        "provenance_missing": missing, "provenance_invalid": invalid_refs,
        "explicit_negative_hits": len(neg_hits),
        "explicit_negative_rate": (len(neg_hits) / len(interests)
                                   if interests else 0.0),
        "question_duplicates": dup_q,
        "per_target": per_target,
        "generated": time.strftime("%Y-%m-%dT%H%M%S"),
    }
    out = Path(args.out)
    _write(out, report)
    print(json.dumps({k: v for k, v in report.items()
                      if k != "per_target"}, indent=2))
    return 0


# --------------------------------------------------------------- perturb

def cmd_perturb(args) -> int:
    from ef.evidence_clusters import evidence_cluster_inventory
    from scripts.build_interest_graph import run_bootstrap
    inv = evidence_cluster_inventory()
    clusters = inv["clusters"]
    ids = [c["cluster_id"] for c in clusters]
    scheme = args.scheme
    removed = []
    if scheme == "p1":
        rng = random.Random(1337)
        k = max(8, round(0.05 * len(ids)))
        removed = rng.sample(ids, k)
    elif scheme == "p3":
        removed = [c["cluster_id"] for c in sorted(
            clusters, key=lambda c: -c["channels"])[:10]]
    elif scheme == "p4":
        removed = [c["cluster_id"] for c in sorted(
            clusters, key=lambda c: (c["channels"], -c["member_count"])
        )[:10]]
    elif scheme == "p2":
        rm = _load(args.removed_json)
        removed = rm["remove_cluster_ids"]
    else:
        raise SystemExit(f"unknown scheme {scheme}")
    rset = set(removed)
    inv2 = dict(inv)
    inv2["clusters"] = [c for c in clusters
                        if c["cluster_id"] not in rset]
    _write(Path(args.artifact_root) / "perturbation.json",
           {"scheme": scheme, "removed": removed})
    res = run_bootstrap(provider=args.provider, allow_spend=True,
                        artifact_root=args.artifact_root,
                        inventory=inv2)
    print(json.dumps(res["summary"]))
    return 0


# --------------------------------------------------------------- verdict

def cmd_verdict(args) -> int:
    a = _load(args.arm_a)
    b = _load(args.arm_b)
    reg = _load(args.registration)
    th = reg["verdict_thresholds"]

    recall_better = (b["recall_all"] >= a["recall_all"]
                     + th["min_recall_gain"])
    narrow_ok = (b["narrow_recall"] >= a["narrow_recall"]
                 and b["narrow_recall"] >= th["min_narrow_recall"])
    prov_ok = b["provenance_valid_frac"] >= th["min_provenance_valid"]
    neg_ok = b["explicit_negative_rate"] <= th["max_explicit_negative_rate"]
    explosion_ok = b["n_interests"] <= th["max_interests"]
    pert = {}
    for p in (args.perturbations or []):
        d = _load(p)
        name = Path(p).stem
        pert[name] = {
            "status": d.get("status"),
            "final_interests": d.get("final_interests"),
        }
    pert_ok = all(v["status"] == "success" for v in pert.values())

    checks = {"recall_gain": recall_better, "narrow": narrow_ok,
              "provenance": prov_ok, "negatives": neg_ok,
              "explosion": explosion_ok, "perturbation_runs": pert_ok}
    if not recall_better:
        verdict = "FAIL"
    elif all(checks.values()):
        verdict = "PASS"
    else:
        verdict = "PARTIAL"
    out = {"verdict": verdict, "checks": checks,
           "arm_a": {k: a[k] for k in
                     ("recall_all", "recall_supported", "narrow_recall",
                      "n_interests", "provenance_valid_frac",
                      "explicit_negative_rate")},
           "arm_b": {k: b[k] for k in
                     ("recall_all", "recall_supported", "narrow_recall",
                      "n_interests", "provenance_valid_frac",
                      "explicit_negative_rate")},
           "perturbations": pert,
           "generated": time.strftime("%Y-%m-%dT%H%M%S")}
    _write(Path(args.out), out)
    print(json.dumps({k: v for k, v in out.items()
                      if k != "per_target"}, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("support")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_support)

    s = sub.add_parser("score")
    s.add_argument("--arm", required=True)
    s.add_argument("--result", required=True)
    s.add_argument("--support", required=True)
    s.add_argument("--negatives", default=str(CASE_CONTROL))
    s.add_argument("--out", required=True)
    s.add_argument("--cache", default=None)
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("perturb")
    s.add_argument("--scheme", required=True,
                   choices=["p1", "p2", "p3", "p4"])
    s.add_argument("--artifact-root", required=True)
    s.add_argument("--removed-json", default=None)
    s.add_argument("--provider", default="codex")
    s.set_defaults(fn=cmd_perturb)

    s = sub.add_parser("verdict")
    s.add_argument("--arm-a", required=True)
    s.add_argument("--arm-b", required=True)
    s.add_argument("--registration", required=True)
    s.add_argument("--perturbations", nargs="*")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_verdict)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
