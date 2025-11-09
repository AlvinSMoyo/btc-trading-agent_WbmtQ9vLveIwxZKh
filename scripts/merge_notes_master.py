#!/usr/bin/env python3
"""
Merge full notes from a master trades CSV into balances_from_trades.csv.

- Uses balances_from_trades.csv for cash/btc/equity_after
- Uses trades_with_notes_master.csv for Note
- Joins on exact UTC timestamp
- Writes a new file: state/balances_with_notes.csv (does NOT overwrite original)
"""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
HIST  = STATE / "history"

LEDGER_PATH = STATE / "balances_from_trades.csv"
MASTER_PATH = HIST  / "trades_with_notes_master.csv"
OUT_PATH    = STATE / "balances_with_notes.csv"

print(f"📄 Ledger: {LEDGER_PATH}")
print(f"📄 Master notes: {MASTER_PATH}")

if not LEDGER_PATH.exists():
    raise SystemExit(f"❌ Missing {LEDGER_PATH}")
if not MASTER_PATH.exists():
    raise SystemExit(f"❌ Missing {MASTER_PATH} – did you copy trades_master_latest.csv?")

# --- Load both ---
df_ledger = pd.read_csv(LEDGER_PATH)
df_master = pd.read_csv(MASTER_PATH)

# Normalise time to a common key
df_ledger["t_dt"] = pd.to_datetime(df_ledger["Time (UTC)"], utc=True, errors="coerce")

# trades_master_latest has a 'ts' column with the UTC time
time_col = "ts" if "ts" in df_master.columns else "time"
df_master["t_dt"] = pd.to_datetime(df_master[time_col], utc=True, errors="coerce")

# Keep only the columns we need from master
master_cols = ["t_dt"]
if "note" in df_master.columns:
    master_cols.append("note")

dfm = df_master[master_cols].drop_duplicates(subset=["t_dt"])

# Merge: master note overrides ledger Note where available
merged = df_ledger.merge(dfm, on="t_dt", how="left", suffixes=("", "_master"))

# Start from existing Note col if it exists
if "Note" not in merged.columns:
    merged["Note"] = ""

# If master has a note, use it; otherwise keep existing Note
if "note" in merged.columns:
    merged["Note"] = merged["note"].where(merged["note"].notna(), merged["Note"])
    merged.drop(columns=["note"], inplace=True)

# Remove placeholder backfill spam notes
placeholder_prefix = "fee:backfill | balances:recomputed | notional:recomputed"
merged["Note"] = merged["Note"].fillna("")
merged.loc[merged["Note"].str.startswith(placeholder_prefix, na=False), "Note"] = ""

# Drop helper column and restore original Time format
merged.drop(columns=["t_dt"], inplace=True)

# Fill any remaining NaNs in Note
merged["Note"] = merged["Note"].fillna("")

merged.to_csv(OUT_PATH, index=False)
print(f"✅ Wrote merged ledger with notes → {OUT_PATH}")
