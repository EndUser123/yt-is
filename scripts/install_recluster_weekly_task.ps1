# Weekly full topic recluster (YtisTopicReclusterWeekly).
# Authorized by operator ruling 2026-09-19: "schedule overnight, measure the
# first run before making it recurring." First run measured 2026-09-20:
# ~3h35m wall, exit 0, 378 clusters / 980k chunks (ef/clustering-latest.json).
# Weekly cadence: the daily incremental assign keeps new chunks in existing
# clusters between runs; full reclusters are what let new topics form.
$TaskName = "YtisTopicReclusterWeekly"
$Py = "C:\Python314\pythonw.exe"
$Root = "P:\packages\yt-is"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $Py `
    -Argument "scripts\run_recluster_logged.py" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "02:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings `
    -Description "Weekly full topic recluster with stderr capture (logs/recluster-last.log). Measured first run 2026-09-20: 3h35m, 378 clusters." | Out-Null
Write-Output "registered $TaskName weekly Sunday 02:00"
