import os, re, html, sys
from pathlib import Path
import pandas as pd, numpy as np
from datetime import datetime, timezone

ROOT   = Path(os.getenv("PROJECT_ROOT", Path.cwd()))
STATE  = ROOT / "state"
REPORT = STATE / "reports"
REPORT.mkdir(parents=True, exist_ok=True)

# Single source of truth for reports:
# rebuilt master ledger with balances + notes
LEDGER = STATE / "history" / "master_ledger_with_balances_and_notional_v2.csv"

if not LEDGER.exists():
    raise SystemExit(
        f"[ERR] Expected ledger {LEDGER}. "
        "Run scripts/rebuild_master_ledger.py first."
    )

print(f"[LEDGER] Using {LEDGER}")

OUT = REPORT / "weekly_email.html"   # this is the file you open in the browser

def pick(df, *opts):
    m = {c.lower().strip(): c for c in df.columns}
    for o in opts:
        c = m.get(o.lower())
        if c:
            return c
    return None

def _to_num(x):
    try:
        if x is None: return np.nan
        if isinstance(x, (int, float, np.number)): return float(x)
        s = str(x).strip()
        if s in ("", "nan", "None"): return np.nan
        return float(s.replace(",", ""))
    except Exception:
        return np.nan

def _last_valid_numeric(series):
    try:
        s = pd.to_numeric(series, errors="coerce")
        idx = s.last_valid_index()
        return float(s.loc[idx]) if idx is not None else np.nan
    except Exception:
        return np.nan

if not LEDGER.exists():
    raise SystemExit(f"[ERR] Missing ledger: {LEDGER}")

df = pd.read_csv(LEDGER)

# column mapping
ts     = pick(df, "Time (UTC)","time (utc)","time","ts","ts_utc","ts_dt")
side   = pick(df, "Side","side")
reason = pick(df, "Reason","reason")
price  = pick(df, "Price","price")
qty    = pick(df, "Qty BTC","qty btc","qty_btc","quantity","qty")
fee    = pick(df, "Fee","fee","fees","fee_usd")
note   = pick(df, "Note","note","reason_short","why")
cash_a = pick(df, "Cash After","cash after","cash_after")
btc_a  = pick(df, "BTC After","btc after","btc_after")
eq_a   = pick(df, "Equity After","equity after","equity_after")

need = [ts, side, reason, price, qty, fee]
if any(x is None for x in need):
    raise SystemExit("[ERR] ledger missing required columns (need time/side/reason/price/qty/fee)")

# Canonical timestamp
df[ts] = pd.to_datetime(df[ts], utc=True, errors="coerce")
df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)

# Numeric helpers on the full df
df["_price"] = pd.to_numeric(df[price], errors="coerce")
df["_qty"]   = pd.to_numeric(df[qty],   errors="coerce")
df["_fee"]   = pd.to_numeric(df[fee],   errors="coerce")

# If after-balances are missing, simulate on FULL df (before slicing)
_have_cash = cash_a and pd.to_numeric(df[cash_a], errors="coerce").notna().sum() > 0
_have_btc  = btc_a  and pd.to_numeric(df[btc_a],  errors="coerce").notna().sum() > 0
_have_eq   = eq_a   and pd.to_numeric(df[eq_a],   errors="coerce").notna().sum() > 0

if not (_have_cash and _have_btc and _have_eq):
    start_cash = _last_valid_numeric(df[cash_a]) if cash_a else _to_num(os.getenv("START_CASH", 10000))
    start_btc  = _last_valid_numeric(df[btc_a])  if btc_a  else _to_num(os.getenv("START_BTC", 0))
    cur_cash = start_cash if not np.isnan(start_cash) else 10000.0
    cur_btc  = start_btc  if not np.isnan(start_btc)  else 0.0
    cash_seq, btc_seq, eq_seq = [], [], []
    for _, r in df.iterrows():
        p, q, f = _to_num(r[price]), _to_num(r[qty]), _to_num(r[fee])
        s = str(r[side]).upper()
        if s.startswith("B"):
            cur_cash -= (p*q) + (f if not np.isnan(f) else 0.0)
            cur_btc  += q
        elif s.startswith("S"):
            cur_cash += (p*q) - (f if not np.isnan(f) else 0.0)
            cur_btc  -= q
        cash_seq.append(cur_cash)
        btc_seq.append(cur_btc)
        eq_seq.append(cur_cash + cur_btc * p)
    df["Cash After"]   = cash_seq
    df["BTC After"]    = btc_seq
    df["Equity After"] = eq_seq
    cash_a, btc_a, eq_a = "Cash After","BTC After","Equity After"

# Slice 7 days
end   = df[ts].max()
start = end - pd.Timedelta(days=7)
w = df[(df[ts] >= start) & (df[ts] <= end)].copy()

# Working columns
w["_ts"]     = pd.to_datetime(w[ts], utc=True, errors="coerce")
w["_side"]   = w[side].astype(str).str.upper()
w["_reason"] = w[reason].astype(str).str.upper()
w["_note"]   = w[note].astype(str) if note else ""

# -------------------------------
# Normalize balance column names from ledger -> report
# -------------------------------
if cash_a and "Cash After" not in w.columns:
    w["Cash After"] = pd.to_numeric(w[cash_a], errors="coerce")

if btc_a and "BTC After" not in w.columns:
    w["BTC After"] = pd.to_numeric(w[btc_a], errors="coerce")

if eq_a and "Equity After" not in w.columns:
    w["Equity After"] = pd.to_numeric(w[eq_a], errors="coerce")

# -------------------------------
# Keep 25–26 Oct backfills; drop test rows; keep rows that have any after-balance
# OR are DCA OR are allowed backfills
# -------------------------------
allow_backfill_dates = (
    (w["_ts"].dt.date >= pd.to_datetime("2025-10-25").date()) &
    (w["_ts"].dt.date <= pd.to_datetime("2025-10-26").date())
)
is_backfill   = w["_note"].str.contains(r"backfill|recomputed", case=False, na=False)
keep_backfill = is_backfill & allow_backfill_dates

# ENGINE + explicit "test" rows are noise
is_test = (
    w["_reason"].eq("ENGINE") |
    w["_note"].str.contains("test", case=False, na=False)
)

# Does this row already have any after-balance in the original ledger?
if cash_a:
    has_cash = pd.to_numeric(w[cash_a], errors="coerce").notna()
else:
    has_cash = pd.Series(False, index=w.index)

if btc_a:
    has_btc = pd.to_numeric(w[btc_a], errors="coerce").notna()
else:
    has_btc = pd.Series(False, index=w.index)

if eq_a:
    has_eq = pd.to_numeric(w[eq_a], errors="coerce").notna()
else:
    has_eq = pd.Series(False, index=w.index)

has_any_balance = has_cash | has_btc | has_eq

# Always keep DCA rows
is_dca = w["_reason"].eq("DCA")

keep_mask = (has_any_balance | is_dca | keep_backfill) & (~is_test)
w = w[keep_mask].copy()

# -------------------------------
# De-duplicate by second+side+qty (keeps first)
# -------------------------------
w["tsec"]      = w["_ts"].dt.floor("s")
w["_qty_num"]  = pd.to_numeric(w[qty], errors="coerce")
w["_dedup_key"] = (
    w["tsec"].astype(str)
    + "|" + w["_side"].str[0]
    + "|" + w["_qty_num"].round(8).astype(str)
)
w = w[~w.duplicated("_dedup_key", keep="first")].copy()

# -------------------------------
# Numerics / notional
# -------------------------------
w["Price"]     = pd.to_numeric(w[price], errors="coerce")
w["Qty BTC"]   = pd.to_numeric(w[qty],   errors="coerce")
w["Fee (USD)"] = pd.to_numeric(w[fee],   errors="coerce").round(2)
w["Notional"]  = (w["Price"] * w["Qty BTC"]).abs()

# -------------------------------

# -------------------------------
# Tech rows to hide from the table (but still affect balances)
# -------------------------------
tech_mask = (
    w["_reason"].isin(["ENGINE", "REPLAY", "BACKFILL"]) |
    w["_note"].str.contains("test", case=False, na=False)
)

table_source = w[~tech_mask].copy()

# -------------------------------
# Normalize fees on the table_source
# -------------------------------
note_str = table_source["_note"]
bps_in_note = note_str.str.extract(
    r'feerecalc_from_bps\s*=\s*([0-9]+(?:\.[0-9]+)?)',
    expand=False
).astype(float)

mask_bps = bps_in_note.notna() & table_source["Notional"].gt(0)
table_source.loc[mask_bps, "Fee (USD)"] = (
    table_source.loc[mask_bps, "Notional"] * (bps_in_note[mask_bps] / 10000.0)
).round(2)

# Guardrail: if Fee > 5% of notional and not DCA, clamp to 10 bps of notional
mask_implausible = (
    (table_source["Notional"] > 0) &
    (table_source["Fee (USD)"] > 0.05 * table_source["Notional"]) &
    (~table_source["_reason"].eq("DCA"))
)
table_source.loc[mask_implausible, "Fee (USD)"] = (
    table_source.loc[mask_implausible, "Notional"] * 0.0010
).round(2)

with np.errstate(divide="ignore", invalid="ignore"):
    table_source["Fee %"] = (
        100.0 * (table_source["Fee (USD)"] / table_source["Notional"])
    ).replace([np.inf, -np.inf], np.nan)
table_source["Fee %"] = table_source["Fee %"].round(2)

# Public columns
table_source["Time (UTC)"] = table_source["_ts"].dt.strftime(
    "%Y-%m-%d %H:%M:%S%z"
).str.replace(r"\+0000$", "+00:00", regex=True)
table_source["Side"]   = table_source[side].astype(str)
table_source["Reason"] = table_source[reason].astype(str)
table_source["Note"]   = (table_source[note].astype(str) if note else "")

# ----- Note cleaning / overrides (as you already had) -----
raw = (table_source[note].astype(str) if note else "")

only_maintenance = raw.str.fullmatch(
    r"(?:\s*(backfill|balancesrecomputed|notionalrecomputed)\s*\|?)+",
    case=False
)

pretty = (raw
    .str.replace(r"\b(backfill|balancesrecomputed|notionalrecomputed)\b", "", regex=True)
    .str.replace(r"\s*\|\s*\|\s*", " | ", regex=True)
    .str.replace(r"^\s*\|\s*|\s*\|\s*$", "", regex=True)
    .str.replace(r"\s{2,}", " ", regex=True)
    .str.strip()
)

table_source["Note"] = np.where(
    only_maintenance,
    "backfill (fee/balances re-computed)",
    pretty
)

OVERRIDE_CSV = STATE / "history" / "note_overrides.csv"
if OVERRIDE_CSV.exists():
    try:
        ov = pd.read_csv(OVERRIDE_CSV)
        ts_col = next((c for c in ["Time (UTC)", "time (utc)", "ts", "ts_dt"] if c in ov.columns), None)
        if ts_col:
            ov["_ts"] = pd.to_datetime(ov[ts_col], utc=True, errors="coerce")
            ov = ov.dropna(subset=["_ts"]).rename(columns={"Note": "NoteOverride"})
            table_source = table_source.merge(
                ov[["_ts", "NoteOverride"]],
                on="_ts",
                how="left"
            )
            table_source["Note"] = np.where(
                table_source["NoteOverride"].notna(),
                table_source["NoteOverride"],
                table_source["Note"]
            )
            table_source.drop(columns=["NoteOverride"], inplace=True)
    except Exception as e:
        print(f"[WARN] note_overrides.csv present but could not be applied: {e}")

# Build table sorted by real timestamp (newest first)
cols = [
    "Time (UTC)","Side","Reason","Price","Qty BTC",
    "Notional","Fee (USD)","Fee %","Note"
]
if "Cash After" in table_source.columns:   cols += ["Cash After"]
if "BTC After" in table_source.columns:    cols += ["BTC After"]
if "Equity After" in table_source.columns: cols += ["Equity After"]

table = (
    table_source[cols + ["_ts"]]
    .sort_values("_ts", ascending=False)
    .drop(columns=["_ts"])
    .reset_index(drop=True)
)

mismatch = (
    w["Cash After"] + w["BTC After"] * w["Price"]
) - w["Equity After"]
print(f"[CHK] max equity mismatch: {float(np.nanmax(np.abs(mismatch.fillna(0.0)))):.6f}")
print(f"[ROWS] window rows: {len(w)}  table rows: {len(table)}")

chk = (
    w["Cash After"] + w["BTC After"] * w["Price"]
) - w["Equity After"]
bad = np.nanmax(np.abs(chk.fillna(0.0)))
print(f"[CHK] max equity mismatch = {bad:.6f}")

# Header stats from FULL df
latest_price = _last_valid_numeric(df[price])
latest_cash  = _last_valid_numeric(df[cash_a]) if cash_a else np.nan
latest_btc   = _last_valid_numeric(df[btc_a])  if btc_a  else np.nan
latest_eq    = _last_valid_numeric(df[eq_a])   if eq_a   else (
    latest_cash + latest_btc * latest_price
    if not np.isnan(latest_cash) and not np.isnan(latest_btc) and not np.isnan(latest_price)
    else np.nan
)

def fm2(x):  return "—" if x is None or np.isnan(x) else f"${x:,.2f}"
def fmbtc(x):return "—" if x is None or np.isnan(x) else f"{x:,.8f}"

header_html = f"""
<p class="meta">
<b>Latest price</b>: {fm2(latest_price)}<br>
<b>Cash</b>: {fm2(latest_cash)}<br>
<b>BTC</b>: {fmbtc(latest_btc)}<br>
<b>Equity</b>: {fm2(latest_eq)}<br>
<b>Fills (7d)</b>: {len(table)}<br>
<span class="small">Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</span>
</p>
"""

# ---- per-column formatters (strings only; no None) ----
def fmt2(x):  return "" if pd.isna(x) else f"{float(x):,.2f}"     # dollars & percents
def fmt8(x):  return "" if pd.isna(x) else f"{float(x):,.8f}"     # BTC precision

formatters = {
    "Price":        fmt2,
    "Notional":     fmt2,
    "Fee (USD)":    fmt2,
    "Fee %":        (lambda x: "" if pd.isna(x) else f"{float(x):.2f}"),
    "Cash After":   fmt2,
    "Equity After": fmt2,
    "Qty BTC":      fmt8,
    "BTC After":    fmt8,
}

table_html = table.to_html(
    index=False,
    border=0,
    classes="grid",
    justify="center",
    formatters=formatters,
    na_rep=""
)

page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Weekly Balance</title>
<style>
body{{font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; padding:16px}}
h1{{margin: 0 0 8px 0}}
.meta{{margin: 8px 0 16px 0}}
.small{{color:#666; font-size:12px}}
.grid{{border-collapse: collapse; width:100%}}
.grid th, .grid td{{border:1px solid #ddd; padding:6px; font-size:12px}}
.grid th{{background:#f8f8f8; position: sticky; top:0}}
.right{{text-align:right}}
</style>
</head>
<body>
<h1>Weekly Balance — {start.strftime('%Y-%m-%d %H:%M UTC')} — {end.strftime('%Y-%m-%d %H:%M UTC')}</h1>
{header_html}
{table_html}
</body>
</html>
"""

OUT.write_text(page, encoding="utf-8")
print(f"[OK] Wrote {OUT}")
