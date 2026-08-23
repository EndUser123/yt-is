@echo off
rem Nightly DHT capture chain (verified end-to-end 2026-08-22):
rem   app WITH the live archive open -> fetch fresh tracking script
rem   (token rotates per app run) -> capture browser (selection page)
rem   -> graceful app close -> ingest.
rem The app's tracking server only exists while an archive is open, and
rem a cold app opens none — the archive argument is load-bearing.
set PATH=C:\Python314;C:\Python314\Scripts;%PATH%
start "" /min "P:\tools\dht\DiscordHistoryTracker.exe" "P:\.data\yt-is\dht\live.dht"
timeout /t 25 /nobreak >nul
python "P:\packages\yt-is\scripts\dht-capture\fetch_tracking_script.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
if errorlevel 1 goto ingest
set DHT_TRACKING_JS=P:\packages\yt-is\scripts\dht-capture\tracking-script-fresh.js
python "P:\packages\yt-is\scripts\dht-capture\capture.py" capture >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
:ingest
powershell -NoProfile -Command "$p = Get-Process DiscordHistoryTracker -ErrorAction SilentlyContinue; if ($p) { $p.CloseMainWindow() | Out-Null; Start-Sleep 8; if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } }"
python "P:\packages\yt-is\scripts\run_dht_ingest.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
