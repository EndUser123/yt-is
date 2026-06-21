"""Clean shutdown of benchmark-owned lane Chrome processes with sub-process drain.

Safe to run any time you need to reset yt-is-owned browser state before or after
a benchmark rerun. This helper only targets the dedicated lane roots and leaves
user-owned Chrome or Comet sessions alone.
"""

from __future__ import annotations

import re
import sys
import time

import psutil


LANE_BROWSER_ROOTS = (
    r"P:\.data\yt-is\browser\notebooklm-pro",
    r"P:\.data\yt-is\browser\notebooklm-free",
)
DEFAULT_BROWSER_ROOT = r"P:\.data\yt-is\browser\notebooklm"


def _normalize_cmdline(text: str) -> str:
    return re.sub(r"\\+", r"\\", text).lower()


def _chrome_cmdline(process: psutil.Process) -> str:
    return " ".join(process.info.get("cmdline") or [])


def _is_chrome_process(process: psutil.Process) -> bool:
    return (process.info.get("name") or "").lower() == "chrome.exe"


def _is_lane_chrome_cmdline(cmdline: str) -> bool:
    normalized = _normalize_cmdline(cmdline)
    return any(_normalize_cmdline(root) in normalized for root in LANE_BROWSER_ROOTS)


def _is_default_notebooklm_cmdline(cmdline: str) -> bool:
    normalized = _normalize_cmdline(cmdline)
    return _normalize_cmdline(DEFAULT_BROWSER_ROOT) in normalized and "--user-data-dir=" in normalized


def get_lane_chrome_pids() -> list[int]:
    pids: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if _is_chrome_process(process) and _is_lane_chrome_cmdline(_chrome_cmdline(process)):
                pids.append(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def main() -> int:
    print("Step 1: Terminate all lane Chrome processes...")
    all_killed: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if _is_chrome_process(process) and _is_lane_chrome_cmdline(_chrome_cmdline(process)):
                process.kill()
                all_killed.append(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    print(f"  Terminated {len(all_killed)} processes: {all_killed}")

    print("\nStep 2: Wait for sub-process drain (up to 90s)...")
    start = time.monotonic()
    deadline = start + 90.0
    last_report = -1
    while time.monotonic() < deadline:
        time.sleep(3)
        remaining = get_lane_chrome_pids()
        elapsed = time.monotonic() - start
        if not remaining:
            print(f"  All lane Chrome processes exited after {elapsed:.1f}s")
            break
        report_interval = int(elapsed / 10)
        if report_interval != last_report:
            print(f"  [{elapsed:.0f}s] Still running: {sorted(remaining)}")
            last_report = report_interval
    else:
        remaining = get_lane_chrome_pids()
        print(f"\nWARNING: {len(remaining)} lane processes still alive after 90s")

    print("\nStep 3: Verify clean state for browser health gate...")
    default_procs: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if _is_chrome_process(process):
                cmdline = _chrome_cmdline(process)
                if _is_default_notebooklm_cmdline(cmdline) and not _is_lane_chrome_cmdline(cmdline):
                    default_procs.append(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    lane_pids = get_lane_chrome_pids()
    print(f"  Default profile Chrome: {len(default_procs)} (should be 0)")
    print(f"  Lane profile Chrome: {len(lane_pids)} (should be 0)")

    if default_procs or lane_pids:
        print("\nFAILED: Chrome processes still running")
        return 1
    print("\nPASSED: Clean browser state confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
