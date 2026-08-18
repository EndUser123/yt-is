"""Slice-1 performance measurements (decision-quality, not a benchmark).

Launches the real console subprocess and measures the acceptance paths via
Playwright/Chromium against live read-only monitor data.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from playwright.sync_api import sync_playwright

LIVE_CHUNK, LIVE_ACCOUNT, LIVE_VIDEO = 63, "a.hominidae", "ACmFKptXc0s"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base, proc):
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base}/", timeout=2).read(1)
            return
        except urllib.error.HTTPError:
            return
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("console exited")
            time.sleep(0.1)
    raise RuntimeError("not ready")


env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("PYTEST")}
port = free_port()
base = f"http://127.0.0.1:{port}"
t0 = time.time()
proc = subprocess.Popen(
    [sys.executable, "-m", "scripts.ops_console", "--port", str(port)],
    cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
cold_start = None
try:
    wait_ready(base, proc)
    cold_start = time.time() - t0

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1500, "height": 950})

        def timed(path, text, timeout=90000):
            t = time.time()
            page.goto(base + path, wait_until="domcontentloaded")
            page.wait_for_selector(f"text={text}", timeout=timeout)
            return round(time.time() - t, 2)

        m = {"cold_start_to_http_ready_s": round(cold_start, 2)}
        m["health_initial_render_s"] = timed("/operations", "PAUSED_BUT_RESUME_INEFFECTIVE")

        # refresh retains previous, then completes
        page.get_by_role("button").filter(has_text="Refresh health").click()
        page.wait_for_selector("text=previous result shown below", timeout=8000)
        t = time.time()
        page.wait_for_selector("text=previous result shown below", state="detached", timeout=90000)
        m["health_refresh_s"] = round(time.time() - t, 2)

        # navigation while health computes (fresh page load, click away immediately)
        t = time.time()
        page.goto(f"{base}/operations", wait_until="domcontentloaded")
        page.get_by_role("link", name="Chunks").click()
        page.wait_for_selector(".ag-row", timeout=30000)
        m["nav_while_health_computes_s"] = round(time.time() - t, 2)

        m["chunks_table_load_s"] = round(time.time() - t, 2)
        m["deep_link_account_warm_s"] = timed(
            f"/operations/chunk/{LIVE_CHUNK}/account/{LIVE_ACCOUNT}", "Stage latency"
        )
        m["video_drill_load_s"] = timed(
            f"/operations/chunk/{LIVE_CHUNK}/video/{LIVE_VIDEO}?account={LIVE_ACCOUNT}", "events"
        )
        b.close()
finally:
    proc.terminate()
print(json.dumps(m, indent=2))
