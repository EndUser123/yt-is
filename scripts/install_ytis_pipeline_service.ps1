# install_ytis_pipeline_service.ps1 — NSSM service for the continuous
# ytis corpus pipeline (sync -> incremental index -> status -> sleep).
#
# Run from an ELEVATED PowerShell once:
#   powershell -ExecutionPolicy Bypass -File P:\packages\yt-is\scripts\install_ytis_pipeline_service.ps1
#
# Health contract (what "healthy" means, checked by humans/agents):
#   P:/.data/yt-is/ef/pipeline-status.json  — ts fresher than 2x next_cycle_s
#                                             AND ok=true on the last cycle
#   P:/.data/yt-is/ef/operational-status.json — index_lag_count bounded, no
#                                             last_index_error growth
#   nssm status ytis-pipeline               — SERVICE_RUNNING
# Failure isolation: each cycle is a fresh subprocess pair; a wedged step is
# abandoned at 1h and retried with exponential backoff (max 1h). NSSM restarts
# the supervisor itself if it dies (AppExit Default -> Restart).

$ErrorActionPreference = "Stop"
$svc = "ytis-pipeline"
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) { $nssm = "C:\Tools\nssm\nssm.exe" }
if (-not (Test-Path $nssm)) { throw "nssm not found (winget install nssm or adjust `$nssm path)" }

$pythonw = (Get-Command pythonw.exe).Source
$script  = "P:\packages\yt-is\scripts\ytis_pipeline_service.py"

if ([bool](Get-Service $svc -ErrorAction SilentlyContinue)) {
    Write-Host "service $svc already exists - removing old copy"
    & $nssm stop $svc | Out-Null
    & $nssm remove $svc confirm | Out-Null
}

& $nssm install $svc $pythonw "`"$script`""
& $nssm set $svc AppDirectory "P:\packages\yt-is"
& $nssm set $svc DisplayName "ytis continuous corpus pipeline"
& $nssm set $svc Description "sync channels + fetch transcripts + incremental EF index, every 15 min, backoff on failure; health: pipeline-status.json"
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc AppStdout "P:\.data\yt-is\ef\pipeline-service.log"
& $nssm set $svc AppStderr "P:\.data\yt-is\ef\pipeline-service.err.log"
& $nssm set $svc AppRotateFiles 1
& $nssm set $svc AppRotateBytes 5242880
# supervisor crash -> NSSM restarts it; the PID guard prevents doubles
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 30000

Start-Service $svc
& $nssm status $svc
Write-Host ""
Write-Host "Health check (run anytime):"
Write-Host "  Get-Content P:\.data\yt-is\ef\pipeline-status.json"
Write-Host "If switching from the old 05:00/06:00 scheduled tasks to this service,"
Write-Host "disable those two tasks so the work is not duplicated:"
Write-Host "  Disable-ScheduledTask -TaskName 'YtisIndexIncremental','YtisContentSync' -ErrorAction SilentlyContinue"
