# Run ELEVATED. Final step: swap ef_qdrant to WinSW (dependents-first delete),
# then start the EF trio. Output also teed to C:\temp log (P: drive safety).
Start-Transcript -Path 'C:\Users\brsth\AppData\Local\Temp\winsw-fix2.log' -Force
$W = 'P:\.data\winsw'

# 1. Stop the two dependents cleanly (they are already stopped; no-op safe)
foreach ($d in @('search_ef','ef_warm_query')) { sc.exe stop $d | Out-Null }
Start-Sleep -Seconds 2

# 2. Stop ef_qdrant (NSSM) and kill the process if SCM stop hangs
sc.exe stop ef_qdrant | Out-Null
Start-Sleep -Seconds 5
Get-Process qdrant -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 3. Delete NSSM ef_qdrant (dependents are stopped; delete may still mark — retry)
sc.exe delete ef_qdrant | Out-Null
Start-Sleep -Seconds 3
$gone = -not (Get-Service ef_qdrant -ErrorAction SilentlyContinue)
if (-not $gone) {
    # second attempt after handle release
    sc.exe delete ef_qdrant | Out-Null
    Start-Sleep -Seconds 3
    $gone = -not (Get-Service ef_qdrant -ErrorAction SilentlyContinue)
}
Write-Output ("ef_qdrant old registration gone: " + $gone)

# 4. Install + start WinSW ef_qdrant
if ($gone) {
    & "$W\ef_qdrant.exe" install
    Start-Sleep -Seconds 2
    Start-Service ef_qdrant
    Start-Sleep -Seconds 6
}
$pn = (Get-CimInstance Win32_Service -Filter "Name='ef_qdrant'" -ErrorAction SilentlyContinue).PathName
Write-Output ("ef_qdrant: " + (Get-Service ef_qdrant -ErrorAction SilentlyContinue).Status + " : " + $pn)

# 5. Start dependents
Start-Service search_ef -ErrorAction SilentlyContinue
Start-Service ef_warm_query -ErrorAction SilentlyContinue
Start-Sleep -Seconds 8
Write-Output ("search_ef: " + (Get-Service search_ef -ErrorAction SilentlyContinue).Status)
Write-Output ("ef_warm_query: " + (Get-Service ef_warm_query -ErrorAction SilentlyContinue).Status)
Write-Output '--- all six ---'
Get-Service search_wiki,search_chat,search_web,search_ef,ef_qdrant,ef_warm_query -ErrorAction SilentlyContinue |
    ForEach-Object { "$($_.Name) : $($_.Status)" }
Stop-Transcript | Out-Null
