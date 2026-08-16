[CmdletBinding()]
param(
    [string]$PythonExecutable = 'C:\Python314\python.exe',
    [string]$TaskName = 'YtisNlmAuthKeepalive',
    [DateTime]$At = (Get-Date -Hour 3 -Minute 0 -Second 0)
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logPath = 'P:/.data/yt-is/nlm-auth/keepalive.log'
$arguments = '-m csf.nlm_keepalive --log-file "{0}"' -f $logPath

$action = New-ScheduledTaskAction `
    -Execute $PythonExecutable `
    -Argument $arguments `
    -WorkingDirectory $packageRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Daily token-only yt-is NLM auth keepalive. See P:/packages/yt-is/docs/operations/nlm-auth-architecture.md' `
    -Force | Out-Null

$taskXml = [xml](Export-ScheduledTask -TaskName $TaskName)
$node = $taskXml.SelectSingleNode("//*[local-name()='Settings']")
$expectedSettings = @{
    DisallowStartIfOnBatteries = 'false'
    StopIfGoingOnBatteries      = 'false'
    MultipleInstancesPolicy     = 'IgnoreNew'
    StartWhenAvailable           = 'true'
}
foreach ($name in $expectedSettings.Keys) {
    $value = $node.SelectSingleNode("*[local-name()='$name']")
    if ($null -eq $value -or $value.InnerText -ne $expectedSettings[$name]) {
        throw "Task Scheduler verification failed for $name"
    }
}
$idleStop = $node.SelectSingleNode("*[local-name()='IdleSettings']/*[local-name()='StopOnIdleEnd']")
if ($null -eq $idleStop -or $idleStop.InnerText -ne 'false') {
    throw 'Task Scheduler verification failed for StopOnIdleEnd'
}
$exec = $taskXml.SelectSingleNode("//*[local-name()='Actions']/*[local-name()='Exec']")
if ($null -eq $exec) {
    throw 'Task Scheduler verification failed: no Exec action'
}
if ($exec.SelectSingleNode("*[local-name()='Command']").InnerText -ne $PythonExecutable) {
    throw 'Task Scheduler verification failed for Python executable'
}
if ($exec.SelectSingleNode("*[local-name()='WorkingDirectory']").InnerText -ne $packageRoot) {
    throw 'Task Scheduler verification failed for working directory'
}
if ($exec.SelectSingleNode("*[local-name()='Arguments']").InnerText -ne $arguments) {
    throw 'Task Scheduler verification failed for arguments'
}

Write-Output "Registered $TaskName for daily $($At.ToString('HH:mm')) with log $logPath"
Write-Output 'Verified direct action, daily settings, battery behavior, idle behavior, and overlap policy.'
