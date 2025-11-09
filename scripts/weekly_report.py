import os
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT   = Path(os.getenv("PROJECT_ROOT", Path.cwd()))
STATE  = ROOT / "state"
REPORT = ROOT / "state" / "reports"
PLOTS  = ROOT / "state" / "plots"
REPORT.mkdir(parents=True, exist_ok=True)
PLOTS.mkdir(parents=True, exist_ok=True)

LEDGER = STATE / "trades.csv"
HTML   = REPORT / "weekly_report.html"

if not LEDGER.exists():
    raise SystemExit(f"[ERR] Missing {LEDGER}")

df = pd.read_csv(LEDGER)
cols = {c.lower().strip(): c for c in df.columns}
def pick(*opts):
    for o in opts:
        k = o.lower()
        if k in cols: return cols[k]
    return None

ts    = pick("time (utc)","time","ts_utc")
side  = pick("side")
price = pick("price")
qty   = pick("qty btc","qty_btc","quantity","qty")
fee   = pick("fee","fees")
note  = pick("note","reason","reason_short")

for need in [ts, side, price, qty]:
    if need is None:
        raise SystemExit("[ERR] trades.csv missing required columns")

df[ts] = pd.to_datetime(df[ts], utc=True, errors="coerce")
df = df.dropna(subset=[ts]).sort_values(ts)

end = df[ts].max()
start = end - pd.Timedelta(days=7)
w = df[(df[ts] >= start) & (df[ts] <= end)].copy()

w["notional"] = w[price].astype(float) * w[qty].astype(float)
w["fee_usd"]  = w[fee].astype(float) if fee else 0.0
fills = len(w)
buys  = (w[side].str.upper().str.startswith("B")).sum()
sells = (w[side].str.upper().str.startswith("S")).sum()
spent = w.loc[w[side].str.upper().str.startswith("B"), "notional"].sum()
realized = w.loc[w[side].str.upper().str.startswith("S"), "notional"].sum()
fees = w["fee_usd"].sum() if fee else 0.0

# Price series fallback from trades if no external price.csv
price_csv = STATE / "price.csv"
if price_csv.exists():
    px = pd.read_csv(price_csv)
    px.columns = [c.strip().lower() for c in px.columns]
    tcol = next(c for c in px.columns if "time" in c)
    pcol = next(c for c in px.columns if "price" in c or "close" in c)
    px = px.rename(columns={tcol:"ts", pcol:"price"})
    px["ts"] = pd.to_datetime(px["ts"], utc=True, errors="coerce")
else:
    px = w[[ts, price]].rename(columns={ts:"ts", price:"price"}).dropna()

px = px.sort_values("ts")

def rsi(series, n=14):
    delta = series.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / (down.replace(0, np.nan))
    return 100 - (100 / (1 + rs))

px["rsi14"] = rsi(px["price"].astype(float), 14)

# plots
plt.figure(figsize=(12,4))
plt.plot(px["ts"], px["price"], label="Price")
plt.grid(True, alpha=0.3); plt.title("Price"); plt.tight_layout()
p1 = PLOTS / "price.png"; plt.savefig(p1, dpi=160)

plt.figure(figsize=(12,3))
plt.plot(px["ts"], px["rsi14"], label="RSI-14")
plt.axhline(70, linestyle="--"); plt.axhline(30, linestyle="--")
plt.grid(True, alpha=0.3); plt.title("RSI-14"); plt.tight_layout()
p2 = PLOTS / "rsi.png"; plt.savefig(p2, dpi=160)

plt.figure(figsize=(12,4))
mask_b = w[side].str.upper().str.startswith("B")
plt.scatter(w.loc[mask_b, ts], w.loc[mask_b, price], label="BUY", s=20)
plt.scatter(w.loc[~mask_b, ts], w.loc[~mask_b, price], label="SELL", s=20)
plt.legend(); plt.grid(True, alpha=0.3); plt.title("Fills"); plt.tight_layout()
p3 = PLOTS / "fills.png"; plt.savefig(p3, dpi=160)

table = w[[ts, side, price, qty, fee, note]].tail(20).copy()
table.columns = ["Time (UTC)","Side","Price","Qty BTC","Fee","Note"]

html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Weekly Report</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 16px; }}
.kv {{ display:grid; grid-template-columns: 200px 1fr; gap:6px 12px; margin:12px 0 24px; }}
.kv div:first-child {{ color:#555; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #eee; padding: 6px 8px; font-size: 14px; }}
th {{ background:#fafafa; text-align:left; }}
img {{ max-width: 100%; height: auto; border:1px solid #eee; }}
.small {{ color:#666; font-size: 12px; }}
</style>
</head>
<body>
<h1>Weekly Report</h1>
<div class="small">{start.strftime('%Y-%m-%d %H:%M UTC')} → {end.strftime('%Y-%m-%d %H:%M UTC')}</div>

<div class="kv">
  <div>Total fills</div><div>{fills}</div>
  <div>Buys / Sells</div><div>{buys} / {sells}</div>
  <div>Buy notional (USD)</div><div>{spent:,.2f}</div>
  <div>Sell notional (USD)</div><div>{realized:,.2f}</div>
  <div>Fees (USD)</div><div>{fees:,.2f}</div>
</div>

<h2>Charts</h2>
<p><img src="../plots/price.png" alt="price"><br>
<img src="../plots/rsi.png" alt="rsi"><br>
<img src="../plots/fills.png" alt="fills"></p>

<h2>Recent Fills (last 20)</h2>
{table.to_html(index=False)}
</body>
</html>
"""
HTML.write_text(html, encoding="utf-8")
print(f"[OK] Wrote weekly report: {HTML}")
