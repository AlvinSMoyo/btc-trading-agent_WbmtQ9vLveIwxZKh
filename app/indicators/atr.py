import numpy as np

try:
    import pandas as pd  # optional
except Exception:
    pd = None

def _as_hlc(c):
    # dict or list/tuple row
    if isinstance(c, dict):
        return float(c["high"]), float(c["low"]), float(c["close"])
    # Binance kline array: [openTime, open, high, low, close, ...]
    return float(c[2]), float(c[3]), float(c[4])

def atr(candles, period=14):
    """
    candles: list of dicts / lists, np.ndarray, or pandas.DataFrame of klines
    returns latest ATR or None if not enough data
    """
    if candles is None:
        return None

    # --- normalize to numpy arrays of highs/lows/closes ---
    highs = lows = closes = None

    # pandas DataFrame path
    if pd is not None and isinstance(candles, pd.DataFrame):
        cols = [c.lower() for c in candles.columns.astype(str)]
        # prefer named columns if they exist
        def col(name, fallback_idx):
            return candles[ candles.columns[cols.index(name)] ] if name in cols else candles.iloc[:, fallback_idx]
        # binance order usually: open, high, low, close at positions 1,2,3,4
        highs  = np.asarray(col("high", 2), dtype=float)
        lows   = np.asarray(col("low",  3), dtype=float)
        closes = np.asarray(col("close",4), dtype=float)

    # numpy matrix path
    elif isinstance(candles, np.ndarray):
        highs  = candles[:, 2].astype(float)
        lows   = candles[:, 3].astype(float)
        closes = candles[:, 4].astype(float)

    # list/tuple path
    else:
        highs, lows, closes = [], [], []
        for c in candles:
            h, l, cl = _as_hlc(c)
            highs.append(h); lows.append(l); closes.append(cl)
        highs  = np.asarray(highs,  dtype=float)
        lows   = np.asarray(lows,   dtype=float)
        closes = np.asarray(closes, dtype=float)

    n = len(closes)
    if n < period + 2:
        return None

    prev_close = np.roll(closes, 1)
    tr = np.maximum(highs - lows,
         np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    tr[0] = highs[0] - lows[0]

    # Wilder-style EMA
    alpha = 1.0 / period
    ema = tr[1:period+1].mean()
    for i in range(period+1, n):
        ema = (1 - alpha) * ema + alpha * tr[i]
    return float(ema)
