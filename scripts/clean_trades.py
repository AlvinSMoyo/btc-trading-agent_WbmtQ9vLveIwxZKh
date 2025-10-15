import os
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import timezone

STATE_DIR   = os.getenv("STATE_DIR","state")
TRADES_CSV  = os.path.join(STATE_DIR,"trades.csv")
OUT_CSV     = TRADES_CSV  # overwrite in place
COLUMNS_OUT = ["ts","side","source","reason","price","qty_btc","fee_usd","note","confidence"]

def _parse_ts(s):
    # Accept ISO, epoch seconds, or epoch ms; force UTC
    s = str(s).strip()
    if not s:
        return pd.NaT
    try:
        v = float(s)
        if v > 1e12:  # ms
            return pd.to_datetime(int(v), unit="ms", utc=True, errors="coerce")
        if v > 1e9:   # s
            return pd.to_datetime(int(v), unit="s", utc=True, errors="coerce")
    except Exception:
        pass
    return pd.to_datetime(s, utc=True, errors="coerce")

def _canon_source(x:str) -> str:
    x = ("" if pd.isna(x) else str(x)).strip()
    return x.upper()

def main():
    if not Path(TRADES_CSV).exists():
        raise SystemExit(f"missing {TRADES_CSV}")

    df = pd.read_csv(TRADES_CSV, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]

    # unify timestamp column
    if "ts" not in df.columns and "ts_utc" in df.columns:
        df["ts"] = df["ts_utc"]

    # parse ts and keep a copy for formatting later
    df["ts"] = df["ts"].map(_parse_ts)
    df = df.dropna(subset=["ts"]).copy()

    # normalize core fields
    df["side"]   = df.get("side","").astype(str).str.lower().str.strip()
    df["source"] = df.get("source", df.get("reason","")).map(_canon_source)
    df["reason"] = df.get("reason","").fillna("").astype(str)

    # choose qty_btc if present else qty
    qty_col = "qty_btc" if "qty_btc" in df.columns else ("qty" if "qty" in df.columns else None)
    if qty_col is None:
        df["qty_btc"] = np.nan
    else:
        df["qty_btc"] = pd.to_numeric(df[qty_col], errors="coerce")

    df["price"]     = pd.to_numeric(df.get("price"), errors="coerce")
    df["fee_usd"]   = pd.to_numeric(df.get("fee_usd"), errors="coerce")
    df["confidence"]= pd.to_numeric(df.get("confidence"), errors="coerce")
    df["note"]      = df.get("note","").fillna("").astype(str)

    # ---- sensible default fees where obviously missing ----
    # LLM trades usually .30; DCA ~.02; leave others if unknown
    m_missing_fee = df["fee_usd"].isna()
    df.loc[m_missing_fee & (df["source"]=="LLM"), "fee_usd"] = 0.30
    df.loc[m_missing_fee & (df["source"]=="DCA"), "fee_usd"] = 0.02

    # ---- de-duplication (exact and near-duplicate within ±5s) ----
    # (i) exact dupes on exact ts + side + source + reason + price + qty_btc
    df = df.sort_values("ts")
    df = df.drop_duplicates(subset=["ts","side","source","reason","price","qty_btc"], keep="last")

    # (ii) near-dup: same side/source/reason/price/qty within 5s (round ts to 5s)
    ts5 = (df["ts"].astype("int64") // 5_000_000_000) * 5_000_000_000  # ns resolution
    df["_ts_5s"] = pd.to_datetime(ts5, utc=True)
    df["_price_r"] = df["price"].round(2)
    df["_qty_r"]   = df["qty_btc"].round(8)
    df = df.drop_duplicates(subset=["_ts_5s","side","source","reason","_price_r","_qty_r"], keep="last")
    df = df.drop(columns=["_ts_5s","_price_r","_qty_r"], errors="ignore")

    # final ordering/formatting
    out = pd.DataFrame(columns=COLUMNS_OUT)
    out["ts"]         = df["ts"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["side"]       = df["side"]
    out["source"]     = df["source"]
    out["reason"]     = df["reason"]
    out["price"]      = df["price"]
    out["qty_btc"]    = df["qty_btc"]
    out["fee_usd"]    = df["fee_usd"]
    out["note"]       = df["note"]
    out["confidence"] = df["confidence"]

    out.to_csv(OUT_CSV, index=False)
    print(f"[clean_trades] wrote {OUT_CSV} rows: {len(out)}")

if __name__ == "__main__":
    main()
