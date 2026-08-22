$env:MCP_HTTP_PORT = '8324'
$env:PYTHONUNBUFFERED = '1'
Set-Location 'P:/packages/yt-is'
& 'C:\Python314\python.exe' '-m' 'ef.mcp_server' *>> 'P:/.data/logs/nssm/search-ef-mcp.all.log'
