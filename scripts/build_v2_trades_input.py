#!/usr/bin/env python
import pandas as pd
from pathlib import Path

ROOT  = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
HIST  = STATE / "history"

master = HIST / "master_ledger_with_balances_and_notional_v2.csv"
out    = HIST / "v2_trades_input.csv"

if not master.exists():
    raise SystemExit(f"[ERR] Missing {master} – run rebuild_master_ledger.py first")

df = pd.read_csv(master, parse_dates=["Time (UTC)"])

# Map master ledger -> v2 input
v2 = pd.DataFrame({
    "ts_dt":        df["Time (UTC)"],
    "side":         df["Side"],
    "price":        df["Price"],
    "qty_btc":      df["Qty"],
    "fee_usd":      df["Fee"],
    "note":         df["Note"],
    "equity_after": df["equity_after"],
})

out.parent.mkdir(parents=True, exist_ok=True)
v2.to_csv(out, index=False)
print(f"[OK] Wrote {out} with {len(v2)} rows")
