"""DHT capture automation — a dedicated browser profile does the work.

Setup (DONE 2026-08-22): capture.py login once; the tracking script is
fetched fresh each run by fetch_tracking_script.py (the app token
rotates per session). Channel selection: http://127.0.0.1:6393/dht.

Nightly chain (nightly.cmd): DHT app opens WITH the live archive ->
fresh tracking script -> this capture loop -> graceful app close ->
run_dht_ingest.

Rate discipline (red team 2026-08-22): Discord anti-abuse watches
rapid channel-hopping with hard scrolling on the ACCOUNT, so the loop
paces between channels and caps work per run:
  DHT_MAX_CHANNELS  max channels per run (default 150; rotation cursor
                    wraps through the selection over successive nights)
  DHT_MAX_RUNTIME_S hard wall-clock budget (default 14400 = 4h)
  DHT_PAUSE_MIN_S / DHT_PAUSE_MAX_S  inter-channel pause jitter
                    (default 6-14s)
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / "profile"
TRACKING_JS = Path(os.environ.get(
    "DHT_TRACKING_JS", str(HERE / "tracking-script.js")))
CHANNELS = HERE / "channels.txt"
SELECTION = Path("P:/.data/yt-is/dht-capture-selection.json")
CURSOR = Path("P:/.data/yt-is/dht-capture-cursor.json")
LOCK = Path("P:/.data/yt-is/dht-capture.lock")
DWELL_S = int(os.environ.get("DHT_DWELL_S", "180"))
MAX_CHANNELS = int(os.environ.get("DHT_MAX_CHANNELS", "150"))
MAX_RUNTIME_S = int(os.environ.get("DHT_MAX_RUNTIME_S", "14400"))
PAUSE_MIN_S = float(os.environ.get("DHT_PAUSE_MIN_S", "6"))
PAUSE_MAX_S = float(os.environ.get("DHT_PAUSE_MAX_S", "14"))

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _acquire_lock() -> None:
    """Single instance: two captures would fight over the one profile."""
    if LOCK.exists():
        try:
            old_pid = int(LOCK.read_text().strip() or 0)
        except ValueError:
            old_pid = 0
        if old_pid:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {old_pid} -ErrorAction SilentlyContinue)"
                 ".ProcessName"],
                capture_output=True, text=True, timeout=20).stdout.strip()
            if r in ("python", "pythonw"):
                sys.exit(f"another capture is running (pid {old_pid}); "
                         "refusing to double-drive the browser profile")
    LOCK.write_text(str(os.getpid()))


def _context(pw, headless: bool):
    # user_data_dir is a launch_persistent_context argument (a profile
    # directory is a browser-level setting, not a context-level one);
    # browser.new_context(user_data_dir=...) is the API misuse that broke
    # login mode until 2026-08-22. closing the returned context closes
    # the browser too, so callers' ctx.close() is unchanged.
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome", executable_path=CHROME, headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])


def logged_in(page) -> bool:
    """Logged-in Discord shows the app shell; logged-out shows the login
    form. Poll a few cheap markers."""
    for sel in ('[aria-label="User Settings"]', '[aria-label="Close"]',
                'nav[aria-label="Channels"]'):
        try:
            if page.locator(sel).first().is_visible():
                return True
        except Exception:
            continue
    return "login" in (page.url or "")


def login_mode():
    with sync_playwright() as pw:
        ctx = _context(pw, headless=False)
        page = ctx.new_page()
        page.goto("https://discord.com/channels/@me")
        print("Browser open — log into Discord in that window.")
        print("This profile remembers the login; you only do this once.")
        deadline = time.time() + 1200
        while time.time() < deadline:
            if logged_in(page):
                print("Login detected — saved to the capture profile.")
                time.sleep(5)
                break
            page.wait_for_timeout(5000)
        ctx.close()


def _selected_urls(limit=None):
    """Nightly capture list: the /dht page's selection (catalog-backed)
    when it has enabled entries, else the legacy channels.txt lines.
    Applies the rotation cursor + per-run cap: successive runs walk the
    selection in order and wrap, so a large selection is covered over
    several nights instead of one giant run. --limit bypasses the
    cursor (test mode takes the head of the selection)."""
    try:
        sel = json.loads(SELECTION.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sel = {}
    urls = sorted(f"https://discord.com/channels/{key}"
                  for key, on in sel.items() if on)
    if not urls:
        if not CHANNELS.exists():
            sys.exit("No capture selection: enable channels on "
                     "http://127.0.0.1:6393/dht (or fill channels.txt)")
        return [l.strip() for l in CHANNELS.read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    servers = len({u.split("/")[-2] for u in urls})
    if limit:
        print(f"[capture] test mode: first {min(limit, len(urls))} "
              f"of the selection")
        return urls[:limit]
    try:
        cursor = int(json.loads(CURSOR.read_text())["cursor"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        cursor = 0
    cursor %= len(urls)
    take = min(MAX_CHANNELS, len(urls))
    batch = urls[cursor:cursor + take]
    if len(batch) < take:               # wrap around the end
        batch += urls[:take - len(batch)]
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({"cursor": (cursor + take) % len(urls),
                                  "advanced_from": cursor,
                                  "took": len(batch)}))
    print(f"[capture] selection: {len(urls)} channel(s) across {servers} "
          f"server(s); this run: {len(batch)} (rotation from #{cursor + 1}, "
          f"cap {MAX_CHANNELS}, budget {MAX_RUNTIME_S // 3600}h)")
    return batch


def capture_mode(limit=None):
    if not TRACKING_JS.exists():
        sys.exit("No tracking script — nightly runs fetch it via "
                 "fetch_tracking_script.py; manual: run that first")
    _acquire_lock()
    try:
        _run_capture(limit)
    finally:
        LOCK.unlink(missing_ok=True)


def _run_capture(limit):
    urls = _selected_urls(limit)
    if not urls:
        sys.exit("capture selection is empty — enable channels on "
                 "http://127.0.0.1:6393/dht")
    script = TRACKING_JS.read_text(encoding="utf-8")
    started = time.monotonic()
    done = 0

    with sync_playwright() as pw:
        ctx = _context(pw, headless=False)  # visible: Discord behaves better
        page = ctx.new_page()
        page.on("console", lambda m: print(f"  [console:{m.type}] "
                                           f"{m.text[:160]}", flush=True))
        page.on("pageerror", lambda e: print(f"  [pageerror] "
                                             f"{str(e)[:160]}", flush=True))
        for url in urls:
            if time.monotonic() - started > MAX_RUNTIME_S:
                print(f"[capture] wall-clock budget "
                       f"({MAX_RUNTIME_S // 3600}h) reached at "
                       f"{done} channel(s) — stopping for tonight",
                      flush=True)
                break
            if done:
                pause = random.uniform(PAUSE_MIN_S, PAUSE_MAX_S)
                print(f"[capture] pacing {pause:.0f}s before next channel",
                      flush=True)
                time.sleep(pause)
            print(f"[capture] {url} — dwelling {DWELL_S}s", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  nav error: {str(e)[:80]}")
                continue
            # NOTE: add_init_script does NOT reliably execute this script
            # (verified 2026-08-22: init injection never sets DHT_LOADED;
            # page.evaluate of the same script works). Each goto() is a
            # full navigation, so (re)inject per channel via evaluate.
            page.wait_for_timeout(4000)
            if not page.evaluate("() => window.DHT_LOADED"):
                try:
                    page.evaluate(script)
                    print("  [dht] script injected", flush=True)
                except Exception as e:
                    print(f"  [dht] inject failed: {str(e)[:100]}",
                          flush=True)
                    continue
                page.wait_for_timeout(2500)
            # DHT has NO autostart: click its Start button (tolerate
            # already-tracking — the label flips to "Pause Tracking")
            try:
                page.click("#dht-ctrl-track", timeout=8000)
                print("  [dht] tracking started", flush=True)
            except Exception:
                print("  [dht] track button not clickable (already on?)",
                      flush=True)
            # access check: no message list = gated/empty channel
            if page.locator("li[id^='chat-messages']").count() == 0:
                page.wait_for_timeout(5000)
                if page.locator("li[id^='chat-messages']").count() == 0:
                    print("  [dht] WARNING: no messages visible — channel "
                          "gated or empty", flush=True)
            # Dwell, but move on early once DHT says the channel is fully
            # caught up ("Reached End") — already-captured channels finish
            # in seconds instead of burning the whole dwell.
            import time as _time
            deadline = _time.monotonic() + DWELL_S
            while _time.monotonic() < deadline:
                page.wait_for_timeout(5000)
                try:
                    st = page.locator("#dht-ctrl-status").first.text_content() or ""
                except Exception:
                    st = ""
                if "Reached End" in st:
                    print("  [dht] channel caught up early", flush=True)
                    break
            done += 1
        ctx.close()
    print(f"[capture] done: {done}/{len(urls)} channel(s) this run")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="")
    ap.add_argument("--limit", type=int, default=None,
                    help="capture only the first N enabled channels (tests)")
    a = ap.parse_args()
    if a.mode == "login":
        login_mode()
    elif a.mode == "capture":
        capture_mode(limit=a.limit)
    else:
        print(__doc__)
