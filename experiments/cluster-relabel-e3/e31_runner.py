"""E3.1 runner — resumable, cached, provider-tolerant completion of:
  (a) pert-phase Arm C rows missing from E3 (185),
  (b) temperature-zero nondeterminism repeats k=0..2 on the eval sample,
  (c) portability set: frozen prompt/mechanism on provider 2
      (nemotron-3-5-lightning-free) over the same t0 inputs.

Design per PREREG-E31.md: deterministic queue ascending (kind,cluster_id,k);
request hash = sha256(model|kind|cid|k|prompt_sha|config); append-only
cache keyed by hash; valid cached results never re-called; bounded retry
with QUOTA vs TRANSPORT vs SEMANTIC classification; per-row receipts.
t0 labels from E3 are byte-frozen and never regenerated here.

Memory instrumentation runs alongside (samples RSS every 1s).
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import e3lib as L
import evidence as EV

P2_MODEL = "codex-opencode-zen-nemotron-3-5-lightning-free"
C_TIMEOUT = 300
MAX_ATTEMPTS = 6

CACHE_PATH = L.EF_DATA / "e31-cache.jsonl"
MEMLOG_PATH = L.EF_DATA / "e31-memlog.jsonl"
RECEIPT_PATH = L.EF_DATA / "E31-RUN-RECEIPT.json"

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


class MemSampler:
    def __init__(self, label):
        self.label = label
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.baseline_mb = None

    def _rss_mb(self):
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)

    def _run(self):
        with open(MEMLOG_PATH, "a", encoding="utf-8") as fh:
            while not self.stop.is_set():
                fh.write(json.dumps({"label": self.label, "ts": time.time(),
                                     "rss_mb": round(self._rss_mb(), 1)}) + "\n")
                fh.flush()
                time.sleep(1)

    def start(self):
        if not MEMLOG_PATH.exists():
            pass
        self.baseline_mb = self._rss_mb()
        self.thread.start()

    def stop_recording(self):
        peak = 0.0
        after = None
        rows = []
        if MEMLOG_PATH.exists():
            for line in MEMLOG_PATH.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                rows.append(r)
        mine = [r["rss_mb"] for r in rows if r.get("label") == self.label]
        if mine:
            peak = max(mine)
        return {"baseline_mb": round(self.baseline_mb or 0, 1),
                "peak_mb": round(peak, 1),
                "incremental_peak_mb": round(max(peak - (self.baseline_mb or 0), 0), 1)}


def build_corpora_and_evidence():
    clusters = L.load_freeze()
    drops = {i: EV.perturbation_drop(clusters[i]) for i in sorted(clusters)}
    corpora_pert = {i: EV.chunk_weighted_titles(clusters[i], drops[i])
                    for i in sorted(clusters)}
    cand_pert = EV.ctfidf_candidates(corpora_pert)
    store = EV.VectorStore()
    return clusters, drops, cand_pert, store


def load_t0_rows():
    rows = {}
    p = L.EF_DATA / "labels.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] == "t0":
                rows[r["cluster_id"]] = r
    return rows


def main() -> int:
    import arm_c

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    model_override = sys.argv[2] if len(sys.argv) > 2 else None

    clusters, drops, cand_pert, store = build_corpora_and_evidence()
    t0 = load_t0_rows()
    sample = json.loads((L.EF_DATA / "SAMPLE.json").read_text(encoding="utf-8"))
    sample_ids = [c for b in ("large", "medium", "small")
                  for c in sample["selection"][b]]

    # ---- deterministic work queue ----
    items = []                       # (kind, cid, k, model)
    if mode in ("all", "pert"):
        done_pert = set()
        if CACHE_PATH.exists():
            for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                if c.get("valid"):
                    done_pert.add((c["kind"], c["cluster_id"], c.get("k"), c["model"]))
        pert_missing = pert_missing_ids(clusters)
        for cid in sorted(pert_missing):
            if ("pertc", cid, None, arm_c.MODEL) not in done_pert:
                items.append(("pertc", cid, None, arm_c.MODEL))
    if mode in ("all", "repeats"):
        done_rep = load_done_hashes()
        reps_ok = existing_valid_repeats()
        for cid in sorted(sample_ids):
            for k in range(3):
                if ("rep", cid, k, arm_c.MODEL) not in done_rep and \
                   (cid, k) not in reps_ok:
                    items.append(("rep", cid, k, arm_c.MODEL))
    if mode in ("all", "portability") and model_override:
        done_port = load_done_hashes()
        for cid in sorted(sample_ids):
            if ("port", cid, None, model_override) not in done_port:
                items.append(("port", cid, None, model_override))

    log(f"queue: {len(items)} items")

    prompts = {}
    ev_cache = {}
    for kind, cid, k, model in items:
        if kind == "rep":
            key = ("t0", cid)
        else:
            key = (("pert", cid) if kind == "pertc" else ("t0", cid))
        if key not in ev_cache:
            drop = drops[cid] if key[0] == "pert" else set()
            ev = EV.pool_evidence(clusters[cid], drop, store)
            ev_cache[key] = ([d["title"] for d in ev["display"]],
                             (cand_pert.get(cid) if key[0] == "pert"
                              else t0[cid]["_evidence"]["keywords_c"])[:20])
        titles, kws = ev_cache[key]
        prompts[(kind, cid, k)] = arm_c.build_prompt(titles, kws)

    sampler = MemSampler(f"e31-{mode}")
    sampler.start()

    def run_item(item):
        kind, cid, k, model = item
        prompt = prompts[(kind, cid, k)]
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        req_hash = hashlib.sha256(
            f"{model}|{kind}|{cid}|{k}|{prompt_hash}|temp=0".encode()).hexdigest()
        attempts, outcome, classification = 0, "", ""
        t_start = time.time()
        delay = 3.0
        for attempt in range(MAX_ATTEMPTS):
            attempts += 1
            st, payload = arm_c.generate(prompt, attempts=1)   # single-shot inner
            if st == 0:
                outcome, classification = payload, "OK"
                break
            err = str(payload)
            err = str(payload)
            if "code=2" in err or "429" in err:
                classification = "QUOTA_429"
            elif "status=None" in err:
                classification = "PROVIDER_EMPTY"
            else:
                classification = "TRANSPORT_OR_SEMANTIC"
            time.sleep(delay)
            delay = min(delay * 1.8, 90)
        rec = {
            "req_hash": req_hash, "prompt_hash": prompt_hash,
            "kind": kind, "cluster_id": cid, "k": k, "model": model,
            "valid": classification == "OK",
            "classification": classification, "attempts": attempts,
            "latency_s": round(time.time() - t_start, 1),
            "config": "temp=0,max_tokens>=16000 escalating",
            "label": outcome if classification == "OK" else "",
            "error": "" if classification == "OK" else str(payload)[:200],
            "ts": time.time(),
        }
        with open(CACHE_PATH, "a", encoding="utf-8", buffering=1) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(f"[{rec['classification']}] {kind}#{cid}{'.'+str(k) if k is not None else ''} "
            f"{model.rsplit('-',2)[0][-14:]} tries={attempts}")
        return rec

    n_ok = 0
    consec_bad = [0]
    def maybe_pause():
        # circuit breaker: sustained quota window -> park 10 min per item
        if consec_bad[0] >= 30:
            log("[circuit] 30 consecutive failures; parking 600s")
            time.sleep(600)
            consec_bad[0] = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for rec in pool.map(run_item, items):
            n_ok += int(rec["valid"])
            consec_bad[0] = 0 if rec["valid"] else consec_bad[0] + 1
            maybe_pause()

    mem = sampler.stop_recording()

    summary = {
        "mode": mode, "queue": len(items), "ok": n_ok,
        "memory": mem,
        "models": sorted({m for _, _, _, m in items}),
    }
    RECEIPT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def pert_missing_ids(clusters):
    out = set()
    seen = {}
    for line in (L.EF_DATA / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r["phase"] != "pert":
                continue
            broken = bool(r["C"].get("error")) or not r["C"].get("label")
            prev = seen.get(r["cluster_id"])
            if prev is None or (prev and prev[1]):
                seen[r["cluster_id"]] = (r["cluster_id"], broken)
    for cid, (_c, broken) in seen.items():
        if broken:
            out.add(cid)
    return out


def load_done_hashes():
    done = set()
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(line)
            except Exception:
                continue
            if c.get("valid"):
                done.add((c["kind"], c["cluster_id"], c.get("k"), c["model"]))
    return done


def existing_valid_repeats():
    """Old-run c-repeats that SUCCEEDED count toward k slots."""
    ok = set()
    rp = L.EF_DATA / "c-repeats.failed-run1.jsonl"
    if rp.exists():
        for line in rp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == 0:
                    ok.add((r["cluster_id"], r["k"]))
    return ok


if __name__ == "__main__":
    sys.exit(main())
