# app/config/schema.py
from __future__ import annotations
from typing import Any, Dict

# Canonical defaults (safe, conservative)
DEFAULTS: Dict[str, Any] = {
    # DCA
    "DCA_DROP_PCT": 3.0,
    "DCA_LOT_USD":  50.0,
    "DCA_MIN_COOLDOWN_MIN": 60,

    # ATR / swing (kept off unless you flip later)
    "ATR_PERIOD": 14,
    "ATR_K": 1.5,
    "SWING_ENABLED": False,

    # Regime / guardrails (Patch 2.5)
    "EMA_SLOPE_BPS_PER_HR_MIN": 2.0,
    "ADX_TREND_MIN": 18.0,
    "CONF_TREND_MIN": 0.55,
    "CONF_CHOP_MIN": 0.70,
    "REGIME_MIN_HOURS": 36,

    # LLM advisor knobs
    "LLM_SIZE_USD": 300.0,
    "LLM_STOP_ATR_K_DEFAULT": 1.3,
    "LLM_MIN_CONFIDENCE": 0.60,

    # Budget / cooldown
    "CASH_FLOOR_USD": 2000.0,
    "DAILY_BUY_LIMIT_USD": 5000.0,
    "COOLDOWN_MIN": 5.0,

    # Symbol / interval (runner defaults)
    "SYMBOL": "BTC-USD",
    "INTERVAL_MINUTES": 30,
}

# Type coercion rules (key -> callable)
COERCE = {
    "DCA_DROP_PCT": float,
    "DCA_LOT_USD": float,
    "DCA_MIN_COOLDOWN_MIN": int,
    "ATR_PERIOD": int,
    "ATR_K": float,
    "SWING_ENABLED": lambda x: str(x).strip().lower() in ("1","true","yes","on"),
    "EMA_SLOPE_BPS_PER_HR_MIN": float,
    "ADX_TREND_MIN": float,
    "CONF_TREND_MIN": float,
    "CONF_CHOP_MIN": float,
    "REGIME_MIN_HOURS": float,
    "LLM_SIZE_USD": float,
    "LLM_STOP_ATR_K_DEFAULT": float,
    "LLM_MIN_CONFIDENCE": float,
    "CASH_FLOOR_USD": float,
    "DAILY_BUY_LIMIT_USD": float,
    "COOLDOWN_MIN": float,
    "SYMBOL": str,
    "INTERVAL_MINUTES": int,
}

def coerce_key(k: str, v: str):
    fn = COERCE.get(k)
    if not fn:
        return v
    try:
        return fn(v)
    except Exception:
        return DEFAULTS.get(k)
