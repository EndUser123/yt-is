"""Qdrant server lifecycle (PID-owned, dedicated yt-is ports).

C-gate decision 2: native Windows binary, dedicated config/storage under
P:/.data/yt-is/ef/server/, ports 6390 (HTTP) / 6392 (gRPC), loopback only,
PID-owned lifecycle. Never touch another Qdrant instance, never kill by
image name (D013/D014).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

EF_DATA = Path("P:/.data/yt-is/ef")
SERVER_DIR = EF_DATA / "server"
QDRANT_BIN = EF_DATA / "tools" / "qdrant.exe"
CONFIG = SERVER_DIR / "config.yaml"
PIDFILE = SERVER_DIR / "qdrant.pid"
START_LOCK_STALE_S = 120.0
HTTP_PORT = 6390
# gRPC moved off 6391: with host pinned to loopback, a specific 127.0.0.1
# gRPC bind on 6391 collides with the warm query HTTP service that owns
# that address. The old wildcard bind coexisted with it only by socket
# accident. No consumer uses qdrant gRPC; HTTP on 6390 is the only client
# surface.
GRPC_PORT = 6392
URL = f"http://127.0.0.1:{HTTP_PORT}"

_CONFIG_BODY = f"""storage:
  storage_path: {SERVER_DIR.as_posix()}/storage
service:
  host: 127.0.0.1
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
    """Start the yt-is-owned qdrant server if not already running.

    Serialized by an exclusive start-lock file: two concurrent cold
    starters otherwise both Popen a qdrant and the loser overwrites the
    PIDFILE (orphaned process, dead-pid status). A stale lock (holder
    died) is reclaimed after START_LOCK_STALE_S."""
    st = status()
    if st["running"]:
        return st
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    if not QDRANT_BIN.exists():
        raise FileNotFoundError(f"qdrant binary missing: {QDRANT_BIN}")
    lock = SERVER_DIR / "start.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime > START_LOCK_STALE_S:
        lock.unlink(missing_ok=True)   # holder died holding the lock
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        # another starter holds it — wait for it to finish or fail
        for _ in range(120):
            time.sleep(0.5)
            st = status()
            if st["running"]:
                return st
            if not lock.exists():
                return start()
        st = status()
        if st["running"]:
            return st
        raise RuntimeError("qdrant start lock held but server not ready")
    try:
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
    finally:
        lock.unlink(missing_ok=True)


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
    """Connected client; starts the server if needed. Cached client is
    cheap-revalidated on every call (get_collections ~1ms) — NO blind
    trust window: a server killed moments ago must not hand out a stale
    client (b-prime rule 8)."""
    global _CLIENT, _CLIENT_AT
    import time as _time
    for attempt in range(3):
        if _CLIENT is not None:
            try:
                _CLIENT.get_collections()
                _CLIENT_AT = _time.monotonic()
                return _CLIENT
            except Exception:
                try:
                    _CLIENT.close()
                except Exception:
                    pass
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
