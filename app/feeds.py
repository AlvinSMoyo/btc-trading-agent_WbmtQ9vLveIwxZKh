from __future__ import annotations
import os, time, requests, pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_DIR = Path(os.getenv("STATE_DIR", "/content/drive/MyDrive/btc-trading-agent/state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(symbol: str, interval_minutes: int) -> Path:
    safe_symbol = symbol.replace("/", "-")
    return STATE_DIR / f"candles_{safe_symbol}_{interval_minutes}m.csv"

def _kraken_fetch(interval_minutes=30, lookback_days=30) -> pd.DataFrame:
    # Kraken supports 30m (interval=30). Symbol: XBTUSD.
    since = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "XBTUSD", "interval": int(interval_minutes), "since": since}
    r = requests.get(url, params=params, timeout=20); r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError("Kraken error: " + ", ".join(j["error"]))
    keys = [k for k in j["result"] if k != "last"]
    rows = j["result"][keys[0]]
    df = pd.DataFrame(rows, columns=["time","Open","High","Low","Close","vwap","Volume","count"])
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")[["Open","High","Low","Close","Volume"]].dropna().sort_index()
    return df

def fetch_yfinance(symbol: str, lookback_days: int = 30, interval_minutes: int = 30) -> pd.DataFrame:
    cache = _cache_path(symbol, interval_minutes)

    # 1) Try Yahoo with short retry
    try:
        import yfinance as yf
        interval = f"{interval_minutes}m"
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        for attempt in range(3):
            df = yf.download(symbol, start=start, end=end, interval=interval,
                             progress=False, auto_adjust=False)
            if df is not None and len(df) > 0:
                df = df.rename(columns=str.title).dropna()
                if df.index.tz is None: df.index = df.index.tz_localize("UTC")
                else: df.index = df.index.tz_convert("UTC")
                out = df[["Open","High","Low","Close","Volume"]]
                try: out.to_csv(cache)
                except Exception: pass
                print("[feed] yfinance")
                return out
            time.sleep(1.5 * (attempt + 1))
    except Exception:
        pass

    # 2) Fallback: Kraken (since Binance is blocked for you)
    try:
        if int(interval_minutes) != 30:
            raise ValueError("Kraken fallback currently implemented for 30m only.")
        out = _kraken_fetch(interval_minutes=30, lookback_days=lookback_days)
        try: out.to_csv(cache)
        except Exception: pass
        print("[feed] kraken fallback")
        return out
    except Exception:
        pass

    # 3) Last resort: local cache
    if cache.exists():
        out = pd.read_csv(cache, parse_dates=[0], index_col=0)
        if out.index.tz is None: out.index = out.index.tz_localize("UTC")
        print(f"[feed] cache → {cache.name}")
        return out[["Open","High","Low","Close","Volume"]]

    raise RuntimeError("No data source available (yfinance, Kraken, cache all failed)")
