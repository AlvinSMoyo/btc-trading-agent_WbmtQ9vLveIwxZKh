#!/usr/bin/env python3
import os, json, io
from pathlib import Path
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT    = Path("/root/btc-trading-agent")
STATE   = ROOT / "state"
REPORTS = STATE / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

# ---------- helpers ----------
def pick(df, names, default=None):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([default]*len(df), index=df.index)

def parse_time(df):
    cand = next((n for n in ["Time (UTC)","time","timestamp","ts_dt","ts","Time","datetime"] if n in df.columns), None)
    if cand is None:
        raise SystemExit("[ERR] no usable time column")
    s = df[cand]
    s_num = pd.to_numeric(s, errors="coerce")
    # epoch ms
    if s_num.notna().mean() > 0.8 and s_num.max() > 9e11:
        return pd.to_datetime(s_num, unit="ms", utc=True, errors="coerce")
    # epoch s
    if s_num.notna().mean() > 0.8 and s_num.max() > 9e8:
        return pd.to_datetime(s_num, unit="s",  utc=True, errors="coerce")
    # ISO-ish
    return pd.to_datetime(s, utc=True, errors="coerce")

def load_trades_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    t     = parse_time(df)
    side  = pick(df, ["Side","side","action","order_side"], "").astype(str).str.upper()
    price = pd.to_numeric(pick(df, ["Price","price","px"], 0.0), errors="coerce").fillna(0.0)
    qty   = pd.to_numeric(pick(df, ["Qty BTC","qty_btc","qty","quantity","size","Size BTC"], 0.0), errors="coerce").fillna(0.0)
    fee   = pd.to_numeric(pick(df, ["Fee","fee_usd","fee","Fees (USD)"], 0.0), errors="coerce").fillna(0.0)
    reason= pick(df, ["Reason","reason","reason_short"], "")
    note  = pick(df, ["Note","note"], "")

    out = pd.DataFrame({
        "Time (UTC)": t, "Side": side, "Price": price, "Qty BTC": qty,
        "Fee": fee, "Reason": reason, "Note": note
    }).dropna(subset=["Time (UTC)"])
    out = out[(out["Price"] > 0) & (out["Qty BTC"] > 0)]
    # sanitize text early
    for col in ("Reason","Note"):
        out[col] = out[col].astype("string").fillna("").str.strip()
    return out.sort_values("Time (UTC)").reset_index(drop=True)

def svg_from_weekly(weekly_df: pd.DataFrame) -> str:
    if weekly_df.empty:
        return "<p class='small'>No weekly equity yet.</p>"
    fig, ax = plt.subplots(figsize=(7.5,3.2), dpi=120)
    ax.plot(weekly_df.index, weekly_df["Equity"])
    ax.set_title("Weekly Equity (UTC, Monday close)")
    ax.set_xlabel("Week"); ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.35)
    buf = io.BytesIO(); plt.tight_layout(); fig.savefig(buf, format="svg"); plt.close(fig)
    return buf.getvalue().decode("utf-8")

def html_page(title: str, blocks: list[str]) -> str:
    style = """
    <style>
      :root{color-scheme: light dark}
      body{font:14px system-ui,Segoe UI,Arial;margin:20px}
      h1{margin:0 0 8px} h2{margin:18px 0 8px}
      table{border-collapse:collapse;width:100%;table-layout:auto}
      th,td{border:1px solid #ccc;padding:6px 8px;text-align:left;vertical-align:top;white-space:nowrap}
      thead th{background:#f5f5f5}
      .small{color:#666}
      .wrap td{white-space:normal}
    </style>
    """
    return f"<!doctype html><meta charset='utf-8'><title>{title}</title>{style}<h1>{title}</h1>" + "".join(blocks)

def df_to_table(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, justify="left", classes=["wrap"])

# ---------- load + ledger ----------
src = STATE / "trades.csv"
if not src.exists():
    raise SystemExit("[ERR] state/trades.csv not found")

df = load_trades_csv(src)

# seeds from env/state.json
seed_cash = float(os.getenv("START_CASH", 10000.0))
seed_btc  = float(os.getenv("START_BTC", 0.0))
st_json = STATE / "state.json"
if st_json.exists():
    try:
        st = json.loads(st_json.read_text())
        seed_cash = float(st.get("cash_usd", seed_cash))
        seed_btc  = float(st.get("btc", seed_btc))
    except Exception:
        pass

cash, btc = seed_cash, seed_btc
rows = []
for _, r in df.iterrows():
    ts, side = r["Time (UTC)"], r["Side"]
    px, qty, fee = float(r["Price"]), float(r["Qty BTC"]), float(r["Fee"])
    if side.startswith("BUY"):
        cash -= qty*px + fee
        btc  += qty
    elif side.startswith("SELL"):
        cash += qty*px - fee
        btc  -= qty
    equity = cash + btc*px
    rows.append({
        "Time (UTC)": ts, "Side": side, "Price": px, "Qty BTC": qty, "Fee": fee,
        "Cash After": cash, "BTC After": btc, "Equity After": equity,
        "Reason": r.get("Reason",""), "Note": r.get("Note","")
    })

ledger = pd.DataFrame(rows).sort_values("Time (UTC)").reset_index(drop=True)

# --- enrich BEFORE saving ---
for col in ("Reason","Note"):
    if col not in ledger.columns:
        ledger[col] = ""
    ledger[col] = ledger[col].astype("string").fillna("").str.strip()

notional = (ledger["Price"].astype(float) * ledger["Qty BTC"].astype(float)).clip(lower=1e-12)
ledger["Fee %"] = (ledger["Fee"].astype(float) / notional) * 100
ledger["Notional USD"] = notional.round(2)

def _mk_note(row):
    bits = []
    if row["Note"]:
        bits.append(row["Note"])
    if row["Reason"] and row["Reason"] not in bits:
        bits.append(row["Reason"])
    bits.append(f"fee {row['Fee']:.2f} USD ({row['Fee %']:.3f}%)")
    return " | ".join(bits)

ledger["NoteDisplay"] = ledger.apply(_mk_note, axis=1)
ledger["Fee %"] = ledger["Fee %"].round(3)
ledger["Price"] = ledger["Price"].round(2)
ledger["Fee"]   = ledger["Fee"].round(2)

# Backfill Note from NoteDisplay if empty (purely for CSV convenience)
_empties = ledger["Note"].astype(str).str.strip().eq("")
ledger.loc[_empties, "Note"] = ledger.loc[_empties, "NoteDisplay"]

(REPORTS / "ledger_latest.csv").write_text(ledger.to_csv(index=False, na_rep=""))


# ---------- series ----------
eq = ledger.set_index("Time (UTC)")["Equity After"].sort_index()
daily_eq  = eq.groupby(pd.Grouper(freq="D")).last().ffill().to_frame(name="Equity")
weekly_eq = eq.groupby(pd.Grouper(freq="W-MON")).last().ffill().to_frame(name="Equity")

stamp = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%MZ")
daily_eq.to_csv(REPORTS/f"daily_balance_{stamp}.csv")
weekly_eq.to_csv(REPORTS/f"weekly_balance_{stamp}.csv")
daily_eq.to_csv(REPORTS/"daily_balance_latest.csv")
weekly_eq.to_csv(REPORTS/"weekly_balance_latest.csv")

# ---------- daily summary tables ----------
ld = ledger.set_index("Time (UTC)").sort_index()
g  = ld.groupby(pd.Grouper(freq="D"))
daily_summary = pd.DataFrame({
    "Open Eq": g["Equity After"].first(),
    "Close Eq": g["Equity After"].last(),
    "PnL": g["Equity After"].last() - g["Equity After"].first(),
    "Trades": g.size(),
    "Fees USD": g["Fee"].sum(),
    "Buy Notional": g.apply(lambda x: (x.loc[x["Side"].str.startswith("BUY"),  "Qty BTC"] * x.loc[x["Side"].str.startswith("BUY"),  "Price"]).sum()),
    "Sell Notional":g.apply(lambda x: (x.loc[x["Side"].str.startswith("SELL"), "Qty BTC"] * x.loc[x["Side"].str.startswith("SELL"), "Price"]).sum()),
}).fillna(0.0)

daily_balances = ld[["Equity After","Cash After","BTC After"]].groupby(pd.Grouper(freq="D")).last().rename(
    columns={"Equity After":"Equity","Cash After":"Cash","BTC After":"BTC"}
)

# ---------- summary header ----------
summary = {
    "Trades": len(ledger),
    "Start UTC": ledger["Time (UTC)"].min().strftime("%Y-%m-%d %H:%M:%S UTC") if len(ledger) else "",
    "End UTC":   ledger["Time (UTC)"].max().strftime("%Y-%m-%d %H:%M:%S UTC") if len(ledger) else "",
    "Last Equity": f"{eq.iloc[-1]:,.2f}" if len(eq) else "n/a",
}
def dict_table(d):
    return pd.DataFrame({"Metric": list(d.keys()), "Value": list(d.values())}).to_html(index=False, border=0, justify="left")

# ---------- HTML pages ----------
def html_blocks_daily():
    recent_cols = [
        "Time (UTC)","Side","Price","Qty BTC","Notional USD",
        "Fee","Fee %","Equity After","Reason","NoteDisplay"
    ]
    recent_tbl = df_to_table(ledger.tail(30)[recent_cols].rename(columns={"NoteDisplay":"Note"}))
    return [
        "<h2>Summary</h2>", dict_table(summary),
        "<h2>Daily Equity</h2>", df_to_table(daily_eq.reset_index().rename(columns={"index":"Time (UTC)"})),
        "<h2>Daily Balances (Equity, Cash, BTC)</h2>", df_to_table(daily_balances.reset_index()),
        "<h2>Daily Summary (open/close, PnL, trades, fees)</h2>", df_to_table(daily_summary.reset_index().rename(columns={"index":"Date"})),
        "<h2>Recent Fills (last 30)</h2>", recent_tbl
    ]

weekly_svg = svg_from_weekly(weekly_eq)

(REPORTS/"daily_balance_latest.html").write_text(
    html_page("Daily Balance (UTC)", html_blocks_daily())
)

(REPORTS/"weekly_balance_latest.html").write_text(
    html_page("Weekly Balance (UTC)", [
        "<h2>Summary</h2>", dict_table(summary),
        "<h2>Weekly Equity (Mon)</h2>", df_to_table(weekly_eq.reset_index()),
        "<h2>Overlay</h2>", weekly_svg
    ])
)

print("[OK] HTML + CSV reports written to", REPORTS)
