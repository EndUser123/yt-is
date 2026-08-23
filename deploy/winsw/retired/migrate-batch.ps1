# Run ELEVATED. BATCH migration: remaining 5 services (pilot search_wiki done).
# Order: independent leaves first, then ef_qdrant, then its dependents.
$NSSM = 'C:\Users\brsth\AppData\Local\Microsoft\WinGet\Packages\NSSM.NSSM_Microsoft_Winget.Source_8wekyb3d8bbwe\nssm-2.24-101-g897c7ad\win64\nssm.exe'
$W = 'P:\.data\winsw'

foreach ($svc in @('search_chat','search_web','ef_qdrant','search_ef','ef_warm_query')) {
    & $NSSM dump $svc > "P:/.data/logs/nssm/predump-$svc.txt" 2>$null
    & $NSSM stop $svc 2>$null
    & $NSSM remove $svc confirm 2>$null
    & "$W\$svc.exe" install
    Start-Sleep -Seconds 2
    Start-Service $svc
    Start-Sleep -Seconds 3
    $s = Get-Service $svc
    Write-Output ("$svc : " + $s.Status)
}
# qdrant needs a beat before dependents were started; restart dependents to be safe
Restart-Service search_ef -ErrorAction SilentlyContinue
Restart-Service ef_warm_query -ErrorAction SilentlyContinue
Write-Output 'batch complete'
