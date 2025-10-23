cd C:\Users\alvin\projects\btc-trading-agent

# 1) Refresh equity curve & overlay
python .\scripts\baseline_overlay.py

# 2) Send weekly email now (or write preview if SMTP missing)
python -m app.voice_email --send-now

# 3) Open local artifacts
if (Test-Path .\state\weekly_report_preview.html) { Start-Process .\state\weekly_report_preview.html }
if (Test-Path .\state\baseline_compare.png)      { Start-Process .\state\baseline_compare.png }
