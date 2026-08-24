$p = Get-Process DiscordHistoryTracker -ErrorAction SilentlyContinue
if ($p) {
    $p.CloseMainWindow() | Out-Null
    Start-Sleep -Seconds 8
    if (-not $p.HasExited) {
        Stop-Process -Id $p.Id -Force
        Write-Output "force-killed $($p.Id)"
    } else {
        Write-Output "closed gracefully"
    }
} else {
    Write-Output "not running"
}
