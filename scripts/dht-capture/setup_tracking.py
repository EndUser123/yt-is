"""One-time setup: capture the DHT tracking script from the clipboard.

Run this, switch to the Discord History Tracker app, click
'Copy Tracking Script' in its Tracking tab, come back and press Enter.
The script (with its local-server token) is saved to tracking-script.js
and is stable across restarts of the app.
"""

from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
OUT = HERE / "tracking-script.js"

input("Open the DHT app -> Tracking tab -> click 'Copy Tracking Script', "
      "then press Enter here... ")

clip = subprocess.run(
    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
    capture_output=True, text=True).stdout

if "discord" not in clip.lower() and "tracker" not in clip.lower() \
        and "DHT" not in clip and len(clip) < 500:
    print("Clipboard doesn't look like the tracking script "
          f"({len(clip)} chars). Nothing saved.")
    raise SystemExit(1)

OUT.write_text(clip, encoding="utf-8")
print(f"Saved tracking script ({len(clip):,} chars) -> {OUT}")
