param(
    [int]$Port = 6391,
    [string]$LogPath = 'P:\.data\yt-is\ef\warm_query_service.log',
    [string]$Python = 'C:\Python314\python.exe'
)

$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $releaseRoot
$env:YTIS_EF_QUERY_PORT = [string]$Port

$logParent = Split-Path -Parent $LogPath
if ($logParent) {
    New-Item -ItemType Directory -Force -Path $logParent | Out-Null
}

& $Python -u -m ef.warm_query_service *>> $LogPath
exit $LASTEXITCODE
