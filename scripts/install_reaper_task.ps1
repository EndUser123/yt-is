# Registers the daily workspace-reaper dry-run task (YtisWorkspaceReaper).
# Accumulates the zero-false-positive evidence the graduation check
# (automation-4d9a6a05, 2026-09-09) evaluates — the dated trigger the
# 2026-08-26 /todo insight scan found missing. Pattern follows
# install_restic_snapshot_task.ps1; re-running is idempotent.

$TaskName = "YtisWorkspaceReaper"
$Action = "C:\Python314\pythonw.exe"
$Script = "P:\.agents\scripts\workspace_reaper.py"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

# daily report path carries the date; the graduation check globs these
$cmd = "`"$Script`" --output P:\.data\telemetry\reaper-dry-run-$(Get-Date -Format yyyyMMdd).json"
$act = New-ScheduledTaskAction -Execute $Action -Argument $cmd
$trigger = New-ScheduledTaskTrigger -Daily -At "06:15"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger `
    -Settings $settings -Description "Daily workspace-reaper DRY-RUN (reads state, deletes nothing); reports accumulate for the 2-week graduation window" | Out-Null
Write-Output "registered $TaskName daily at 06:15"
