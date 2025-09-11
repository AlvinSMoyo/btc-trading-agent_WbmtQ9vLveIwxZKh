"""app/engine.py — portfolio state & execution (paper mode)

- Robust get_last_close(): handles yfinance DataFrame shapes (single- and MultiIndex)
- Paper trade execution (paper_fill) with ledger/state persistence
- Minimal state helpers (load_state/save_state)

Design notes:
- Paths come from STATE_DIR env var (defaults to Drive path)
- Ledger includes a 'note' column for LLM rationale
- All functions have docstrings; inline comments explain *why* when non-obvious
"""

from __future__ import annotations
import os, json, csv, time
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

# ---------- Paths ----------
STATE_DIR = Path(os.getenv("STATE_DIR", "/content/drive/MyDrive/btc-trading-agent/state"))
LEDGER_PATH = STATE_DIR / "trades.csv"
STATE_PATH  = STATE_DIR / "portfolio_state.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# ---------- File init ----------
def _init_files() -> None:
    """Ensure state & ledger exist and have the expected columns."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not LEDGER_PATH.exists():
        # include 'note' column for rationale (LLM reason, etc.)
        with open(LEDGER_PATH, "w", newline="") as f:
            csv.writer(f).writerow(["ts","side","reason","price","qty_btc","fee_usd","note"])
    if not STATE_PATH.exists():
        # default $10k starting cash; can be overridden later by config module
        STATE_PATH.write_text(json.dumps({
            "cash_usd": 10000.0,
            "btc": 0.0,
            "last_dca_price": None,
            "active_swing": None,
            "trades_today": 0,
            "trades_today_date": None,
            "last_trade_ts": None
        }, indent=2))

# ---------- State I/O ----------
def load_state() -> dict:
    """Load (and if needed, initialize) the persistent paper state."""
    _init_files()
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        # Minimal safe fallback; avoids hard crashes if file is malformed
        return {
            "cash_usd": 10000.0, "btc": 0.0,
            "last_dca_price": None, "active_swing": None,
            "trades_today": 0, "trades_today_date": None,
            "last_trade_ts": None
        }

def save_state(s: dict) -> None:
    """Persist the current state JSON."""
    STATE_PATH.write_text(json.dumps(s, indent=2))

# ---------- Market helpers ----------
def get_last_close(candles: pd.DataFrame) -> float:
    """Return the latest close as float from a yfinance candles DataFrame.

    Handles:
      • Single-index columns with 'Close' or 'Adj Close' (case-insensitive)
      • MultiIndex columns where *any* level equals 'Close'/'Adj Close'
        regardless of level order (ticker-first or field-first)
      • Fallback: last numeric column

    Raises:
        ValueError if no numeric data can be found.
    """
    if candles is None or len(candles) == 0:
        raise ValueError("Empty candles frame")

    def _series_to_float_last(s: pd.Series) -> float:
        s = pd.to_numeric(s, errors="coerce").dropna()
        if s.empty:
            raise ValueError("No numeric close values")
        return float(s.iloc[-1])

    cols = candles.columns

    if isinstance(cols, pd.MultiIndex):
        # Try each level to locate 'Close' or 'Adj Close'
        for level in range(cols.nlevels):
            for key in ("Close","close","Adj Close","adj close"):
                try:
                    sub = candles.xs(key, axis=1, level=level)
                    # xs may return a DataFrame (multiple tickers) — pick first column
                    s = sub.iloc[:, 0] if isinstance(sub, pd.DataFrame) else sub
                    return _series_to_float_last(s)
                except Exception:
                    pass
        # Fallback: pick last numeric column across all columns
        num = candles.select_dtypes(include="number")
        if num.shape[1] > 0:
            return _series_to_float_last(num.iloc[:, -1])
        # Last resort: coerce the last column
        return _series_to_float_last(pd.Series(candles.iloc[:, -1]))
    else:
        # Single level: prefer Close → Adj Close
        lower = [str(c).lower() for c in cols]
        if "close" in lower:
            return _series_to_float_last(candles.iloc[:, lower.index("close")])
        if "adj close" in lower:
            return _series_to_float_last(candles.iloc[:, lower.index("adj close")])
        # Fallback: last numeric column
        num = candles.select_dtypes(include="number")
        if num.shape[1] > 0:
            return _series_to_float_last(num.iloc[:, -1])
        return _series_to_float_last(pd.Series(candles.iloc[:, -1]))

# ---------- Execution ----------
def paper_fill(side: str, reason: str, price: float, qty_btc: float, fee_bps: float = 10.0, note: str = ""):
    """Simulate a trade and persist to ledger & state.

    Args:
        side: 'buy' or 'sell'
        reason: e.g., 'DCA', 'LLM', 'ATR Stop'
        price: fill price in USD per BTC
        qty_btc: quantity of BTC to buy/sell
        fee_bps: fee in basis points (default 10 = 0.10%)
        note: optional rationale (e.g., LLM reason_short)

    Returns:
        (ok, info_or_err): ok=True with dict details, else False with error string.
    """
    _init_files()
    s = load_state()
    side = (side or "").lower()
    price = float(price)
    qty_btc = float(qty_btc)
    notional = price * qty_btc
    fee_usd = notional * (float(fee_bps) / 10000.0)

    # Guard against overspending/overselling with tiny epsilons for float safety
    if side == "buy":
        if s["cash_usd"] + 1e-8 < notional + fee_usd:
            return False, "Insufficient cash"
        s["cash_usd"] -= (notional + fee_usd)
        s["btc"] += qty_btc
    elif side == "sell":
        if s["btc"] + 1e-12 < qty_btc:
            return False, "Insufficient BTC"
        s["btc"] -= qty_btc
        s["cash_usd"] += (notional - fee_usd)
    else:
        return False, f"Unknown side: {side}"

    # Unix ts for easy downstream processing
    ts = int(time.time())
    with open(LEDGER_PATH, "a", newline="") as f:
        csv.writer(f).writerow([
            ts, side, reason, round(price,2), round(qty_btc,8), round(fee_usd,2), note
        ])
    save_state(s)
    return True, {
        "ts": ts, "side": side, "reason": reason,
        "price": price, "qty_btc": qty_btc, "fee": fee_usd, "note": note
    }
