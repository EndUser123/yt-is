@echo off
rem Nightly DHT capture chain: app -> capture browser -> ingest
set PATH=C:\Python314;C:\Python314\Scripts;%PATH%
start "" /min "P:\tools\dht\DiscordHistoryTracker.exe"
timeout /t 20 /nobreak >nul
python "P:\packages\yt-is\scripts\dht-capture\capture.py" capture >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
taskkill /im DiscordHistoryTracker.exe /f >nul 2>&1
python "P:\packages\yt-is\scripts\run_dht_ingest.py" >> P:\packages\yt-is\.logs\dht_capture.log 2>&1
