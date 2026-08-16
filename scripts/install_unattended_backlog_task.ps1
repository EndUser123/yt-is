[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonExecutable = 'C:\Python314\python.exe',
    [string]$TaskName = 'YtisUnattendedBacklog',
    [DateTime]$At = (Get-Date -Hour 4 -Minute 0 -Second 0),
    [string]$DbPath = 'P:/.data/yt-is/batch_status.sqlite',
    [string]$TranscriptCacheDbPath = 'P:/.data/yt-is/transcripts.sqlite',
    [string]$StatePath = 'P:/.data/yt-is/unattended-backlog/state.json',
    [string]$OutputRoot = 'P:/packages/yt-is/.logs/multi_account_fetch/unattended',
    [string]$AccountSettingsPath = '',
    [string]$FullBacklogAuthorizationPath = '',
    [ValidateSet('Interactive', 'S4U', 'Password')]
    [string]$LogonType = 'S4U',
    [string]$UserId = '',
    [System.Management.Automation.PSCredential]$Credential,
    [int]$ChunkSize = 400,
    [int]$WorkersPerAccount = 3,
    [int]$MaxChunks = 1,
    [switch]$RouteNoCaptionsToFallback,
    [switch]$RouteIndustrialFailuresToFallback,
    [switch]$RouteSourceAddFailuresToFallback,
    [switch]$RouteSourceAddressabilityFailuresToFallback,
    [switch]$AdaptiveWorkers,
    [int]$AdaptiveMinWorkers = 1,
    [int]$AdaptiveMaxWorkers = 0,
    [int]$AdaptiveScaleUpBacklog = 2,
    [int]$AdaptiveScaleDownBacklog = 0,
    [double]$AdaptiveCooldownS = 60.0,
    [int]$AdaptiveHealthWindow = 2,
    [switch]$Execute,
    [switch]$UntilEmpty,
    [switch]$Inspect
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}
if ($ChunkSize -lt 1 -or $WorkersPerAccount -lt 1 -or $MaxChunks -lt 1) {
    throw 'ChunkSize, WorkersPerAccount, and MaxChunks must be positive.'
}
if ($AdaptiveWorkers) {
    if ($AdaptiveMinWorkers -lt 1 -or $AdaptiveMaxWorkers -lt $AdaptiveMinWorkers) {
        throw 'AdaptiveMinWorkers must be positive and AdaptiveMaxWorkers must be >= AdaptiveMinWorkers.'
    }
    if ($AdaptiveScaleUpBacklog -lt 0 -or $AdaptiveScaleDownBacklog -lt 0 -or $AdaptiveCooldownS -lt 0 -or $AdaptiveHealthWindow -lt 1) {
        throw 'Adaptive backlog thresholds/cooldown must be non-negative and AdaptiveHealthWindow must be positive.'
    }
}
if ($UntilEmpty -and -not $Execute) {
    throw '-UntilEmpty requires -Execute.'
}
if ($UntilEmpty -and -not $AccountSettingsPath) {
    throw '-UntilEmpty requires -AccountSettingsPath.'
}
if ($UntilEmpty -and -not $FullBacklogAuthorizationPath) {
    throw '-UntilEmpty requires -FullBacklogAuthorizationPath.'
}
if (-not $UserId) {
    $UserId = "$env:USERDOMAIN\$env:USERNAME"
}
if ($LogonType -eq 'Password' -and $null -eq $Credential) {
    throw '-LogonType Password requires -Credential; do not put a password in the task arguments.'
}
if ($LogonType -eq 'Password' -and [string]::IsNullOrWhiteSpace($Credential.UserName)) {
    throw '-Credential must contain a non-empty user name.'
}
$principalUserId = if ($LogonType -eq 'Password') { $Credential.UserName } else { $UserId }

function Assert-ReadableFile {
    param(
        [string]$Path,
        [string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label file not found: $Path"
    }
    try {
        $stream = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
        $stream.Dispose()
    } catch {
        throw "$Label file is not readable: $Path ($($_.Exception.Message))"
    }
}

function Assert-ParentDirectory {
    param(
        [string]$Path,
        [string]$Label
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $fullPath
    if ([string]::IsNullOrWhiteSpace($parent)) {
        $parent = (Get-Location).Path
    }
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "$Label parent directory not found: $parent"
    }
}

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$supervisor = Join-Path $packageRoot 'scripts/run_unattended_backlog.py'
$arguments = @(
    "`"$supervisor`"",
    '--db-path', "`"$DbPath`"",
    '--transcript-cache-db-path', "`"$TranscriptCacheDbPath`"",
    '--state-path', "`"$StatePath`"",
    '--output-root', "`"$OutputRoot`"",
    '--chunk-size', $ChunkSize,
    '--workers-per-account', $WorkersPerAccount,
    '--max-chunks', $MaxChunks
)
if ($AccountSettingsPath) {
    $arguments += @('--account-settings', "`"$AccountSettingsPath`"")
}
if ($FullBacklogAuthorizationPath) {
    $arguments += @('--full-backlog-authorization', "`"$FullBacklogAuthorizationPath`"")
}
if ($RouteNoCaptionsToFallback) {
    $arguments += '--route-no-captions-to-fallback'
}
if ($RouteIndustrialFailuresToFallback) {
    $arguments += '--route-industrial-failures-to-fallback'
}
if ($RouteSourceAddFailuresToFallback) {
    $arguments += '--route-source-add-failures-to-fallback'
}
if ($RouteSourceAddressabilityFailuresToFallback) {
    $arguments += '--route-source-addressability-failures-to-fallback'
}
if ($AdaptiveWorkers) {
    $arguments += @(
        '--adaptive-workers',
        '--adaptive-min-workers', $AdaptiveMinWorkers,
        '--adaptive-max-workers', $AdaptiveMaxWorkers,
        '--adaptive-scale-up-backlog', $AdaptiveScaleUpBacklog,
        '--adaptive-scale-down-backlog', $AdaptiveScaleDownBacklog,
        '--adaptive-cooldown-s', $AdaptiveCooldownS,
        '--adaptive-health-window', $AdaptiveHealthWindow
    )
}
if ($Execute) {
    $arguments += '--execute'
}
if ($UntilEmpty) {
    $arguments += '--until-empty'
}
$argumentString = $arguments -join ' '

function Test-YtisBacklogTask {
    param(
        [string]$Name,
        [string]$ExpectedArguments,
        [string]$ExpectedPython,
        [string]$ExpectedWorkingDirectory,
        [string]$ExpectedUserId,
        [string]$ExpectedLogonType,
        [string]$ExpectedRunLevel = 'LeastPrivilege'
    )
    $taskXml = [xml](Export-ScheduledTask -TaskName $Name)
    $settings = $taskXml.SelectSingleNode("//*[local-name()='Settings']")
    $expectedSettings = @{
        DisallowStartIfOnBatteries = 'false'
        StopIfGoingOnBatteries      = 'false'
        MultipleInstancesPolicy     = 'IgnoreNew'
        StartWhenAvailable           = 'true'
    }
    foreach ($name in $expectedSettings.Keys) {
        $value = $settings.SelectSingleNode("*[local-name()='$name']")
        if ($null -eq $value -or $value.InnerText -ne $expectedSettings[$name]) {
            throw "Task Scheduler verification failed for $name"
        }
    }
    $exec = $taskXml.SelectSingleNode("//*[local-name()='Actions']/*[local-name()='Exec']")
    if ($null -eq $exec) { throw 'Task Scheduler verification failed: no Exec action' }
    if ($exec.SelectSingleNode("*[local-name()='Command']").InnerText -ne $ExpectedPython) {
        throw 'Task Scheduler verification failed for Python executable'
    }
    if ($exec.SelectSingleNode("*[local-name()='WorkingDirectory']").InnerText -ne $ExpectedWorkingDirectory) {
        throw 'Task Scheduler verification failed for working directory'
    }
    if ($exec.SelectSingleNode("*[local-name()='Arguments']").InnerText -ne $ExpectedArguments) {
        throw 'Task Scheduler verification failed for arguments'
    }
    $principal = $taskXml.SelectSingleNode("//*[local-name()='Principals']/*[local-name()='Principal']")
    if ($null -eq $principal) { throw 'Task Scheduler verification failed: no Principal' }
    $userNode = $principal.SelectSingleNode("*[local-name()='UserId']")
    $logonNode = $principal.SelectSingleNode("*[local-name()='LogonType']")
    $runLevelNode = $principal.SelectSingleNode("*[local-name()='RunLevel']")
    $expectedXmlLogonType = if ($ExpectedLogonType -eq 'Interactive') { 'InteractiveToken' } else { $ExpectedLogonType }
    $expectedPrincipalId = $ExpectedUserId
    if ($ExpectedUserId -notmatch '^S-\d-\d+') {
        try {
            $expectedPrincipalId = (
                New-Object System.Security.Principal.NTAccount($ExpectedUserId)
            ).Translate([System.Security.Principal.SecurityIdentifier]).Value
        } catch {
            # Keep the original spelling for environments where the account
            # cannot be resolved locally; the comparison remains exact.
            $expectedPrincipalId = $ExpectedUserId
        }
    }
    if ($null -eq $userNode -or $userNode.InnerText -ne $expectedPrincipalId) {
        throw 'Task Scheduler verification failed for principal user'
    }
    if ($null -eq $logonNode -or $logonNode.InnerText -ne $expectedXmlLogonType) {
        throw "Task Scheduler verification failed for logon type: expected $expectedXmlLogonType"
    }
    # Task Scheduler omits the RunLevel element when the task uses the
    # default least-privilege level. Treat the omitted default as equivalent
    # to an explicit LeastPrivilege value.
    $actualRunLevel = if ($null -eq $runLevelNode -or [string]::IsNullOrWhiteSpace($runLevelNode.InnerText)) {
        'LeastPrivilege'
    } else {
        $runLevelNode.InnerText
    }
    if ($actualRunLevel -ne $ExpectedRunLevel) {
        throw 'Task Scheduler verification failed for run level'
    }
    $executionLimit = $settings.SelectSingleNode("*[local-name()='ExecutionTimeLimit']")
    if ($null -eq $executionLimit -or $executionLimit.InnerText -ne 'PT23H') {
        throw 'Task Scheduler verification failed for execution limit'
    }
}

if ($Inspect) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    Test-YtisBacklogTask -Name $TaskName -ExpectedArguments $argumentString -ExpectedPython $PythonExecutable -ExpectedWorkingDirectory $packageRoot -ExpectedUserId $principalUserId -ExpectedLogonType $LogonType
    Write-Output "Verified $TaskName"
    exit 0
}

Assert-ReadableFile -Path $DbPath -Label 'Database'
Assert-ReadableFile -Path $TranscriptCacheDbPath -Label 'Transcript cache database'
Assert-ParentDirectory -Path $StatePath -Label 'State path'
Assert-ParentDirectory -Path $OutputRoot -Label 'Output root'
Assert-ReadableFile -Path $AccountSettingsPath -Label 'Account settings'
Assert-ReadableFile -Path $FullBacklogAuthorizationPath -Label 'Full-backlog authorization'
if (-not (Test-Path -LiteralPath $supervisor -PathType Leaf)) {
    throw "Supervisor script not found: $supervisor"
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Register unattended yt-is backlog task')) {
    $action = New-ScheduledTaskAction -Execute $PythonExecutable -Argument $argumentString -WorkingDirectory $packageRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 23)
    $principal = New-ScheduledTaskPrincipal -UserId $principalUserId -LogonType $LogonType -RunLevel Limited
    $registration = @{
        TaskName = $TaskName
        Action = $action
        Trigger = $trigger
        Settings = $settings
        Description = 'Bounded yt-is unattended backlog supervisor. See docs/operations/unattended-backlog-operation.md'
        Force = $true
    }
    if ($LogonType -eq 'Password') {
        Register-ScheduledTask @registration -User $Credential.UserName -Password $Credential.GetNetworkCredential().Password | Out-Null
    } else {
        Register-ScheduledTask @registration -Principal $principal | Out-Null
    }
    $expectedTaskArguments = $argumentString
    Test-YtisBacklogTask -Name $TaskName -ExpectedArguments $expectedTaskArguments -ExpectedPython $PythonExecutable -ExpectedWorkingDirectory $packageRoot -ExpectedUserId $principalUserId -ExpectedLogonType $LogonType
    Write-Output "Registered and verified $TaskName at $($At.ToString('HH:mm')); UserId=$principalUserId LogonType=$LogonType Execute=$Execute UntilEmpty=$UntilEmpty RouteNoCaptionsToFallback=$RouteNoCaptionsToFallback RouteIndustrialFailuresToFallback=$RouteIndustrialFailuresToFallback RouteSourceAddFailuresToFallback=$RouteSourceAddFailuresToFallback RouteSourceAddressabilityFailuresToFallback=$RouteSourceAddressabilityFailuresToFallback AdaptiveWorkers=$AdaptiveWorkers"
} else {
    Write-Output "WhatIf: would register $TaskName with bounded arguments: $argumentString"
}
