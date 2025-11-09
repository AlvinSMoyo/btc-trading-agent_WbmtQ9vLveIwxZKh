# app/risk/guardrails.py
from __future__ import annotations
import os
from typing import Tuple

_TRUE = {"1", "true", "yes", "on", "y", "t"}

def _is_true(v) -> bool:
    return str(v).strip().lower() in _TRUE

def _f(env, key, default: str) -> float:
    try:
        return float(env.get(key, default))
    except Exception:
        return float(default)

def global_pause(env=os.environ) -> Tuple[bool, str]:
    """Hard pause switch via env (future: sheet-backed)."""
    if _is_true(env.get("GLOBAL_PAUSE", "0")):
        return True, "global_pause_switch"
    return False, "ok"

def position_limits(position_btc: float, price: float, equity_usd: float, env=os.environ) -> Tuple[bool, str]:
    """
    Cap BTC exposure as % of equity (default 80%).
    Blocks additional BUY exposure if breached.
    """
    max_pos = _f(env, "MAX_POSITION_PCT", "80.0") / 100.0
    pos_val = max(0.0, float(position_btc)) * max(0.0, float(price))
    if equity_usd <= 0:
        return True, "no_equity_cap"
    if pos_val > equity_usd * max_pos:
        return False, f"pos_cap {pos_val:.2f} > {max_pos*100:.0f}% of equity"
    return True, "ok"

def daily_loss_cap(pnl_today_usd: float, env=os.environ) -> Tuple[bool, str]:
    """
    Guard: block BUYs if today's PnL breaches configured loss limits.

    Supports two modes (absolute or percent):
      1) DAILY_LOSS_CAP_USD = 250
         -> block if pnl_today_usd <= -250
      2) MAX_DAILY_LOSS_PCT = 5, EQUITY_REF_USD = 10000
         -> block if loss > 5% of $10k

    If neither is set (>0), the cap is effectively disabled.
    Always returns (passed: bool, reason: str)
    """
    # --- Absolute USD cap first (takes priority if set) ---
    try:
        abs_cap = float(env.get("DAILY_LOSS_CAP_USD", "0") or "0")
    except Exception:
        abs_cap = 0.0

    if abs_cap > 0:
        if pnl_today_usd <= -abs_cap:
            return False, f"hit_abs_cap:{abs_cap:.2f}"
        return True, "abs_cap_ok"

    # --- Percent-of-equity cap ---
    try:
        cap_pct = float(env.get("MAX_DAILY_LOSS_PCT", "0") or "0")
    except Exception:
        cap_pct = 0.0
    try:
        eq_ref = float(env.get("EQUITY_REF_USD", "0") or "0")
    except Exception:
        eq_ref = 0.0

    if cap_pct > 0 and eq_ref > 0:
        if pnl_today_usd < 0 and abs(pnl_today_usd) > (cap_pct / 100.0) * eq_ref:
            return False, f"hit_pct_cap:{cap_pct:.2f}%~{eq_ref:.2f}"
        return True, "pct_cap_ok"

    # --- Disabled if nothing configured ---
    return True, "disabled"


__all__ = ["global_pause", "position_limits", "daily_loss_cap"]

