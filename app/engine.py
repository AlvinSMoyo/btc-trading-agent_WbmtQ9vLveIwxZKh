"""app/engine.py â€” portfolio state & execution (paper mode)

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
RAW_LEDGER_PATH = STATE_DIR / "trades_with_balances.csv"
STATE_PATH  = STATE_DIR / "portfolio_state.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)

def _append_human_trade_row(time_iso, side, source, price, qty_btc, fee_usd, note):
    """Append to state/trades.csv in the format reports expect."""
    trades_path = STATE_DIR / "trades.csv"
    need_header = not trades_path.exists()
    with trades_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if need_header:
            w.writerow(["time","side","source","price","qty_btc","fee","note"])
        w.writerow([
            time_iso,
            side.upper(),
            source.upper(),
            f"{float(price):.2f}",
            f"{float(qty_btc):.8f}",
            f"{float(fee_usd):.2f}",
            note or "",
        ])

# ---------- File init ----------
def _init_files() -> None:
    """Ensure state & ledger exist and have the expected columns."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not RAW_LEDGER_PATH.exists():
        with RAW_LEDGER_PATH.open("w", newline="") as f:
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
            "last_trade_ts": None,
            "last_side": None,
            "last_conf": None
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
            "last_trade_ts": None,
            "last_side": None,
            "last_conf": None,
        }


def save_state(s: dict) -> None:
    """Persist the current state JSON."""
    STATE_PATH.write_text(json.dumps(s, indent=2))

# ---------- Market helpers ----------
def get_last_close(candles: pd.DataFrame) -> float:
    """Return the latest close as float from a yfinance candles DataFrame.

    Handles:
      â€¢ Single-index columns with 'Close' or 'Adj Close' (case-insensitive)
      â€¢ MultiIndex columns where *any* level equals 'Close'/'Adj Close'
        regardless of level order (ticker-first or field-first)
      â€¢ Fallback: last numeric column

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
                    # xs may return a DataFrame (multiple tickers) â€” pick first column
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
        # Single level: prefer Close â†’ Adj Close
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
def paper_fill(side: str, reason: str, price: float, qty_btc: float,
               fee_bps: float = 10.0, note: str = ""):
    """Simulate a trade and persist to (a) raw audit ledger and (b) report CSV.

    - Raw audit ledger â†’ state/trades_with_balances.csv (machine-friendly)
    - Reports CSV      â†’ state/trades.csv (what weekly/overlay expect)
    """
    _init_files()

    s = load_state()
    side    = (side or "").lower()
    reason  = (reason or "").strip().upper()   # <â€” normalize so ledger Source is LLM/DCA
    price   = float(price)
    qty_btc = float(qty_btc)

    notional = price * qty_btc
    fee_usd  = notional * (float(fee_bps) / 10000.0)

    # Risk guards
    if side == "buy":
        if s["cash_usd"] + 1e-8 < notional + fee_usd:
            return False, "Insufficient cash"
        s["cash_usd"] -= (notional + fee_usd)
        s["btc"]      += qty_btc
    elif side == "sell":
        if s["btc"] + 1e-12 < qty_btc:
            return False, "Insufficient BTC"
        s["btc"]      -= qty_btc
        s["cash_usd"] += (notional - fee_usd)
    else:
        return False, f"Unknown side: {side}"

    # Timestamps
    ts     = int(time.time())
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # (A) Append the human/report row (explicit SOURCE = LLM/DCA/etc.)
    _append_human_trade_row(
        time_iso=ts_iso,
        side=side,
        source=reason,          # show as LLM / DCA / MANUAL / ATR STOP ...
        price=price,
        qty_btc=qty_btc,
        fee_usd=fee_usd,
        note=note,
    )

    # (B) Append the raw/audit row (machine-friendly)
    with RAW_LEDGER_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            ts,
            side,
            reason,
            round(price, 2),
            round(qty_btc, 8),
            round(fee_usd, 2),
            note,
        ])

    save_state(s)

    return True, {
        "ts": ts,
        "time": ts_iso,
        "side": side,
        "source": reason.upper(),
        "price": price,
        "qty_btc": qty_btc,
        "fee": fee_usd,
        "note": note,
    }

# ---------- Env helpers ----------
def _get_env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default

def _now_ts() -> float:
    return time.time()

def portfolio_value_usd(price: float, s: dict | None = None) -> float:
    if s is None:
        s = load_state()
    return float(s.get("cash_usd", 0.0)) + float(s.get("btc", 0.0)) * float(price)

def position_usd(price: float, s: dict | None = None) -> float:
    if s is None:
        s = load_state()
    return float(s.get("btc", 0.0)) * float(price)


# ---------- Trade gate (adaptive cooldown + flip hysteresis) ----------
from dataclasses import dataclass
import math
from typing import Optional

@dataclass
class GateContext:
    now_ts: float
    last_ts: Optional[float]
    last_side: Optional[str]
    last_conf: Optional[float]
    rsi: Optional[float]
    atr: Optional[float]
    spread_bps: Optional[float]
    position_usd: float
    max_position_usd: float
    max_spread_bps: float
    min_conf: float
    allow_side_switch: bool

def _adaptive_cooldown_sec(atr: Optional[float]) -> int:
    # calm â†’ short cooldown; choppy â†’ longer. 5s..60s
    if not atr or not math.isfinite(atr):
        return 10
    return int(max(5, min(60, atr / 2.0)))

def allow_trade(ctx: GateContext, side: str, conf: float, notional_usd: float, reason_out: list[str]) -> bool:
    # 1) confidence
    if conf < ctx.min_conf:
        reason_out.append(f"conf {conf:.2f} < {ctx.min_conf:.2f}")
        return False

    # 2) exposure cap (buys only)
    if side == "buy" and ctx.position_usd >= ctx.max_position_usd:
        reason_out.append(f"pos {ctx.position_usd:.0f} >= cap {ctx.max_position_usd:.0f}")
        return False

    # 3) spread quality
    if ctx.spread_bps is not None and ctx.spread_bps > ctx.max_spread_bps:
        reason_out.append(f"spread {ctx.spread_bps:.1f}bps > {ctx.max_spread_bps}bps")
        return False

    # 4) min notional will be checked by caller after sizing; gate is agnostic here

    # 5) adaptive cooldown with flip hysteresis (+10% absolute conf rise required)
    if ctx.last_ts is not None:
        cd = _adaptive_cooldown_sec(ctx.atr)
        elapsed = max(0, ctx.now_ts - ctx.last_ts)
        if elapsed < cd:
            if (
                ctx.allow_side_switch and
                (ctx.last_side or side) != side and
                ctx.last_conf is not None and
                conf >= (ctx.last_conf + 0.10)
            ):
                pass  # allow opportunistic flip inside cooldown
            else:
                reason_out.append(f"cooldown {int(cd - elapsed)}s left")
                return False

    return True


# ---------- Order builder (confidence-scaled size + ATR stop/TP) ----------
def build_order(side: str, price: float, conf: float, atr: Optional[float]) -> dict:
    """
    Returns: dict(size_usd, stop, take_profit)
    - size scales with confidence (0.45â†’0.2x ... 1.00â†’1.0x of MAX_TRADE_USD)
    - attaches ATR stop/TP (risk:R â‰ˆ 1:1.5). Uses fallback ATR=50 if missing.
    """
    max_trade = _get_env_float("MAX_TRADE_USD", 50.0)
    # map [0.45..1.00] -> [0.2..1.0]
    k = max(0.2, min(1.0, (conf - 0.45) / 0.55))
    size_usd = max(10.0, min(max_trade, k * max_trade))

    a = atr if (atr and atr > 0) else 50.0
    if side == "buy":
        stop = price - 1.2 * a
        tp   = price + 1.8 * a
    else:
        stop = price + 1.2 * a
        tp   = price - 1.8 * a

    return {"size_usd": float(size_usd), "stop": float(stop), "take_profit": float(tp)}


# ---------- Single call from your loop ----------
def try_execute_trade(
    side: str,
    conf: float,
    reason: str,
    price: float,
    atr: Optional[float],
    spread_bps: Optional[float],
    rsi: Optional[float] = None,
    note: str = ""
):
    """
    The only function your loop needs to call.
    - Builds order (size + stop/TP)
    - Runs gate with adaptive cooldown & flip hysteresis
    - Executes via paper_fill (converts size_usd -> qty_btc)
    - Updates state last_* fields for next tick
    Returns: (ok, info_or_reason_string)
    """
    side = (side or "").lower().strip()
    if side not in ("buy","sell"):
        return False, f"invalid side: {side}"

    s = load_state()
    now = _now_ts()
    pos_usd = position_usd(price, s)

    # env rails
    min_conf         = _get_env_float("MIN_CONF", 0.45)
    max_spread_bps   = _get_env_float("MAX_SPREAD_BPS", 12.0)
    max_position_usd = _get_env_float("MAX_POSITION_USD", 3000.0)
    min_notional_usd = _get_env_float("MIN_NOTIONAL_USD", 10.0)
    allow_flip       = os.getenv("ALLOW_SIDE_SWITCH", "1") == "1"

    # build order first so we know intended notional
    order = build_order(side, price, conf, atr)
    notional = order["size_usd"]
    if notional < min_notional_usd:
        return False, f"notional {notional:.2f} < min_notional {min_notional_usd:.2f}"

    # gate
    reasons: list[str] = []
    ctx = GateContext(
        now_ts=now,
        last_ts=s.get("last_trade_ts"),
        last_side=s.get("last_side"),
        last_conf=s.get("last_conf"),
        rsi=rsi, atr=atr, spread_bps=spread_bps,
        position_usd=pos_usd,
        max_position_usd=max_position_usd,
        max_spread_bps=max_spread_bps,
        min_conf=min_conf,
        allow_side_switch=allow_flip,
    )
    if not allow_trade(ctx, side, conf, notional, reasons):
        return False, "; ".join(reasons) if reasons else "blocked"

    # convert to qty and execute
    qty_btc = order["size_usd"] / float(price)

    # Keep reason strictly as 'LLM' here; put the descriptive tag in note
    detail = (note or reason or "").strip()  # 'reason' may be the tactical tag like "RSI overbought"
    ok, info = paper_fill(
        side=side,
        reason="LLM",  # <â€” enforce Source
        price=price,
        qty_btc=qty_btc,
        fee_bps=10.0,
        note=(detail + f" | conf={conf:.2f} atr={atr or 0:.2f} "
          	  f"stop={order['stop']:.2f} tp={order['take_profit']:.2f}").strip()
    )
    if not ok:
        return False, str(info)

    # update last_* in state for next tick
    s["last_trade_ts"] = now
    s["last_side"]     = side
    s["last_conf"]     = float(conf)
    save_state(s)

    return True, info

