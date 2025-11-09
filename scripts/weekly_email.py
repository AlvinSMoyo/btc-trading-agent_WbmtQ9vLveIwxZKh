import os, json
import pandas as pd, numpy as np
from pathlib import Path

ROOT=Path.cwd(); STATE=ROOT/'state'
SRC = STATE/'history'/'trades_all.csv'
OUT = STATE/'reports'/'weekly_email.html'
CFG = STATE/'config.json'

df = pd.read_csv(SRC)

def pick(d,*opts):
    m={c.lower():c for c in d.columns}
    for o in opts:
        c=m.get(o.lower())
        if c: return c

ts     = pick(df,"Time (UTC)","time (utc)","ts","ts_dt")
side   = pick(df,"Side")
reason = pick(df,"Reason")
price  = pick(df,"Price")
qty    = pick(df,"Qty BTC","qty btc","qty_btc","quantity")
feeusd = pick(df,"Fee","fee","fee_usd")
note   = pick(df,"Note")
feeraw = pick(df,"fee_raw")
cash_a = pick(df,"Cash After","cash after")
btc_a  = pick(df,"BTC After","btc after")
eq_a   = pick(df,"Equity After","equity after")

if not all([ts,side,reason,price,qty,feeusd]):
    raise SystemExit("[ERR] required columns missing")

# Parse time, slice last 7d
df[ts] = pd.to_datetime(df[ts], utc=True, errors="coerce")
end = df[ts].max(); start = end - pd.Timedelta(days=7)
w = df[df[ts].between(start, end)].copy()

# Numerics
w["_price"] = pd.to_numeric(w[price], errors="coerce")
w["_qty"]   = pd.to_numeric(w[qty],   errors="coerce")
w["_fee"]   = pd.to_numeric(w[feeusd],errors="coerce")
w["_note"]  = w[note].astype(str) if note else ""
w["_side"]  = w[side].astype(str).str.upper()
w["_reason"]= w[reason].astype(str).str.upper()
if feeraw:
    w["_fee_raw"] = pd.to_numeric(w[feeraw], errors="coerce")

# Signed notional for balances, abs for rate math
w["_notional_signed"] = w["_price"] * w["_qty"]
w["_notional_abs"]    = w["_notional_signed"].abs()

# ---- Fee repair: broadened safe guard (catches 12.09% & 11.52%) ----
BAD_START = pd.Timestamp("2025-10-25T00:00:00Z")
BAD_END   = pd.Timestamp("2025-10-31T23:59:59Z")    # inclusive window end
in_bad    = (w[ts] >= BAD_START) & (w[ts] <= BAD_END)

with np.errstate(divide="ignore", invalid="ignore"):
    fee_pct_raw = 100.0 * w["_fee"] / w["_notional_abs"]
fee_pct_raw = fee_pct_raw.replace([np.inf,-np.inf], np.nan)

# suspicious if >1% or >5% of notional, on tiny trades
sus_fee = (w["_notional_abs"].between(20, 120)) & ((fee_pct_raw > 1.0) | (w["_fee"] > 0.05*w["_notional_abs"]))

if feeraw:
    sane_raw = w["_fee_raw"].between(0, 200, inclusive="both")
    raw_bps  = w["_fee_raw"].where(sane_raw)
else:
    raw_bps  = pd.Series(index=w.index, dtype=float)

need_fix = in_bad & sus_fee & (w["_notional_abs"] > 0)
bps = np.where(need_fix & raw_bps.notna(), raw_bps, 10.0)  # default 10 bps
bps_s = pd.Series(bps, index=w.index)

fee_fix = (w["_notional_abs"] * (bps_s / 10000.0)).round(2)
w.loc[need_fix, "_fee"] = fee_fix
w.loc[need_fix, "_note"] = (
    w.loc[need_fix, "_note"].fillna("").astype(str)
    + " | fee:recalc_from_bps=" + bps_s[need_fix].round(2).astype(str)
)

# Recompute fee % after fix
with np.errstate(divide="ignore", invalid="ignore"):
    w["_fee_pct"] = (100.0 * w["_fee"] / w["_notional_abs"]).replace([np.inf,-np.inf], np.nan).round(2)

# ---- Balances: rebuild NaNs by simulation over time ----
# Seed balances (prefer last known in full ledger before window)
seed_cash = np.nan
seed_btc  = np.nan
if cash_a and btc_a:
    before = df[df[ts] < start].copy()
    if not before.empty:
        seed_cash = pd.to_numeric(before[cash_a], errors="coerce").dropna().iloc[-1] if cash_a in before else np.nan
        seed_btc  = pd.to_numeric(before[btc_a],  errors="coerce").dropna().iloc[-1] if btc_a  in before else np.nan
# Fallback to config/env
if np.isnan(seed_cash) or np.isnan(seed_btc):
    try:
        cfg = json.loads(CFG.read_text())
    except Exception:
        cfg = {}
    if np.isnan(seed_cash):
        seed_cash = float(os.getenv("START_CASH", cfg.get("cash_usd", 10000.0)))
    if np.isnan(seed_btc):
        seed_btc  = float(os.getenv("START_BTC",  cfg.get("btc", 0.0)))

# Attach After columns
if cash_a: w["Cash After"]   = pd.to_numeric(w[cash_a], errors="coerce")
if btc_a:  w["BTC After"]    = pd.to_numeric(w[btc_a],  errors="coerce")
if eq_a:   w["Equity After"] = pd.to_numeric(w[eq_a],   errors="coerce")

w = w.sort_values(ts)  # ensure chronological for simulation
cash_run = seed_cash
btc_run  = seed_btc

for i,row in w.iterrows():
    # If row already has valid after balances, trust them and update the running state
    has_ca = ("Cash After" in w.columns) and pd.notna(row.get("Cash After"))
    has_ba = ("BTC After"  in w.columns) and pd.notna(row.get("BTC After"))

    if not (has_ca and has_ba):
        notional = float(row["_notional_abs"]) if pd.notna(row["_notional_abs"]) else 0.0
        fee      = float(row["_fee"])          if pd.notna(row["_fee"])          else 0.0
        qtyv     = float(row["_qty"])          if pd.notna(row["_qty"])          else 0.0
        sidev    = str(row["_side"])

        if sidev == "BUY":
            cash_run -= (notional + fee)
            btc_run  += qtyv
        elif sidev == "SELL":
            cash_run += (notional - fee)
            btc_run  -= qtyv

        # write back
        w.at[i, "Cash After"] = cash_run
        w.at[i, "BTC After"]  = btc_run

    else:
        cash_run = float(row["Cash After"])
        btc_run  = float(row["BTC After"])

    # always recompute equity after when we have price and both balances
    if pd.notna(w.at[i, "Cash After"]) and pd.notna(w.at[i, "BTC After"]) and pd.notna(row["_price"]):
        w.at[i, "Equity After"] = w.at[i, "Cash After"] + w.at[i, "BTC After"] * row["_price"]

# ---- Filter placeholder SELL spam; keep DCA and legitimate rows ----
spray = (
    w["_reason"].eq("LLM")
    & w["_side"].eq("SELL")
    & w["_note"].str.strip().isin(["","nan","None","NaN"])
    & w["_notional_abs"].between(20.5, 21.5)
    & w["_fee"].between(0.019, 0.021)
)

is_test = w["_reason"].eq("ENGINE") | w["_note"].str.contains("test", case=False, na=False)
keep_mask = (~is_test) & (~spray)
w = w[keep_mask].copy()

# ---- Present (ascending; set ascending=False to flip) ----
w["Time (UTC)"] = w[ts].dt.strftime("%Y-%m-%d %H:%M:%S%z").str.replace(r"\+0000$", "+00:00", regex=True)
present_cols = {
    "Side": w["_side"],
    "Reason": w["_reason"],
    "Price": w["_price"].round(2),
    "Qty BTC": w["_qty"].round(8),
    "Notional": w["_notional_abs"].round(2),
    "Fee (USD)": w["_fee"].round(2),
    "Fee %": w["_fee_pct"],
    "Note": w["_note"],
}
tab = pd.DataFrame({"Time (UTC)": w["Time (UTC)"], **present_cols})
for c in ["Cash After","BTC After","Equity After"]:
    if c in w.columns:
        tab[c] = w[c]

table = tab.sort_values("Time (UTC)", ascending=True).reset_index(drop=True)

# Header summary
def last(s): return s.iloc[-1] if len(s) else np.nan
latest_price = last(w["_price"])
latest_cash  = last(w["Cash After"])   if "Cash After" in w.columns   else np.nan
latest_btc   = last(w["BTC After"])    if "BTC After" in w.columns    else np.nan
latest_eq    = last(w["Equity After"]) if "Equity After" in w.columns else np.nan

fee_fixes = int(need_fix.sum())
placeholder_count = int(spray.sum())

def fm2(x):   return "—" if pd.isna(x) else f"{x:,.2f}"
def fmbtc(x): return "—" if pd.isna(x) else f"{x:,.8f}"

head = (
    f"<h2>Weekly Balance — {table['Time (UTC)'].iloc[0]} — {table['Time (UTC)'].iloc[-1]}</h2>"
    f"<p><b>Latest price</b>: {fm2(latest_price)}<br>"
    f"<b>Cash</b>: {fm2(latest_cash)}<br>"
    f"<b>BTC</b>: {fmbtc(latest_btc)}<br>"
    f"<b>Equity</b>: {fm2(latest_eq)}<br>"
    f"<b>Fills (7d)</b>: {len(table)}<br>"
    f"<span style='color:#666'>Fee fixes: {fee_fixes} | Placeholders filtered: {placeholder_count}</span></p>"
)

html = f"""<!doctype html><meta charset="utf-8">
<title>Weekly Balance</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:16px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:6px;font-size:12px}}
th{{background:#f6f6f6;position:sticky;top:0}}
</style>
{head}
{table.to_html(index=False, border=0)}
"""
OUT.write_text(html, encoding="utf-8")
print(f"[OK] wrote {OUT}")
