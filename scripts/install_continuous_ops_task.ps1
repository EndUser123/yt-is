<#
.SYNOPSIS
Registers the yt-is continuous-operations heartbeat task (default mode).

Runs scripts/run_continuous_ops.py every 15 minutes under the interactive
token (S4U registration is denied on this host — see
20260812_scheduler_s4u_registration_recheck_run01). The tick is idempotent
and lock-guarded; every step is independently skip-safe.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonExecutable = 'C:\Python314\python.exe',
    [string]$TaskName = 'YtisContinuousOps',
    [int]$IntervalMinutes = 15,
    [string]$DbPath = 'P:/.data/yt-is/batch_status.sqlite',
    [string]$StatePath = 'P:/.data/yt-is/unattended-backlog/state.json',
    [double]$MinScore = 1.2,
    [switch]$Inspect
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}
$driver = 'P:/packages/yt-is/scripts/run_continuous_ops.py'
if (-not (Test-Path -LiteralPath $driver -PathType Leaf)) {
    throw "Driver script not found: $driver"
}

if ($Inspect) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Get-ScheduledTaskInfo
    return
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExecutable `
    -Argument ('"{0}" --db-path "{1}" --state-path "{2}" --min-score {3}' -f $driver, $DbPath, $StatePath, $MinScore) `
    -WorkingDirectory 'P:\packages\yt-is'

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval ([TimeSpan]::FromMinutes($IntervalMinutes))

# Interactive token: the task runs only while the operator is logged on,
# which is the only registration mode Windows permits on this host.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit ([TimeSpan]::FromMinutes(10)) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "Registered $TaskName every $IntervalMinutes minutes (interactive token)."
