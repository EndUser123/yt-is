# Registers the recurring yt-is audio-drain task (deferred audio -> Whisper -> ledger).
# Replaces harness-spawned drain loops: three silent deaths 2026-09-12 when the
# loop pwsh was reaped with its launcher's process tree. Task Scheduler runs
# outside every agent harness process tree, so it cannot be reaped with them.
# Pattern follows scripts/install_state_backup_task.ps1. Re-running is idempotent.
#
# Serialization: deferred_audio_feeder.py has NO internal lock, so overlapping
# instances would double-process. -MultipleInstances IgnoreNew makes Task
# Scheduler skip a trigger while the previous pass still runs -> back-to-back
# passes, never concurrent. 5-min trigger + ~5-70-min passes = continuous drain.
#
# Desktop quietness: pythonw.exe (no console). Never swap back to python.exe:
# a console window would flash on the operator's desktop every 5 minutes.

$TaskName = "YtisAudioDrain"
$PythonW = "C:\Python314\pythonw.exe"
$Root = "P:\packages\yt-is"

if (-not (Test-Path $PythonW)) {
    Write-Output "FATAL: $PythonW not found"
    exit 1
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $PythonW `
    -Argument "scripts\deferred_audio_feeder.py drain --limit 25" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings `
    -Description "Recurring yt-is deferred-audio drain pass (Whisper CPU, ledger-evicted). Serialized via IgnoreNew." | Out-Null
Write-Output "registered $TaskName every 5 min (IgnoreNew, pythonw, 4h limit)"
