# restic snapshot — 15-min recurring task (burst 2026-08-25)
# Scope: hooks, wiki, alerts, supervisor state, telemetry, info-harness.
# Excludes session worktrees (derived git checkouts, already on GitHub)
# and large SQLite DBs (backed up by their own nightly tasks).
#
# Password: G:\backups\restic-ytis-password (restricted ACL, brsth only)
# Repo:     G:\backups\restic-ytis (second volume — survives P: loss)

$restic = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\restic.exe"
$env:RESTIC_REPOSITORY = "G:\backups\restic-ytis"
$env:RESTIC_PASSWORD_FILE = "G:\backups\restic-ytis-password"

$logDir = "P:\.data\logs\restic"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = "$logDir\snapshot-$(Get-Date -Format yyyyMM).log"

if (-not (Test-Path "G:\backups\restic-ytis")) {
    "ERROR: repo G:\backups\restic-ytis not found - G: offline?" | Add-Content $log
    exit 1
}

$started = Get-Date
& $restic backup `
    "P:\.agents" `
    "P:\.data\wiki" `
    "P:\.data\yt-is\alerts" `
    "P:\.data\yt-is\unattended-backlog" `
    "P:\.data\telemetry" `
    "P:\.data\info-harness" `
    --tag scheduled 2>>$log | Add-Content $log
$rc = $LASTEXITCODE
$elapsed = ((Get-Date) - $started).TotalSeconds

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] rc=$rc elapsed=$([Math]::Round($elapsed,1))s" | Add-Content $log

# Daily retention: keep all within 24h, then 7 daily, 4 weekly
# Runs at most once per day (checks if last forget was > 23h ago)
$forgetMarker = "$logDir\last-forget"
if (-not (Test-Path $forgetMarker) -or ((Get-Date) - (Get-Item $forgetMarker).LastWriteTime).TotalHours -gt 23) {
    & $restic forget --keep-within 24h --keep-daily 7 --keep-weekly 4 --prune 2>>$log | Add-Content $log
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] retention: rc=$LASTEXITCODE" | Add-Content $log
    Set-Content $forgetMarker "$(Get-Date -Format o)"
}

exit $rc
