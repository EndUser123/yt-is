# Registers the 15-minute restic snapshot task (YtisResticSnapshot).
# Durable installer for the task that already runs (creation-time
# durability: the task was registered manually 2026-08-25 without a
# committed installer — review follow-up 2026-08-26). Pattern follows
# scripts/install_state_backup_task.ps1. Re-running is idempotent: an
# existing task with the same name is removed first.
#
# pythonw.exe: GUI-subsystem, no console flash (two-halves window rule).

$TaskName = "YtisResticSnapshot"
$Action = "C:\Python314\pythonw.exe"
$Script = "P:\packages\yt-is\scripts\restic_snapshot.py"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $Action -Argument "`"$Script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger `
    -Settings $settings -Description "15-min restic snapshots of durable roots + worktrees + object stores to G:\backups\restic-ytis" | Out-Null
Write-Output "registered $TaskName every 15 minutes"
