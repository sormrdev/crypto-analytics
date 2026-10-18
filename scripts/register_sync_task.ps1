# Registers a Windows scheduled task that runs sync_local.ps1 every Sunday at 18:00 while you are logged in.
# Weekly fits inside the 7.5 days Kraken's OHLC endpoint covers; a run missed because the computer was asleep
# or off starts as soon as it is available again, and a pause past 7.5 days needs `crypto backfill`.
#   powershell -ExecutionPolicy Bypass -File scripts\register_sync_task.ps1             # create or update
#   Unregister-ScheduledTask -TaskName "CryptoAnalytics sync" -Confirm:$false           # remove

$script = Join-Path $PSScriptRoot "sync_local.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "18:00"
# Without a UTC offset the time stays 18:00 local across daylight-saving changes.
$trigger.StartBoundary = (Get-Date "18:00").ToString("yyyy-MM-ddTHH:mm:ss")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "CryptoAnalytics sync" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Latest Kraken candles + dbt build for the Crypto Analytics project (logs\sync.log)." -Force |
    Select-Object TaskName, State
