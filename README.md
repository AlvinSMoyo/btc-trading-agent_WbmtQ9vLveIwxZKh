<!-- ──────────────────────────────────────────────── -->
<h1 align="center">🪙 BTC Trading Agent</h1>
<p align="center">
  <i>Autonomous Bitcoin trading system with live signal evaluation, LLM-assisted decision logic, and scheduled reporting.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Platform-Ubuntu%2025.04-orange?logo=ubuntu&logoColor=white" alt="Ubuntu 25.04">
  <img src="https://img.shields.io/badge/LLM-GPT--4o%20mini-8A2BE2?logo=openai&logoColor=white" alt="LLM: GPT-4o mini">
  <img src="https://img.shields.io/badge/Status-Live%20Trading-green?logo=bitcoin&logoColor=white" alt="Status: Live Trading">
</p>

---

## 1. Project Overview  

This project delivers an autonomous, 24/7 Bitcoin trading agent engineered for cloud deployment, addressing the challenge of building a **“smart,” self-managing trading system** that operates with minimal human supervision and adapts dynamically to market volatility.  

At its core, the agent uses a **hybrid trading strategy** that blends rule-based execution with LLM-assisted reasoning:  

- **💰 Base Accumulation (DCA):** Implements a **Dollar-Cost Averaging** strategy to steadily accumulate Bitcoin — purchasing small, configurable amounts when prices fall by a set percentage.  
- **⚖️ Dynamic Risk Management:** Employs an **ATR (Average True Range)**-driven stop-loss system that expands or contracts thresholds in line with current volatility, ensuring risk responsiveness.  
- **🧠 Intelligent Decisioning:** Integrates a **lightweight LLM** to enhance situational awareness — analyzing indicators like RSI and ATR, generating reasoning narratives, and filtering actions through a **confidence gate** (`llm_min_confidence ≥ 0.6`).  

The entire system is designed for **robust, headless operation**, maintaining a persistent portfolio state, logging all fills, and autonomously reporting its performance.  
It provides continuous visibility through **real-time Telegram alerts** for trade events and **automated weekly reports** summarizing P&L, equity changes, and asset allocation.  

---

## 2. System Architecture
| Component | Function |
|------------|-----------|
| `app/main.py` | Orchestrates continuous ticks (`--loop --interval-minutes N`) |
| `scripts/fill_watcher.py` | Monitors fills from `runner.log` and appends to `state/trades.csv` |
| `scripts/weekly_report.py` | Builds HTML equity report and overlay chart |
| `state/` | Persistent state folder (portfolio, trades, logs, and reports) |
| `systemd` services | Auto-start and recover the agent after reboot |

---

## 3. Strategy Logic
- **Indicators:** RSI(14), ATR(14)  
- **Modes:** Bull / Bear / Chop detection via regime filters  
- **Action rules:**  
  - Buy when RSI < 30 and volatility dips (`flags=dip`)  
  - Sell when RSI > 70 or stop-loss triggered  
- **Confidence Gate:** LLM approval threshold (`llm_min_confidence ≥ 0.6`)

---

## 4. Key Metrics (as of 22 Oct 2025, 13:01:45 UTC — emailed report)

| Metric | Value | Comment |
|---------|--------|---------|
| **Equity Δ (7 d)** | **+0.59%** | Week-on-week portfolio growth |
| **Cash (report)** | **$8,700.00** | From emailed “Weekly Summary” |
| **BTC Held** | **0.012090 BTC** | Approx. $1,300 buy notional this week |
| **Trades (7 d)** | **26 buys, 0 sells** | All recorded for week ending 22 Oct |
| **Total Trades** | **271 cumulative** | All confirmed in `trades.csv` |
| **Next Report** | **24 Oct 2025, 07:05 UTC** | Scheduled via `btc-agent-report.timer` |

---

## 5. Directory Snapshot
```text

btc-trading-agent/

├── app/

│   ├── main.py

│   └── advisor.py

├── scripts/

│   ├── fill_watcher.py

│   ├── weekly_report.py

│   └── add_trade_balances.py

├── state/

│   ├── runner.log

│   ├── trades.csv

│   ├── portfolio_state.json

│   └── reports/

│       ├── weekly_report.html

│       └── equity_overlay.png

└── .venv/

---

## 6. Deployment

```bash
# Enable persistent background services
sudo systemctl enable btc-agent.service btc-fill-watcher.service btc-agent-report.timer
sudo systemctl start  btc-agent.service btc-fill-watcher.service

# Follow live logs (recommended over tail -f)
journalctl -fu btc-agent.service | grep -E 'tick|\[feed\]|\[obs\]|\[dec\]|\[fill\]'

# To rebuild reports manually
cd ~/btc-trading-agent
MPLBACKEND=Agg REPORT_WINDOW_DAYS=30 \
  ./.venv/bin/python scripts/weekly_report.py
```

Reports are written to:
`/root/btc-trading-agent/state/reports/weekly_report.html`

and visual equity overlays at:
`/root/btc-trading-agent/state/reports/equity_overlay.png`

---

## 7. Recent Highlights

✅ Stable service restart under `systemd`

✅ Telegram notifications operational (`buy/sell` alerts observed)

✅ Equity tracking rebuilt with normalized trades

🔄 Scheduled weekly reporting (auto-email pending activation)

---

## 8. Planned Enhancements & Expansion Roadmap

The current agent runs reliably under systemd with real-time Telegram alerts and scheduled equity reporting.
The next phase focuses on enhancing autonomy, resilience, and insight generation through the following improvements:

**🧱 Persistent State Backups:** Mount /mnt/state for durable volume snapshots, ensuring long-term traceability and crash recovery.

**☁️ Cloud Sync Automation:** Seamless upload of HTML and PNG reports to Google Drive or Telegram for remote monitoring.

**📊 Adaptive Risk Sizing:** Introduce regime-aware ATR scaling that dynamically adjusts trade exposure based on volatility regime detection.

**🧠 LLM Commentary Engine:** Add GPT-4o-based narrative summaries of weekly P&L trends, regime shifts, and confidence scores to accompany reports.

---

**Maintainer:**
**Alvin Siphosenkosi Moyo**
Apziva AI Residency 2025 · Finance & Machine Learning
📧 [alvinsmoyo@gmail.com](mailto:alvinsmoyo@gmail.com) 🌐 [GitHub Profile](https://github.com/AlvinSMoyo)


