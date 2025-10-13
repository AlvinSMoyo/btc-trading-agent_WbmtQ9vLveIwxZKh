import os
import csv
import time
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

# --- Local Imports ---
from .risk.guardrails import global_pause, position_limits, daily_loss_cap
from .feeds import fetch_yfinance
from .indicators.atr import atr
from .indicators_core import rsi
from .strategies.dca import dca_actions
from .engine import get_last_close, paper_fill, load_state, save_state
from .advisor import ask_model, validate_decision, coerce_to_schema
from .guardrails_regime import (
    detect_regime_from_1m,
    reset_daily_budget_if_needed,
    guardrails_pass,
    regime_gate,
    note_trade_side_time,
    apply_daily_buy_accum,
    utc_now,
)

# --- Optional Imports ---
try:
    from app.notify.telegram import send_trade_alert
except ImportError:
    send_trade_alert = None # Define a no-op if not found

try:
    from .voice_email import send_weekly_email
except ImportError:
    send_weekly_email = None # Define a no-op if not found

# --- State Management ---
STATE_DIR = os.getenv("STATE_DIR", "state")
EQUITY_CSV_PATH = os.path.join(STATE_DIR, "equity_history.csv")

def append_equity_row(price: float, state: dict) -> None:
    """Appends a new row to the equity history CSV."""
    os.makedirs(STATE_DIR, exist_ok=True)
    header = ["ts_utc", "price", "cash_usd", "btc", "equity", "trades_today"]
    
    cash_usd = float(state.get("cash_usd", 0.0))
    btc = float(state.get("btc", 0.0))
    equity = cash_usd + (btc * price)
    trades_today = int(state.get("trades_today", 0))
    
    row_data = [
        datetime.now(timezone.utc).isoformat(),
        f"{price:.2f}",
        f"{cash_usd:.2f}",
        f"{btc:.8f}",
        f"{equity:.2f}",
        trades_today,
    ]

    file_exists = os.path.exists(EQUITY_CSV_PATH)
    with open(EQUITY_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        writer.writerow(row_data)

# --- Helper Functions ---
def _last_float(x) -> float | None:
    try:
        s = pd.Series(np.asarray(x).ravel())
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(s.iloc[-1]) if not s.empty else None
    except Exception:
        try:
            return float(x)
        except Exception:
            return None

def _execute_and_notify(trade_details: dict) -> dict | None:
    """A centralized helper to execute a paper trade and send notifications."""
    tx_result, _ = paper_fill(**trade_details)
    if not tx_result:
        return None
    
    _mark_trade()
    if send_trade_alert:
        try:
            send_trade_alert(tx_result)
        except Exception as e:
            print(f"[alert:error] Failed to send Telegram alert: {e}")
    return tx_result

def _mark_trade() -> None:
    """Updates trade counters and timestamps in the state file."""
    state = load_state()
    today_str = datetime.now(timezone.utc).date().isoformat()
    
    if state.get("trades_today_date") != today_str:
        state["trades_today_date"] = today_str
        state["trades_today"] = 0
        
    state["trades_today"] = state.get("trades_today", 0) + 1
    state["last_trade_ts"] = int(time.time())
    save_state(state)

def _safe_round(x, nd=2, default=np.nan):
    try:
        return round(float(x), nd)
    except Exception:
        return float(default)

def build_observation(candles: pd.DataFrame, atr_series: pd.Series, rsi_series: pd.Series, interval_minutes: int) -> dict:
    """Constructs the observation dictionary for the LLM advisor."""
    price = get_last_close(candles)
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "price": round(price, 2),
        "rsi14": _safe_round(_last_float(rsi_series), 2, default=np.nan),
        "atr14": _safe_round(_last_float(atr_series), 2, default=np.nan),
        "interval_min": int(interval_minutes),
    }

# --- Main Runner Logic ---
def run_once(symbol="BTC-USD", interval_minutes=30):
    """Executes a single trading tick."""
    # 1. Fetch data and build observation
    candles = fetch_yfinance(symbol, lookback_days=30, interval_minutes=interval_minutes)
    df_1m = fetch_yfinance(symbol, lookback_days=2, interval_minutes=1)
    
    # compute price first
    price = get_last_close(candles)

    # write a fresh equity row immediately (even if later logic fails)
    state = load_state()
    append_equity_row(price, state)

    # now build indicators/observation
    atr_series = atr(candles, 14)
    rsi_series = rsi(candles, 14)
    obs = build_observation(candles, atr_series, rsi_series, interval_minutes)


    # 2. Execute DCA Strategy
    dca_config = {
        "DCA_DROP_PCT": float(os.getenv("DCA_DROP_PCT", "3.0")),
        "DCA_LOT_USD": float(os.getenv("DCA_LOT_USD", "50")),
        "DCA_MIN_COOLDOWN_MIN": int(os.getenv("DCA_MIN_COOLDOWN_MIN", "60")),
    }
    for intent in dca_actions(state, price, dca_config):
        qty = float(intent.get("qty", 0.0))
        if qty > 0:
            trade_details = {"side": "BUY", "reason": "dca", "price": price, "qty_btc": qty, "note": "auto dca"}
            tx = _execute_and_notify(trade_details)
            if tx:
                state = load_state() # Reload state after trade
                state["last_dca_price"] = price
                state["last_dca_ts"] = datetime.now(timezone.utc).isoformat()
                save_state(state)
                print("[fill][dca]", tx)
    append_equity_row(price, load_state())


    # 3. Get LLM Advisor Decision
    strat_config = {
        "llm_size_usd": float(os.getenv("LLM_SIZE_USD", "300")),
        "llm_stop_atr_k_default": float(os.getenv("LLM_STOP_ATR_K_DEFAULT", "1.3")),
        "llm_min_confidence": float(os.getenv("LLM_MIN_CONFIDENCE", "0.60")),
    }
    raw_decision = ask_model(obs, strat_config)
    is_valid, _ = validate_decision(raw_decision)
    decision = raw_decision if is_valid else coerce_to_schema(raw_decision, strat_config, obs)
    print("[obs]", obs)
    print("[dec]", decision)
    
    # 4. Apply Guardrails
    regime = detect_regime_from_1m(df_1m)
    obs["regime"] = regime.get("label", "unknown")
    print(f"[regime] label={obs['regime']} trend={regime.get('trending')}")

    # Check all guardrails
    guards_to_check = [
        global_pause(),
        guardrails_pass(decision, float(state.get("cash_usd", 0.0)), utc_now()),
        regime_gate(decision, obs["regime"], metrics=regime),
    ]
    if decision.get("action", "").upper() == "BUY":
        equity = float(state.get("cash_usd", 0.0)) + (float(state.get("btc", 0.0)) * price)
        pnl_today = float(state.get("pnl_today_usd", 0.0))
        guards_to_check.extend([
            position_limits(float(state.get("btc", 0.0)), price, equity),
            daily_loss_cap(pnl_today),
        ])

    for passed, reason in guards_to_check:
        if not passed:
            print(f"[gate] {reason} → skip")
            return

    # 5. Execute LLM Trade
    action = decision.get("action", "").upper()
    size_usd = float(decision.get("size_usd", 0.0))
    
    if action in ("BUY", "SELL") and size_usd > 0:
        qty_btc = size_usd / price
        if action == "SELL":
            qty_btc = min(qty_btc, float(state.get("btc", 0.0)))

        trade_details = {
            "side": action,
            "reason": "LLM",
            "price": price,
            "qty_btc": qty_btc,
            "note": str(decision.get("reason_short", ""))[:120]
        }
        tx = _execute_and_notify(trade_details)
        if tx:
            note_trade_side_time(action)
            apply_daily_buy_accum(action, size_usd)
            append_equity_row(price, load_state())
            print("[fill]", tx)
        else:
            print("[fill] no-op")
    else:
        print("[fill] no-op (hold decision or zero size)")

def run_loop(symbol="BTC-USD", interval_minutes=30, max_ticks=None):
    """Runs the trading bot in a continuous loop."""
    tick_count = 0
    while True:
        print(f"\n— tick {tick_count} {datetime.now(timezone.utc):%H:%M:%S UTC}")
        try:
            run_once(symbol, interval_minutes)
            
            # Weekly email check
            now = datetime.now(timezone.utc)
            if send_weekly_email and now.weekday() == 0 and now.hour == 9 and now.minute == 0:
                flag_path = os.path.join(os.getenv("TEMP", "/tmp"), f"weekly_sent_{now:%Y%m%d}")
                if not os.path.exists(flag_path):
                    _, msg = send_weekly_email(preview_if_missing_creds=False)
                    print(msg)
                    try:
                        open(flag_path, "w").close()
                    except OSError as e:
                        print(f"[email:error] Could not create flag file: {e}")
        except Exception as e:
            print(f"[tick:error] {type(e).__name__}: {e}")

        tick_count += 1
        if max_ticks is not None and tick_count >= max_ticks:
            break
        
        time.sleep(max(5, int(interval_minutes) * 60))

