# --- BTC agent launcher (robust paths) ---
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
Set-Location $Root

. "$Root\.venv\Scripts\Activate.ps1"

$env:STATE_DIR = (Resolve-Path "$Root\state").Path
$env:PYTHONUNBUFFERED = "1"

$log = Join-Path $Root "state\agent.log"
Write-Host "Starting agent... logging to $log"

# Run the agent and tee output to log (append)
python -u -m app.runner *>&1 | Tee-Object -FilePath $log -Append
