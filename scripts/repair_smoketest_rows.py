import os
import pandas as pd
from pathlib import Path

STATE_DIR = os.getenv("STATE_DIR","state")
PATH = Path(STATE_DIR) / "trades.csv"
BAK  = Path(STATE_DIR) / "trades.before_fix.csv"

df = pd.read_csv(PATH, dtype=str)
df.columns = [c.strip().lower() for c in df.columns]

# normalize timestamp column name
if "ts" not in df.columns and "ts_utc" in df.columns:
    df["ts"] = df["ts_utc"]

# coerce numerics we will use
for c in ["price","qty_btc","fee_usd"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

# parse timestamps
ts = pd.to_datetime(df["ts"], utc=True, errors="coerce")
df["_ts"] = ts

# ---- identify the two bad rows (price missing, qty_btc insanely large) around 21:45 ----
mask_time   = (ts >= pd.Timestamp("2025-10-15 21:45:00", tz="UTC")) & (ts <= pd.Timestamp("2025-10-15 21:45:20", tz="UTC"))
mask_source = df.get("source","").str.upper().eq("LLM")
mask_weird  = df["price"].isna() & (df["qty_btc"] > 1000)

mask = mask_time & mask_source & mask_weird
n_bad = int(mask.sum())

if n_bad:
    # backup first
    pd.read_csv(PATH).to_csv(BAK, index=False)
    print(f"[fix] backed up {PATH} -> {BAK}")

    # swap qty_btc <-> price
    old_qty = df.loc[mask, "qty_btc"].copy()
    old_prc = df.loc[mask, "price"].copy()
    df.loc[mask, "price"]   = old_qty
    df.loc[mask, "qty_btc"] = 0.0025  # your intended test size
    # ensure fee present and add a note tag
    df.loc[mask, "fee_usd"] = df.loc[mask, "fee_usd"].fillna(0.30).replace(0, 0.30)
    note = df.get("note")
    if note is not None:
        df.loc[mask, "note"] = note.where(~mask, other="smoke test (fixed)")
    else:
        df["note"] = ""
        df.loc[mask, "note"] = "smoke test (fixed)"

    print(f"[fix] corrected {n_bad} bad row(s)")

# write back with a consistent column order
cols = ["ts","side","source","reason","price","qty_btc","fee_usd","note","confidence"]
for c in cols:
    if c not in df.columns:
        df[c] = ""
df[cols].to_csv(PATH, index=False)
print(f"[fix] wrote {PATH}")
