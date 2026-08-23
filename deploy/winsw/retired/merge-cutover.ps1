# Run ELEVATED. Merged-model cutover: retire search_ef service, give
# ef_warm_query the MCP_HTTP_PORT=8324 env, restart it.
Start-Transcript -Path 'C:\Users\brsth\AppData\Local\Temp\winsw-merge.log' -Force
$W = 'P:\.data\winsw'

# 1. Retire search_ef (no other service depends on it)
sc.exe stop search_ef | Out-Null
Start-Sleep -Seconds 4
sc.exe delete search_ef | Out-Null
Start-Sleep -Seconds 2
Write-Output ("search_ef retired: " + (-not (Get-Service search_ef -ErrorAction SilentlyContinue)))

# 2. Add MCP_HTTP_PORT=8324 to ef_warm_query.xml (idempotent via regex replace)
$xml = "$W\ef_warm_query.xml"
$content = [IO.File]::ReadAllText($xml)
if ($content -notmatch 'MCP_HTTP_PORT') {
    $content = $content -replace '(<workingdirectory>[^<]+</workingdirectory>)', "`$1`n  <env name=`"MCP_HTTP_PORT`" value=`"8324`"/>"
    [IO.File]::WriteAllText($xml, $content)
    Write-Output 'ef_warm_query.xml: MCP_HTTP_PORT=8324 added'
} else {
    Write-Output 'ef_warm_query.xml: MCP_HTTP_PORT already present'
}

# 3. Restart ef_warm_query (config change needs re-read)
Restart-Service ef_warm_query
Start-Sleep -Seconds 6
Write-Output ("ef_warm_query: " + (Get-Service ef_warm_query).Status)
Write-Output '--- remaining fleet ---'
Get-Service search_wiki,search_chat,search_web,ef_qdrant,ef_warm_query -ErrorAction SilentlyContinue |
    ForEach-Object { "$($_.Name) : $($_.Status)" }
Stop-Transcript | Out-Null
