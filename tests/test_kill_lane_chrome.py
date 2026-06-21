"""Tests for the benchmark-owned Chrome cleanup helper."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import kill_lane_chrome


class _FakeProcess:
    def __init__(self, pid: int, name: str, cmdline: list[str]):
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_get_lane_chrome_pids_matches_over_escaped_lane_cmdline(monkeypatch):
    lane_cmd = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "--type=renderer",
        r"--user-data-dir=P:\\\\\\.data\yt-is\browser\notebooklm-free",
        "--remote-debugging-port=18871",
    ]
    other_cmd = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"--user-data-dir=C:\Users\brsth\AppData\Local\Google\Chrome\User Data",
    ]
    fake_processes = [
        _FakeProcess(101, "chrome.exe", lane_cmd),
        _FakeProcess(202, "chrome.exe", other_cmd),
    ]
    monkeypatch.setattr(kill_lane_chrome.psutil, "process_iter", lambda attrs: iter(fake_processes))

    assert kill_lane_chrome.get_lane_chrome_pids() == [101]


def test_is_default_notebooklm_cmdline_requires_user_data_dir():
    assert kill_lane_chrome._is_default_notebooklm_cmdline(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe --user-data-dir=P:\.data\yt-is\browser\notebooklm --profile-directory=Default"
    )
    assert not kill_lane_chrome._is_default_notebooklm_cmdline(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe --user-data-dir=C:\Users\brsth\AppData\Local\Google\Chrome\User Data"
    )
