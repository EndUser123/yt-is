@echo off
rem Nightly DHT capture chain (verified end-to-end 2026-08-22):
rem   app WITH the live archive open -> readiness poll -> fetch fresh
rem   tracking script (token rotates per app run) -> capture browser
rem   (selection page) -> graceful app close -> ingest -> cold backup.
rem The app's tracking server only exists while an archive is open, and
rem a cold app opens none — the archive argument is load-bearing. The
rem live.dht backup runs AFTER the graceful close (SQLite checkpoints
rem its WAL then); the old 03:35-task copy was mid-capture and WAL-blind
rem — it missed the entire night (84,755 msgs over 2 nights, verified).
set PATH=C:\Python314;C:\Python314\Scripts;%PATH%
start "" /min "P:\tools\dht\DiscordHistoryTracker.exe" "P:\.data\yt-is\dht\live.dht"
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(90); while((Get-Date) -lt $deadline) { try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:50000/' -UseBasicParsing -TimeoutSec 2; break } catch { Start-Sleep -Seconds 3 } }"
python "P:\packages\yt-is\scripts\dht-capture\fetch_tracking_script.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
if errorlevel 1 goto ingest
set DHT_TRACKING_JS=P:\packages\yt-is\scripts\dht-capture\tracking-script-fresh.js
python "P:\packages\yt-is\scripts\dht-capture\capture.py" capture >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
:ingest
powershell -NoProfile -ExecutionPolicy Bypass -File "P:\packages\yt-is\scripts\dht-capture\close_dht_app.ps1" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
python "P:\packages\yt-is\scripts\run_dht_ingest.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
python "P:\packages\yt-is\scripts\dht-capture\backup_live.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
