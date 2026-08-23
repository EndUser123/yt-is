# Run ELEVATED. Deploy both fixes: encoder lock (ef_warm_query) + retry (search_web).
Start-Transcript -Path 'C:\Users\brsth\AppData\Local\Temp\winsw-fixes.log' -Force
Restart-Service ef_warm_query
Restart-Service search_web
Start-Sleep -Seconds 6
Write-Output ("ef_warm_query: " + (Get-Service ef_warm_query).Status)
Write-Output ("search_web: " + (Get-Service search_web).Status)
Write-Output '--- all five ---'
Get-Service search_wiki,search_chat,search_web,ef_qdrant,ef_warm_query -ErrorAction SilentlyContinue |
    ForEach-Object { "$($_.Name) : $($_.Status)" }
Stop-Transcript | Out-Null
