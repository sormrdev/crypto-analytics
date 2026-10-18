# Registers a Windows scheduled task that runs sync_local.ps1 every 4 hours while you are logged in.
# Missed runs (computer asleep or off) start as soon as it is available again.
#   powershell -ExecutionPolicy Bypass -File scripts\register_sync_task.ps1             # create or update
#   Unregister-ScheduledTask -TaskName "CryptoAnalytics sync" -Confirm:$false           # remove

$script = Join-Path $PSScriptRoot "sync_local.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 4)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "CryptoAnalytics sync" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Latest Kraken candles + dbt build for the Crypto Analytics project (logs\sync.log)." -Force |
    Select-Object TaskName, State
