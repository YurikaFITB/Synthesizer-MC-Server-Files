<#
    backup_server.ps1

    Zips the server folder to a local backup drive, with Discord warnings
    5 minutes and 1 minute before the backup actually happens (so players
    have a chance to log off / stop building near anything fragile), then
    a completion webhook once it's done. Also rotates old backups.

    SCHEDULING (Windows Task Scheduler):
      - Trigger: repeat every 2 hours, indefinitely.
      - Action:  powershell.exe -ExecutionPolicy Bypass -File "C:\path\to\backup_server.ps1"
      - IMPORTANT: this script waits 5 minutes before it actually zips
        anything (that's the warning window below). So the real backup
        happens 5 minutes after each trigger fires — factor that in if you
        care about exact timing.

    Bedrock/Endstone has no RCON, so this can't pause world saving before
    zipping. Scheduling backups during low-activity hours, plus the warning
    pings below, are the mitigation for that.
#>

param(
    # Folder containing your Bedrock server (bedrock_server.exe, worlds/, etc.)
    [string]$ServerRoot = "O:\Server\Endstone\bedrock_server",

    # Where zipped backups get saved (any local drive)
    [string]$BackupRoot = "O:\BACKUP\MC Backups",

    # Discord webhook used for backup warnings + completion messages
    [string]$DiscordWebhookUrl = "https://discord.com/api/webhooks/https://discord.com/api/webhooks/1532035806531813456/jfiG9G8P6rdrHlTsdnm7c-o3yK-F_f9T54kabIBVjahSyJHevaM3OQulEqsxiDSUH0CY",

    # How many recent backups to keep before deleting the oldest
    [int]$KeepBackups = 48
)

function Send-DiscordMessage {
    param([string]$Content)
    try {
        $body = @{ content = $Content } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri $DiscordWebhookUrl -Method Post -Body $body `
            -ContentType "application/json; charset=utf-8" -UserAgent "Mozilla/5.0" | Out-Null
    } catch {
        Write-Host "Discord webhook failed: $_"
    }
}

# ----------------------------------------------------------------------
# Warning phase — timer starts here, only when a backup cycle kicks off
# ----------------------------------------------------------------------
Write-Host "Backup cycle starting. Sending 5-minute warning..."
Send-DiscordMessage "⚠️ **Backup Notice:** Synthesizer will be backed up in **5 minutes**. Brief lag may occur."
Start-Sleep -Seconds 240   # 4 minutes, leaving 1 minute for the next warning

Write-Host "Sending 1-minute warning..."
Send-DiscordMessage "⚠️ **Backup Notice:** Synthesizer will be backed up in **1 minute**."
Start-Sleep -Seconds 60

# ----------------------------------------------------------------------
# Backup phase
# ----------------------------------------------------------------------
$timestamp  = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$backupName = "Synthesizer_Backup_$timestamp.zip"
$backupPath = Join-Path $BackupRoot $backupName

if (-not (Test-Path $BackupRoot)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
}

Write-Host "Starting backup: $backupPath"
$startTime = Get-Date

try {
    Compress-Archive -Path (Join-Path $ServerRoot "*") -DestinationPath $backupPath -CompressionLevel Optimal -Force

    $duration = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
    $sizeMB   = [math]::Round((Get-Item $backupPath).Length / 1MB, 1)

    Write-Host "Backup complete: $backupPath ($sizeMB MB, ${duration}s)"
    Send-DiscordMessage "✅ **Backup Complete:** ``$backupName`` saved ($sizeMB MB) in ${duration}s."
} catch {
    Write-Host "Backup FAILED: $_"
    Send-DiscordMessage "🔴 **Backup FAILED:** $_"
    exit 1
}

# ----------------------------------------------------------------------
# Rotation — keep only the newest $KeepBackups zip files
# ----------------------------------------------------------------------
$allBackups = Get-ChildItem -Path $BackupRoot -Filter "Synthesizer_Backup_*.zip" | Sort-Object LastWriteTime -Descending
if ($allBackups.Count -gt $KeepBackups) {
    $toDelete = $allBackups | Select-Object -Skip $KeepBackups
    foreach ($file in $toDelete) {
        Write-Host "Rotating out old backup: $($file.Name)"
        Remove-Item $file.FullName -Force
    }
}

Write-Host "Backup cycle finished."