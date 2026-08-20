"""Slice-1 performance measurements (decision-quality, not a benchmark).

Run from the repo root as ``python -m scripts.ops_console.measure`` (module
execution makes the package importable without path bootstrapping).
Launches the real console subprocess and measures the acceptance paths via
Playwright/Chromium against live read-only monitor data.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]


def _live_anchors() -> tuple[int, str]:
    """(chunk, account) from the newest executed chunk — never hard-coded
    (the original chunk-63 anchors were swept by staging cleanup)."""
    from scripts.pipeline_monitor import MonitorContext, analyze_run

    payload = analyze_run(MonitorContext.create())
    candidates = [
        c for c in payload.get("chunks", [])
        if c.get("status") not in (None, "planned") and c.get("accounts")
    ]
    if not candidates:
        raise SystemExit("no executed chunk with accounts available to measure")
    chunk = candidates[-1]
    account = next(
        (a["account"] for a in chunk["accounts"] if a.get("account")),
        chunk["accounts"][0].get("account"),
    )
    return chunk["chunk"], account


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


env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
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

        # Measure whatever state the monitor currently reports — never a
        # hard-coded incident string.
        from scripts.pipeline_monitor import MonitorContext, compute_health

        current_state = compute_health(MonitorContext.create()).get("state", "")
        assert current_state, "monitor returned no state"
        m["monitor_state_measured"] = current_state
        m["health_initial_render_s"] = timed("/operations", current_state)

        # refresh retains the current presentation, then completes
        page.get_by_role("button").filter(has_text="Refresh health").click()
        page.wait_for_selector("text=previous result shown below", timeout=8000)
        assert page.locator(f"text={current_state}").count() > 0
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
        live_chunk, live_account = _live_anchors()
        m["anchors"] = f"chunk {live_chunk}/{live_account}"
        m["deep_link_account_warm_s"] = timed(
            f"/operations/chunk/{live_chunk}/account/{live_account}", "Stage latency"
        )

        # video drill timing against a real manifest video id (read-only)
        live_video = None
        from scripts.pipeline_monitor import MonitorContext

        for record in MonitorContext.create().chunk_records():
            if record.index == live_chunk and record.output_root:
                manifest = Path(record.output_root) / "manifests" / f'{live_account.replace(".", "-")}.json'
                try:
                    items = json.loads(manifest.read_text(encoding="utf-8")).get("videos") or []
                    if items:
                        live_video = items[0].get("video_id")
                except (OSError, ValueError):
                    pass
                break
        if live_video:
            m["video_drill_load_s"] = timed(
                f"/operations/chunk/{live_chunk}/video/{live_video}?account={live_account}", "events"
            )
        b.close()
finally:
    proc.terminate()
print(json.dumps(m, indent=2))
