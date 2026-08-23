"""Fetch the fresh, session-tokened tracking script from the DHT app.

The app rotates its server token per run; the only token source is the
app UI's "Copy Tracking Script" button (clipboard). This module drives
that click via Windows UI Automation and fetches the full script.

Usage (after the app is running WITH an archive open):
    python fetch_tracking_script.py            # -> prints script path
The script is saved to tracking-script-fresh.js next to this file;
point capture.py at it via DHT_TRACKING_JS.
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "tracking-script-fresh.js"
SERVER = "http://127.0.0.1:50000"

CLICK_PS = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$pid_dht = (Get-Process DiscordHistoryTracker -ErrorAction SilentlyContinue).Id
if (-not $pid_dht) { Write-Output 'ERR no DHT process'; exit 1 }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$pidCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $pid_dht)
$tabCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Tracking')
$tab = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
                       (New-Object System.Windows.Automation.AndCondition($pidCond, $tabCond)))
if ($tab) {
  $tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
  Start-Sleep -Seconds 2
}
$btnCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Copy Tracking Script')
$btn = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
                       (New-Object System.Windows.Automation.AndCondition($pidCond, $btnCond)))
if (-not $btn) { Write-Output 'ERR button not found'; exit 1 }
$btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Seconds 2
$clip = Get-Clipboard -Raw
Write-Output ('OK ' + $clip.Length)
Set-Content -Path 'ENV:CLIPFILE' -Value $clip -NoNewline
"""


def get_tracking_script() -> Path:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", CLICK_PS],
        capture_output=True, text=True, timeout=120)
    if "OK" not in r.stdout:
        sys.exit(f"UIA click failed: {(r.stdout + r.stderr).strip()[:300]}")
    clip = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
        capture_output=True, text=True, timeout=30).stdout
    m = re.search(r"token=([A-Za-z0-9]+)", clip)
    if not m:
        sys.exit("clipboard has no token (is the app's Tracking tab "
                 "showing 'Copy Tracking Script'?)")
    token = m.group(1)
    body = urllib.request.urlopen(
        f"{SERVER}/get-tracking-script?token={token}", timeout=15).read()
    if b"DISCORD" not in body[:4000]:
        sys.exit("fetched body doesn't look like the tracking script")
    OUT.write_bytes(body)
    print(f"fresh tracking script ({len(body):,} chars, token {token[:6]}…) "
          f"-> {OUT}")
    return OUT


if __name__ == "__main__":
    get_tracking_script()
