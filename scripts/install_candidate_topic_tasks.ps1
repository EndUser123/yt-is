$TaskName = "YtisChannelCandidates"
$Py = "C:\Python314\pythonw.exe"
$Root = "P:\packages\yt-is"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $Py `
    -Argument "scripts\channel_candidates.py" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At "06:25"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings `
    -Description "Daily yt-is channel-candidates refresh (5s measured) before the 06:30 YtisCandidateApply — ends the frozen-candidates starvation (task audit 2026-09-16)." | Out-Null
Write-Output "registered $TaskName daily 06:25 (pythonw, 10min limit)"

# Daily incremental topic assignment: 06:10, before the candidates+sync window
$T2 = "YtisTopicAssignNew"
$e2 = Get-ScheduledTask -TaskName $T2 -ErrorAction SilentlyContinue
if ($e2) { Unregister-ScheduledTask -TaskName $T2 -Confirm:$false }
$act2 = New-ScheduledTaskAction -Execute $Py `
    -Argument "-m ef.clustering --assign-new" `
    -WorkingDirectory $Root
$trig2 = New-ScheduledTaskTrigger -Daily -At "06:10"
$set2 = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $T2 -Action $act2 -Trigger $trig2 -Settings $set2 `
    -Description "Daily incremental topic assignment (repaired 2026-09-19; Qdrant-payload source). Steady-state small deltas; the first-ever backlog needs full recluster." | Out-Null
Write-Output "registered $T2 daily 06:10 (pythonw, 1h limit)"
