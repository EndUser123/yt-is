# Run ELEVATED. Finisher: migrate the 5 remaining services to WinSW via sc.exe
# (no NSSM dependency). Dependent-first order: ef dependents before ef_qdrant.
# Rollback record: install-services-admin.ps1 (committed) + registry read directly.
$W = 'P:\.data\winsw'
$results = @()

foreach ($svc in @('search_chat','search_web','search_ef','ef_warm_query','ef_qdrant')) {
    # Archive the live registry config (Parameters) before removal
    reg export "HKLM\SYSTEM\CurrentControlSet\Services\$svc" "P:\.data\logs\nssm\predump-$svc.reg" /y | Out-Null

    sc.exe stop $svc | Out-Null
    Start-Sleep -Seconds 3
    sc.exe delete $svc | Out-Null
    Start-Sleep -Seconds 2

    & "$W\$svc.exe" install | Out-Null
    Start-Sleep -Seconds 2
    Start-Service $svc
    Start-Sleep -Seconds 4

    $pn = (Get-CimInstance Win32_Service -Filter "Name='$svc'").PathName
    $st = (Get-Service $svc).Status
    $owner = if ($pn -match 'winsw') { 'WinSW' } else { 'FAIL:' + $pn }
    $results += "$svc : $st : $owner"
}

# qdrant may have restarted after its dependents' installs; bounce dependents
foreach ($svc in @('search_ef','ef_warm_query')) {
    Restart-Service $svc -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5
Write-Output '--- migration results ---'
$results
Write-Output '--- post-restart states ---'
Get-Service search_wiki,search_chat,search_web,search_ef,ef_qdrant,ef_warm_query |
    ForEach-Object { "$($_.Name) : $($_.Status)" }
