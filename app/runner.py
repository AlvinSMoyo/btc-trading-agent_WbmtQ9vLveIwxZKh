# app/runner.py
import os, time
import pandas as pd
from datetime import datetime, timezone

from .feeds import fetch_yfinance
from .indicators import atr, rsi
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
    if isinstance(x, pd.DataFrame):
        x = x.iloc[:, 0]
    s = pd.to_numeric(x, errors="coerce").dropna()
    return float(s.iloc[-1])


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
    tx = paper_fill(action, price, qty_btc, reason="LLM", note=note)
    _mark_trade()
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

    # --- Gate 0: confidence threshold ---
    if float(dec.get("confidence", 0.0)) < float(strat["llm_min_confidence"]):
        print("[gate] below confidence → skip")
        return

    # --- Regime detection from 1m data ---
    reg = detect_regime_from_1m(df_1m)
    obs["regime"] = reg["label"]
    print(f"[regime] {reg}")

    # --- Guardrails (budget/cooldowns/position sanity) ---
    now = utc_now()
    reset_daily_budget_if_needed(now)

    portfolio_cash = float(state.get("cash_usd", 0.0) or 0.0)
    ok_g, why_g = guardrails_pass(dec, portfolio_cash, now)
    if not ok_g:
        print(f"[gate] {why_g} → skip")
        return

    # --- Regime gate (map actions to trend regime) ---
    ok_r, why_r = regime_gate(dec, obs["regime"])
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

