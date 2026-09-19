# One-shot full topic recluster over the ~200k-video corpus.
# Scheduled overnight (02:00) per operator ruling 2026-09-19: measure the
# first run (receipt at ef/clustering-latest.json carries started/finished
# and counts) BEFORE making it recurring. Until then this stays one-shot.
$TaskName = "YtisTopicReclusterOnce"
$Py = "C:\Python314\pythonw.exe"
$Root = "P:\packages\yt-is"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$act = New-ScheduledTaskAction -Execute $Py `
    -Argument "-m ef.clustering" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At "2026-09-20 02:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings `
    -Description "One-shot full topic recluster (first-ever backlog run). Measure via ef/clustering-latest.json; make recurring only after the first run is measured." | Out-Null
Write-Output "registered $TaskName once at 2026-09-20 02:00 (pythonw, 6h limit)"
