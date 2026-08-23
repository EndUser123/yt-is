# Run ELEVATED. Kill the old ef_incremental daemon, clean up, restart via task.
Start-Transcript -Path 'C:\Users\brsth\AppData\Local\Temp\winsw-daemon.log' -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -match 'ef_incremental_service'
} | ForEach-Object {
    Write-Output ("killing daemon PID " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force
}
Start-Sleep -Seconds 3
Remove-Item 'P:\.data\yt-is\ef\incremental-service.pid' -Force -ErrorAction SilentlyContinue
Remove-Item 'P:\packages\yt-is\.data' -Recurse -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName 'YtisIndexIncremental'
Start-Sleep -Seconds 10
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -match 'ef_incremental_service'
} | ForEach-Object { Write-Output ("new daemon PID " + $_.ProcessId) }
if (Test-Path 'P:\.data\yt-is\ef\incremental-service.pid') {
    Write-Output ("PID file: " + (Get-Content 'P:\.data\yt-is\ef\incremental-service.pid'))
} else {
    Write-Output 'PID file NOT created'
}
Stop-Transcript | Out-Null
