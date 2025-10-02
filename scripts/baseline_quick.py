import os, sys, io
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

STATE_DIR = os.path.join(".", "state")
EQ_PATH   = os.path.join(STATE_DIR, "equity_history.csv")
OUT_PNG   = os.path.join(STATE_DIR, "baseline_compare.png")
OUT_HTML  = os.path.join(STATE_DIR, "baseline_summary.html")

def pick_date_col(df):
    # common names + generous fallbacks
    candidates = ["date","timestamp","ts","ts_dt","datetime","time","created","created_at","run_at","dt","day"]
    for c in candidates:
        if c in df.columns: 
            return c
    # fallback: best-parsable column
    best, score = None, 0
    for c in df.columns:
        try:
            parsed = pd.to_datetime(df[c], errors="coerce")
            s = parsed.notna().mean()
            if s > score:
                best, score = c, s
        except Exception:
            pass
    return best if best and score >= 0.6 else None

def pick_first(df, names):
    for n in names:
        if n in df.columns: return n
    return None

if not os.path.exists(EQ_PATH):
    sys.exit(f"❌ Missing {EQ_PATH}. Run your agent once to generate it.")

# try utf-8 then utf-16
try:
    df = pd.read_csv(EQ_PATH)
except UnicodeError:
    df = pd.read_csv(EQ_PATH, encoding="utf-16")

df.columns = [c.strip().lower() for c in df.columns]

date_col = pick_date_col(df)
if not date_col:
    sys.exit("❌ Could not detect a date/timestamp column. Rename or add one (e.g., date/timestamp/ts_dt).")
df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
df = df.dropna(subset=[date_col]).sort_values(by=date_col).reset_index(drop=True)

equity_col = pick_first(df, ["equity","total_equity","portfolio_equity","portfolio_value","nav","value"])
if not equity_col:
    cash_col = pick_first(df, ["cash","cash_usd"])
    btc_col  = pick_first(df, ["btc","qty","position_btc"])
    price_guess = pick_first(df, ["price","close","btc_price","btc_close","btc_usd"])
    if cash_col and btc_col and price_guess:
        df["__equity__"] = df[cash_col].astype(float) + df[btc_col].astype(float)*df[price_guess].astype(float)
        equity_col = "__equity__"
    else:
        sys.exit("❌ Could not find an equity column (equity/total_equity/portfolio_value).")

price_col = pick_first(df, ["price","close","btc_price","btc_close","btc_usd"])
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
        sys.exit(f"❌ No price column and failed to fetch BTC-USD: {e}")

df = df.dropna(subset=[price_col, equity_col]).copy()
if df.empty:
    sys.exit("❌ No rows left after cleaning; check CSV contents (price/equity/date).")

# --- Baselines aligned to same dates ---
initial_cash = float(df[equity_col].iloc[0])
dca_lot_usd  = 500.0

first_price = float(df[price_col].iloc[0])
hold_qty = initial_cash / first_price
df["hold_equity"] = hold_qty * df[price_col]

start_date = df[date_col].iloc[0]
dca_dates = pd.date_range(start=start_date, end=df[date_col].iloc[-1], freq="7D")
dca_idx = set(df.index[df[date_col].isin(dca_dates)].tolist())
dca_qty, dca_cash = 0.0, initial_cash
for i, row in df.iterrows():
    if i in dca_idx and dca_cash >= dca_lot_usd:
        dca_qty  += dca_lot_usd / float(row[price_col])
        dca_cash -= dca_lot_usd
df["dca_equity"] = dca_cash + dca_qty * df[price_col]

hybrid_final = float(df[equity_col].iloc[-1])
hold_final   = float(df["hold_equity"].iloc[-1])
dca_final    = float(df["dca_equity"].iloc[-1])

fmt = lambda x: f"{x:,.2f}"
rel = lambda a,b: (f"{(a/b - 1)*100:,.2f}%" if b else "n/a")
summary = {
    "Start": df[date_col].iloc[0].strftime("%Y-%m-%d"),
    "End":   df[date_col].iloc[-1].strftime("%Y-%m-%d"),
    "Initial Cash (aligned)": f"${fmt(initial_cash)}",
    "Hybrid Final Equity":    f"${fmt(hybrid_final)}",
    "Hold Final Equity":      f"${fmt(hold_final)}",
    "DCA Final Equity":       f"${fmt(dca_final)}",
    "Hybrid vs Hold":         rel(hybrid_final, hold_final),
    "Hybrid vs DCA":          rel(hybrid_final, dca_final),
}

plt.figure(figsize=(11,6))
plt.plot(df[date_col], df[equity_col], label="Hybrid (Your Agent)")
plt.plot(df[date_col], df["hold_equity"], label="Buy & Hold")
plt.plot(df[date_col], df["dca_equity"], label="Weekly DCA ($500)")
plt.title("Equity Curve — Hybrid vs Hold vs DCA")
plt.xlabel("Date"); plt.ylabel("Equity (USD)")
plt.legend(); plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150)

html = io.StringIO()
html.write("<h2>Baseline Comparison Summary</h2><table border='1' cellpadding='6' cellspacing='0'>")
for k,v in summary.items(): html.write(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>")
html.write("</table><p><img src='baseline_compare.png' style='max-width:100%;height:auto;'/></p>")
with open(OUT_HTML, "w", encoding="utf-8") as f: f.write(html.getvalue())

print("✅ Wrote:", OUT_PNG)
print("✅ Wrote:", OUT_HTML)
print("➡  Hybrid vs Hold:", summary["Hybrid vs Hold"])
print("➡  Hybrid vs DCA:", summary["Hybrid vs DCA"])
