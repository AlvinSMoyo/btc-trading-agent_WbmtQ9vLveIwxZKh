import os
DEFAULT_INTERVAL_MINUTES = int(os.getenv('INTERVAL_MINUTES', '30'))
_FEED_TRACE = os.getenv('FEED_TRACE','0') not in ('','0','false','False','no','No')
def _t(msg):
    if _FEED_TRACE:
        print(f"[feed] {msg}")
import math
from pathlib import Path
import pandas as pd

# Optional deps
try:
    import yfinance as yf  # type: ignore
except Exception:
    yf = None

try:
    import ccxt  # type: ignore
except Exception:
    ccxt = None

# ------------------- helpers -------------------
def _cache_path(symbol: str, interval_minutes: int) -> Path:
    state_dir = Path(os.getenv("STATE_DIR", "state"))
    d = state_dir / "cache" / "feeds"
    d.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "_").replace("-", "_")
    return d / f"{safe}_{int(interval_minutes)}m.csv"

def _needed_candles(lookback_days: int, interval_minutes: int) -> int:
    return max(50, int((lookback_days * 24 * 60) / max(1, int(interval_minutes))) + 10)

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["Open","High","Low","Close","Volume"]
    # Ensure expected columns exist and types are numeric
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    out = df[cols].copy()
    out.index.name = "time"
    # drop nonsense
    out = out.dropna(subset=["Close"])
    return out

def _map_yf_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace(" ", "")
    if raw in ("BTC/USDT","BTCUSDT","BTCUSD","BTC-USD"):
        return "BTC-USD"
    # generic mapping, e.g. ETH/USDT -> ETH-USDT
    return raw.replace("/", "-")

def _tf_str(minutes: int) -> str:
    m = int(minutes) if str(minutes).isdigit() else 30
    if m in (1,2,5,15,30):
        return f"{m}m"
    if m in (60, 90):
        return "60m" if m == 60 else "90m"
    if m in (120, 240):
        return "60m"  # yfinance limitation; will still give usable data
    return "30m"

# ------------------- sources -------------------
def _yfinance_ohlcv(symbol: str, lookback_days: int, interval_minutes: int) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance not available")
    yf_symbol = _map_yf_symbol(symbol)
    interval_minutes = DEFAULT_INTERVAL_MINUTES if interval_minutes is None else int(interval_minutes)
    tf = _tf_str(interval_minutes)
    period = f"{max(1, int(lookback_days))}d"
    df = yf.download(yf_symbol, period=period, interval=tf, progress=False,
                     auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise RuntimeError("yfinance returned empty")
    # YF columns: Open, High, Low, Close, Adj Close, Volume
    df = df.rename_axis("time")
    cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"yfinance missing columns: {missing}")
    df = df[cols].copy()
    # make index timezone-aware UTC
    df.index = pd.to_datetime(df.index, utc=True)
    return _normalize_ohlcv(df)

def _binance_ohlcv(symbol: str, interval_minutes: int, limit: int) -> pd.DataFrame:
    if ccxt is None:
        raise RuntimeError("ccxt not available")
    ex = ccxt.binance()
    s = symbol.strip().upper().replace(" ", "")
    # ensure slash form for ccxt
    if "/" not in s:
        if s in ("BTCUSDT","BTCUSD","BTC-USD"):
            s = "BTC/USDT"
        else:
            s = s.replace("-", "/")
    # ccxt timeframe
    m = int(interval_minutes)
    timeframe = {1:"1m",5:"5m",15:"15m",30:"30m",60:"1h"}.get(m, "30m")
    ohlcv = ex.fetch_ohlcv(s, timeframe=timeframe, limit=int(limit))
    raw = pd.DataFrame(ohlcv, columns=["time","Open","High","Low","Close","Volume"])
    raw["time"] = pd.to_datetime(raw["time"], unit="ms", utc=True)
    raw = raw.set_index("time")
    return _normalize_ohlcv(raw)

# ------------------- public API -------------------
# kept for backward compatibility with runner import
def fetch_yfinance(symbol: str, lookback_days: int = 30, interval_minutes: int | None = None) -> pd.DataFrame:
    """
    Unified feed loader respecting FEED_ORDER.
    Default: yfinance,binance,cache (env can override)
    """
    order = os.getenv("FEED_ORDER", "yfinance,binance,cache").split(",")
    order = [s.strip().lower() for s in order if s.strip()]
    need  = min(1500, _needed_candles(lookback_days, interval_minutes))
    cache = _cache_path(symbol, interval_minutes)

    last_err = None
    for src in order:
        try:
            if src == "yfinance":
                df = _yfinance_ohlcv(symbol, lookback_days, interval_minutes)
            elif src == "binance":
                df = _binance_ohlcv(symbol, interval_minutes, need)
            elif src == "kraken":
                df = _kraken_ohlcv(symbol, interval_minutes, need)  # only if you really have this
            elif src == "cache":
                if cache.exists():
                    df = pd.read_csv(cache, parse_dates=["time"]).set_index("time")
                else:
                    raise RuntimeError("cache miss")
            else:
                continue

            # write-through cache (best effort)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                df.reset_index().to_csv(cache, index=False)
            except Exception:
                pass

            return df

        except Exception as e:
            last_err = e
            try:
                _t(f"fail {src} → {type(e).__name__}: {str(e)[:120]}")
            except Exception:
                pass

    raise RuntimeError(f"No data source available ({last_err})")

# === FEED_TRACE_PATCH v1 (do not remove) ===
# Safe wrappers that print where OHLCV actually comes from, with row counts.
import os, sys, datetime

def _feed_trace(msg: str):
    try:
        print(f"[feed] {msg}", flush=True)
    except Exception:
        # Never let logging crash the feed
        pass

# ---- wrap fetch_yfinance ----
if 'fetch_yfinance' in globals() and '_orig_fetch_yfinance' not in globals():
    _orig_fetch_yfinance = fetch_yfinance
    def fetch_yfinance(*args, **kwargs):
        sym = (args[0] if args else kwargs.get('symbol', '?'))
        interval = (args[2] if len(args) > 2 else kwargs.get('interval_minutes'))
        order = os.getenv("FEED_ORDER", "binance,yfinance,kraken,cache")
        _feed_trace(f"call fetch_yfinance symbol={sym} interval_min={interval} order={order}")
        try:
            df = _orig_fetch_yfinance(*args, **kwargs)
            try:
                start = df.index.min()
                end   = df.index.max()
                rows  = len(df)
                _feed_trace(f"ok fetch_yfinance rows={rows} start={start} end={end}")
            except Exception as e:
                _feed_trace(f"ok fetch_yfinance (could not summarize df: {type(e).__name__})")
            return df
        except Exception as e:
            _feed_trace(f"error fetch_yfinance: {type(e).__name__}: {e}")
            raise

# ---- wrap _binance_ohlcv ----
if '_binance_ohlcv' in globals() and '_orig__binance_ohlcv' not in globals():
    _orig__binance_ohlcv = _binance_ohlcv
    def _binance_ohlcv(*args, **kwargs):
        try:
            sym = args[0] if args else kwargs.get('symbol', '?')
            itv = (args[1] if len(args) > 1 else kwargs.get('interval_minutes'))
            _feed_trace(f"enter _binance_ohlcv symbol={sym} interval_min={itv}")
        except Exception:
            _feed_trace("enter _binance_ohlcv")

        df = _orig__binance_ohlcv(*args, **kwargs)

        try:
            _feed_trace(f"exit  _binance_ohlcv rows={len(df)}")
        except Exception:
            _feed_trace("exit  _binance_ohlcv (no len)")
        return df

# ---- wrap _yfinance_ohlcv (if your file has it) ----
if '_yfinance_ohlcv' in globals() and '_orig__yfinance_ohlcv' not in globals():
    _orig__yfinance_ohlcv = _yfinance_ohlcv
    def _yfinance_ohlcv(*args, **kwargs):
        try:
            sym = args[0] if args else kwargs.get('symbol','?')
            itv = (args[2] if len(args)>2 else kwargs.get('interval_minutes'))
            _feed_trace(f"enter _yfinance_ohlcv symbol={sym} interval_min={itv}")
        except Exception:
            _feed_trace("enter _yfinance_ohlcv")
        df = _orig__yfinance_ohlcv(*args, **kwargs)
        try:
            _feed_trace(f"exit  _yfinance_ohlcv rows={len(df)}")
        except Exception:
            _feed_trace("exit  _yfinance_ohlcv (no len)")
        return df

# ---- wrap _kraken_ohlcv (if present) ----
if '_kraken_ohlcv' in globals() and '_orig__kraken_ohlcv' not in globals():
    _orig__kraken_ohlcv = _kraken_ohlcv
    def _kraken_ohlcv(*args, **kwargs):
        try:
            sym = args[0] if args else kwargs.get('symbol','?')
            itv = (args[2] if len(args)>2 else kwargs.get('interval_minutes'))
            _feed_trace(f"enter _kraken_ohlcv symbol={sym} interval_min={itv}")
        except Exception:
            _feed_trace("enter _kraken_ohlcv")
        df = _orig__kraken_ohlcv(*args, **kwargs)
        try:
            _feed_trace(f"exit  _kraken_ohlcv rows={len(df)}")
        except Exception:
            _feed_trace("exit  _kraken_ohlcv (no len)")
        return df
