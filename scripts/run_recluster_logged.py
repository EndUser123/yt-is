"""Windowless launcher for the full topic recluster with captured output.

Runs C:/Python314/python.exe -m ef.clustering hidden (CREATE_NO_WINDOW) and
writes stdout+stderr to logs/recluster-last.log so failures are diagnosable
(pythonw itself has no stdout; the 2026-09-20 02:00 run failed invisibly).
"""
import subprocess
from pathlib import Path

ROOT = Path("P:/packages/yt-is")
LOG = ROOT / "logs" / "recluster-last.log"

# BelowNormal for self + child: the recluster (UMAP+HDBSCAN over the full
# embedding corpus) runs for hours; at Normal priority it starves the
# operator's desktop. BelowNormal yields to every interactive process while
# still progressing when the machine is idle (2026-09-20 operator friction).
ctypes = __import__("ctypes")
windll = ctypes.windll.kernel32
windll.SetPriorityClass(windll.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS

r = subprocess.run(
    ["C:/Python314/python.exe", "-m", "ef.clustering"],
    cwd=str(ROOT),
    capture_output=True,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
LOG.parent.mkdir(parents=True, exist_ok=True)
LOG.write_bytes((r.stdout or b"") + (r.stderr or b""))
raise SystemExit(r.returncode)
