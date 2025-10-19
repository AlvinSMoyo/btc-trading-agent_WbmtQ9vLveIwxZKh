# app/feeds.py
from __future__ import annotations

import os
import time
import requests
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------
# State / Cache
# ---------------------------------------------------------------------
STATE_DIR = Path(os.getenv("STATE_DIR", "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(symbol: str, interval_minutes: int) -> Path:
    safe_symbol = symbol.replace("/", "-")
    return STATE_DIR / f"candles_{safe_symbol}_{interval_minutes}m.csv"

def _needed_candles(lookback_days: int, interval_minutes: int) -> int:
    return max(50, int(lookback_days * (24 * 60 / interval_minutes)) + 5)


# ---------------------------------------------------------------------
# Normalization (works for binance / yfinance / kraken / cache)
# ---------------------------------------------------------------------
def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to columns ['Open','High','Low','Close','Volume'] with a UTC DateTimeIndex.
    Handles yfinance MultiIndex columns and various source quirks.
    """
    if df is None or len(df) == 0:
        raise RuntimeError("empty dataframe")

    # Flatten MultiIndex columns if present (yfinance sometimes returns (Ticker, Field))
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.droplevel(0, axis=1)
        except Exception:
            df.columns = df.columns.get_level_values(-1)

    # Common rename map (case-insensitive)
    rename_map = {
        "ts": "time", "date": "time", "datetime": "time",
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "adj close": "Adj Close", "volume": "Volume",
    }
    df.columns = [rename_map.get(str(c).lower(), str(c)) for c in df.columns]

    # If there is a 'time' column, make it the index
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.set_index("time")

    # Force UTC index when possible
    try:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    except Exception:
        # If index is non-datetime, weâ€™ll leave it as-is; downstream wonâ€™t rely on tz ops.
        pass

    # Keep only OHLCV columns that exist
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    if not cols:
        raise RuntimeError("expected OHLCV columns not found")
    df = df[cols]

    # Coerce numerics per column (avoid setting dtype on whole frame)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Clean and sanity check
    df = df.dropna(how="any").sort_index()
    if len(df) < 20:
        raise RuntimeError("insufficient rows after normalization")

    return df


# ---------------------------------------------------------------------
# Source: Binance via CCXT
# ---------------------------------------------------------------------
def _binance_klines(symbol: str, interval_minutes: int, limit: int) -> pd.DataFrame:
    """
    Pull OHLCV from Binance using ccxt and return a normalized DataFrame with columns:
    ['Open','High','Low','Close','Volume'] and UTC index.
    """
    # symbol mapping: BTC-USD -> BTC/USDT for Binance
    sym = symbol.replace("-", "/").replace("USD", "USDT")
    tf_map = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h"}
    tf = tf_map.get(str(interval_minutes), f"{interval_minutes}m")

    ex = ccxt.binance({"enableRateLimit": True, "options": {"adjustForTimeDifference": True}})
    ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=int(limit))
    raw = pd.DataFrame(ohlcv, columns=["time", "Open", "High", "Low", "Close", "Volume"])
    raw["time"] = pd.to_datetime(raw["time"], unit="ms", utc=True)
    df = raw.set_index("time").sort_index()
    return _normalize_ohlcv(df)


# ---------------------------------------------------------------------
# Source: Kraken (public OHLC, supports 30m)
# ---------------------------------------------------------------------
def _kraken_fetch(interval_minutes=30, lookback_days=30) -> pd.DataFrame:
    """
    Pull OHLC from Kraken. Only 30m is wired here as an emergency fallback.
    """
    since = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "XBTUSD", "interval": int(interval_minutes), "since": since}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError("Kraken error: " + ", ".join(j["error"]))
    keys = [k for k in j["result"] if k != "last"]
    rows = j["result"][keys[0]]
    raw = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close", "vwap", "Volume", "count"])
    raw["time"] = pd.to_datetime(raw["time"], unit="s", utc=True)
    df = raw.set_index("time")[["Open", "High", "Low", "Close", "Volume"]]
    return _normalize_ohlcv(df)


# ---------------------------------------------------------------------
# Public Entry: fetch_yfinance
# (kept for backward compatibility with your runner import)
# Strategy: Binance â†’ yfinance â†’ Kraken (30m only) â†’ cache
# ---------------------------------------------------------------------
def fetch_yfinance(symbol: str, lookback_days: int = 30, interval_minutes: int = 30) -> pd.DataFrame:
    cache = _cache_path(symbol, interval_minutes)
    # allow runtime preference without further code edits
    order = os.getenv("FEED_ORDER", "binance,yfinance,kraken,cache").split(",")
    sources = [s.strip().lower() for s in order]

    # BINANCE
    if "binance" in sources:
        try:
            need = min(1500, _needed_candles(lookback_days, interval_minutes))
            last_err = None
            for attempt in range(3):
                try:
                    df = _binance_klines(symbol, interval_minutes, limit=need)
                    try: df.to_csv(cache)
                    except: pass
                    print("[feed] binance klines")
                    return df
                except Exception as e:
                    last_err = e; time.sleep(1.5*(attempt+1))
            raise last_err if last_err else RuntimeError("binance unknown error")
        except Exception as e:
            print(f"[feed] binance failed â†’ {type(e).__name__}: {e}")

    # YFINANCE
    if "yfinance" in sources:
        try:
            import yfinance as yf
            end = datetime.now(timezone.utc); start = end - timedelta(days=lookback_days)
            interval = f"{interval_minutes}m"
            last_err = None
            for attempt in range(3):
                try:
                    raw = yf.download(symbol, start=start, end=end, interval=interval,
                                      progress=False, auto_adjust=False)
                    if raw is not None and len(raw) > 0:
                        out = _normalize_ohlcv(raw)
                        try: out.to_csv(cache)
                        except: pass
                        print("[feed] yfinance")
                        return out
                except Exception as ex:
                    last_err = ex
                time.sleep(1.5*(attempt+1))
            if last_err: raise last_err
        except Exception as e:
            print(f"[feed] yfinance failed â†’ {type(e).__name__}: {e}")

    # KRAKEN
    if "kraken" in sources and int(interval_minutes) == 30:
        try:
            out = _kraken_fetch(interval_minutes=30, lookback_days=lookback_days)
            try: out.to_csv(cache)
            except: pass
            print("[feed] kraken fallback")
            return out
        except Exception as e:
            print(f"[feed] kraken failed â†’ {type(e).__name__}: {e}")

    # CACHE
    if "cache" in sources and _cache_path(symbol, interval_minutes).exists():
        try:
            out = pd.read_csv(cache, parse_dates=[0], index_col=0)
            out = _normalize_ohlcv(out)
            print(f"[feed] cache â†’ {cache.name}")
            return out
        except Exception as e:
            print(f"[feed] cache read failed â†’ {type(e).__name__}: {e}")

    raise RuntimeError("No data source available")


