#!/usr/bin/env python3
import argparse, io, re, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

CSS = """
body{font:14px system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:16px;color:#111}
h1{margin:0 0 6px 0;font-size:20px}
.meta{display:flex;gap:18px;flex-wrap:wrap;margin:4px 0 12px 0}
.meta .ts{opacity:.7}
.tbl{border-collapse:collapse;width:100%}
.tbl th,.tbl td{border:1px solid #ddd;padding:6px 8px;vertical-align:top}
.tbl th{background:#fafafa;text-align:left}
.tbl tr:nth-child(even){background:#fcfcfc}
"""

def _read_trades(csv_path: Path) -> pd.DataFrame:
    lines = csv_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return pd.DataFrame()
    hdr_idx = 0
    header_re = re.compile(r"(Time \(UTC\)|ts(_dt)?|Side|Price|Qty(_BTC)?)", re.I)
    for i, line in enumerate(lines[:50]):
        if header_re.search(line):
            hdr_idx = i; break
    buf = io.StringIO("\n".join(lines[hdr_idx:]))
    return pd.read_csv(buf, engine="python", sep=None)

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    cols = {c.lower().strip(): c for c in df.columns}
    def has(*opts): return next((cols.get(o.lower()) for o in opts if cols.get(o.lower())), None)

    # timestamp -> ts_dt (UTC)
    ts_col = has("ts_dt","Time (UTC)","ts","timestamp","time")
    if ts_col is None: raise ValueError("No timestamp column found.")
    ts = pd.to_datetime(df[ts_col], utc=True, errors="coerce") if ts_col.lower()!="ts" \
         else pd.to_datetime(df[ts_col], unit="s", utc=True, errors="coerce")

    def numcol(*names, default=None):
        name = has(*names)
        if name is None: return pd.Series(default, index=df.index, dtype="float64")
        return pd.to_numeric(df[name], errors="coerce")

    price = numcol("Price")
    qty   = numcol("Qty BTC","qty_btc","qty", default=0.0)
    fee   = numcol("Fee (USD)","fee_usd","fee", default=0.0)
    fee_pct = numcol("Fee %","fe %","fe_pct","fee_pct", default=None)  # tolerate variants

    notional = numcol("Notional","notional_usd", default=None)
    notional = notional.where(notional.notna(), price*qty)

    side   = df.get(has("Side"), pd.Series("", index=df.index)).astype(str)
    reason = df.get(has("Reason"), pd.Series("", index=df.index)).astype(str)

    ccols = [c for c in df.columns if c.lower() in {"note","comment","comments"}]
    note = (df[ccols[0]].astype(str) if ccols else pd.Series("", index=df.index, dtype="string")).fillna("")
    note = note.where(note.str.len()>0, reason).fillna("").replace(r"\s+"," ", regex=True)

    cash_after   = numcol("Cash After","cash_after")
    btc_after    = numcol("BTC After","btc_after")
    equity_after = numcol("Equity After","equity_after","equity")

    # Compute Fee % where missing: 100 * fee / notional
    if fee_pct.isna().any():
        with pd.option_context('mode.use_inf_as_na', True):
            calc = (fee / notional) * 100.0
        fee_pct = fee_pct.where(fee_pct.notna(), calc)

    # If values look fractional (<=0.5), treat as fraction and convert to percent
    med = fee_pct.dropna().median()
    if pd.notna(med) and med <= 0.5:
        fee_pct = fee_pct * 100.0

    out = pd.DataFrame({
        "ts_dt": ts,
        "Side": side,
        "Reason": reason,
        "Price": price,
        "Qty BTC": qty,
        "Notional": notional,
        "Fee (USD)": fee,
        "Fee %": fee_pct,
        "Note": note,
        "Cash After": cash_after,
        "BTC After": btc_after,
        "Equity After": equity_after,
    })

    return out.sort_values("ts_dt", kind="mergesort").reset_index(drop=True)

def _rolling(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty: return df
    end = df["ts_dt"].max()
    if pd.isna(end): return df.iloc[0:0]
    start = end - timedelta(days=days)
    return df.loc[df["ts_dt"].between(start, end, inclusive="both")].copy()

def _format_range(title: str, df: pd.DataFrame) -> str:
    return f"{title} — {df['ts_dt'].min().strftime('%Y-%m-%d %H:%M UTC')} — {df['ts_dt'].max().strftime('%Y-%m-%d %H:%M UTC')}"

def _latest(df: pd.DataFrame, col: str):
    return df[col].dropna().iloc[-1] if col in df.columns and df[col].notna().any() else None

def _to_html(dfw: pd.DataFrame, title: str) -> str:
    if dfw.empty:
        return f"<!doctype html><meta charset='utf-8'><style>{CSS}</style><h1>{title}</h1><p>No rows in window.</p>"

    disp = dfw.rename(columns={"ts_dt":"Time (UTC)"}).copy()
    cols = ["Time (UTC)","Side","Reason","Price","Qty BTC","Notional","Fee (USD)","Fee %","Note","Cash After","BTC After","Equity After"]
    disp = disp[[c for c in cols if c in disp.columns]]

    fmt = {
        "Price":"{:,.2f}","Qty BTC":"{:,.8f}","Notional":"{:,.2f}","Fee (USD)":"{:,.2f}",
        "Fee %":"{:.2f}","Cash After":"{:,.2f}","BTC After":"{:,.8f}","Equity After":"{:,.2f}"
    }
    for c,f in fmt.items():
        if c in disp.columns:
            disp[c] = disp[c].apply(lambda x: f.format(x) if pd.notna(x) else "")

    latest_price=_latest(dfw,"Price"); last_cash=_latest(dfw,"Cash After")
    last_btc=_latest(dfw,"BTC After"); last_equity=_latest(dfw,"Equity After"); fills=len(dfw)

    head = f"""
<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>{CSS}</style>
<h1>{title}</h1>
<div class="meta">
  <div><b>Latest price:</b> {f"${latest_price:,.2f}" if latest_price is not None else "—"}</div>
  <div><b>Cash:</b> {f"${last_cash:,.2f}" if last_cash is not None else "—"}</div>
  <div><b>BTC:</b> {f"{last_btc:,.8f}" if last_btc is not None else "—"}</div>
  <div><b>Equity:</b> {f"${last_equity:,.2f}" if last_equity is not None else "—"}</div>
  <div><b>Fills (7d):</b> {fills}</div>
  <div class="ts">Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
</div>
"""
    return head + disp.to_html(index=False, classes="tbl", border=0, escape=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--title", type=str, default="Weekly Balance")
    ap.add_argument("--order", choices=["newest","oldest"], default="newest")
    a = ap.parse_args()

    if not a.csv.exists():
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(f"<!doctype html><meta charset='utf-8'><style>{CSS}</style><h1>{a.title}</h1><p>No data.</p>", encoding="utf-8")
        return

    df = _normalize(_read_trades(a.csv))
    win = _rolling(df, a.days)

    # Drop backfill/placeholder/test rows to avoid fake cash swings
    if not win.empty:
        low_note = win.get("Note","").astype(str).str.lower()
        low_reason = win.get("Reason","").astype(str).str.lower()
        mask = ~(
            low_note.str.contains(r"backfill|placeholder", na=False) |
            low_reason.isin(["engine","test","manual backfill"])
        )
        win = win.loc[mask].copy()

    if a.order == "newest":
        win = win.sort_values("ts_dt", ascending=False, kind="mergesort").reset_index(drop=True)
    else:
        win = win.sort_values("ts_dt", ascending=True,  kind="mergesort").reset_index(drop=True)

    title = _format_range(a.title, win) if not win.empty else a.title
    html = _to_html(win, title)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding="utf-8")

if __name__ == "__main__":
    main()
