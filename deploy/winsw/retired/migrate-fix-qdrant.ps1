# Run ELEVATED. Emergency: clear ef_qdrant service limbo, bring EF trio back.
$W = 'P:\.data\winsw'

# Stop any running qdrant process (orphan of a possibly-deleted service is fine to kill;
# qdrant is disk-backed and restart-safe)
sc.exe stop ef_qdrant 2>$null | Out-Null
Start-Sleep -Seconds 4
Get-Process qdrant -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# Delete whatever service registration remains (no-op if already gone)
sc.exe delete ef_qdrant 2>$null | Out-Null
Start-Sleep -Seconds 3

# If still present (marked-for-deletion can linger while a handle exists), report it
$still = Get-Service ef_qdrant -ErrorAction SilentlyContinue
if ($still) {
    Write-Output ("ef_qdrant registration still present: " + $still.Status + " (marked for deletion — handles pending)")
}

# Fresh WinSW install and start
& "$W\ef_qdrant.exe" install
Start-Sleep -Seconds 2
Start-Service ef_qdrant
Start-Sleep -Seconds 6
$pn = (Get-CimInstance Win32_Service -Filter "Name='ef_qdrant'" -ErrorAction SilentlyContinue).PathName
Write-Output ("ef_qdrant: " + (Get-Service ef_qdrant -ErrorAction SilentlyContinue).Status + " : " + $pn)

# Start the dependents (their WinSW configs are already installed)
Start-Service search_ef
Start-Service ef_warm_query
Start-Sleep -Seconds 6
Write-Output ("search_ef: " + (Get-Service search_ef -ErrorAction SilentlyContinue).Status)
Write-Output ("ef_warm_query: " + (Get-Service ef_warm_query -ErrorAction SilentlyContinue).Status)
Write-Output '--- all six ---'
Get-Service search_wiki,search_chat,search_web,search_ef,ef_qdrant,ef_warm_query -ErrorAction SilentlyContinue |
    ForEach-Object { "$($_.Name) : $($_.Status)" }
