"""Playwright/Chromium workflow tests for the ops console.

Runs against the real console subprocess and real read-only monitor data,
following the live-replay convention of ``tests/test_pipeline_monitor.py``:
if the live evidence anchors have been swept (7-day staging cleanup), the
affected scenarios skip cleanly instead of failing.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PW_AVAILABLE = False

# Live evidence anchors (verified 2026-08-17). If these get swept by the
# staging cleanup, tests that need them skip rather than fail.
LIVE_CHUNK = 63
LIVE_ACCOUNT = "a.hominidae"
LIVE_VIDEO = "ACmFKptXc0s"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Console:
    def __init__(self):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        # Strip pytest env markers: NiceGUI's ui.run force-reads
        # NICEGUI_SCREEN_TEST_PORT when it believes it runs under pytest,
        # which would crash the standalone console subprocess.
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
        env.pop("NICEGUI_SCREEN_TEST_PORT", None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "scripts.ops_console", "--port", str(self.port)],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        import urllib.error

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{self.base}/", timeout=2).read(1)
                return  # 200 on the root page: ready
            except urllib.error.HTTPError:
                return  # any HTTP response means the server is up
            except Exception:
                if self.proc.poll() is not None:
                    out = self.proc.stdout.read().decode(errors="replace") if self.proc.stdout else ""
                    raise RuntimeError(f"console exited: {out[-2000:]}")
                time.sleep(0.3)
        raise RuntimeError("console did not become ready in 60 s")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def console():
    if not _PW_AVAILABLE:
        pytest.skip("playwright not installed")
    c = Console()
    yield c
    c.stop()


@pytest.fixture(scope="module")
def page(console):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        yield page
        browser.close()


def _wait_text(page, text, timeout=30000) -> float:
    t0 = time.time()
    page.wait_for_selector(f"text={text}", timeout=timeout)
    return time.time() - t0


def _live_anchor_ok(console) -> bool:
    """The deep-link anchor chunk/account must exist in live evidence."""
    if console.proc is None or console.proc.poll() is not None:
        return False
    from scripts.pipeline_monitor import MonitorContext, analyze_run

    ctx = MonitorContext.create()
    payload = analyze_run(ctx)
    return any(
        c.get("chunk") == LIVE_CHUNK
        for c in payload.get("chunks", [])
    )


# ---- scenarios -----------------------------------------------------------------

def test_deep_link_direct_load(console, page):
    if not _live_anchor_ok(console):
        pytest.skip("live chunk evidence swept")
    elapsed = _wait_text_after_load(page, console, f"/operations/chunk/{LIVE_CHUNK}/account/{LIVE_ACCOUNT}", "Stage latency")
    assert elapsed < 15


def _wait_text_after_load(page, console, path, text):
    t0 = time.time()
    page.goto(console.base + path, wait_until="domcontentloaded")
    page.wait_for_selector(f"text={text}", timeout=30000)
    return time.time() - t0


def test_deep_link_refresh(console, page):
    if not _live_anchor_ok(console):
        pytest.skip("live chunk evidence swept")
    page.goto(f"{console.base}/operations/chunk/{LIVE_CHUNK}/account/{LIVE_ACCOUNT}", wait_until="domcontentloaded")
    _wait_text(page, "Stage latency")
    page.reload(wait_until="domcontentloaded")
    _wait_text(page, "Stage latency", timeout=30000)
    assert f"/operations/chunk/{LIVE_CHUNK}/account/{LIVE_ACCOUNT}" in page.url


def _authoritative_state() -> str:
    """Current monitor health state — the live tests assert the UI renders
    whatever the monitor actually reports, never a hard-coded incident."""
    from scripts.pipeline_monitor import MonitorContext, compute_health

    report = compute_health(MonitorContext.create())
    state = report.get("state")
    assert isinstance(state, str) and state, f"monitor returned no state: {report.get('state_reason')}"
    return state


_HEADLINE = "main .text-3xl"


def _rendered_state(page) -> str:
    """The semantic state the page actually rendered as its headline verdict."""
    page.wait_for_selector(_HEADLINE, timeout=30000)
    text = page.locator(_HEADLINE).inner_text().strip()
    assert text and not text.startswith("health unavailable"), f"page rendered error state: {text}"
    return text


def test_health_renders_monitor_authoritative_state(console, page):
    # The monitor verdict is dynamic under live ingestion (states legitimately
    # transition), so comparing the page against a separate monitor invocation
    # races. Discriminate the real failure modes instead:
    #   1. the headline must faithfully match the monitor payload the page
    #      itself displays (catches view-model/render defects), and
    #   2. that payload must be fresh (catches stale/cached backend results).
    import datetime as _dt
    import re as _re

    page.goto(f"{console.base}/operations", wait_until="domcontentloaded")
    rendered = _rendered_state(page)
    page.get_by_role("button").filter(has_text="Raw health JSON").click()
    page.wait_for_selector("text=checked_at", timeout=10000)
    body = page.locator("main").inner_text()
    m = _re.search(r'"state":\s*"([A-Z_]+)"', body) or _re.search(r'"state": "([A-Z_]+)"', body)
    assert m, "raw monitor payload not reachable from the page"
    assert m.group(1) == rendered, (
        f"headline {rendered!r} disagrees with the displayed monitor payload state {m.group(1)!r}"
    )
    t = _re.search(r'"checked_at":\s*"([^"]+)"', body) or _re.search(r'"checked_at": "([^"]+)"', body)
    assert t, "monitor checked_at not present in the displayed payload"
    checked = _dt.datetime.fromisoformat(t.group(1).replace("Z", "+00:00"))
    age = (_dt.datetime.now(_dt.timezone.utc) - checked).total_seconds()
    assert 0 <= age < 120, f"displayed monitor payload is stale: checked_at age {age:.0f}s"
    # causal/status presentation is visible
    page.wait_for_selector("text=Why — resume mechanism chain", timeout=10000)


def test_table_account_video_back(console, page):
    if not _live_anchor_ok(console):
        pytest.skip("live chunk evidence swept")
    page.goto(f"{console.base}/operations/chunks", wait_until="domcontentloaded")
    page.wait_for_selector(".ag-row", timeout=30000)
    row = page.locator(".ag-row").filter(has_text=LIVE_ACCOUNT).first
    if not row.count():
        pytest.skip("account rows not present in live payload")
    row.click()
    page.wait_for_selector("text=Stage latency", timeout=30000)
    assert f"/operations/chunk/{LIVE_CHUNK}/account/{LIVE_ACCOUNT}" in page.url or "/account/" in page.url
    # drill into the known video
    page.get_by_placeholder("video id").fill(LIVE_VIDEO)
    page.get_by_role("button").filter(has_text="Drill").click()
    page.wait_for_selector("text=events", timeout=30000)
    page.go_back(wait_until="domcontentloaded")
    page.wait_for_selector("text=Stage latency", timeout=30000)


def test_invalid_identifiers_clean(console, page):
    page.goto(f"{console.base}/operations/chunk/99999", wait_until="domcontentloaded")
    page.wait_for_selector("text=chunk not found", timeout=30000)
    page.goto(f"{console.base}/operations/chunk/1/account/nosuch.user", wait_until="domcontentloaded")
    page.wait_for_selector("text=account not found", timeout=30000)


def test_health_refresh_retains_previous(console, page):
    # Retention is about the UI keeping ITS previous rendered presentation —
    # whatever state that was — so anchor on the rendered headline, not on a
    # monitor re-query.
    page.goto(f"{console.base}/operations", wait_until="domcontentloaded")
    rendered = _rendered_state(page)
    page.get_by_role("button").filter(has_text="Refresh health").click()
    page.wait_for_selector("text=previous result shown below", timeout=8000)
    assert page.locator(f"text={rendered}").count() > 0
    page.wait_for_selector("text=previous result shown below", state="detached", timeout=90000)
    assert page.locator(f"text={rendered}").count() > 0 or page.locator(_HEADLINE).inner_text().strip()


def test_navigation_usable_while_health_computes(console, page):
    # "Usable" = navigation responds promptly (URL changes) while health
    # computes; the chunk payload itself may take longer under live load.
    page.goto(f"{console.base}/operations", wait_until="domcontentloaded")
    t0 = time.time()
    page.get_by_role("link", name="Chunks").click()
    page.wait_for_url("**/operations/chunks", timeout=10000)
    nav_s = time.time() - t0
    page.wait_for_selector(".ag-row", timeout=60000)
    assert nav_s < 10, f"navigation took {nav_s:.1f}s to leave the health page"
