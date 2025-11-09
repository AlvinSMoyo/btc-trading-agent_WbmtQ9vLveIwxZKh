#!/usr/bin/env python3
"""
Generate weekly or daily HTML statements from balances_from_trades.csv.

Usage:
  python scripts/make_statement_html.py weekly
  python scripts/make_statement_html.py daily
"""

import sys, shutil, datetime as dt
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
HISTORY = STATE / "history"
REPORTS = STATE / "reports"
PLOTS = REPORTS / "plots"

LIVE = STATE / "balances_with_notes.csv"
BACKUP = HISTORY / "balances_master_backup.csv"

REPORTS.mkdir(exist_ok=True)
PLOTS.mkdir(exist_ok=True)
HISTORY.mkdir(exist_ok=True)

mode = sys.argv[1] if len(sys.argv) > 1 else "weekly"
ts = utc_now().strftime("%Y%m%d_%H%M%S")
out_html = REPORTS / f"{mode}_statement_{ts}.html"

# === Load CSV (with fallback) ===============================================
try:
    df = pd.read_csv(LIVE)
    print(f"✅ Loaded live ledger: {LIVE}")
except Exception as e:
    print(f"⚠️ Failed to read live ledger: {e}")
    if BACKUP.exists():
        df = pd.read_csv(BACKUP)
        print(f"⚠️ Using backup ledger: {BACKUP}")
    else:
        raise SystemExit("❌ No valid ledger available.")

# Backup once a week (Sunday) or if missing
if not BACKUP.exists() or (utc_now().weekday() == 6 and mode == "weekly"):
    try:
        shutil.copy2(LIVE, BACKUP)
        print(f"🧩 Master backup refreshed → {BACKUP}")
    except Exception as e:
        print(f"⚠️ Failed to refresh backup: {e}")

# === Normalise / clean dataframe ============================================
# Parse time as UTC, sort
df["Time (UTC)"] = pd.to_datetime(df["Time (UTC)"], utc=True, errors="coerce")
df.sort_values("Time (UTC)", inplace=True)

# Ensure columns exist
required = ["Time (UTC)", "Side", "Price", "Qty", "Fee",
            "Reason", "Note", "cash_after", "btc_after"]
missing = [c for c in required if c not in df.columns]
if missing:
    print(f"⚠️ Missing columns in balances_from_trades.csv: {missing}")

# Clean notes: no NaN in output
if "Note" in df.columns:
    df["Note"] = df["Note"].fillna("")
else:
    df["Note"] = ""

# Compute equity_after if missing
if "equity_after" not in df.columns:
    df["equity_after"] = df["cash_after"] + df["btc_after"] * df["Price"]
    print("📈 Computed equity_after column")

# === Select rows for this report ============================================
if mode == "daily":
    # Last calendar day with data
    last_ts = df["Time (UTC)"].dropna().max()
    if pd.isna(last_ts):
        df_rep = df.tail(60).copy()
    else:
        day_mask = df["Time (UTC)"].dt.date == last_ts.date()
        df_rep = df[day_mask].copy()
        # Fallback if somehow empty
        if df_rep.empty:
            df_rep = df.tail(60).copy()
else:
    # Weekly mode: show all history (your call earlier)
    df_rep = df.copy()

# Format display time
df_rep["Time (UTC)"] = df_rep["Time (UTC)"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")

# === Build display dataframe ================================================
cols = ["Time (UTC)", "Side", "Price", "Qty", "Fee",
        "Reason", "Note", "cash_after", "btc_after", "equity_after"]
df_disp = df_rep[cols].copy()

# Friendly column names
df_disp.rename(columns={
    "cash_after": "Cash After",
    "btc_after": "BTC After",
    "equity_after": "Equity After"
}, inplace=True)

# Basic formatting
df_disp["Price"] = df_disp["Price"].round(2)
df_disp["Qty"] = df_disp["Qty"].astype(float).map(lambda x: f"{x:.8f}".rstrip("0").rstrip("."))
df_disp["Fee"] = df_disp["Fee"].round(2)
df_disp["Cash After"] = df_disp["Cash After"].round(2)
df_disp["BTC After"] = df_disp["BTC After"].astype(float).map(lambda x: f"{x:.8f}".rstrip("0").rstrip("."))
df_disp["Equity After"] = df_disp["Equity After"].round(2)

# === Optional equity plot for daily (only if enough data) ===================
plot_html = ""
if mode == "daily":
    last_ts = df_rep["Time (UTC)"].dropna().max()
    if pd.notna(last_ts):
        day_str = last_ts[:10] if isinstance(last_ts, str) else last_ts.strftime("%Y-%m-%d")
    else:
        day_str = ""
    # Use original df (with datetime) to get time/equity for that day
    dfd = df[df["Time (UTC)"].dt.strftime("%Y-%m-%d") == day_str] if day_str else df.iloc[0:0]
    if len(dfd) >= 5:
        plt.figure(figsize=(8, 4))
        plt.plot(dfd["Time (UTC)"], dfd["equity_after"])
        plt.title(f"Equity Curve {day_str}")
        plt.xlabel("Time (UTC)")
        plt.ylabel("Equity (USD)")
        plot_path = PLOTS / f"equity_curve_{day_str}.png"
        plt.tight_layout()
        plt.savefig(plot_path)
        plt.close()
        plot_html = f'<h3>📈 Equity Curve ({day_str})</h3><img src="../plots/{plot_path.name}" width="800">'

# === Simple CSS (dark, compact) =============================================
style = """
<style>
body { font-family: system-ui, -apple-system, sans-serif; background:#0c0c0c; color:#f2f2f2; margin:20px; }
h1 { margin-bottom: 6px; }
h3 { margin: 16px 0 8px 0; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { border: 1px solid #444; padding: 4px 6px; text-align: right; white-space: nowrap; }
th { background: #222; }
tr:nth-child(even) { background: #111; }
tr:hover { background: #222; }
td:nth-child(7) { text-align: left; } /* Note column */
.footer { margin-top: 10px; font-size: 11px; color: #aaa; }
</style>
"""

# === Render table & header ===================================================
table_html = df_disp.to_html(
    index=False,
    classes="dataframe data",
    border=0,
    justify="right",
    escape=False,
    na_rep=""
)

generated_at = utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")

html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>BTC Trading Statement — {mode.title()} Report</title>
    {style}
  </head>
  <body>
    <h1>BTC Trading Statement — {mode.title()} Report</h1>
    {plot_html}
    {table_html}
    <div class="footer">
      Generated {generated_at} | Mode: {mode}
    </div>
  </body>
</html>
"""

out_html.write_text(html, encoding="utf-8")
print("✅ Statement written →", out_html)
