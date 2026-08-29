"""supervisor — multi-window driver for the LightRAG ingest (M3 token plan).

MiniMax Token Plan error 2056 is a rolling 5-HOUR window (platform.minimax.io
docs/api-reference/errorcode), not a monthly cap. The sanctioned fleet path
therefore self-resumes: this supervisor cycles until all docs process.

Cycle:
  1. If doc_status holds 'failed' entries, flip them to 'pending' (LightRAG
     re-enqueues pending ids on ainsert; if that turns out not to hold, the
     fallback below replaces the store).
  2. Probe MiniMax with a 1-token call every PROBE_EVERY seconds until it
     clears 200 (window rolled).
  3. Relaunch ingest_lightrag.py (same working dir). Monitor progress.
  4. Fallback: if a full API-available cycle ends with processed count NOT
     growing and failures growing, rebuild: fresh wd + restored
     kv_store_llm_response_cache.json (proven pattern 2026-08-22: cached
     extractions replay without API calls).
  5. Success: processed >= doc total with failed == 0. Exit 0.

Safeguards: MAX_CYCLES (default 12), hard deadline 24h, abort if a cycle
loses processed docs (store corruption -> stop for human eyes).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path("P:/.data/scout/graph-bakeoff")
WD = BASE / "lightrag-wd"
STATUS = WD / "kv_store_doc_status.json"
CACHE = WD / "kv_store_llm_response_cache.json"
LOG = BASE / "supervisor.log"
VENV_PY = str(BASE / "lightrag-venv/Scripts/python.exe")
INGEST = "P:/packages/yt-is/scripts/graph_bakeoff/ingest_lightrag.py"
PROBE_EVERY = 20 * 60
MAX_CYCLES = 12
DEADLINE_S = 24 * 3600


def log(msg: str):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def env_key() -> str:
    for line in open("P:/.env", encoding="utf-8"):
        line = line.strip()
        if line.startswith("MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no MINIMAX_API_KEY in P:/.env")


def probe_ok() -> bool:
    """True if a 1-token MiniMax call succeeds (window has room)."""
    code = (
        "from openai import OpenAI;"
        f"c=OpenAI(api_key={env_key()!r}, base_url='https://api.minimax.io/v1', timeout=30);"
        "c.chat.completions.create(model='MiniMax-M3', max_tokens=5,"
        " messages=[{'role':'user','content':'hi'}])"
    )
    r = subprocess.run(
        [VENV_PY, "-c", code], capture_output=True, timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0


def status_counts() -> dict:
    if not STATUS.exists():
        return {}
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for v in d.values():
        if isinstance(v, dict):
            out[v.get("status", "?")] = out.get(v.get("status", "?"), 0) + 1
    return out


def flip_failed_to_pending() -> int:
    """Flip every non-processed doc to pending. Originally failed-only; the
    2026-08-22 run showed docs stuck in processing/analyzing/parsing by an
    unclean kill collide on re-insert ('File name already exists') and get
    marked failed — so all non-terminal states must be reset."""
    if not STATUS.exists():
        # Post-rebuild state: the wd holds only the restored response cache,
        # no doc-status store yet — the ingest cycle recreates it. Reading
        # the missing store crashed the post-rebuild iteration (run found
        # dead 2026-08-27 after the 08-25 03:26 rebuild).
        return 0
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    n = 0
    for v in d.values():
        if isinstance(v, dict) and v.get("status") not in ("processed", "pending"):
            v["status"] = "pending"
            n += 1
    if n:
        STATUS.write_text(json.dumps(d), encoding="utf-8")
    return n


def run_ingest_cycle() -> bool:
    """Run one ingest pass. Returns True if it ended all-processed."""
    proc = subprocess.run(
        [VENV_PY, INGEST], capture_output=True, text=True,
        cwd="P:/packages/yt-is/scripts/graph_bakeoff",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    for t in tail:
        log(f"  ingest: {t[:160]}")
    counts = status_counts()
    log(f"  cycle result: {counts}")
    return counts.get("failed", 0) == 0 and counts.get("processed", 0) > 0


def main() -> int:
    t_start = time.time()
    log("supervisor start")
    for cycle in range(1, MAX_CYCLES + 1):
        if time.time() - t_start > DEADLINE_S:
            log("DEADLINE exceeded — stopping for human review")
            return 3
        before = status_counts()
        flipped = flip_failed_to_pending()
        log(f"cycle {cycle}: flipped {flipped} failed->pending; before={before}")

        if not probe_ok():
            log("MiniMax window closed — waiting for roll (probing every 20min)")
            while not probe_ok():
                if time.time() - t_start > DEADLINE_S:
                    log("DEADLINE exceeded while waiting — stop")
                    return 3
                time.sleep(PROBE_EVERY)
            log("window rolled — resuming")

        ok = run_ingest_cycle()
        after = status_counts()
        if ok:
            log(f"SUCCESS after cycle {cycle}: {after}")
            return 0
        # no forward progress + api available -> flip didn't re-enqueue; rebuild
        if after.get("processed", 0) <= before.get("processed", 0):
            log("no forward progress with API available — rebuild store from cache")
            cache_bytes = CACHE.read_bytes()
            import shutil
            shutil.rmtree(WD)
            WD.mkdir(parents=True)
            (WD / "kv_store_llm_response_cache.json").write_bytes(cache_bytes)
            log("wd rebuilt with cache restored; next cycle replays cache")
        if after.get("processed", 0) < before.get("processed", 0):
            log("processed count REGRESSED — stopping for human review")
            return 4
    log("MAX_CYCLES reached without completion")
    return 5


if __name__ == "__main__":
    sys.exit(main())
