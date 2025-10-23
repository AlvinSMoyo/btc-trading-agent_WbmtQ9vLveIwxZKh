# scripts/baseline_overlay.py  — lean overlay
# Env:
#   STATE_DIR=state (default) | OVERLAY_DAYS="all" or int days | DCA_USD="300"

import os
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

STATE_DIR  = os.getenv("STATE_DIR", "state")
EQ_CSV     = os.path.join(STATE_DIR, "equity_history.csv")
TRADES_CSV = os.path.join(STATE_DIR, "trades.csv")
DCA_LOT    = float(os.getenv("DCA_USD", "300"))
OVERLAY    = (os.getenv("OVERLAY_DAYS", "all") or "all").strip().lower()

# ---------- helpers ----------
def ensure_utc(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, errors="coerce")
    if getattr(s.dt, "tz", None) is None:
        s = s.dt.tz_localize("UTC")
    else:
        s = s.dt.tz_convert("UTC")
    return s

def equity_at(ts: pd.Timestamp, eq: pd.DataFrame) -> float:
    i = int(eq["ts"].searchsorted(ts, side="right")) - 1
    i = max(0, min(i, len(eq) - 1))
    return float(eq["equity"].iloc[i])

def _as_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)

def _source_from_row(r) -> str:
    # prefer explicit 'source', else derive from reason
    src = str(r.get("source", "") or "").strip()
    if src:
        return src.upper()
    rsn = str(r.get("reason", "") or "").strip()
    return rsn.upper() if rsn else ""

def equity_row_before(eq: pd.DataFrame, ts: pd.Timestamp) -> dict:
    """Return the most recent equity row <= ts (as dict)."""
    i = int(eq["ts"].searchsorted(ts, side="right")) - 1
    i = max(0, min(i, len(eq)-1))
    return {
        "cash_usd": _as_float(eq["cash_usd"].iloc[i]) if "cash_usd" in eq.columns else 0.0,
        "btc":      _as_float(eq["btc"].iloc[i])      if "btc"      in eq.columns else 0.0,
        "price":    _as_float(eq["price"].iloc[i])    if "price"    in eq.columns else 0.0,
    }

def _qty_btc(row) -> float:
    """Prefer qty_btc if present; else qty; handle blanks."""
    try:
        return float(row.get("qty", row.get("qty_btc", 0) or 0) or 0.0)
    except Exception:
        return 0.0

def _src(row) -> str:
    return str(row.get("source") or row.get("reason") or "").upper()

def _note(row) -> str:
    # prefer explicit note; else show reason (LLM rationale etc.)
    return str(row.get("note") or row.get("reason") or "")

def _is_na(v) -> bool:
    try:
        return pd.isna(v) or str(v).strip().lower() in {"nan", "none", ""}
    except Exception:
        return False

def _to_float_or_none(v):
    try:
        x = float(v)
        return None if not np.isfinite(x) else x
    except Exception:
        return None

def _fmt_money(v: object) -> str:
    x = _to_float_or_none(v)
    return "" if x is None else f"${x:,.2f}"

def _fmt_btc(v: object) -> str:
    x = _to_float_or_none(v)
    return "" if x is None else f"{x:.8f}"

def _fmt_fee(v: object) -> str:
    # fee is money; blank if missing
    return _fmt_money(v)

def enrich_trades_with_balances(trades: pd.DataFrame, eq: pd.DataFrame) -> pd.DataFrame:
    """
    Replays trades forward, computing notional, cash_after, btc_after, equity_after.
    Uses the equity snapshot just before the first trade as the starting point.
    """
    if trades is None or trades.empty:
        return trades

    t = trades.copy()

    # --- ensure clean, numeric inputs
    t["ts"]   = pd.to_datetime(t["ts"], utc=True, errors="coerce")
    t["side"] = t.get("side","").astype(str).str.lower().str.strip()
    # prefer qty then qty_btc, but keep both as numeric for safety
    if "qty" in t.columns:
        t["qty"] = pd.to_numeric(t["qty"], errors="coerce")
    if "qty_btc" in t.columns:
        t["qty_btc"] = pd.to_numeric(t["qty_btc"], errors="coerce")
    t["price"]   = pd.to_numeric(t.get("price", 0),    errors="coerce")
    t["fee_usd"] = pd.to_numeric(t.get("fee_usd", 0),  errors="coerce")

    # canonical qty accessor
    def _q(row):
        q = row["qty"] if "qty" in row and pd.notna(row["qty"]) else row.get("qty_btc", 0)
        return float(q) if pd.notna(q) else 0.0

    t = t.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    # starting balances from the equity history right before the first trade
    start_bal = equity_row_before(eq, t["ts"].iloc[0])
    cash = float(start_bal.get("cash_usd", 0) or 0.0)
    btc  = float(start_bal.get("btc", 0) or 0.0)

    out_rows = []
    for _, r in t.iterrows():
        side = r["side"]
        px   = float(r["price"]  if pd.notna(r["price"])   else 0.0)
        qty  = _q(r)
        fee  = float(r["fee_usd"] if pd.notna(r["fee_usd"]) else 0.0)
        notional = px * qty

        if side == "buy":
            cash -= (notional + fee)
            btc  += qty
        elif side == "sell":
            btc  -= qty
            cash += (notional - fee)

        equity_after = cash + btc * px

        o = dict(r)
        o["qty_btc"]      = qty
        o["notional"]     = notional
        o["cash_after"]   = cash
        o["btc_after"]    = btc
        o["equity_after"] = equity_after
        o["ts_dt"]        = pd.to_datetime(r["ts"]).strftime("%Y-%m-%d %H:%M")
        out_rows.append(o)

    return pd.DataFrame(out_rows)

# ---------- load ----------
def load_equity() -> pd.DataFrame:
    if not Path(EQ_CSV).exists():
        raise SystemExit(f"[overlay] missing {EQ_CSV}")
    df = pd.read_csv(EQ_CSV)
    ts_col = next((c for c in ("ts_utc","ts","ts_dt") if c in df.columns), None)
    if ts_col is None:  # headerless
        df = pd.read_csv(EQ_CSV, header=None,
                         names=["ts","price","cash_usd","btc","equity","agent_flag"])
        ts_col = "ts"
    df["ts"]      = ensure_utc(df[ts_col])
    df["price"]   = pd.to_numeric(df.get("price", np.nan), errors="coerce")
    df["btc"]     = pd.to_numeric(df.get("btc",   np.nan), errors="coerce")
    df["equity"]  = pd.to_numeric(df.get("equity",np.nan), errors="coerce")
    df = (df.dropna(subset=["ts"]).sort_values("ts")
            .drop_duplicates(subset=["ts"], keep="last")
            .reset_index(drop=True))
    return df

def parse_all_trades_utc() -> pd.DataFrame:
    """
    Load ALL trades and produce a UTC-aware 'ts' column.
    Handles:
      - Headered CSV with columns like: ts|ts_utc, side, reason, price, qty_btc/qty, fee_usd, note, confidence, source
      - Headerless legacy lines with 7+ fields: id,side,reason,price,qty,fee,note,(confidence...), and we map best-effort
      - Epoch seconds/ms or ISO timestamps
    """
    path = Path(TRADES_CSV)
    if not path.exists():
        return pd.DataFrame(columns=["ts","side","price","qty","reason","source","confidence","fee_usd","note"])

    def to_ts_any(x):
        s = str(x).strip()
        if not s:
            return pd.NaT
        try:
            v = float(s)
            if v > 1e12:  # epoch ms
                return pd.to_datetime(int(v), unit="ms", utc=True, errors="coerce")
            if v > 1e9:   # epoch s
                return pd.to_datetime(int(v), unit="s", utc=True, errors="coerce")
        except Exception:
            pass
        return pd.to_datetime(s, utc=True, errors="coerce")

    # --- sniff header safely (without using pandas kwargs that vary by version) ---
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            first = f.readline().strip().lower()
        headered = any(k in first.split(",") for k in ["ts", "ts_utc", "side", "price", "qty", "qty_btc"])
    except Exception:
        headered = True  # safe default

    if headered:
        # read normally
        df = pd.read_csv(path, dtype=str)  # no 'errors' kw
        cols = {c.strip().lower(): c for c in df.columns}

        # timestamps
        if "ts_utc" in cols:
            ts = pd.to_datetime(df[cols["ts_utc"]], utc=True, errors="coerce")
        elif "ts" in cols:
            raw = df[cols["ts"]].astype(str).str.strip()
            num = pd.to_numeric(raw, errors="coerce")
            ts  = pd.to_datetime(num, unit="s", utc=True, errors="coerce")
            bad = ts.isna()
            if bad.any():
                ts.loc[bad] = pd.to_datetime(raw[bad], utc=True, errors="coerce")
        else:
            ts = pd.NaT

        out = pd.DataFrame()
        out["ts"]   = ts
        out["side"] = df[cols["side"]].astype(str).str.lower().str.strip() if "side" in cols else ""
        out["price"]= pd.to_numeric(df[cols["price"]], errors="coerce") if "price" in cols else np.nan

        # qty (prefer qty_btc)
        if "qty_btc" in cols:
            out["qty"] = pd.to_numeric(df[cols["qty_btc"]], errors="coerce")
        elif "qty" in cols:
            out["qty"] = pd.to_numeric(df[cols["qty"]], errors="coerce")
        else:
            out["qty"] = np.nan

        # meta
        out["reason"]     = df[cols["reason"]] if "reason" in cols else ""
        out["source"]     = df[cols["source"]] if "source" in cols else out["reason"]
        out["confidence"] = pd.to_numeric(df[cols["confidence"]], errors="coerce") if "confidence" in cols else np.nan
        out["fee_usd"]    = pd.to_numeric(df[cols["fee_usd"]],    errors="coerce") if "fee_usd"    in cols else np.nan
        out["note"]       = df[cols["note"]] if "note" in cols else ""
    else:
        # headerless legacy — open with errors='ignore' and pass the handle to pandas
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            df = pd.read_csv(fh, header=None, dtype=str)

        n = df.shape[1]
        out = pd.DataFrame()
        out["ts"]   = df.iloc[:, 0].map(to_ts_any)
        out["side"] = df.iloc[:, 1].astype(str).str.lower().str.strip() if n >= 2 else ""

        # assume: id,side,reason,price,qty,fee,note,(confidence?),...
        out["reason"]  = df.iloc[:, 2] if n >= 3 else ""
        out["source"]  = out["reason"]
        out["price"]   = pd.to_numeric(df.iloc[:, 3], errors="coerce") if n >= 4 else np.nan
        out["qty"]     = pd.to_numeric(df.iloc[:, 4], errors="coerce") if n >= 5 else np.nan
        out["fee_usd"] = pd.to_numeric(df.iloc[:, 5], errors="coerce") if n >= 6 else np.nan
        out["note"]    = df.iloc[:, 6] if n >= 7 else ""
        out["confidence"] = pd.to_numeric(df.iloc[:, 7], errors="coerce") if n >= 8 else np.nan

    out = out.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    print(f"[overlay] trades parsed: {len(out)} | utc range: {out['ts'].min() if len(out) else '—'} -> {out['ts'].max() if len(out) else '—'}")
    return out


# ---------- benchmarks ----------
def build_benchmarks(eq: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    if eq.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    e0, p0 = float(eq["equity"].iloc[0]), float(eq["price"].iloc[0])
    qty = (e0 / p0) if np.isfinite(p0) and p0 > 0 else 0.0
    hold = (qty * eq["price"]).rename("hold_equity")

    dca_btc, last, vals = 0.0, None, []
    for ts, price in zip(eq["ts"], eq["price"]):
        if (last is None) or (ts - last >= pd.Timedelta(days=7)):
            if np.isfinite(price) and price > 0:
                dca_btc += DCA_LOT / float(price)
            last = ts
        vals.append(dca_btc * float(price if np.isfinite(price) else np.nan))
    dca = pd.Series(vals, index=eq.index, name="dca_equity")
    return hold, dca

# ---------- plot ----------
def plot(eq, trades, hold, dca, out_png):
    fig, ax = plt.subplots(figsize=(16, 6))
    ax.plot(eq["ts"], eq["equity"], label="Hybrid (Your Agent)", zorder=3)
    ax.plot(eq["ts"], hold,        label="Buy & Hold",          zorder=2)
    ax.plot(eq["ts"], dca,         label=f"Weekly DCA (${int(DCA_LOT)})", zorder=1)

    if not trades.empty:
        rng = float(eq["equity"].max() - eq["equity"].min() or 1.0)
        def scatter(side, marker, color, off):
            df = trades[trades["side"]==side]
            if df.empty: return
            xs, ys = [], []
            for _, r in df.iterrows():
                xs.append(r["ts"])
                ys.append(equity_at(r["ts"], eq) + off*0.01*rng)
            ax.scatter(xs, ys, marker=marker, s=220, zorder=7,
                       facecolor=color, edgecolor="black", linewidth=0.8,
                       label=side.capitalize())
        scatter("buy",  "^", "#1f77b4", +1)
        scatter("sell", "v", "#d62728", -1)

    ax.set_title("Equity Curve — Hybrid vs Hold vs DCA (with trade markers)")
    ax.set_xlabel("Date"); ax.set_ylabel("Equity (USD)")
    h,l = ax.get_legend_handles_labels(); ax.legend(dict(zip(l,h)).values(), dict(zip(l,h)).keys())
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out_png, dpi=150); plt.close(fig)
    print(f"[overlay] wrote PNG: {out_png}")

# ---------- html ----------
def write_html(out_png: str, eq: pd.DataFrame, trades_window: pd.DataFrame, all_trades: pd.DataFrame):
    out_html = os.path.join(STATE_DIR, "baseline_summary_with_trades.html")
    latest = eq.iloc[-1]

    # ---- Trades inside the overlay window (use trades_window) ----
    rows = ""
    if not trades_window.empty:
        tshow = trades_window.sort_values("ts", ascending=False).head(25)
        for _, r in tshow.iterrows():
            rows += (
                f"<tr><td>{pd.to_datetime(r['ts']).strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{r.get('side','')}</td><td>{_src(r)}</td>"
                f"<td>{_fmt_money(r.get('price'))}</td>"
                f"<td>{_fmt_btc(r.get('qty_btc', r.get('qty')))}</td>"
                f"<td>{_fmt_fee(r.get('fee_usd'))}</td>"
                f"<td>{_note(r)}</td>"
                f"<td>{_fmt_money(r.get('cash_after'))}</td>"
                f"<td>{_fmt_btc(r.get('btc_after'))}</td>"
                f"<td>{_fmt_money(r.get('equity_after'))}</td>"
                f"<td>{_fmt_money(r.get('notional'))}</td></tr>"
            )

    header_win_ext = (
        "<tr><th>Time (UTC)</th><th>Side</th><th>Source</th>"
        "<th>Price</th><th>Qty (BTC)</th><th>Fee</th><th>Note</th>"
        "<th>Cash &rarr;</th><th>BTC &rarr;</th><th>Equity &rarr;</th><th>Notional</th></tr>"
    )
    table = ("<table border='1' cellspacing='0' cellpadding='4'>"
             f"{header_win_ext}{rows}</table>") if rows else "<p>No trades in overlay window.</p>"

    # ---- Recent 48h across ALL trades (use all_trades; do NOT reparse) ----
    recent_html = "<p>No trades in last 48 hours.</p>"
    try:
        if all_trades is not None and not all_trades.empty:
            rec_df = all_trades.copy()
            rec_df["ts"] = pd.to_datetime(rec_df["ts"], utc=True, errors="coerce")
            rec_df = rec_df.dropna(subset=["ts"])

            latest_ts = rec_df["ts"].max()
            cutoff = latest_ts - pd.Timedelta(hours=48)

            rec = rec_df[rec_df["ts"] >= cutoff].sort_values("ts", ascending=False).head(25)
            if not rec.empty:
                rows2 = []
                for _, r in rec.iterrows():
                    rows2.append(
                        f"<tr><td>{pd.to_datetime(r['ts']).strftime('%Y-%m-%d %H:%M')}</td>"
                        f"<td>{r.get('side','')}</td><td>{str(r.get('source','')).upper()}</td>"
                        f"<td>{_fmt_money(r.get('price'))}</td>"
                        f"<td>{_fmt_btc(r.get('qty_btc', r.get('qty')))}</td>"
                        f"<td>{_fmt_fee(r.get('fee_usd'))}</td>"
                        f"<td>{_note(r)[:60]}</td></tr>"
                    )
                header_recent = (
                    "<tr><th>Time (UTC)</th><th>Side</th><th>Source</th>"
                    "<th>Price</th><th>Qty (BTC)</th><th>Fee</th><th>Note</th></tr>"
                )
                recent_html = ("<table border='1' cellspacing='0' cellpadding='4'>"
                               f"{header_recent}{''.join(rows2)}</table>")
    except Exception as e:
        recent_html = f"<p>Recent-trades section error: {e}</p>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Baseline Overlay</title>
<meta http-equiv="refresh" content="120">
</head>
<body>
<h2>Baseline Overlay</h2>
<p><b>Latest equity:</b> ${float(latest['equity']):,.2f}
&nbsp; <b>Price:</b> ${float(latest['price']):,.2f}
&nbsp; <b>BTC:</b> {float(latest['btc']):.8f}</p>
<p><img src="{os.path.basename(out_png)}" style="max-width:100%;height:auto;border:1px solid #ddd"/></p>

<h3>Trades in window</h3>
{table}

<h3>Recent trades (last 48 hours)</h3>
{recent_html}
</body></html>"""
    Path(out_html).write_text(html, encoding="utf-8")
    print(f"[overlay] wrote HTML: {out_html}")

header_week_ext = (
    "<tr><th>Time (UTC)</th><th>Side</th><th>Source</th>"
    "<th>Price</th><th>Qty (BTC)</th><th>Fee</th><th>Note</th>"
    "<th>Cash &rarr;</th><th>BTC &rarr;</th><th>Equity &rarr;</th><th>Notional</th></tr>"
)

# ------- weekly report ----------
def write_weekly_report(eq: pd.DataFrame, trades: pd.DataFrame) -> None:
    """
    Weekly report with start/end balances (cash, BTC, equity) + last-7d trades.
    Writes: state/weekly_report_preview.html
    """
    out_html = os.path.join(STATE_DIR, "weekly_report_preview.html")

    if eq.empty:
        Path(out_html).write_text("<h3>No equity data for weekly report.</h3>", encoding="utf-8")
        print(f"[overlay] wrote weekly report: {out_html}")
        return

    # 7-day window (UTC) based on the last equity timestamp
    end_ts = pd.to_datetime(eq["ts"].max(), errors="coerce")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    else:
        end_ts = end_ts.tz_convert("UTC")

    # Include the full last 7 calendar days, inclusive of both endpoints
    s_utc = (end_ts - pd.Timedelta(days=7)).floor("D")
    e_utc = end_ts.ceil("D")  # push to end-of-day so late trades are included

    # balance snapshots from equity rows in that window (fallback to last)
    eqw = eq.copy()
    eqw["ts"] = pd.to_datetime(eqw["ts"], utc=True, errors="coerce")
    eqw = eqw.dropna(subset=["ts"])
    eqw_win = eqw[(eqw["ts"] >= s_utc) & (eqw["ts"] <= e_utc)].copy()
    if eqw_win.empty:
        eqw_win = eqw.tail(1).copy()
    snap_start, snap_end = eqw_win.iloc[0], eqw_win.iloc[-1]

    def vals(row):
        cash = float(row.get("cash_usd", 0) or 0)
        btc  = float(row.get("btc", 0) or 0)
        px   = float(row.get("price", 0) or 0)
        return cash, btc, px

    cash0, btc0, px0 = vals(snap_start)
    cash1, btc1, px1 = vals(snap_end)
    eq0 = cash0 + btc0 * px0
    eq1 = cash1 + btc1 * px1
    chg_usd = eq1 - eq0
    chg_pct = (chg_usd / eq0 * 100.0) if eq0 else 0.0

    # ---- last 7d trades (USE ENRICHED 'trades' PASSED IN) ----
    trw = pd.DataFrame()
    if trades is not None and not trades.empty:
        t2 = trades.copy()
        t2["_ts_utc"] = pd.to_datetime(t2["ts"], utc=True, errors="coerce")
        t2 = t2.dropna(subset=["_ts_utc"])
        trw = t2[(t2["_ts_utc"] >= s_utc) & (t2["_ts_utc"] <= e_utc)].sort_values("_ts_utc", ascending=False)

    # Debug print -> will show you if the trades are in range or not
    print(f"[overlay] weekly window: {s_utc} → {e_utc} | trades in week: {len(trw)}")

    if not trw.empty:
        rows = []
        for _, r in trw.head(40).iterrows():  # cap rows for readability
            rows.append(
                f"<tr><td>{pd.to_datetime(r['_ts_utc']).strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{r.get('side','')}</td><td>{_src(r)}</td>"
                f"<td>{_fmt_money(r.get('price'))}</td>"
                f"<td>{_fmt_btc(r.get('qty_btc', r.get('qty')))}</td>"
                f"<td>{_fmt_fee(r.get('fee_usd'))}</td>"
                f"<td>{_note(r)[:100]}</td>"
                f"<td>{_fmt_money(r.get('cash_after'))}</td>"
                f"<td>{_fmt_btc(r.get('btc_after'))}</td>"
                f"<td>{_fmt_money(r.get('equity_after'))}</td>"
                f"<td>{_fmt_money(r.get('notional'))}</td></tr>"
            )
        trades_html = (
            "<table border='1' cellspacing='0' cellpadding='4'>"
            "<tr><th>Time (UTC)</th><th>Side</th><th>Source</th>"
            "<th>Price</th><th>Qty (BTC)</th><th>Fee</th><th>Note</th>"
            "<th>Cash &rarr;</th><th>BTC &rarr;</th><th>Equity &rarr;</th><th>Notional</th></tr>"
            + "".join(rows) + "</table>"
        )
    else:
        trades_html = "<p>No trades in the last 7 days.</p>"

    bal_html = f"""
    <table border='1' cellspacing='0' cellpadding='6'>
      <tr><th></th><th>Cash (USD)</th><th>BTC</th><th>BTC @ Price</th><th>Equity</th></tr>
      <tr>
        <td>Start {s_utc.date()}</td>
        <td>${cash0:,.2f}</td><td>{btc0:.8f}</td><td>${px0:,.2f}</td><td><b>${eq0:,.2f}</b></td>
      </tr>
      <tr>
        <td>End {e_utc.date()}</td>
        <td>${cash1:,.2f}</td><td>{btc1:.8f}</td><td>${px1:,.2f}</td><td><b>${eq1:,.2f}</b></td>
      </tr>
    </table>
    <p><b>Change:</b> ${chg_usd:,.2f} ({chg_pct:,.2f}%)</p>
    """

    img_name = "baseline_overlay_latest.png"
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Weekly Report</title></head>
<body>
<h2>Weekly Report — {s_utc.date()} → {e_utc.date()}</h2>
{bal_html}
<h3>Trades (last 7 days)</h3>
{trades_html}
<hr/>
<p><b>Overlay:</b></p>
<p><img src="{img_name}" style="max-width:100%;height:auto;border:1px solid #ddd"/></p>
</body></html>"""
    Path(out_html).write_text(html, encoding="utf-8")
    print(f"[overlay] wrote weekly report: {out_html}  |  trades in week: {len(trw)}  |  window: {s_utc} → {e_utc}")

# ---------- main ----------
def main():
    eq_full = load_equity()
    if eq_full.empty:
        raise SystemExit("[overlay] no equity rows")

    end = eq_full["ts"].max()
    # If your env var is OVERLAY_DAYS, set OVERLAY = os.getenv("OVERLAY_DAYS","all") above.
    if OVERLAY == "all":
        eq = eq_full.copy()
    else:
        try:
            days = int(OVERLAY)
        except Exception:
            days = 30
        start = end - pd.Timedelta(days=days)
        eq = eq_full[eq_full["ts"] >= start].copy()

    # 1) parse trades robustly (handles ISO, epoch s/ms, scientific)
    trades_all = parse_all_trades_utc()
    # compute running balances for richer tables
    trades_all = enrich_trades_with_balances(trades_all, eq_full)

    # 2) window for plotting = equity window ±7 days
    buf   = pd.Timedelta(days=7)
    s_win = pd.Timestamp(eq["ts"].min()).tz_convert("UTC") - buf
    e_win = pd.Timestamp(eq["ts"].max()).tz_convert("UTC") + buf
    trades_win = trades_all[(trades_all["ts"] >= s_win) & (trades_all["ts"] <= e_win)].copy()
    print(f"[overlay] trades in window: {len(trades_win)}  | window: {s_win} -> {e_win}")

    # 3) benchmarks and outputs
    hold, dca = build_benchmarks(eq)
    out_png = os.path.join(STATE_DIR, "baseline_overlay_latest.png")
    plot(eq, trades_win, hold, dca, out_png)
    write_html(out_png, eq, trades_win, trades_all)
    write_weekly_report(eq_full, trades_all)

if __name__ == "__main__":
    main()
