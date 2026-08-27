"""E3 step 3 — produce candidate labels for ALL non-series clusters.

Phases (both over the frozen membership):
  t0   : primary label sets
  pert : preregistered stability rerun (20% member-doc drop)

Arm C nondeterminism: k=3 extra identical temperature-zero calls on the
45-cluster evaluation sample (t0 inputs).

Checkpointed: results appended per cluster to labels.jsonl in the private
data dir; a rerun resumes past finished rows and finishes with a summary.
Artifacts never touch production.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

import arm_c
import e3lib as L
import evidence as EV


def _peak_rss_kb() -> int | None:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        pass
    try:
        import psutil
        return psutil.Process().memory_info().peak_wset // 1024
    except Exception:
        return None

LABELS_PATH = L.EF_DATA / "labels.jsonl"
SAMPLE = json.loads((L.EF_DATA / "SAMPLE.json").read_text(encoding="utf-8"))
SAMPLE_IDS = [cid for b in ("large", "medium", "small")
              for cid in SAMPLE["selection"][b]]

_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def label_cluster(c: L.FrozenCluster, corpora: dict[int, list[str]],
                  cand_map: dict[int, list[str]], store: EV.VectorStore,
                  drop: set[str]) -> dict:
    out = {}
    weighted = corpora[c.cluster_id]
    terms_a = EV.arm_a_terms(weighted)
    out["A"] = {"label": EV.arm_a_label(terms_a), "top_terms": terms_a}

    ev = EV.pool_evidence(c, drop, store)
    if ev["cent"] is None or not ev["rep_order"]:
        out["B"] = {"label": "", "error": "no_vectors"}
        rep_matrix = None
    else:
        pos = {v: i for i, v in enumerate(ev["pool_vids"])}
        idxs = [pos[v] for v in ev["rep_order"] if v in pos]
        rep_matrix = ev["pool_vecs"][idxs]
        cands = cand_map.get(c.cluster_id) or []
        try:
            blabel, scored = EV.arm_b_label(cands, rep_matrix)
            out["B"] = {"label": blabel,
                        "scored_top20": [t for t in scored[:20]],
                        "candidates": cands}
        except Exception as e:
            out["B"] = {"label": "", "error": f"{type(e).__name__}: {e}"}

    # Arm C consumes the same display titles reviewers will see
    disp = [d["title"] for d in ev["display"]]
    kws = [t for t in (cand_map.get(c.cluster_id) or [])[:20]]
    prompt = arm_c.build_prompt(disp, kws)
    st, payload = arm_c.generate(prompt)
    if st == 0:
        out["C"] = {"label": payload}
    else:
        out["C"] = {"label": "", "error": str(payload)[:200]}
    out["_evidence"] = {
        "display_titles": [d["title"] for d in ev["display"]],
        "keywords_c": kws,
        "arm_a_terms_t": terms_a[:10],
        "n_pool_docs": len(ev.get("pool_vids") or []),
    }
    out["_prompt_len"] = len(prompt)
    return out


def load_done(repair_ok: bool = False,
              repair_arms: set[str] | None = None) -> dict[tuple[str, int], dict]:
    """repair_ok=True rewrites labels.jsonl keeping only rows that are not
    scheduled for redo. With repair_arms (e.g. {"B"}), only rows whose
    broken arms fall inside that set get dropped — rows failing other arms
    (e.g. Arm C under upstream throttling) are left untouched."""
    done = {}
    if LABELS_PATH.exists():
        lines = LABELS_PATH.read_text(encoding="utf-8").splitlines()
        good_lines = []
        for line in lines:
            if not line.strip():
                continue
            rec = json.loads(line)
            if repair_ok:
                broken = {a for a in ("A", "B", "C")
                          if not rec[a].get("label") or rec[a].get("error")}
                redo = (broken and
                        (repair_arms is None or broken <= repair_arms))
                if redo:
                    continue          # dropped: row gets relabeled this run
            done[(rec["phase"], rec["cluster_id"])] = rec
            good_lines.append(line)
        if repair_ok and len(good_lines) != len(lines):
            tmp = LABELS_PATH.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(good_lines) + ("\n" if good_lines else ""),
                           encoding="utf-8")
            tmp.replace(LABELS_PATH)
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--repair", action="store_true",
                    help="drop-and-redo rows with arm errors")
    ap.add_argument("--repair-arms", type=str, default=None,
                    help="comma list: redo only rows whose broken arms "
                         "are inside this set, e.g. 'B' or 'B,C'")
    ap.add_argument("--skip-repeats", action="store_true",
                    help="skip the Arm C nondeterminism phase")
    ap.add_argument("--only-sample", action="store_true",
                    help="restrict to the 45 eval clusters (smoke)")
    args = ap.parse_args()

    rarms = set(args.repair_arms.split(",")) if args.repair_arms else None

    clusters = L.load_freeze()
    todo_ids = SAMPLE_IDS if args.only_sample else sorted(clusters)
    done = load_done(repair_ok=args.repair, repair_arms=rarms)
    # build phase texts/candidates ONCE (corpora depend on phase drop-set)
    drops = {i: EV.perturbation_drop(clusters[i]) for i in todo_ids}

    def corpora_for(phase: str) -> dict[int, list[str]]:
        return {i: EV.chunk_weighted_titles(
            clusters[i], drops[i] if phase == "pert" else None)
            for i in todo_ids}

    phases_needed = []
    for phase in ("t0", "pert"):
        if any((phase, i) not in done for i in todo_ids):
            phases_needed.append(phase)

    t_start = time.time()
    fh = open(LABELS_PATH, "a", encoding="utf-8", buffering=1)

    def run_phase(phase: str):
        corpora = corpora_for(phase)
        cand_map = EV.ctfidf_candidates(corpora)
        store = EV.VectorStore()

        need = [(phase, i) for i in todo_ids if (phase, i) not in done]
        log(f"[{phase}] {len(need)} clusters to label")

        futures = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for _, i in need:
                fut = pool.submit(label_cluster, clusters[i], corpora,
                                  cand_map, store, drops[i])
                futures[fut] = i
            n_ok = 0
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"A": {"label": "", "error": repr(e)},
                           "B": {"label": "", "error": repr(e)},
                           "C": {"label": "", "error": repr(e)}}
                ok = bool(res["A"]["label"]) and bool(res["B"].get("label")) \
                     and bool(res["C"].get("label"))
                n_ok += int(ok)
                row = {"cluster_id": cid, "phase": phase,
                       "ts": time.time(), **res}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                if n_ok % 15 == 0:
                    log(f"[{phase}] progress ok={n_ok}/{len(need)} "
                        f"({time.time()-t_start:.0f}s)")
        log(f"[{phase}] complete ok={n_ok}/{len(need)}")

    for phase in phases_needed:
        run_phase(phase)
    fh.close()

    # ---- Arm C nondeterminism repeats on the eval sample (t0 inputs) ----
    reps_path = L.EF_DATA / "c-repeats.jsonl"
    if args.skip_repeats:
        print("skipping repeats phase")
        return 0
    reps_done = {}
    if reps_path.exists():
        for line in reps_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                reps_done[(r["cluster_id"], r["k"])] = True

    corpus0 = corpora_for("t0")
    cand0 = EV.ctfidf_candidates(corpus0)
    store = EV.VectorStore()
    missing_repeat_threads = []
    for i in SAMPLE_IDS:
        base_ev = EV.pool_evidence(clusters[i], set(), store)
        disp = [d["title"] for d in base_ev["display"]]
        prompt = arm_c.build_prompt(disp, (cand0.get(i) or [])[:20])
        for k in range(3):
            if (i, k) in reps_done:
                continue
            missing_repeat_threads.append((i, k, prompt))
    log(f"[repeats] {len(missing_repeat_threads)} C-calls")
    with open(reps_path, "a", encoding="utf-8", buffering=1) as rf:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(arm_c.generate, p): (i, k)
                    for i, k, p in missing_repeat_threads}
            for fut in as_completed(futs):
                i, k = futs[fut]
                st, payload = fut.result()
                rf.write(json.dumps({
                    "cluster_id": i, "k": k,
                    "status": st,
                    "label": payload if st == 0 else "",
                    "error": "" if st == 0 else str(payload)[:160],
                }, ensure_ascii=False) + "\n")

    peak_kb = _peak_rss_kb()
    summary = {
        "elapsed_s": round(time.time() - t_start, 1),
        "peak_rss_kb": peak_kb,
        "labels_file": str(LABELS_PATH),
    }
    (L.EF_DATA / "LABELING-SUMMARY.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
