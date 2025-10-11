# app/guardrails_regime.py
from __future__ import annotations

import os
import datetime as dt
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

# ---------- EMA / ADX / Regime ----------

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _adx14_hourly(df_h: pd.DataFrame) -> Optional[float]:
    """Return the latest ADX(14) from hourly OHLC, or None if not computable."""
    req = {"high", "low", "close"}
    if not req.issubset(df_h.columns):
        return None

    high, low, close = df_h["high"], df_h["low"], df_h["close"]
    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [(high - low),
         (high - close.shift()).abs(),
         (low  - close.shift()).abs()],
        axis=1
    ).max(axis=1)

    n = 14
    atr      = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di  = (pd.Series(plus_dm,  index=df_h.index).ewm(alpha=1/n, adjust=False).mean() / atr) * 100
    minus_di = (pd.Series(minus_dm, index=df_h.index).ewm(alpha=1/n, adjust=False).mean() / atr) * 100
    denom    = (plus_di + minus_di).replace(0, np.nan)
    dx       = ((plus_di - minus_di).abs() / denom) * 100
    adx      = dx.ewm(alpha=1/n, adjust=False).mean().dropna()

    return float(adx.iloc[-1]) if len(adx) else None

def _ensure_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common vendor column variants; ensure open/high/low/close/volume exist."""
    rename = {
        "Open":"open", "High":"high", "Low":"low", "Close":"close", "Adj Close":"close",
        "Volume":"volume", "Price":"close", "Last":"close"
    }
    df = df.rename(columns={c: rename.get(c, c) for c in df.columns})

    # If only 'price' exists, mirror to close (handled by mapping above).
    if "close" not in df.columns and "price" in df.columns:
        df["close"] = df["price"]

    # Backfill missing OHLC from close.
    for c in ("open", "high", "low"):
        if c not in df.columns and "close" in df.columns:
            df[c] = df["close"]

    if "volume" not in df.columns:
        df["volume"] = 0.0

    return df[["open","high","low","close","volume"]].copy()

def detect_regime_from_1m(df_1m: pd.DataFrame) -> Dict[str, Any]:
    """
    Inputs:
      df_1m: DatetimeIndex (UTC or naive), columns at least: open,high,low,close,volume
    Output:
      dict with 'label' in {'bull','bear','chop'} + diagnostics.
    """
    if df_1m is None or df_1m.empty:
        return {"label": "chop", "why": "no_data"}

    # Normalize columns and index (UTC)
    df_1m = _ensure_ohlc(df_1m)
    if not isinstance(df_1m.index, pd.DatetimeIndex):
        df_1m.index = pd.to_datetime(df_1m.index, utc=True)
    elif df_1m.index.tz is None:
        df_1m.index = df_1m.index.tz_localize("UTC")
    else:
        df_1m.index = df_1m.index.tz_convert("UTC")
    df_1m = df_1m.sort_index()

    # Build hourly bars
    df_h = (
        df_1m
        .resample("1h", label="right", closed="right")
        .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
        .dropna()
    )

    hist_hours = int(len(df_h))
    min_hours = int(os.getenv("REGIME_MIN_HOURS", "36"))
    short_hist = hist_hours < min_hours

    if hist_hours == 0:
        return {"label": "chop", "why": "no_hourly_data"}

    c = df_h["close"]
    ema50  = _ema(c, 50)
    ema200 = _ema(c, 200)

    # Robust EMA200 slope (hrs) even with short history
    ema200_valid = ema200.dropna()
    if len(ema200_valid) >= 2:
        k = min(5, len(ema200_valid) - 1)  # up to 5h slope; smaller if history is short
        slope200 = float(ema200_valid.diff(k).iloc[-1] / k)
    else:
        slope200 = 0.0

    lastc = float(c.iloc[-1])
    slope_bps = ((slope200 / lastc) * 1e4) if lastc else 0.0

    # ADX(14) on hourly, but don't trust for 'trending' if history is short
    adx = _adx14_hourly(df_h)
    adx_trend_min = float(os.getenv("ADX_TREND_MIN", "20"))
    trending = (adx is not None and adx >= adx_trend_min and not short_hist)

    # RSI(14) hourly (robust)
    rsi_h: Optional[float] = None
    try:
        delta = c.diff()
        up = delta.clip(lower=0.0)
        down = -delta.clip(upper=0.0)
        roll = 14
        up_ema = up.ewm(alpha=1/roll, adjust=False).mean()
        dn_ema = down.ewm(alpha=1/roll, adjust=False).mean().replace(0.0, np.nan)
        rs = up_ema / dn_ema
        rsi_h = float(100 - (100 / (1 + rs)).iloc[-1])
        if np.isnan(rsi_h):
            rsi_h = None
    except Exception:
        rsi_h = None

    # Voting
    slope_pos_bps = float(os.getenv("SLOPE_POS_BPS", "1.0"))
    slope_neg_bps = float(os.getenv("SLOPE_NEG_BPS", "-1.0"))
    rsi_hi = float(os.getenv("RSI_HI", "55"))
    rsi_lo = float(os.getenv("RSI_LO", "45"))

    votes = 0
    if len(ema50.dropna()) and len(ema200.dropna()):
        if float(ema50.iloc[-1]) > float(ema200.iloc[-1]): votes += 1
        if float(ema50.iloc[-1]) < float(ema200.iloc[-1]): votes -= 1
    if slope_bps > slope_pos_bps:  votes += 1
    if slope_bps < slope_neg_bps:  votes -= 1
    if rsi_h is not None:
        if rsi_h >= rsi_hi: votes += 1
        if rsi_h <= rsi_lo: votes -= 1

    votes_need = 2 if trending else 3
    label = "chop"
    if votes >=  votes_need:  label = "bull"
    if votes <= -votes_need:  label = "bear"

    out: Dict[str, Any] = {
        "label": label,
        "ema50": float(ema50.iloc[-1]) if len(ema50.dropna()) else None,
        "ema200": float(ema200.iloc[-1]) if len(ema200.dropna()) else None,
        "ema200_slope_bps_per_hr": float(slope_bps),
        "adx14_h": (None if adx is None else float(adx)),
        "rsi14_h": (None if rsi_h is None else float(rsi_h)),
        "votes": int(votes),
        "votes_needed": int(votes_need),
        "trending": bool(trending),
        "history_hours": hist_hours,
        "short_history": bool(short_hist),
    }
    return out

# ---------- Guardrails / budget / cooldown ----------

_last_side_time: Dict[str, Optional[dt.datetime]] = {"BUY": None, "SELL": None}
_daily_buy_spend: float = 0.0
_daily_buy_day: Optional[dt.datetime] = None

def utc_now() -> dt.datetime:
    # Avoid deprecated utcnow(); always return timezone-aware UTC
    return dt.datetime.now(dt.timezone.utc)

def _same_utc_day(a: dt.datetime, b: dt.datetime) -> bool:
    return a.date() == b.date()

def reset_daily_budget_if_needed(now_utc: dt.datetime) -> None:
    global _daily_buy_day, _daily_buy_spend
    if _daily_buy_day is None or not _same_utc_day(now_utc, _daily_buy_day):
        _daily_buy_day = now_utc
        _daily_buy_spend = 0.0

def guardrails_pass(dec: Dict[str, Any], portfolio_cash: float, now_utc: dt.datetime) -> tuple[bool, str]:
    """
    dec: {'action':'buy'|'sell'|'hold', 'size_usd': float, 'confidence': float}
    Enforces per-side cooldown, cash floor, and daily buy cap.
    """
    global _last_side_time, _daily_buy_spend

    # Roll the daily budget if the UTC date changed
    reset_daily_budget_if_needed(now_utc)

    cash_floor   = float(os.getenv("CASH_FLOOR_USD", "3000"))
    buy_cap_day  = float(os.getenv("DAILY_BUY_LIMIT_USD", "5000"))
    cooldown_min = float(os.getenv("COOLDOWN_MIN", "5"))

    side = str(dec.get("action", "")).upper()
    size_usd = float(dec.get("size_usd", 0.0))

    # Per-side cooldown
    if side in ("BUY", "SELL"):
        last_t = _last_side_time.get(side)
        if last_t is not None:
            mins = max(0.0, (now_utc - last_t).total_seconds() / 60.0)  # clamp negatives
            if mins < cooldown_min:
                return False, f"cooldown {side}: {mins:.1f}m<{cooldown_min}m"

    # Cash floor / daily buy budget
    if side == "BUY":
        if portfolio_cash < cash_floor:
            return False, f"cash_floor: ${portfolio_cash:,.2f} < ${cash_floor:,.2f}"
        if (_daily_buy_spend + size_usd) > buy_cap_day:
            return False, f"daily buy cap: ${_daily_buy_spend:,.0f}/{buy_cap_day:,.0f}"

    return True, "ok"

def note_trade_side_time(side: str) -> None:
    global _last_side_time
    _last_side_time[str(side).upper()] = utc_now()

def apply_daily_buy_accum(side: str, notional_usd: float) -> None:
    global _daily_buy_spend
    if str(side).upper() == "BUY":
        _daily_buy_spend += float(notional_usd)

def regime_gate(dec: Dict[str, Any], regime_label: str) -> tuple[bool, str]:
    """
    Confidence gates by regime. Environment overrides:
      BULL_BUY_CONF, BULL_SELL_CONF, BEAR_BUY_CONF, BEAR_SELL_CONF,
      CHOP_CONF_REQ, CHOP_SKIP
    """
    conf = float(dec.get("confidence", 0.0))
    side = str(dec.get("action", "")).upper()

    bull_buy  = float(os.getenv("BULL_BUY_CONF",  "0.65"))
    bull_sell = float(os.getenv("BULL_SELL_CONF", "0.80"))
    bear_buy  = float(os.getenv("BEAR_BUY_CONF",  "0.80"))
    bear_sell = float(os.getenv("BEAR_SELL_CONF", "0.60"))
    chop_req  = float(os.getenv("CHOP_CONF_REQ",  "0.75"))
    chop_skip = os.getenv("CHOP_SKIP", "0") == "1"

    if regime_label == "bull":
        if side == "BUY"  and conf >= bull_buy:  return True, "bull-buy"
        if side == "SELL" and conf >= bull_sell: return True, "bull-sell"
        return False, f"bull gate: conf={conf:.2f}/{bull_buy if side=='BUY' else bull_sell:.2f}"

    if regime_label == "bear":
        if side == "SELL" and conf >= bear_sell: return True, "bear-sell"
        if side == "BUY"  and conf >= bear_buy:  return True, "bear-buy"
        return False, f"bear gate: conf={conf:.2f}/{bear_sell if side=='SELL' else bear_buy:.2f}"

    # chop
    if chop_skip:
        return False, "chop-skip"
    return (conf >= chop_req), f"chop gate conf={conf:.2f}/{chop_req:.2f}"

