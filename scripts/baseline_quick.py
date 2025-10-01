import os, sys, io
import pandas as pd
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta
import matplotlib.pyplot as plt

STATE_DIR = os.path.join(".", "state")
EQ_PATH   = os.path.join(STATE_DIR, "equity_history.csv")
OUT_PNG   = os.path.join(STATE_DIR, "baseline_compare.png")
OUT_HTML  = os.path.join(STATE_DIR, "baseline_summary.html")

# --- 1) Load hybrid equity and infer price ---
if not os.path.exists(EQ_PATH):
    sys.exit(f"❌ Missing {EQ_PATH}. Run your agent once to generate it.")

df = pd.read_csv(EQ_PATH)
# Normalize column names
df.columns = [c.strip().lower() for c in df.columns]

# date/timestamp column candidates
for cand in ["date","timestamp","ts","ts_dt","datetime"]:
    if cand in df.columns:
        date_col = cand
        break
else:
    sys.exit("❌ equity_history.csv must have a date/timestamp column (date/timestamp/ts/ts_dt/datetime).")

df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
df = df.dropna(subset=[date_col]).sort_values(by=date_col).reset_index(drop=True)

# Hybrid equity column
for cand in ["equity","total_equity","portfolio_equity","nav","value"]:
    if cand in df.columns:
        hybrid_col = cand
        break
else:
    sys.exit("❌ equity_history.csv needs an equity column (e.g., equity/total_equity/portfolio_equity).")

# Price column (if not present, try to get from yfinance)
price_col = None
for cand in ["price","close","btc_price","btc_close"]:
    if cand in df.columns:
        price_col = cand
        break

if price_col is None:
    try:
        import yfinance as yf
        start = df[date_col].min().strftime("%Y-%m-%d")
        end   = (df[date_col].max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        px = yf.download("BTC-USD", start=start, end=end, progress=False)["Close"].rename("close").to_frame()
        px = px.reset_index().rename(columns={"Date": date_col})
        df = pd.merge_asof(df.sort_values(date_col), px.sort_values(date_col), on=date_col)
        price_col = "close"
    except Exception as e:
        sys.exit(f"❌ No price column in equity_history.csv and failed to fetch BTC-USD: {e}")

# Clean
df = df.dropna(subset=[price_col, hybrid_col]).copy()
if df.empty:
    sys.exit("❌ No rows left after cleaning; check your CSV columns.")

# --- 2) Build Baselines on same dates ---
initial_cash = float(df[hybrid_col].iloc[0])  # start with same cash as hybrid
dca_lot_usd  = 500.0                          # weekly DCA amount

# Buy & Hold: buy all at first close
first_price = float(df[price_col].iloc[0])
hold_qty = initial_cash / first_price
df["hold_equity"] = hold_qty * df[price_col]

# Weekly DCA (every 7 days on/after start)
start_date = df[date_col].iloc[0]
dca_dates = [start_date]
while dca_dates[-1] + pd.Timedelta(days=7) <= df[date_col].iloc[-1]:
    dca_dates.append(dca_dates[-1] + pd.Timedelta(days=7))

dca_qty = 0.0
dca_cash = initial_cash
dca_idx_set = set(df.index[df[date_col].isin(pd.to_datetime(dca_dates))].tolist())

for i, row in df.iterrows():
    if i in dca_idx_set and dca_cash >= dca_lot_usd:
        price = float(row[price_col])
        dca_qty += dca_lot_usd / price
        dca_cash -= dca_lot_usd
df["dca_equity"] = dca_cash + dca_qty * df[price_col]

# --- 3) Summaries ---
def final_val(col): return float(df[col].iloc[-1])

hybrid_final = final_val(hybrid_col)
hold_final   = final_val("hold_equity")
dca_final    = final_val("dca_equity")

def fmt_money(x): return f"{x:,.2f}"
def rel(a,b): 
    try: return f"{(a/b - 1)*100:,.2f}%"
    except: return "n/a"

summary = {
    "Start": df[date_col].iloc[0].strftime("%Y-%m-%d"),
    "End":   df[date_col].iloc[-1].strftime("%Y-%m-%d"),
    "Initial Cash (aligned)": f"${fmt_money(initial_cash)}",
    "Hybrid Final Equity":    f"${fmt_money(hybrid_final)}",
    "Hold Final Equity":      f"${fmt_money(hold_final)}",
    "DCA Final Equity":       f"${fmt_money(dca_final)}",
    "Hybrid vs Hold":         rel(hybrid_final, hold_final),
    "Hybrid vs DCA":          rel(hybrid_final, dca_final),
}

# --- 4) Plot ---
plt.figure(figsize=(11,6))
plt.plot(df[date_col], df[hybrid_col], label="Hybrid (Your Agent)")
plt.plot(df[date_col], df["hold_equity"], label="Buy & Hold")
plt.plot(df[date_col], df["dca_equity"], label="Weekly DCA ($500)")
plt.title("Equity Curve — Hybrid vs Hold vs DCA")
plt.xlabel("Date")
plt.ylabel("Equity (USD)")
plt.legend()
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150)

# --- 5) Minimal HTML report ---
html = io.StringIO()
html.write("<h2>Baseline Comparison Summary</h2>")
html.write("<table border='1' cellpadding='6' cellspacing='0'>")
for k,v in summary.items():
    html.write(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>")
html.write("</table>")
html.write(f"<p><img src='baseline_compare.png' style='max-width:100%;height:auto;'/></p>")

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html.getvalue())

print("✅ Wrote:", OUT_PNG)
print("✅ Wrote:", OUT_HTML)
print("➡  Hybrid vs Hold:", summary["Hybrid vs Hold"])
print("➡  Hybrid vs DCA:", summary["Hybrid vs DCA"])
