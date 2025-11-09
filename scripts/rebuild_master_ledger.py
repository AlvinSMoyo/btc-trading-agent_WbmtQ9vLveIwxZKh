import pandas as pd
from pathlib import Path

ROOT = Path("/root/btc-trading-agent")
HIST = ROOT / "state" / "history"

src = HIST / "balances_with_notes.csv"
dst = HIST / "master_ledger_with_balances_and_notional_v2.csv"

print(f"[INFO] Loading {src}")
df = pd.read_csv(src, engine="python", sep=None)

print("[INFO] Columns in balances_with_notes:", list(df.columns))

# --- Standardise expected column names ---

rename_map = {}

# Time
if "Time (UTC)" in df.columns:
    time_col = "Time (UTC)"
elif "ts_utc" in df.columns:
    rename_map["ts_utc"] = "Time (UTC)"
    time_col = "Time (UTC)"
else:
    raise SystemExit("❌ Could not find a time column (expected 'Time (UTC)' or 'ts_utc').")

# Side
if "Side" not in df.columns and "side" in df.columns:
    rename_map["side"] = "Side"

# Qty
if "Qty" not in df.columns:
    if "qty_btc" in df.columns:
        rename_map["qty_btc"] = "Qty"
    elif "qty" in df.columns:
        rename_map["qty"] = "Qty"
    else:
        raise SystemExit("❌ Could not find quantity column (expected 'Qty' / 'qty_btc' / 'qty').")

# Price
if "Price" not in df.columns and "price" in df.columns:
    rename_map["price"] = "Price"

# Fee
if "Fee" not in df.columns and "fee" in df.columns:
    rename_map["fee"] = "Fee"

# Balances: cash_after / btc_after / equity_after
if "cash_after" not in df.columns:
    if "cash" in df.columns:
        rename_map["cash"] = "cash_after"
    elif "cash_usd_after" in df.columns:
        rename_map["cash_usd_after"] = "cash_after"

if "btc_after" not in df.columns:
    if "btc" in df.columns:
        rename_map["btc"] = "btc_after"
    elif "btc_balance_after" in df.columns:
        rename_map["btc_balance_after"] = "btc_after"

if "equity_after" not in df.columns:
    if "equity_usd_after" in df.columns:
        rename_map["equity_usd_after"] = "equity_after"
    elif "equity" in df.columns:
        rename_map["equity"] = "equity_after"

# Notes
if "Note" not in df.columns and "note" in df.columns:
    rename_map["note"] = "Note"

if rename_map:
    print("[INFO] Renaming columns:", rename_map)
    df = df.rename(columns=rename_map)

# Final sanity check
required = ["Time (UTC)", "Side", "Qty", "Price", "Fee",
            "cash_after", "btc_after", "equity_after", "Note"]
missing = [c for c in required if c not in df.columns]
if missing:
    raise SystemExit(f"❌ After renames, still missing columns: {missing}")

# Sort chronologically
df["Time (UTC)"] = pd.to_datetime(df["Time (UTC)"], utc=True, errors="coerce")
df = df.sort_values("Time (UTC)").reset_index(drop=True)

# Compute notional (how much was spent per trade)
df["notional_usd"] = df["Qty"].astype(float) * df["Price"].astype(float)

print("[INFO] Sample of rebuilt ledger:")
print(df[["Time (UTC)", "Side", "Qty", "Price", "Fee",
          "cash_after", "btc_after", "equity_after",
          "notional_usd", "Note"]].tail(10))

# Save to master ledger path
dst.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(dst, index=False)
print(f"[OK] Wrote rebuilt master ledger to {dst}")
