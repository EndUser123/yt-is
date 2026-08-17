"""Qdrant server lifecycle (PID-owned, dedicated yt-is ports).

C-gate decision 2: native Windows binary, dedicated config/storage under
P:/.data/yt-is/ef/server/, ports 6390/6391, PID-owned lifecycle. Never
touch another Qdrant instance, never kill by image name (D013/D014).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

EF_DATA = Path("P:/.data/yt-is/ef")
SERVER_DIR = EF_DATA / "server"
QDRANT_BIN = EF_DATA / "tools" / "qdrant.exe"
CONFIG = SERVER_DIR / "config.yaml"
PIDFILE = SERVER_DIR / "qdrant.pid"
HTTP_PORT = 6390
GRPC_PORT = 6391
URL = f"http://127.0.0.1:{HTTP_PORT}"

_CONFIG_BODY = f"""storage:
  storage_path: {SERVER_DIR.as_posix()}/storage
service:
  http_port: {HTTP_PORT}
  grpc_port: {GRPC_PORT}
telemetry: false
"""


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).ProcessName"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return out == "qdrant"
    except Exception:
        return False


def status() -> dict:
    if not PIDFILE.exists():
        return {"running": False, "pid": None}
    pid = int(PIDFILE.read_text().strip())
    return {"running": _pid_alive(pid), "pid": pid,
            "url": URL} if _pid_alive(pid) else {"running": False, "pid": pid}


def start() -> dict:
    """Start the yt-is-owned qdrant server if not already running."""
    st = status()
    if st["running"]:
        return st
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    if not QDRANT_BIN.exists():
        raise FileNotFoundError(f"qdrant binary missing: {QDRANT_BIN}")
    if not CONFIG.exists():
        CONFIG.write_text(_CONFIG_BODY, encoding="utf-8")
    log = open(SERVER_DIR / "qdrant.log", "a")
    proc = subprocess.Popen([str(QDRANT_BIN), "--config-path", str(CONFIG)],
                            cwd=str(QDRANT_BIN.parent),
                            stdout=log, stderr=subprocess.STDOUT)
    PIDFILE.write_text(str(proc.pid))
    # wait for readiness
    from qdrant_client import QdrantClient
    for _ in range(120):
        try:
            QdrantClient(url=URL, timeout=10).get_collections()
            return {"running": True, "pid": proc.pid, "url": URL}
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"qdrant did not become ready on {URL} "
                       f"(pid {proc.pid}); see {SERVER_DIR}/qdrant.log")


def stop() -> dict:
    """Stop exactly OUR pid. Never image-name kills."""
    st = status()
    if not st["running"]:
        PIDFILE.unlink(missing_ok=True)
        return {"stopped": False, **st}
    pid = st["pid"]
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(1)
    PIDFILE.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


_CLIENT = None
_CLIENT_AT = 0.0


def client(timeout: int = 120):
    """Connected client; starts the server if needed. Cached with cheap
    revalidation; bounded restart/retry on connection failure (b-prime
    rule 8: stale/dead server must recover without per-query polling)."""
    global _CLIENT, _CLIENT_AT
    import time as _time
    for attempt in range(3):
        now = _time.monotonic()
        if _CLIENT is not None and now - _CLIENT_AT < 30:
            return _CLIENT
        if _CLIENT is not None:
            try:
                _CLIENT.get_collections()
                _CLIENT_AT = now
                return _CLIENT
            except Exception:
                _CLIENT = None
        start()
        from qdrant_client import QdrantClient
        cand = QdrantClient(url=URL, timeout=timeout)
        try:
            cand.get_collections()          # verify before trusting
            _CLIENT, _CLIENT_AT = cand, _time.monotonic()
            return _CLIENT
        except Exception:
            try:
                cand.close()
            except Exception:
                pass
            stop()                          # stale pidfile / half-dead state
            _time.sleep(1)
    raise RuntimeError(f"qdrant unreachable at {URL} after 3 attempts; "
                       f"see {SERVER_DIR}/qdrant.log")


if __name__ == "__main__":
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "status"
    print(json.dumps(globals()[cmd](), indent=1))
