# app/runner.py
import numpy as np
import pandas as pd
import os, time
from datetime import datetime, timezone
from .risk.guardrails import global_pause, position_limits, daily_loss_cap
from .feeds import fetch_yfinance
from .indicators.atr import atr
from .indicators_core import rsi
from .strategies.dca import dca_actions
from app.config import load
from app.notify.telegram import send_trade_alert
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

try:
    from .voice_email import send_weekly_email
except Exception:
    send_weekly_email = None


def _mark_trade():
    s = load_state()
    today = datetime.now(timezone.utc).date().isoformat()
    if s.get("trades_today_date") != today:
        s["trades_today_date"] = today
        s["trades_today"] = 0
    s["trades_today"] = int(s.get("trades_today", 0)) + 1
    s["last_trade_ts"] = int(time.time())
    save_state(s)


def _last_float(x):
    # float/int -> just return
    if isinstance(x, (float, int)):
        try:
            return float(x)
        except Exception:
            return None

    # numpy array / list / tuple -> convert to Series
    if isinstance(x, (list, tuple, np.ndarray)):
        s = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
        return float(s.iloc[-1]) if not s.empty else None

    # pandas objects
    if isinstance(x, (pd.Series, pd.DataFrame)):
        s = pd.to_numeric(pd.Series(x).squeeze(), errors="coerce").dropna()
        return float(s.iloc[-1]) if not s.empty else None

    # last resort
    try:
        return float(x)
    except Exception:
        return None


def build_observation(candles, atr_series, rsi_series, interval_minutes):
    price = get_last_close(candles)
    rsi_last = _last_float(rsi_series)
    atr_last = _last_float(atr_series)
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "price": round(price, 2),
        "rsi14": round(rsi_last, 2),
        "atr14": round(atr_last, 2),
        "interval_min": int(interval_minutes),
    }


def _place_trade(dec: dict, obs: dict):
    """
    Minimal paper execution using engine.paper_fill().
    BUY qty is sized from size_usd/price.
    SELL qty uses size_usd/price capped by current BTC position (conservative).
    """
    action = str(dec.get("action", "")).upper()
    if action not in ("BUY", "SELL"):
        return None

    size_usd = float(dec.get("size_usd", 0.0) or 0.0)
    price = float(obs["price"])
    if price <= 0 or size_usd <= 0:
        return None

    state = load_state()
    if action == "BUY":
        qty_btc = round(size_usd / price, 8)
    else:
        # cap by position on sells
        have = float(state.get("btc", 0.0) or 0.0)
        qty_btc = min(round(size_usd / price, 8), max(have, 0.0))

    note = str(dec.get("reason_short", ""))[:120]
    tx = paper_fill(action, "LLM", float(price), float(qty_btc), note=note)
    _mark_trade()
    try:
        send_trade_alert(tx)
    except Exception:
        pass
    return tx


def _place_dca_buy(price: float, qty_btc: float, note: str = "auto dca"):
    """Place a DCA BUY by quantity, tag as 'dca', and update DCA anchors."""
    if price <= 0 or qty_btc <= 0:
        return None

    # paper_fill(action, price, reason, qty_btc, note)
    tx = paper_fill("BUY", "dca", float(price), float(qty_btc), note=note)
    _mark_trade()
    try:
        send_trade_alert(tx)
    except Exception:
        pass

    # update anchors for next DCA decision
    s = load_state()
    s["last_dca_price"] = float(price)
    s["last_dca_ts"] = datetime.now(timezone.utc).isoformat()
    save_state(s)
    return tx


def run_once(symbol="BTC-USD", interval_minutes=30):
    # Analysis timeframe
    candles = fetch_yfinance(symbol, lookback_days=30, interval_minutes=interval_minutes)
    a = atr(candles, 14)
    r = rsi(candles, 14)
    obs = build_observation(candles, a, r, interval_minutes)

    # 1-minute feed for regime detection
    df_1m = fetch_yfinance(symbol, lookback_days=2, interval_minutes=1)

    # Portfolio state
    state = load_state()

    # --- DCA block (before advisor) ---
    price = get_last_close(candles)
    cfg = {
        "DCA_DROP_PCT": float(os.getenv("DCA_DROP_PCT", "3.0")),
        "DCA_LOT_USD": float(os.getenv("DCA_LOT_USD", "50")),
        "DCA_MIN_COOLDOWN_MIN": int(os.getenv("DCA_MIN_COOLDOWN_MIN", "60")),
    }
    for intent in dca_actions(state, price, cfg):
        # our dca_actions returns qty already; use it directly
        qty = float(intent.get("qty", 0.0) or 0.0)
        if qty > 0:
            tx = _place_dca_buy(price, qty_btc=qty)
            if tx:
                print("[fill][dca]", tx)


    # Strategy knobs (env-tunable)
    strat = {
        "llm_size_usd": float(os.getenv("LLM_SIZE_USD", "300")),
        "llm_stop_atr_k_default": float(os.getenv("LLM_STOP_ATR_K_DEFAULT", "1.3")),
        "llm_min_confidence": float(os.getenv("LLM_MIN_CONFIDENCE", "0.60")),
    }

    # Ask the advisor
    raw = ask_model(obs, strat)
    ok, _ = validate_decision(raw)
    dec = raw if ok else coerce_to_schema(raw, strat, obs)

    print("[obs]", obs)
    print("[dec]", dec)

    # --- Regime detection from 1m data (always log this) ---
    reg = detect_regime_from_1m(df_1m)
    obs["regime"] = reg["label"]
    print(f"[regime] label={reg.get('label')} adx={reg.get('adx14_h')} "
          f"slope_bps_hr={reg.get('ema200_slope_bps_per_hr')} trend={reg.get('trending')} "
          f"hist_h={reg.get('history_hours')}")

    # --- Guardrails (budget/cooldowns/position sanity) ---
    now = utc_now()
    reset_daily_budget_if_needed(now)

    portfolio_cash = float(state.get("cash_usd", 0.0) or 0.0)
    ok_g, why_g = guardrails_pass(dec, portfolio_cash, now)
    if not ok_g:
        print(f"[gate] {why_g} → skip")
        return

    # --- Portfolio risk guardrails v1 (Patch 8.2) ---
    paused, why_p = global_pause()
    if paused:
        print(f"[gate] {why_p} → skip")
        return

    side = str(dec.get("action", "")).upper()
    price = float(obs["price"])
    btc   = float(state.get("btc", 0.0) or 0.0)
    cash  = float(state.get("cash_usd", 0.0) or 0.0)
    equity_usd = cash + btc * price

    # Only block additional exposure on BUY
    if side == "BUY":
        ok_pos, why_pos = position_limits(btc, price, equity_usd)
        if not ok_pos:
            print(f"[gate] {why_pos} → skip")
            return

        pnl_today_usd = float(state.get("pnl_today_usd", 0.0) or 0.0)  # 0 if you don't track it yet
        ok_loss, why_loss = daily_loss_cap(pnl_today_usd)
        if not ok_loss:
            print(f"[gate] {why_loss} → skip")
            return

    # --- Regime gate (uses regime-aware confidence thresholds) ---
    ok_r, why_r = regime_gate(dec, obs["regime"], metrics=reg)
    if not ok_r:
        print(f"[gate] {why_r} → skip")
        return

    # --- Execute ---
    tx = _place_trade(dec, obs)
    if tx:
        # Post-trade guardrail accounting
        note_trade_side_time(dec.get("action"))
        apply_daily_buy_accum(dec.get("action"), float(dec.get("size_usd", 0.0) or 0.0))
        print("[fill]", tx)
    else:
        print("[fill] no-op")


def run_loop(symbol="BTC-USD", interval_minutes=30, max_ticks=None):
    i = 0
    while True:
        print("\n— tick", i, datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        try:
            run_once(symbol, interval_minutes)
            # Optional: weekly email Monday 09:00 UTC
            if send_weekly_email:
                now = datetime.now(timezone.utc)
                if now.weekday() == 0 and now.hour == 9 and now.minute == 0:
                    flag = os.path.join(os.getenv("TEMP", "/tmp"), f"weekly_sent_{now:%Y%m%d}")
                    if not os.path.exists(flag):
                        ok, msg = send_weekly_email(preview_if_missing_creds=False)
                        print(msg)
                        try:
                            open(flag, "w").close()
                        except Exception:
                            pass
        except Exception as e:
            print("[tick:error]", type(e).__name__, e)

        i += 1
        if (max_ticks is not None) and i >= max_ticks:
            break
        time.sleep(max(5, int(interval_minutes) * 60))

