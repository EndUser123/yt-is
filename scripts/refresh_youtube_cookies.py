#!/usr/bin/env python3
"""Refresh ~/youtube_cookies.txt from a live browser cookie store.

Watch-later/history discovery needs valid YouTube cookies. The exported
file goes stale whenever the browser rotates its session (yt-dlp then
fails with "cookies are no longer valid"). This script pulls fresh cookies
straight from the browser through yt-dlp's own extraction and rewrites
the Netscape-format cookie file that ``csf-source watchlater|history``
falls back to when ``--cookies-from-browser`` is not used.

Prefer passing ``--cookies-from-browser`` directly to watchlater/history
for always-fresh reads; this script exists for flows that require the
file (or for refreshing it on a schedule).

Usage:
    python scripts/refresh_youtube_cookies.py --browser firefox:default-release
    python scripts/refresh_youtube_cookies.py --browser chrome

Exit codes: 0 refreshed; 1 extraction/writing failed; 2 usage error.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_browser_spec(spec: str) -> tuple[str, str | None, str | None]:
    parts = spec.split(":")
    browser_and_keyring = parts[0]
    profile = parts[1] if len(parts) > 1 and parts[1] else None
    if "+" in browser_and_keyring:
        browser, keyring = browser_and_keyring.split("+", 1)
    else:
        browser, keyring = browser_and_keyring, None
    browser = browser.strip()
    if not browser:
        raise ValueError("--browser needs a name, e.g. firefox:default-release")
    return browser, profile, keyring


def refresh(browser_spec: str, output: Path) -> int:
    try:
        browser, profile, keyring = _parse_browser_spec(browser_spec)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        print("error: yt-dlp is not installed or too old for cookie extraction", file=sys.stderr)
        return 1

    try:
        jar = extract_cookies_from_browser(browser, profile=profile, keyring=keyring)
    except Exception as exc:
        print(f"error: cookie extraction from {browser!r} failed: {exc}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    jar.save(str(output))
    text = output.read_text(encoding="utf-8", errors="replace")
    youtube_lines = [line for line in text.splitlines() if "youtube.com" in line]
    if not youtube_lines:
        print(f"error: no youtube.com cookies were written to {output}", file=sys.stderr)
        return 1
    print(f"refreshed {output} from {browser_spec}: {len(youtube_lines)} youtube.com cookie lines")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--browser",
        required=True,
        help="BROWSER[+KEYRING][:PROFILE], e.g. firefox:default-release or chrome",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "youtube_cookies.txt",
        help="Cookie file to rewrite (default: ~/youtube_cookies.txt)",
    )
    args = parser.parse_args(argv)
    return refresh(args.browser, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
