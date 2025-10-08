# plot_equity_with_baselines.py
# Plots: Actual equity (from trades.csv) vs Buy&Hold vs Weekly DCA

import os
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

STATE_DIR = Path(os.getenv("STATE_DIR") or (Path.cwd() / "state"))
LEDGER    = STATE_DIR / "trades.csv"
C30       = STATE_DIR / "candles_BTC-USD_30m.csv"
C1        = STATE_DIR / "candles_BTC-USD_1m.csv"
OUT       = STATE_DIR / "equity_compare.png"

START_CASH = float(os.getenv("STARTING_CASH", "10000") or 10000.0)
START_BTC  = float(os.getenv("STARTING_BTC", "0") or 0.0)

def _parse_ts_any(x):
    if pd.isna(x) or (isinstance(x,str) and x.strip()==""):
        return pd.NaT
    try:
        v = float(str(x).strip())
        return pd.to_datetime(int(v), unit=("s" if v < 1e12 else "ms"), utc=True)
    except Exception:
        return pd.to_datetime(x, utc=True, errors="coerce")

def read_ledger(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.strip() if isinstance(v,str) else v)

    ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    if "ts_dt" in df.columns:
        ts = pd.to_datetime(df["ts_dt"], utc=True, errors="coerce")
    if "ts_utc" in df.columns:
        ts = ts.fillna(pd.to_datetime(df["ts_utc"], utc=True, errors="coerce"))
    if "ts" in df.columns:
        ts = ts.fillna(df["ts"].map(_parse_ts_any))
    if ts.isna().any():
        for cand in df.columns:
            lc = cand.lower()
            if "time" in lc or "date" in lc:
                ts = ts.fillna(pd.to_datetime(df[cand], utc=True, errors="coerce"))

    df["ts_dt"] = ts
    for c in ("price","qty_btc","fee_usd"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "side" in df.columns:
        df["side"] = df["side"].astype(str).str.upper()

    df = df.dropna(subset=["ts_dt"]).sort_values("ts_dt").reset_index(drop=True)
    return df

def add_running(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cash, btc = START_CASH, START_BTC
    cash_after, btc_after = [], []
    for _, r in df.iterrows():
        price = float(r.get("price") or 0.0)
        qty   = float(r.get("qty_btc") or 0.0)
        fee   = float(r.get("fee_usd") or 0.0)
        side  = str(r.get("side","")).upper()
        if side == "BUY":
            cash -= price*qty + fee
            btc  += qty
        elif side == "SELL":
            cash += price*qty - fee
            btc  -= qty
        cash_after.append(cash); btc_after.append(btc)
    out = df.copy()
    out["cash_after"] = cash_after
    out["btc_after"]  = btc_after
    return out

def read_candles() -> pd.DataFrame:
    path = C30 if C30.exists() else (C1 if C1.exists() else None)
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    # pick a time column
    tcol = None
    for cand in ["ts_dt","ts_utc","time","timestamp","date","datetime","Time"]:
        if cand in df.columns:
            tcol = cand; break
    if tcol is None:
        return pd.DataFrame()
    ts = pd.to_datetime(df[tcol], utc=True, errors="coerce")

    # pick a close/price column
    pcol = None
    for cand in ["close","Close","price","Price","close_price","c"]:
        if cand in df.columns:
            pcol = cand; break
    if pcol is None:
        return pd.DataFrame()

    out = pd.DataFrame({"ts_dt": ts, "price": pd.to_numeric(df[pcol], errors="coerce")})
    out = out.dropna().sort_values("ts_dt").reset_index(drop=True)
    return out

def resample_actual_to_prices(ledger_run: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    # Forward-fill holdings (cash, btc) across candle timeline
    if ledger_run.empty or px.empty:
        return pd.DataFrame()
    h = ledger_run[["ts_dt","cash_after","btc_after"]].copy()
    # Avoid duplicate times for merge_asof
    h = h.sort_values("ts_dt").drop_duplicates("ts_dt", keep="last")
    px = px.sort_values("ts_dt")
    m = pd.merge_asof(px, h, on="ts_dt", direction="backward")
    # fill initial with starting balances if still NaN
    m["cash_after"] = m["cash_after"].fillna(START_CASH)
    m["btc_after"]  = m["btc_after"].fillna(START_BTC)
    m["equity_actual"] = m["cash_after"] + m["btc_after"] * m["price"]
    return m

def compute_hodl(px: pd.DataFrame, start_time: pd.Timestamp) -> pd.Series:
    if px.empty:
        return pd.Series(dtype=float)
    px = px[px["ts_dt"] >= start_time]
    if px.empty:
        return pd.Series(dtype=float)
    first_price = float(px["price"].iloc[0])
    total_btc   = START_BTC + (START_CASH / first_price)
    return total_btc * px["price"]

def compute_weekly_dca(px: pd.DataFrame, start_time: pd.Timestamp, end_time: pd.Timestamp) -> pd.Series:
    if px.empty:
        return pd.Series(dtype=float)
    px = px[(px["ts_dt"] >= start_time) & (px["ts_dt"] <= end_time)].copy()
    if px.empty:
        return pd.Series(dtype=float)

    # how many weeks in span
    weeks = max(1, int(np.ceil((px["ts_dt"].iloc[-1] - px["ts_dt"].iloc[0]).days / 7)) )
    weekly_budget = START_CASH / weeks

    # schedule buy timestamps
    schedule = [px["ts_dt"].iloc[0] + pd.Timedelta(days=7*i) for i in range(weeks)]
    s = pd.DataFrame({"buy_time": schedule}).sort_values("buy_time")
    # match to nearest candle at/after each scheduled time
    px2 = px.copy()
    px2 = px2.sort_values("ts_dt")
    s = pd.merge_asof(s, px2.rename(columns={"ts_dt":"match_time"}), left_on="buy_time",
                      right_on="match_time", direction="forward")
    s = s.dropna(subset=["match_time","price"])

    btc = START_BTC
    spent = 0.0
    for _, r in s.iterrows():
        p = float(r["price"])
        qty = weekly_budget / p
        btc += qty
        spent += weekly_budget

    # equity through time = btc * price + remaining cash
    remaining = START_CASH - spent
    eq = btc * px["price"] + remaining
    eq.index = px.index
    return eq

def main():
    ledger = read_ledger(LEDGER)
    if ledger.empty:
        print(f"No trades found at {LEDGER}")
        return

    ledger = add_running(ledger)
    px = read_candles()
    if px.empty:
        # fallback: price only at trade times (less smooth)
        tmp = ledger[["ts_dt","price"]].dropna().copy()
        px = tmp.rename(columns={"price":"price"}).sort_values("ts_dt").reset_index(drop=True)

    start = min(ledger["ts_dt"].iloc[0], px["ts_dt"].iloc[0])
    end   = max(ledger["ts_dt"].iloc[-1], px["ts_dt"].iloc[-1])

    actual = resample_actual_to_prices(ledger, px)
    hodl   = compute_hodl(px, start)
    dca    = compute_weekly_dca(px, start, end)

    # Plot
    fig = plt.figure(figsize=(12,5.5))
    # actual
    plt.plot(actual["ts_dt"], actual["equity_actual"], label="Actual (LLM/manual)")
    # baselines
    if not hodl.empty:
        plt.plot(px.loc[hodl.index, "ts_dt"], hodl.values, label="Buy & Hold")
    if not dca.empty:
        plt.plot(px.loc[dca.index, "ts_dt"], dca.values, label="Weekly DCA")

    # mark buy/sell points
    buys  = ledger[ledger["side"]=="BUY"]
    sells = ledger[ledger["side"]=="SELL"]
    # place markers at their nearest actual equity value
    if not buys.empty:
        b_asof = pd.merge_asof(buys[["ts_dt"]].sort_values("ts_dt"),
                               actual[["ts_dt","equity_actual"]].sort_values("ts_dt"),
                               on="ts_dt", direction="nearest")
        plt.scatter(b_asof["ts_dt"], b_asof["equity_actual"], marker="^", label="BUY")
    if not sells.empty:
        s_asof = pd.merge_asof(sells[["ts_dt"]].sort_values("ts_dt"),
                               actual[["ts_dt","equity_actual"]].sort_values("ts_dt"),
                               on="ts_dt", direction="nearest")
        plt.scatter(s_asof["ts_dt"], s_asof["equity_actual"], marker="v", label="SELL")

    plt.title("Equity Curve vs Baselines")
    plt.xlabel("Time (UTC)")
    plt.ylabel("Equity (USD)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=144)
    plt.close(fig)
    print(f"Wrote: {OUT}")

if __name__ == "__main__":
    main()
