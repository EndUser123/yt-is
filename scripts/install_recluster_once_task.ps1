# One-shot full topic recluster over the ~200k-video corpus.
# v3 (2026-09-20): v1 (02:00) failed with exit 1 and no receipt because pythonw
# discards stderr. This version runs scripts/run_recluster_logged.py via
# pythonw, which captures stdout+stderr to logs/recluster-last.log. Still
# one-shot: measure the first successful run (receipt at
# ef/clustering-latest.json) BEFORE making it recurring.
$TaskName = "YtisTopicReclusterOnce"
$Py = "C:\Python314\pythonw.exe"
$Root = "P:\packages\yt-is"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $Py `
    -Argument "scripts\run_recluster_logged.py" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At "2026-09-20 02:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings `
    -Description "One-shot full topic recluster with stderr capture. Measure via ef/clustering-latest.json + logs/recluster-last.log; make recurring only after a successful measured run." | Out-Null
Write-Output "registered $TaskName v3 (log capture via run_recluster_logged.py)"
