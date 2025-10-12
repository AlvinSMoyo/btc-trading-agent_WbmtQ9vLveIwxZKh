import os, pandas as pd, numpy as np

EQ = r".\state\equity_history.csv"
TR = r".\state\trades.csv"

def clean_money(s):
    s = s.astype(str).str.replace(r"[^\d\.\-eE]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")

assert os.path.exists(EQ), f"Missing {EQ}"
eq = pd.read_csv(EQ)
print("EQ raw columns:", list(eq.columns))

eq.columns = [c.strip().lower() for c in eq.columns]
ts = next((c for c in ["ts_utc","ts_dt","date","timestamp","datetime","ts"] if c in eq.columns), None)
print("EQ ts col:", ts)

cands = [c for c in ["equity","total_equity","portfolio_equity","nav","value",
                     "equity_usd","total_equity_usd","portfolio_value"] if c in eq.columns]
print("Equity candidates:", cands[:10])

if ts:
    print("EQ ts min/max:", eq[ts].min(), "?", eq[ts].max())

for c in cands:
    v = clean_money(eq[c])
    print(f"{c}: non-null={v.notna().sum()}  sample={v.head(3).tolist()}")

if os.path.exists(TR):
    tr = pd.read_csv(TR)
    tr.columns = [c.strip().lower() for c in tr.columns]
    tcol = next((c for c in ["ts_dt","ts_utc","timestamp","date","datetime","ts"] if c in tr.columns), None)
    if tcol:
        tr[tcol] = pd.to_datetime(tr[tcol], errors="coerce", utc=True)
        print("TR ts min/max:", tr[tcol].min(), "?", tr[tcol].max())
        bad = tr[tcol].dt.year.lt(2009).sum()
        print("TR rows < 2009:", bad)
