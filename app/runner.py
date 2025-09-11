import os, time, pandas as pd
from datetime import datetime, timezone
from .feeds import fetch_yfinance
from .indicators import atr, rsi
from .engine import get_last_close, paper_fill, load_state, save_state
from .advisor import ask_model, validate_decision, coerce_to_schema
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
    return {"ts_utc": datetime.now(timezone.utc).isoformat(),
            "price": round(price, 2),
            "rsi14": round(rsi_last, 2),
            "atr14": round(atr_last, 2)}

def run_once(symbol="BTC-USD", interval_minutes=30):
    candles = fetch_yfinance(symbol, lookback_days=30, interval_minutes=interval_minutes)
    a = atr(candles, 14); r = rsi(candles, 14)
    obs = build_observation(candles, a, r, interval_minutes)

    strat = {
        "llm_size_usd": float(os.getenv("LLM_SIZE_USD", "300")),
        "llm_stop_atr_k_default": float(os.getenv("LLM_STOP_ATR_K_DEFAULT", "1.3")),
        "llm_min_confidence": float(os.getenv("LLM_MIN_CONFIDENCE", "0.60")),
    }

    raw = ask_model(obs, strat)
    ok, _ = validate_decision(raw)
    dec = raw if ok else coerce_to_schema(raw, strat, obs)
    print("[obs]", obs); print("[dec]", dec)

    if not dec or dec.get("confidence", 0) < strat["llm_min_confidence"]:
        print("[gate] below confidence → skip")
        return False, obs, dec

    traded = False
    if dec["action"] == "buy":
        usd = float(dec.get("size_usd", strat["llm_size_usd"]))
        usd = min(usd, load_state().get("cash_usd", 0.0))
        if usd > 1e-6:
            qty = usd / obs["price"]
            ok, _info = paper_fill("buy", "LLM", obs["price"], qty, note=dec.get("reason_short", ""))
            traded = bool(ok)
            if traded:
                s = load_state()
                k = float(dec.get("stop_atr_k") or strat["llm_stop_atr_k_default"])
                s["active_swing"] = {"entry": obs["price"], "qty_btc": qty, "stop": max(0.0, obs["price"] - k * obs["atr14"])}
                save_state(s); _mark_trade()

    elif dec["action"] == "sell":
        s = load_state()
        qty = float(s["active_swing"]["qty_btc"]) if s.get("active_swing") else min(0.005, s.get("btc", 0.0))
        if qty > 0:
            ok, _info = paper_fill("sell", "LLM", obs["price"], qty, note=dec.get("reason_short",""))
            traded = bool(ok)
            if traded:
                s = load_state(); s["active_swing"] = None; save_state(s); _mark_trade()
    else:
        print("[LLM] HOLD → no action")

    return traded, obs, dec

def run_loop(symbol="BTC-USD", interval_minutes=30, max_ticks=None):
    i = 0
    while True:
        print("\n— tick", i, datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        try:
            run_once(symbol, interval_minutes)
            # Optional weekly email: Monday 09:00 UTC
            if send_weekly_email:
                now = datetime.now(timezone.utc)
                if now.weekday() == 0 and now.hour == 9 and now.minute == 0:
                    flag = f"/tmp/weekly_sent_{now.strftime('%Y%m%d')}"
                    if not os.path.exists(flag):
                        ok, msg = send_weekly_email(preview_if_missing_creds=False)
                        print(msg)
                        open(flag, "w").close()
        except Exception as e:
            print("[tick:error]", type(e).__name__, e)
        i += 1
        if (max_ticks is not None) and i >= max_ticks:
            break
        time.sleep(max(5, int(interval_minutes)*60))
