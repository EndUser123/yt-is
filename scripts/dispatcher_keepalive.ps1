$ErrorActionPreference = 'SilentlyContinue'
$hb = 'P:\packages\yt-is\.logs\dispatch\heartbeat.json'
$lock = 'P:\.data\yt-is\dispatch.instance.lock'
$fresh = $false
if (Test-Path $hb) {
    $age = (Get-Date) - (Get-Item $hb).LastWriteTime
    if ($age.TotalSeconds -lt 600) { $fresh = $true }
}
if ($fresh) { exit 0 }
# stale heartbeat: only relaunch when the instance lock is free
if (Test-Path $lock) {
    $fs = [System.IO.File]::Open($lock, 'Open', 'ReadWrite', 'None')
    try { } catch { $fs.Close(); exit 0 }  # someone holds it: alive
    $fs.Close()
}
Start-Process -WindowStyle Hidden -FilePath 'C:\Python314\python.exe' `
    -ArgumentList '-u','P:\packages\yt-is\scripts\dispatcher.py' `
    -WorkingDirectory 'P:\packages\yt-is'
exit 0
