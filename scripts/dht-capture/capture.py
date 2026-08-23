"""DHT capture automation — a dedicated browser profile does the work.

Manual steps, ONCE each:
  1. python capture.py login        -> log into Discord in the window that
                                       opens (the profile remembers it)
  2. python setup_tracking.py       -> open the DHT desktop app, click
                                       "Copy Tracking Script", come back and
                                       press Enter (saves the script)

After that, everything is scheduled:
  nightly: DHT app starts -> capture browser opens discord.com with the
  tracking script auto-injected -> visits each channel in channels.txt,
  dwelling long enough for auto-scroll history capture -> closes ->
  run_dht_ingest pulls the archive into the knowledge base.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / "profile"
TRACKING_JS = Path(__import__("os").environ.get(
    "DHT_TRACKING_JS", str(HERE / "tracking-script.js")))
CHANNELS = HERE / "channels.txt"
DWELL_S = int(__import__("os").environ.get("DHT_DWELL_S", "180"))

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


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


def _selected_urls():
    """Nightly capture list: the /dht page's selection (catalog-backed)
    when it has enabled entries, else the legacy channels.txt lines."""
    import json
    sel_path = Path("P:/.data/yt-is/dht-capture-selection.json")
    cat_path = Path("P:/.data/yt-is/dht-capture-catalog.json")
    try:
        sel = json.loads(sel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sel = {}
    urls = [f"https://discord.com/channels/{key}"
            for key, on in sel.items() if on]
    if urls:
        servers = len({k.split("/")[0] for k, v in sel.items() if v})
        print(f"[capture] using /dht page selection: "
              f"{len(urls)} channel(s) across {servers} server(s)")
        return urls
    if not CHANNELS.exists():
        sys.exit("No capture selection: enable channels on "
                 "http://127.0.0.1:6391/dht (or fill channels.txt)")
    return [l.strip() for l in CHANNELS.read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def capture_mode(limit=None):
    if not TRACKING_JS.exists():
        sys.exit("No tracking-script.js — run setup_tracking.py first")
    urls = _selected_urls()
    if not urls:
        sys.exit("capture selection is empty — enable channels on "
                 "http://127.0.0.1:6391/dht")
    if limit:
        urls = urls[:limit]
        print(f"[capture] test mode: first {len(urls)} of the selection")
    script = TRACKING_JS.read_text(encoding="utf-8")

    with sync_playwright() as pw:
        ctx = _context(pw, headless=False)  # visible: Discord behaves better
        page = ctx.new_page()
        page.on("console", lambda m: print(f"  [console:{m.type}] "
                                           f"{m.text[:160]}", flush=True))
        page.on("pageerror", lambda e: print(f"  [pageerror] "
                                             f"{str(e)[:160]}", flush=True))
        for url in urls:
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
        ctx.close()
    print("[capture] done")


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
