# Keeps the local database current: fetches the latest candles of tracked pairs and rebuilds the dbt
# reporting tables. Run by the Windows scheduled task from register_sync_task.ps1, or by hand.
# Kraken's OHLC endpoint covers only the last 7.5 days, so a longer pause leaves a hole for `crypto backfill`.

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$log = Join-Path $root "logs\sync.log"
$env:PYTHONUTF8 = "1"

# cmd handles the redirection: PowerShell 5.1 would turn every stderr line of a native program into an error.
Add-Content $log "`n=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
cmd /c "uv run crypto ohlc sync >> `"$log`" 2>&1"
$sync = $LASTEXITCODE
cmd /c "uv run crypto dbt build >> `"$log`" 2>&1"
$dbt = $LASTEXITCODE
Add-Content $log "=== done: ohlc sync exit $sync, dbt build exit $dbt ==="

# Keep the log small: the last 5000 lines are weeks of runs.
$lines = Get-Content $log
if ($lines.Count -gt 5000) { $lines | Select-Object -Last 5000 | Set-Content $log -Encoding utf8 }

exit [Math]::Max($sync, $dbt)
