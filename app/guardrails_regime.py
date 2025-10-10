# app/guardrails_regime.py
import os, math, datetime as dt
import numpy as np
import pandas as pd

# ---------- EMA / ADX / Regime ----------
def _ema(s: pd.Series, span: int):
    return s.ewm(span=span, adjust=False).mean()

def _adx14_hourly(df_h: pd.DataFrame):
    req = {"high","low","close"}
    if not req.issubset(df_h.columns):
        return None
    high, low, close = df_h["high"], df_h["low"], df_h["close"]
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    n = 14
    atr     = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = (pd.Series(plus_dm, index=df_h.index).ewm(alpha=1/n, adjust=False).mean() / atr) * 100
    minus_di= (pd.Series(minus_dm,index=df_h.index).ewm(alpha=1/n, adjust=False).mean() / atr) * 100
    dx      = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0,np.nan)) * 100
    adx     = dx.ewm(alpha=1/n, adjust=False).mean()
    adx = adx.dropna()
    return float(adx.iloc[-1]) if len(adx) else None

def detect_regime_from_1m(df_1m: pd.DataFrame):
    """
    df_1m: DatetimeIndex (UTC), columns: open,high,low,close,volume.
    Returns dict with 'label' in {'bull','bear','chop'}.
    """
    if df_1m is None or df_1m.empty:
        return {"label":"chop","why":"no_data"}

    # hourly bars
    df_h = df_1m.resample("1H", label="right", closed="right").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna()

    if len(df_h) < 220:
        return {"label":"chop","why":"insufficient_history"}

    c = df_h["close"]
    ema50  = _ema(c, 50)
    ema200 = _ema(c, 200)
    slope200 = float(ema200.diff(5).iloc[-1] / 5.0)
    slope_bps = (slope200 / float(c.iloc[-1])) * 1e4

    adx = _adx14_hourly(df_h)

    # RSI(14) hourly (robust)
    rsi_h = None
    try:
        delta = c.diff()
        up = delta.clip(lower=0.0)
        down = -delta.clip(upper=0.0)
        roll = 14
        rs = (up.ewm(alpha=1/roll, adjust=False).mean() /
              down.ewm(alpha=1/roll, adjust=False).mean())
        rsi_h = float(100 - (100/(1+rs)).iloc[-1])
    except Exception:
        pass

    votes = 0
    if ema50.iloc[-1] > ema200.iloc[-1]: votes += 1
    if ema50.iloc[-1] < ema200.iloc[-1]: votes -= 1
    if slope_bps > 1.0:  votes += 1
    if slope_bps < -1.0: votes -= 1
    if rsi_h is not None:
        if rsi_h >= 55: votes += 1
        if rsi_h <= 45: votes -= 1
    trending = (adx is not None and adx >= 20)

    label = "chop"
    if votes >= (2 if trending else 3): label = "bull"
    if votes <= (-(2 if trending else 3)): label = "bear"

    return {
        "label": label,
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "ema200_slope_bps_per_hr": float(slope_bps),
        "adx14_h": (None if adx is None else float(adx)),
        "rsi14_h": rsi_h,
        "votes": int(votes),
        "trending": bool(trending),
    }

# ---------- Guardrails / budget / cooldown ----------
_last_side_time = {"BUY": None, "SELL": None}
_daily_buy_spend = 0.0
_daily_buy_day = None

def utc_now():
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

def _same_utc_day(a: dt.datetime, b: dt.datetime):
    return (a.date() == b.date())

def reset_daily_budget_if_needed(now_utc):
    global _daily_buy_day, _daily_buy_spend
    if _daily_buy_day is None or not _same_utc_day(now_utc, _daily_buy_day):
        _daily_buy_day = now_utc
        _daily_buy_spend = 0.0

def guardrails_pass(dec, portfolio_cash, now_utc):
    """
    dec: {'action':'buy'/'sell', 'size_usd': float, 'confidence': float}
    """
    global _last_side_time, _daily_buy_spend
    cash_floor   = float(os.getenv("CASH_FLOOR_USD", "3000"))
    buy_cap_day  = float(os.getenv("DAILY_BUY_LIMIT_USD", "5000"))
    cooldown_min = float(os.getenv("COOLDOWN_MIN", "5"))

    side = str(dec.get("action","")).upper()

    # per-side cooldown
    if side in ("BUY","SELL"):
        last_t = _last_side_time.get(side)
        if last_t is not None:
            mins = (now_utc - last_t).total_seconds() / 60.0
            if mins < cooldown_min:
                return False, f"cooldown {side}: {mins:.1f}m<{cooldown_min}m"

    # cash floor / daily buy budget
    if side == "BUY":
        if portfolio_cash < cash_floor:
            return False, f"cash_floor: ${portfolio_cash:,.2f} < ${cash_floor:,.2f}"
        if (_daily_buy_spend + float(dec.get("size_usd",0.0))) > buy_cap_day:
            return False, f"daily buy cap: ${_daily_buy_spend:,.0f}/{buy_cap_day:,.0f}"
    return True, "ok"

def note_trade_side_time(side):
    global _last_side_time
    _last_side_time[str(side).upper()] = utc_now()

def apply_daily_buy_accum(side, notional_usd):
    global _daily_buy_spend
    if str(side).upper() == "BUY":
        _daily_buy_spend += float(notional_usd)

def regime_gate(dec, regime_label):
    conf = float(dec.get("confidence", 0.0))
    side = str(dec.get("action","")).upper()

    bull_buy = float(os.getenv("BULL_BUY_CONF", "0.65"))
    bull_sell= float(os.getenv("BULL_SELL_CONF","0.80"))
    bear_buy = float(os.getenv("BEAR_BUY_CONF", "0.80"))
    bear_sell= float(os.getenv("BEAR_SELL_CONF","0.60"))
    chop_req = float(os.getenv("CHOP_CONF_REQ","0.75"))
    chop_skip= os.getenv("CHOP_SKIP","0") == "1"

    if regime_label == "bull":
        if side == "BUY"  and conf >= bull_buy:  return True, "bull-buy"
        if side == "SELL" and conf >= bull_sell: return True, "bull-sell"
        return False, f"bull gate: conf={conf:.2f}"
    elif regime_label == "bear":
        if side == "SELL" and conf >= bear_sell: return True, "bear-sell"
        if side == "BUY"  and conf >= bear_buy:  return True, "bear-buy"
        return False, f"bear gate: conf={conf:.2f}"
    else:
        if chop_skip:
            return False, "chop-skip"
        return (conf >= chop_req), f"chop gate conf={conf:.2f}/{chop_req}"
