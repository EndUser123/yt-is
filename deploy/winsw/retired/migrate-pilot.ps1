# Run ELEVATED. PILOT migration: search_wiki only (migrate-one-first pattern).
$NSSM = 'C:\Users\brsth\AppData\Local\Microsoft\WinGet\Packages\NSSM.NSSM_Microsoft.Winget.Source_8wekyb3d8bbwe\nssm-2.24-101-g897c7ad\win64\nssm.exe'
$W = 'P:\.data\winsw'

# Archive the NSSM config for rollback before touching anything
& $NSSM dump search_wiki > 'P:/.data/logs/nssm/predump-search_wiki.txt' 2>$null

# Remove NSSM service
& $NSSM stop search_wiki
& $NSSM remove search_wiki confirm

# Install + start under WinSW (same name, same port)
& "$W\search_wiki.exe" install
Start-Service search_wiki
Start-Sleep -Seconds 4
$s = Get-Service search_wiki
Write-Output ("pilot search_wiki: " + $s.Status)
