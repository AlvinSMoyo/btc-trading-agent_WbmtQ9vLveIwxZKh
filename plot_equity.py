import os
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

STATE_DIR = Path(os.getenv("STATE_DIR") or (Path.cwd() / "state"))
LEDGER = STATE_DIR / "trades.csv"
OUT = STATE_DIR / "equity_curve.png"

def _parse_ts_any(x):
    if pd.isna(x) or (isinstance(x, str) and x.strip() == ""):
        return pd.NaT
    try:
        v = float(str(x).strip())
        return pd.to_datetime(int(v), unit=("s" if v < 1e12 else "ms"), utc=True)
    except Exception:
        return pd.to_datetime(x, utc=True, errors="coerce")

def read_ledger():
    if not LEDGER.exists():
        raise SystemExit(f"Missing {LEDGER}")
    df = pd.read_csv(LEDGER, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)

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
    df = df.dropna(subset=["ts_dt"]).sort_values("ts_dt").reset_index(drop=True)

    for c in ("price","qty_btc","fee_usd"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    if "side" in df.columns:
        df["side"] = df["side"].astype(str).str.upper()
    if "notional" not in df.columns and set(("price","qty_btc")).issubset(df.columns):
        df["notional"] = df["price"] * df["qty_btc"]
    return df

def add_running(df: pd.DataFrame) -> pd.DataFrame:
    cash = float(os.getenv("STARTING_CASH", "10000") or 10000.0)
    btc  = float(os.getenv("STARTING_BTC", "0") or 0.0)
    cash_after, btc_after, eq_after = [], [], []
    baseline = None
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
        equity = cash + btc*price
        if baseline is None:
            baseline = equity
        cash_after.append(cash)
        btc_after.append(btc)
        eq_after.append(equity)
    out = df.copy()
    out["cash_after"] = cash_after
    out["btc_after"]  = btc_after
    out["equity_after"] = eq_after
    out["cum_pnl"] = out["equity_after"] - (baseline if baseline is not None else 0.0)
    return out

def main():
    df = add_running(read_ledger())
    if df.empty:
        raise SystemExit("Ledger is empty after parsing.")

    plt.figure(figsize=(11,5))
    plt.plot(df["ts_dt"], df["equity_after"], label="Equity (mark-to-market)")
    if "side" in df.columns:
        buys  = df[df["side"]=="BUY"]
        sells = df[df["side"]=="SELL"]
        if not buys.empty:
            plt.scatter(buys["ts_dt"], buys["equity_after"], marker="^", s=40, label="BUY")
        if not sells.empty:
            plt.scatter(sells["ts_dt"], sells["equity_after"], marker="v", s=40, label="SELL")
    plt.title("Equity Curve")
    plt.xlabel("Time (UTC)")
    plt.ylabel("Equity (USD)")
    plt.legend()
    plt.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT, dpi=150)
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    main()
