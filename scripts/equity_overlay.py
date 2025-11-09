import os
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

ROOT   = Path(os.getenv("PROJECT_ROOT", Path.cwd()))
STATE  = ROOT / "state"
OUTDIR = STATE / "overlays"
OUTDIR.mkdir(parents=True, exist_ok=True)

LEDGER = STATE / "trades.csv"
CFG    = STATE / "config.json"  # optional
PNG    = OUTDIR / "equity_overlay.png"

# seed balances
start_cash = float(os.getenv("START_CASH", "10000"))
start_btc  = float(os.getenv("START_BTC",  "0"))

if CFG.exists():
    try:
        import json
        c = json.loads(CFG.read_text())
        start_cash = float(c.get("cash_usd", start_cash))
        start_btc  = float(c.get("btc", start_btc))
    except Exception:
        pass

if not LEDGER.exists():
    raise SystemExit(f"[ERR] Missing {LEDGER}")

df = pd.read_csv(LEDGER)

# column auto-detect
cols = {c.lower().strip(): c for c in df.columns}
def pick(*opts):
    for o in opts:
        k = o.lower()
        if k in cols: return cols[k]
    return None

ts   = pick("time (utc)","time","ts_utc")
side = pick("side")
price= pick("price")
qty  = pick("qty btc","qty_btc","quantity","qty")
fee  = pick("fee","fees")

for need in [ts, side, price, qty]:
    if need is None:
        raise SystemExit("[ERR] trades.csv missing required columns")

df = df.dropna(subset=[ts]).copy()
df[ts] = pd.to_datetime(df[ts], utc=True, errors="coerce")
df = df.sort_values(ts)

cash = start_cash
btc  = start_btc
equity_points = []

for _, r in df.iterrows():
    p = float(r[price])
    q = float(r[qty])
    s = str(r[side]).upper()
    f = float(r[fee]) if fee and not pd.isna(r[fee]) else 0.0

    if s.startswith("B"):
        cash -= p*q + f
        btc  += q
    elif s.startswith("S"):
        cash += p*q - f
        btc  -= q
    equity = cash + btc * p
    equity_points.append((r[ts], equity))

if not equity_points:
    raise SystemExit("[ERR] No trade rows to plot")

e = pd.DataFrame(equity_points, columns=["ts","equity"]).set_index("ts")

plt.figure(figsize=(12,5))
plt.plot(e.index, e["equity"], label="Equity (USD)")
plt.title("Equity Over Time")
plt.xlabel("Time (UTC)")
plt.ylabel("USD")
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(PNG, dpi=160)
print(f"[OK] Wrote overlay: {PNG}")
