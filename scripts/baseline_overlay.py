import os, sys, io
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

STATE_DIR = os.path.join(".", "state")
EQ_PATH   = os.path.join(STATE_DIR, "equity_history.csv")
TR_PATH   = os.path.join(STATE_DIR, "trades.csv")
OUT_PNG   = os.path.join(STATE_DIR, "baseline_compare_with_trades.png")
OUT_HTML  = os.path.join(STATE_DIR, "baseline_summary_with_trades.html")

def pick_first(cols, names):
    for n in names:
        if n in cols: return n
    return None

# ---------- Load equity (single source of truth for the timeline) ----------
if not os.path.exists(EQ_PATH): sys.exit(f"❌ Missing {EQ_PATH}.")
eq = pd.read_csv(EQ_PATH)
eq.columns = [c.strip().lower() for c in eq.columns]

dtcol = pick_first(eq.columns, ["ts_utc","ts_dt","date","timestamp","datetime","ts"]) or sys.exit("❌ equity_history needs a timestamp column.")
eq[dtcol] = pd.to_datetime(eq[dtcol], errors="coerce", utc=True)
# make tz-naive for matplotlib
if getattr(eq[dtcol].dt, "tz", None) is not None:
    eq[dtcol] = eq[dtcol].dt.tz_convert(None)

# keep only valid, sorted rows
eq = eq.dropna(subset=[dtcol]).sort_values(dtcol).reset_index(drop=True)

hyb_col  = pick_first(eq.columns, ["equity","total_equity","portfolio_equity","nav","value"]) or sys.exit("❌ Need an equity column.")
pricecol = pick_first(eq.columns, ["price","close","btc_price","btc_close"])  # optional

# Clean numeric columns just in case
def clean(series):
    s = series.astype(str).str.replace(r"[^0-9\.\-eE]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")

eq[hyb_col] = clean(eq[hyb_col])
if pricecol: eq[pricecol] = clean(eq[pricecol])

# If no price in equity_history, fetch BTC-USD to align by timestamp
if pricecol is None:
    try:
        import yfinance as yf
        start = eq[dtcol].min().date()
        end   = (eq[dtcol].max() + pd.Timedelta(days=1)).date()
        px = yf.download("BTC-USD", start=start, end=end, interval="1h", progress=False)["Close"].rename("close").to_frame().reset_index()
        dcol = "Datetime" if "Datetime" in px.columns else "Date"
        px[dcol] = pd.to_datetime(px[dcol], utc=True).dt.tz_convert(None)
        px = px.rename(columns={dcol:"ts"})
        # align by nearest timestamp
        eq = pd.merge_asof(eq.sort_values(dtcol), px.sort_values("ts"), left_on=dtcol, right_on="ts", direction="nearest")
        pricecol = "close"
    except Exception as e:
        sys.exit(f"❌ Could not fetch BTC-USD price: {e}")

# Final clean and guard
eq = eq.dropna(subset=[hyb_col, pricecol])
if eq.empty: sys.exit("❌ No usable rows in equity_history after cleaning.")

# ---------- Build baselines on *equity timestamps* (no global grid) ----------
initial_cash = float(eq[hyb_col].iloc[0])
first_price  = float(eq[pricecol].iloc[0])

# Buy & Hold
hold_qty = initial_cash / first_price
eq["hold_equity"] = hold_qty * eq[pricecol]

# Weekly DCA ($300 default)
dca_usd  = float(os.environ.get("DCA_USD", "300"))
dca_qty, dca_cash = 0.0, initial_cash
next_dca = eq[dtcol].iloc[0]
dca_vals = []
for _, row in eq.iterrows():
    ts    = row[dtcol]
    price = float(row[pricecol])
    # invest on/after each 7-day mark
    while ts >= next_dca and dca_cash >= dca_usd:
        dca_qty  += dca_usd / price
        dca_cash -= dca_usd
        next_dca += pd.Timedelta(days=7)
    dca_vals.append(dca_cash + dca_qty * price)
eq["dca_equity"] = dca_vals

# ---------- Overlay trades only if inside equity window ----------
buys = sells = pd.DataFrame()
if os.path.exists(TR_PATH):
    try:
        tr = pd.read_csv(TR_PATH)
        tr.columns = [c.strip().lower() for c in tr.columns]
        tcol = pick_first(tr.columns, ["ts_dt","ts_utc","timestamp","date","datetime","ts"])
        if tcol:
            tr[tcol] = pd.to_datetime(tr[tcol], errors="coerce", utc=True)
            if getattr(tr[tcol].dt, "tz", None) is not None:
                tr[tcol] = tr[tcol].dt.tz_convert(None)
            tr = tr.dropna(subset=[tcol])
            tr["side"] = tr.get("side","").astype(str).str.strip().str.lower()
            # keep only trades within (eq.min±1d, eq.max±1d)
            lo = eq[dtcol].min() - pd.Timedelta(days=1)
            hi = eq[dtcol].max() + pd.Timedelta(days=1)
            tr = tr[(tr[tcol] >= lo) & (tr[tcol] <= hi)]
            if not tr.empty:
                anchor = eq[[dtcol, hyb_col]].sort_values(dtcol)
                aligned = pd.merge_asof(tr[[tcol,"side"]].sort_values(tcol),
                                        anchor, left_on=tcol, right_on=dtcol,
                                        direction="nearest", tolerance=pd.Timedelta("7D")).dropna(subset=[dtcol, hyb_col])
                buys  = aligned[aligned["side"]=="buy"]
                sells = aligned[aligned["side"]=="sell"]
                print(f"Markers: trades_in_window={len(aligned)} | buys={len(buys)} | sells={len(sells)}")
            else:
                print("ℹ️ No trades in equity window (markers skipped).")
        else:
            print("ℹ️ No timestamp column in trades.csv (markers skipped).")
    except Exception as e:
        print(f"⚠️ Skipping trade markers: {e}")

# ---------- Summary ----------
def fmt(x): return f"{x:,.2f}"
def rel(a,b):
    try: return f"{(a/b - 1)*100:,.2f}%"
    except: return "n/a"

summary = {
    "Start": eq[dtcol].iloc[0].strftime("%Y-%m-%d %H:%M"),
    "End":   eq[dtcol].iloc[-1].strftime("%Y-%m-%d %H:%M"),
    "Initial Cash (aligned)": f"${fmt(initial_cash)}",
    "Hybrid Final Equity":    f"${fmt(float(eq[hyb_col].iloc[-1]))}",
    "Hold Final Equity":      f"${fmt(float(eq['hold_equity'].iloc[-1]))}",
    "DCA Final Equity":       f"${fmt(float(eq['dca_equity'].iloc[-1]))}",
    "Hybrid vs Hold":         rel(float(eq[hyb_col].iloc[-1]), float(eq["hold_equity"].iloc[-1])),
    "Hybrid vs DCA":          rel(float(eq[hyb_col].iloc[-1]), float(eq["dca_equity"].iloc[-1])),
}

# ---------- Plot ----------
plt.figure(figsize=(11,6))
plt.plot(eq[dtcol], eq[hyb_col], label="Hybrid (Your Agent)")
plt.plot(eq[dtcol], eq["hold_equity"], label="Buy & Hold")
plt.plot(eq[dtcol], eq["dca_equity"],  label=f"Weekly DCA (${int(dca_usd)})")
if not buys.empty:
    plt.scatter(buys[dtcol],  buys[hyb_col],  marker="^", s=160, c="tab:green", edgecolors="black", linewidths=0.7, zorder=6, label="Buy (▲)")
if not sells.empty:
    plt.scatter(sells[dtcol], sells[hyb_col], marker="v", s=160, c="tab:red",   edgecolors="black", linewidths=0.7, zorder=6, label="Sell (▼)")

plt.title("Equity Curve — Hybrid vs Hold vs DCA (with trade markers)")
plt.xlabel("Date"); plt.ylabel("Equity (USD)")
plt.grid(True, alpha=0.3); plt.legend(); plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150)

# ---------- HTML ----------
html = io.StringIO()
html.write("<h2>Baseline Comparison Summary</h2><table border='1' cellpadding='6' cellspacing='0'>")
for k,v in summary.items(): html.write(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>")
html.write("</table><p><img src='baseline_compare_with_trades.png' style='max-width:100%;height:auto;'/></p>")
with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html.getvalue())

print("OK Wrote:", OUT_PNG)
print("OK Wrote:", OUT_HTML)

