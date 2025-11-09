# app/exchange.py
from __future__ import annotations
import os, time
import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

try:
    import ccxt  # type: ignore
except Exception:
    ccxt = None

def _bool(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1","true","yes","on","y","t"}

def _get_keys() -> Dict[str, str]:
    return {
        "key": os.getenv("BINANCE_API_KEY", ""),
        "secret": os.getenv("BINANCE_API_SECRET", ""),
    }

def get_exchange() -> Optional[Any]:
    """
    Return a ccxt binance spot client if ccxt + keys exist; otherwise None.
    """
    if ccxt is None:
        log.info("[exec] skip: ccxt not available")
        return None
    ks = _get_keys()
    if not ks["key"] or not ks["secret"]:
        log.info("[exec] skip: missing BINANCE_API_KEY/SECRET (paper mode?)")
        return None

    ex = ccxt.binance({
        "apiKey": ks["key"],
        "secret": ks["secret"],
        "enableRateLimit": True,
        "options": {"adjustForTimeDifference": True}
    })
    # spot mode (not futures)
    return ex

def _ensure_slash_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace("-", "/")
    if "/" not in s:
        # assume quote is USDT if not specified
        s = s + "/USDT"
    return s

def _min_usd_notional() -> float:
    # user-configurable minimum; default 5 USD to be safe
    return float(os.getenv("ORDER_MIN_NOTIONAL_USD", os.getenv("BUY_MIN_USD", "5")) or "5")

def place_market_usd(side: str, usd: float, symbol: str = "BTC/USDT") -> Dict[str, Any]:
    """
    Market order by USD notional. Returns a dict with status + echo fields.
    If exchange/keys missing, logs and returns a 'skipped' status.
    """
    side_u = str(side).upper()
    symbol_s = _ensure_slash_symbol(symbol)
    try:
        usd_val = float(usd)
    except Exception:
        usd_val = 0.0

    min_notional = _min_usd_notional()
    if usd_val <= 0:
        msg = f"invalid_notional:{usd_val:.2f}"
        log.info("[exec] skip: %s", msg)
        return {"ok": False, "skipped": True, "reason": msg}

    if usd_val < min_notional:
        msg = f"below_min_notional:{usd_val:.2f} < {min_notional:.2f}"
        log.info("[exec] skip: %s", msg)
        return {"ok": False, "skipped": True, "reason": msg}

    ex = get_exchange()
    if ex is None:
        # Soft skip (paper)
        log.info("[exec] paper: side=%s notional=%.2f symbol=%s", side_u, usd_val, symbol_s)
        return {"ok": False, "skipped": True, "reason": "paper_or_missing_keys",
                "side": side_u, "notional": usd_val, "symbol": symbol_s}

    # Fetch price to convert notional -> amount
    ticker = ex.fetch_ticker(symbol_s)
    price = float(ticker["last"])
    amount = usd_val / max(price, 1e-12)

    log.info("[exec] market attempt side=%s symbol=%s notional=%.2f price=%.2f amount=%.8f",
             side_u, symbol_s, usd_val, price, amount)

    # ccxt uses create_order(symbol, type, side, amount, price=None, params={})
    order = ex.create_order(symbol_s, "market", side_u.lower(), amount)
    oid = order.get("id", "")
    log.info("[order] placed id=%s side=%s symbol=%s amount=%.8f", oid, side_u, symbol_s, amount)
    return {"ok": True, "skipped": False, "id": oid, "side": side_u,
            "symbol": symbol_s, "amount": amount, "price_hint": price}

