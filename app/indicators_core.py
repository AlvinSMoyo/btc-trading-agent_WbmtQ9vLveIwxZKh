import pandas as pd

def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high_low  = (df["High"] - df["Low"]).abs()
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close  = (df["Low"]  - df["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()

def rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / (loss.replace(0, 1e-12))
    return 100 - (100 / (1 + rs))

