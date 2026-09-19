# Registers the daily yt-is state backup task (channel-state + transcripts DBs).
# Pattern follows scripts/install_nlm_keepalive_task.ps1. Re-running is idempotent:
# an existing task with the same name is removed first.
#
# The one-fetch principle concentrates months of work into two SQLite files;
# this task is what makes that concentration safe. Fail-open on absence of the
# bins (package moved) — a missing backup tool should not crash the scheduler.

$TaskName = "YtisStateBackup"
$Action = "C:\Python314\python.exe"
$Root = "P:\packages\yt-is"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed existing $TaskName task"
}

$chain = @"
import subprocess, sys
from pathlib import Path

for script in [r"$Root\bin\csf-backup-channel-state", r"$Root\bin\csf-backup-transcripts"]:
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600)
        print(r.stdout.strip() or r.stderr.strip())
    except Exception as exc:
        print(f"backup failed: {script}: {exc}")

# Off-site tier: G: USB drive (configured by scripts/backup_ytis_cold.py at 03:35).
# No C: copy here -- operator directive: never copy to C: without explicit permission.
# The YtisColdBackup scheduled task (03:35) handles the G: tier of the backup ladder.

# Retention: keep last 3 .sqlite on P: per pattern; older snapshots are redundant
# once YtisColdBackup (03:35) has mirrored the newest 3 to G: (KEEP_DEST=3 there;
# 7 -> 3 on both tiers 2026-09-18, operator-ratified, G: at 100%).
src = Path(r"P:\.data\yt-is\backups")
for pattern in ["batch-status-*.sqlite", "transcripts-*.sqlite"]:
    keep = sorted(src.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
    keep_names = {f.name for f in keep}
    for old_file in src.glob(pattern):
        if old_file.name not in keep_names:
            old_file.unlink()
            print(f"pruned: {old_file.name}")
"@

$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($chain))
$act = New-ScheduledTaskAction -Execute $Action -Argument "-c `"import base64;exec(base64.b64decode('$encoded').decode('utf-16-le'))`""
$trigger = New-ScheduledTaskTrigger -Daily -At "03:30"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trigger -Settings $settings -Description "Daily backup of yt-is channel-state and transcripts SQLite DBs" | Out-Null
Write-Output "registered $TaskName daily at 03:30"
