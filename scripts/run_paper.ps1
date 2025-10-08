$ErrorActionPreference = "Stop"
$BASE = "C:\Users\alvin\projects\btc-trading-agent"
Set-Location $BASE

# Console/Python -> UTF-8 + unbuffered
cmd /c chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUNBUFFERED   = "1"
$env:PYTHONIOENCODING   = "utf-8"

# Always use venv python
if (-not (Test-Path .\.venv)) { python -m venv .venv }
$PY = Join-Path $BASE ".\.venv\Scripts\python.exe"
. .\.venv\Scripts\Activate.ps1
if (Test-Path .\requirements.txt) { & $PY -m pip install -r requirements.txt }

# Make app a package (if missing)
if (-not (Test-Path .\app\__init__.py)) { New-Item -ItemType File .\app\__init__.py | Out-Null }

# State + log
$STATE = Join-Path $BASE "state"
New-Item -ItemType Directory -Force -Path $STATE | Out-Null
$LOG = Join-Path $STATE "run.log"
"==== $(Get-Date -Format s) :: paper-live loop start ====" | Out-File $LOG -Append -Encoding utf8

# Optional: mirror Colab-style path to local state via junction (no admin)
$ColabState = "C:\content\drive\MyDrive\btc-trading-agent\state"
$ColabRoot  = Split-Path $ColabState
New-Item -ItemType Directory -Force -Path $ColabRoot | Out-Null
if (-not (Test-Path $ColabState)) { cmd /c mklink /J "$ColabState" "$STATE" | Out-Null }

# Config
$SYMBOL   = "BTC-USD" 
$INTERVAL = 1           # minutes

# Warm-up (single tick)
"[$(Get-Date -Format s)] warmup once..." | Out-File $LOG -Append -Encoding utf8
try {
  & $PY -X utf8 -u -m app.main --once --symbol $SYMBOL --interval-minutes $INTERVAL 2>&1 | Tee-Object -FilePath $LOG -Append
} catch {
  "[$(Get-Date -Format s)] warmup failed: $($_ | Out-String)" | Out-File $LOG -Append -Encoding utf8
}

# Continuous loop with heartbeats
while ($true) {
  "[$(Get-Date -Format s)] loop start..." | Out-File $LOG -Append -Encoding utf8
  try {
    & $PY -X utf8 -u -m app.main --loop --symbol $SYMBOL --interval-minutes $INTERVAL 2>&1 | Tee-Object -FilePath $LOG -Append
  } catch {
    "[$(Get-Date -Format s)] crash: $($_ | Out-String)" | Out-File $LOG -Append -Encoding utf8
  }
  "[$(Get-Date -Format s)] restarting in 10s..." | Out-File $LOG -Append -Encoding utf8
  Start-Sleep -Seconds 10
}



