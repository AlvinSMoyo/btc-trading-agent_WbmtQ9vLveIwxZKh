# btc-trading-agent
Paper-trading bot for Bitcoin using DCA and LLM strategies.

This project simulates a hybrid (LLM + rules) trading approach and produces human-readable reports. It also includes a **Baseline Comparison** to validate performance against simple strategies.

## 📊 Baseline Comparison (Hybrid vs Hold vs DCA)
Compares your **Hybrid** equity curve against:
- **Buy & Hold** — invest all funds at the first available price  
- **Weekly DCA** — invest a fixed **$500** every 7 days

### How to Run (Windows / PowerShell)
1. Ensure the agent has produced `state/equity_history.csv` (run your trading agent once).
2. Run the baseline script:

        python .\scripts\baseline_quick.py

3. Open the report:

        start .\state\baseline_summary.html
        start .\state\baseline_compare.png

### Outputs
- `state/baseline_compare.png` — equity curves: **Hybrid vs Hold vs DCA**  
- `state/baseline_summary.html` — HTML table with start/end dates, final equity, and relative performance (Hybrid vs Hold/DCA)

## 📦 Requirements
Install dependencies (inside your virtual environment if you use one):

    pip install -r requirements.txt

Minimal contents of `requirements.txt` (for the baseline script):

    pandas==2.3.3
    numpy==2.3.3
    matplotlib==3.10.6
    yfinance==0.2.66
    python-dateutil==2.9.0.post0

> If your equity CSV lacks a price column, the script will automatically fetch BTC prices via `yfinance`.

## 🧪 Troubleshooting
**Error:** `equity_history.csv must have a date/timestamp column`  
Preview the file and adjust if needed:

    Get-Content .\state\equity_history.csv -TotalCount 5

The script auto-detects common names like `date`, `timestamp`, `ts`, `ts_dt`, `datetime`. If yours differs (e.g., `time`), rename the column or extend detection in `scripts/baseline_quick.py`.

**No outputs created**  
Make sure `state/equity_history.csv` exists (run the agent first), then re-run:

    python .\scripts\baseline_quick.py



