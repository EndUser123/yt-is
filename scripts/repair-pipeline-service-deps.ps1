# repair-pipeline-service-deps.ps1 - elevated one-shot, SAFE TO RE-RUN (idempotent).
# Run from an ADMIN terminal (Start > Terminal (Admin)):
#   powershell -ExecutionPolicy Bypass -File P:\packages\yt-is\scripts\repair-pipeline-service-deps.ps1
#
# Root cause (RCA 2026-08-25): the ytis-pipeline NSSM service runs as
# LocalSystem, but its Python deps (numpy, qdrant_client, mcp, psutil,
# fasteners, notebooklm, typing_extensions) were pip-installed --user and
# live in C:\Users\<user>\AppData\Roaming\Python\Python314\site-packages,
# which LocalSystem cannot see. Every service cycle since install has died
# at `import numpy` (ef/embedding.py) / `import qdrant_client`
# (ef/ingest_connectors.py); index step exit 1, sync steps
# github/YouTube/ef_ingest/topic_assign exit 1.
#
# This script:
#   1. Installs the pinned dep set machine-wide (C:\Python314), matching
#      the versions proven in the user-site install.
#   2. Verifies imports with `python -s` (user-site disabled = the
#      LocalSystem-equivalent visibility probe).
#   3. Re-applies the delegated service DACL grant for ytis-pipeline
#      (start/stop/query for the current user - the AGENTS.md rule for
#      NSSM/LocalSystem services; this service was installed without it).
#   4. Restarts ytis-pipeline and polls pipeline-status.json for the first
#      post-restart cycle verdict.
# Known-remaining (NOT fixed here): the github connector step needs gh CLI
# auth which does not exist under LocalSystem; expect the cycle detail to
# still show github failed while every other step goes green.
#
# Success marker: P:\.data\yt-is\ef\pipeline-service-deps-repair.json
$ErrorActionPreference = "Continue"
$result = "P:\.data\yt-is\ef\pipeline-service-deps-repair.json"
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

$py = "C:\Python314\python.exe"
$deps = @(
    "numpy==2.3.5",
    "qdrant-client==1.19.0",
    "mcp==1.26.0",
    "psutil==7.1.3",
    "fasteners==0.20",
    "notebooklm-py==0.8.0",
    "typing_extensions==4.15.0"
)

function Test-MachineImports {
    # -s disables the per-user site dir: the LocalSystem-equivalent probe.
    try {
        & $py -s -c "import numpy, qdrant_client, mcp, psutil, fasteners, notebooklm, typing_extensions" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (Test-MachineImports) {
    Write-Output "machine imports already resolve - skipping pip install"
} else {
    Write-Output "installing pinned dep set machine-wide..."
    & $py -m pip install @deps
    if ($LASTEXITCODE -ne 0) { Write-Output "PIP INSTALL FAILED"; exit 1 }
}

if (-not (Test-MachineImports)) {
    Write-Output "VERIFICATION FAILED: imports still missing with user-site disabled"
    exit 1
}
Write-Output "VERIFIED: all deps import with user-site disabled (LocalSystem-visible)"

# Re-apply the delegated grant (SID from the CURRENT identity; scoped
# start/stop/query/control only - no config write, no DACL write).
$userSID = $identity.User.Value
$sddl = "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)(A;;SWRPWPRC;;;$userSID)"
$out = sc.exe sdset ytis-pipeline $sddl 2>&1
Write-Output "sdset ytis-pipeline: $out"

sc.exe stop ytis-pipeline | Out-Null
Start-Sleep -Seconds 5
sc.exe start ytis-pipeline | Out-Null
Start-Sleep -Seconds 10
Write-Output "service status: $((Get-Service ytis-pipeline).Status)"

# One full cycle is ~15-25 min when sync passes; poll to 45.
$statusPath = "P:\.data\yt-is\ef\pipeline-status.json"
$deadline = (Get-Date).AddMinutes(45)
$verdict = "timeout-no-new-cycle"
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 60
    if (-not (Test-Path $statusPath)) { continue }
    try { $st = Get-Content $statusPath -Raw | ConvertFrom-Json } catch { continue }
    $ts = [DateTimeOffset]::Parse($st.ts, [Globalization.CultureInfo]::InvariantCulture)
    if ($ts.UtcDateTime -gt [DateTime]::UtcNow.AddMinutes(-2) -and $st.phase -eq "idle") {
        $verdict = "ok=$($st.ok) detail=$($st.detail)"
        break
    }
    Write-Output "waiting for first post-restart idle cycle... (phase=$($st.phase))"
}

$ok = $verdict -like "ok=True*"
[pscustomobject]@{
    ok            = $ok
    machine_imports = $true
    verdict       = $verdict
    time          = (Get-Date -Format o)
} | ConvertTo-Json | Set-Content -Path $result -Encoding UTF8

if ($ok) {
    Write-Output "VERIFIED: first post-restart cycle ok=true. Marker written."; exit 0
}
Write-Output "Cycle not fully green yet. If detail shows only github failed, that is the"
Write-Output "known LocalSystem gh-auth limitation (documented in the script header)."
Write-Output "Verdict: $verdict"
exit 2
