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
    If today's PnL is below −X% of reference equity, block BUYs (default 5% of $10k).
    """
    cap_pct     = _f(env, "MAX_DAILY_LOSS_PCT", "5.0") / 100.0
    equity_ref  = _f(env, "EQUITY_REF_USD", "10000")
    if pnl_today_usd < 0 and abs(pnl_today_usd) > cap_pct * equity_ref:
        return False, "daily_loss_cap"
    return True, "ok"

__all__ = ["global_pause", "position_limits", "daily_loss_cap"]

