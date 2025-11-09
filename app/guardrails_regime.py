# app/guardrails_regime.py
from __future__ import annotations

import os
import math
import datetime as dt
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

# ============================================================
# EMA / ADX / Regime (stable, configurable)  â€” Patch 2.5
# ============================================================
def _ema_series(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _ema_array(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(arr, dtype=float)
    out[0] = float(arr[0])
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i-1]
    return out

def _ensure_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common vendor column variants; ensure open/high/low/close/volume exist."""
    rename = {
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Adj Close": "close",
        "Volume": "volume", "Price": "close", "Last": "close"
    }
    df = df.rename(columns={c: rename.get(c, c) for c in df.columns})

    if "close" not in df.columns and "price" in df.columns:
        df["close"] = df["price"]

    for c in ("open", "high", "low"):
        if c not in df.columns and "close" in df.columns:
            df[c] = df["close"]

    if "volume" not in df.columns:
        df["volume"] = 0.0

    return df[["open", "high", "low", "close", "volume"]].copy()

def _adx14_hourly(df_h: pd.DataFrame) -> Optional[float]:
    """Return latest ADX(14) from hourly OHLC, or None if not computable."""
    req = {"high", "low", "close"}
    if not req.issubset(df_h.columns):
        return None

    high, low, close = df_h["high"].astype(float), df_h["low"].astype(float), df_h["close"].astype(float)
    if len(close) < 16:
        return None

    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = np.where((up_move > down_move) & (up_move > 0),  up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1
    ).max(axis=1).to_numpy()

    n = 14
    # Wilder smoothing
    def _wilder(x: np.ndarray) -> np.ndarray:
        if len(x) < n:
            return np.array([], dtype=float)
        out = np.zeros_like(x, dtype=float)
        out[n-1] = x[:n].sum()
        for i in range(n, len(x)):
            out[i] = out[i-1] - (out[i-1] / n) + x[i]
        # drop the initial pre-window zeros
        return out[out != 0]

    tr_s   = _wilder(tr[1:])  # align with DM lengths
    pdm_s  = _wilder(plus_dm[1:])
    mdm_s  = _wilder(minus_dm[1:])
    if len(tr_s) == 0:
        return None

    pdi = 100.0 * (pdm_s / np.maximum(tr_s, 1e-12))
    mdi = 100.0 * (mdm_s / np.maximum(tr_s, 1e-12))
    dx  = 100.0 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    if len(dx) < n:
        return None
    adx = _ema_array(dx, n)
    return float(adx[-1])

def _ema200_slope_bps_per_hour(c_close: pd.Series, interval_minutes: int = 30, span: int = 200) -> float:
    """Compute EMA200 slope over last few points, convert to bps/hour."""
    c = c_close.astype(float).dropna()
    if len(c) < max(5, span // 4):
        return 0.0
    ema = _ema_series(c, span).dropna()
    if len(ema) < 3:
        return 0.0
    w = min(5, len(ema) - 1)  # last ~5 points
    y1, y2 = float(ema.iloc[-w]), float(ema.iloc[-1])
    if y1 <= 0:
        return 0.0
    pct = (y2 - y1) / y1  # fraction
    hours = (w - 1) * (interval_minutes / 60.0)
    if hours <= 0:
        return 0.0
    return float((pct / hours) * 10000.0)  # bps/hour

def _read_thresholds() -> Dict[str, float]:
    return {
        "EMA_SLOPE_BPS_PER_HR_MIN": float(os.getenv("EMA_SLOPE_BPS_PER_HR_MIN", "2.0")),
        "ADX_TREND_MIN":            float(os.getenv("ADX_TREND_MIN", "18")),
        "CONF_TREND_MIN":           float(os.getenv("CONF_TREND_MIN", "0.55")),
        "CONF_CHOP_MIN":            float(os.getenv("CONF_CHOP_MIN", "0.70")),
        "REGIME_MIN_HOURS":         float(os.getenv("REGIME_MIN_HOURS", "36")),
    }

def detect_regime_from_1m(df_1m: pd.DataFrame) -> Dict[str, Any]:
    """
    Inputs:
      df_1m: DatetimeIndex (UTC or naive), columns at least: open,high,low,close,volume
    Output:
      dict with 'label' in {'bull','bear','chop'} + diagnostics.
    """
    if df_1m is None or getattr(df_1m, "empty", True):
        return {"label": "chop", "why": "no_data"}

    th = _read_thresholds()

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
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )

    hist_hours = int(len(df_h))
    short_hist = hist_hours < th["REGIME_MIN_HOURS"]
    if hist_hours == 0:
        return {"label": "chop", "why": "no_hourly_data"}

    c = df_h["close"].astype(float)
    ema50  = _ema_series(c, 50)
    ema200 = _ema_series(c, 200)

    # EMA200 slope in bps/hour (stable)
    slope_bps_hr = _ema200_slope_bps_per_hour(c, interval_minutes=30, span=200)

    # ADX(14) hourly
    adx = _adx14_hourly(df_h)

    # Trend â€œvoteâ€: need BOTH decent slope and ADX, and not short history
    has_slope = abs(slope_bps_hr) >= th["EMA_SLOPE_BPS_PER_HR_MIN"]
    has_adx   = (adx is not None) and (adx >= th["ADX_TREND_MIN"])
    trending  = bool(has_slope and has_adx and (not short_hist))

    # Directional label
    label = "chop"
    if trending:
        if slope_bps_hr > 0 and float(ema50.iloc[-1]) > float(ema200.iloc[-1]):
            label = "bull"
        elif slope_bps_hr < 0 and float(ema50.iloc[-1]) < float(ema200.iloc[-1]):
            label = "bear"
        else:
            # conflicting slope vs cross â†’ treat as chop to be conservative
            label = "chop"

    out: Dict[str, Any] = {
        "label": label,
        "ema50": float(ema50.dropna().iloc[-1]) if len(ema50.dropna()) else None,
        "ema200": float(ema200.dropna().iloc[-1]) if len(ema200.dropna()) else None,
        "ema200_slope_bps_per_hr": float(slope_bps_hr),
        "adx14_h": (None if adx is None else float(adx)),
        "trending": trending,
        "history_hours": hist_hours,
        "short_history": bool(short_hist),
        "need_slope_bps_hr": th["EMA_SLOPE_BPS_PER_HR_MIN"],
        "need_adx": th["ADX_TREND_MIN"],
    }
    return out

# ============================================================
# Guardrails / budget / cooldown (unchanged)
# ============================================================
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
    Returns: (passed: bool, reason: str)
    """
    # --- defaults to avoid UnboundLocalError on skipped branches ---
    cash_floor_gate = True
    cash_floor_reason = "ok"
    daily_cap_gate = True
    daily_cap_reason = "ok"
    cooldown_gate = True
    cooldown_reason = "ok"


    global _last_side_time, _daily_buy_spend
    reset_daily_budget_if_needed(now_utc)

    # envs
    cash_floor   = float(os.getenv("CASH_FLOOR_USD", "3000"))
    buy_cap_day  = float(os.getenv("DAILY_BUY_LIMIT_USD", os.getenv("DAILY_BUY_CAP_USD", "0") or "0"))
    cooldown_min = float(os.getenv("COOLDOWN_MINUTES", os.getenv("COOLDOWN_MIN", "5")))

    side = str(dec.get("action", "")).upper()
    size_usd = float(dec.get("size_usd", 0.0) or 0.0)

    # Per-side cooldown
    if side in ("BUY", "SELL"):
        last_t = _last_side_time.get(side)
        if last_t is not None:
            mins = max(0.0, (now_utc - last_t).total_seconds() / 60.0)
            if mins < cooldown_min:
                cooldown_gate   = False
                cooldown_reason = f"cooldown {side}: {mins:.1f}m<{cooldown_min}m"

    # Cash floor + daily BUY cap
    if side == "BUY":
        if portfolio_cash < cash_floor:
            cash_floor_gate   = False
            cash_floor_reason = f"cash_floor: ${portfolio_cash:,.2f} < ${cash_floor:,.2f}"
        if buy_cap_day > 0 and (_daily_buy_spend + size_usd) > buy_cap_day:
            daily_cap_gate   = False
            daily_cap_reason = f"daily_cap: ${_daily_buy_spend:,.0f}/{buy_cap_day:,.0f}"

    all_ok = bool(cash_floor_gate and daily_cap_gate and cooldown_gate)
    reason = "; ".join((
        f"cash:{cash_floor_reason}",
        f"cap:{daily_cap_reason}",
        f"cooldown:{cooldown_reason}",
    ))
    return all_ok, reason

def note_trade_side_time(side: str) -> None:
    global _last_side_time
    _last_side_time[str(side).upper()] = utc_now()

def apply_daily_buy_accum(side: str, notional_usd: float) -> None:
    global _daily_buy_spend
    if str(side).upper() == "BUY":
        _daily_buy_spend += float(notional_usd)

# ============================================================
# Regime gate (simpler: trend vs chop confidence)
# ============================================================
def regime_gate(decision: dict, regime_label: str, metrics: dict | None = None):
    """
    Hard/soft gating based on detected regime.
    Soft override for 'chop' can be enabled via env:
      REGIME_CHOP_ALLOW_BUY=true
      REGIME_CHOP_RSI_MAX=35        # allow if rsi14 <= this
      REGIME_CHOP_CONF_MIN=0.60     # and decision.confidence >= this
    """
    side = str(decision.get("action", "")).upper()

    # --- CHOP handling ---
    if regime_label == "chop" and side == "BUY":
        allow_soft = os.getenv("REGIME_CHOP_ALLOW_BUY", "false").lower() == "true"
        if allow_soft:
            rsi = None
            if isinstance(metrics, dict):
                try:
                    rsi = float(metrics.get("rsi14")) if metrics.get("rsi14") is not None else None
                except Exception:
                    rsi = None
            conf = float(decision.get("confidence", 0.0) or 0.0)

            rsi_max  = float(os.getenv("REGIME_CHOP_RSI_MAX", "35"))
            conf_min = float(os.getenv("REGIME_CHOP_CONF_MIN", "0.60"))

            # if we can't read rsi, don't block on it
            rsi_ok  = (rsi is None) or (rsi <= rsi_max)
            conf_ok = conf >= conf_min

            if rsi_ok and conf_ok:
                return True, f"chop soft-override (rsi={rsi}, conf={conf})"

            return False, f"chop needs rsi<={rsi_max} & conf>={conf_min} (rsi={rsi}, conf={conf})"

        # default hard behavior
        return False, "chop prefers HOLD | side=BUY"

    # --- Everything else: allow by default here; other guards will decide ---
    return True, "ok"


# --- Safe wrapper to avoid UnboundLocalError in odd code paths ---
def guardrails_pass_safe(dec: Dict[str, Any], portfolio_cash: float, now_utc: dt.datetime) -> tuple[bool, str]:
    try:
        return guardrails_pass(dec, portfolio_cash, now_utc)
    except UnboundLocalError:
        # fallback: conservative "fail closed" with clear reason
        try:
            side = str(dec.get("action","")).upper()
        except Exception:
            side = "?"
        return False, f"guardrails_pass_safe: init-fallback (side={side})"
