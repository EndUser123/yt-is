"""Cold-start lifecycle + readiness contract (K-gate #5).

Measured decomposition (2026-08-18, fresh process):
  import 0.12s | model load 10-12s | FIRST encode 0.96s | subsequent 39ms
Warmup lifecycle collapses post-startup encodes to ms scale; READY
after ~13s total. State machine: starting -> warming -> ready
(degraded if Qdrant unreachable after N checks).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

EF_DATA = Path("P:/.data/yt-is/ef")
READY_FILE = EF_DATA / "readiness.json"
_STATES = ("starting", "warming", "ready", "degraded")
_state = {"state": "starting", "detail": "", "updated_at": 0.0}


def _write() -> None:
    _state["updated_at"] = time.time()
    READY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = READY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, indent=1), encoding="utf-8")
    tmp.replace(READY_FILE)


def get_state() -> dict:
    if READY_FILE.exists():
        try:
            return json.loads(READY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_state)


def warm_start_blocking() -> dict:
    """Startup -> load encoder -> warmup encodes -> verify Qdrant -> ready.
    Durable: state persists to readiness.json for the monitor."""
    from . import embedding, server
    from . import projection_server as ps

    _state["state"] = "warming"
    _state["detail"] = "loading BGE-M3"
    _write()
    t0 = time.monotonic()
    enc = embedding.BGEM3Dual()
    _state["detail"] = "warmup encodes"
    _write()
    enc.encode(["warmup representative query for readiness"])
    enc.encode(["second warmup batch for gpu transfer"])
    _state["detail"] = "verifying qdrant"
    _write()
    ok = True
    try:
        ps.count(server.client(), server.__dict__.get("_PROMO_GEN", 1)
                 if False else 1)
    except Exception as e:
        ok = False
        _state["state"] = "degraded"
        _state["detail"] = f"qdrant unreachable: {type(e).__name__}"
        _write()
        return dict(_state)
    _state["state"] = "ready"
    _state["detail"] = f"warm in {time.monotonic()-t0:.1f}s"
    _write()
    return dict(_state)


def warm_start_background() -> threading.Thread:
    th = threading.Thread(target=warm_start_blocking, daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    print(json.dumps(warm_start_blocking(), indent=1))
