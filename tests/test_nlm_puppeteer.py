from pathlib import Path


def test_nlm_puppeteer_does_not_kill_all_chrome_processes() -> None:
    script = Path("P:/packages/yt-is/bin/nlm-puppeteer.js").read_text(encoding="utf-8")

    assert "taskkill /F /IM chrome.exe" not in script
    assert "killChromeProcessesForProfile" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Where-Object { $_.CommandLine -and $_.CommandLine -like \"*$profile*\" }" in script
