# Submission Report — btc-trading-agent

## Problem → Change → Verification

### P0.1 Runner + State Writers
- **Problem:** No guaranteed producer for `state/*.csv`.
- **Change:** Added 24/7 loop, safe CSV writers, paper fills.
- **Verify:** After 2–3 min, `state/equity_history.csv` and `state/trades.csv` exist and grow.

### P0.2 DCA + ATR Stop
- **Problem:** Hybrid expectation (DCA base + tactical stops) missing.
- **Change:** DCA on % drop with cooldown; swing entries use stop = entry − k·ATR; watchdog exits on breach.
- **Verify:** Force small DCA_DROP_PCT; observe dca fills and stop exits in `trades.csv`.

### P0.3 Config Loader (Sheet + Cache)
- **Problem:** Tunables needed live reconfiguration & offline fallback.
- **Change:** Google Sheet → dict with JSON cache; hourly refresh.
- **Verify:** Edit Sheet, wait <= TTL, rerun one tick; values reflect changes. Offline uses cache.

### P0.4 Telegram Alerts
- **Problem:** No trade visibility.
- **Change:** `app/notify/telegram.py` with `ping()` and trade alerts.
- **Verify:** `python -c "from app.notify.telegram import ping; print(ping('hi'))"` returns True; real trades post messages.

### P0.5 Weekly Email (+ Scheduler)
- **Problem:** No weekly Portfolio summary.
- **Change:** HTML summary (cash/btc/equity/trades). APScheduler for Mon 09:00.
- **Verify:** `python -m app.voice_email --send-now` sends or writes preview.

### P0.6 Logging + Atomic CSV
- **Problem:** Fragile CSV/logs.
- **Change:** Rotating file logger; atomic writes.
- **Verify:** `logs/agent.log` rolling; no half-written CSVs.

### P1.7 LLM Gateway (toggle)
- **Problem:** Opportunistic entries needed light LLM gating.
- **Change:** Heuristic/LLM toggle controlling swing entries.
- **Verify:** When enabled, logs rationale, gates swings.

### P1.8 Risk Guardrails
- **Problem:** Safety rails (pause / position cap / daily loss).
- **Change:** `global_pause`, `position_limits`, `daily_loss_cap`.
- **Verify:** Env flips block actions with `[gate]` reason.

## Limitations
- Daily PnL cap uses reference equity (simple v1).
- Swing/stop module off by default; enable via config when confident.
- Live trading connectors are out of scope for this submission.

## Operations (Weekly)
- Runner loop writes state; scheduler sends Monday 09:00 email.
- Telegram delivers real-time fills.
- Baseline scripts regenerate comparison PNG/HTML for review.

## Artifacts
- `state/trades.csv`, `state/equity_history.csv`
- `state/weekly_report_preview.html` (if SMTP missing)
- `logs/agent.log`
