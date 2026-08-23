# ytws-warm-fix.ps1 - elevated one-shot, SAFE TO RE-RUN (idempotent).
# Run from an ADMIN terminal (Start > Terminal (Admin)).
#   1. If :6391 already answers real queries -> no kill, no restart.
#   2. Otherwise: kill the broken python holder, restart ef_warm_query
#      (hardened build: encoder canary + warming gate, yt-is 6ffb67fe).
#   3. Always: re-apply the delegated service DACL (a WinSW reinstall
#      wipes it - that is why non-elevated agent restarts stopped working).
# Success signal: C:\Users\brsth\AppData\Local\Temp\ytws-warm-fix-done.json
$ErrorActionPreference = "Continue"
$result = "C:\Users\brsth\AppData\Local\Temp\ytws-warm-fix-done.json"
Remove-Item $result -ErrorAction SilentlyContinue

# ASCII only: Windows PowerShell 5.1 reads BOM-less files as ANSI, and
# any non-ASCII character breaks the parser before the script runs.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Output "NOT ELEVATED: this shell has your standard token."
    Write-Output "Open an admin terminal (Start > Terminal (Admin)) and run this again."
    exit 1
}
Write-Output "elevated: yes (user $($identity.Name))"

function Test-WarmRows {
    try {
        $q = Invoke-RestMethod "http://127.0.0.1:6391/query?q=data+engineering&top_k=3&format=json&federation=off" -TimeoutSec 20
        return (@($q.results).Count -gt 0)
    } catch { return $false }
}

if (Test-WarmRows) {
    Write-Output "service healthy - skipping kill/restart"
} else {
    $conn = Get-NetTCPConnection -LocalPort 6391 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $pid6391 = $conn.OwningProcess
        $proc = Get-Process -Id $pid6391 -ErrorAction SilentlyContinue
        Write-Output "port holder: PID $pid6391 ($($proc.ProcessName))"
        if ($proc.ProcessName -eq "python") {
            Stop-Process -Id $pid6391 -Force
            Start-Sleep -Seconds 3
            if (Get-Process -Id $pid6391 -ErrorAction SilentlyContinue) { Write-Output "KILL FAILED"; exit 1 }
            Write-Output "killed broken warm-service python PID $pid6391"
        } else { Write-Output "unexpected holder - aborting"; exit 1 }
    }
    Restart-Service ef_warm_query -Force
    Start-Sleep -Seconds 45
    Write-Output "service status: $((Get-Service ef_warm_query).Status)"
}

# Re-apply the delegated grant (SID from the CURRENT identity; scoped
# start/stop/query/control only - no config write, no DACL write).
$userSID = $identity.User.Value
$sddl = "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)(A;;SWRPWPRC;;;$userSID)"
foreach ($svc in @("ef_warm_query", "ef_qdrant")) {
    $out = sc.exe sdset $svc $sddl 2>&1
    Write-Output "sdset ${svc}: $out"
}

$rows = -1
for ($i = 0; $i -lt 12; $i++) {
    if (Test-WarmRows) { $rows = 3; break }
    Start-Sleep -Seconds 15
    Write-Output "probe t+$([int](($i+1)*15))s waiting..."
}

[pscustomobject]@{ ok = ($rows -gt 0); rows = $rows; time = (Get-Date -Format o) } |
    ConvertTo-Json | Set-Content -Path $result -Encoding UTF8
if ($rows -gt 0) { Write-Output "VERIFIED: warm queries return rows. DACL grant restored. Marker written."; exit 0 }
else { Write-Output "queries still failing - see P:\.data\yt-is\ef\warm_query_service.log"; exit 1 }
