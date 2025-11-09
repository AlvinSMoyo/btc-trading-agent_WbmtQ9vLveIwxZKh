import os
#!/usr/bin/env python3
# scripts/make_weekly_report_v2.py
import argparse, base64, io
from pathlib import Path
from datetime import timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def read_trades(path: Path) -> pd.DataFrame:
    import re
    df = pd.read_csv(path)

    # --- 1) Timestamp normalization ---
    time_cols = ["ts_dt","Time (UTC)","ts","timestamp","Timestamp"]
    tcol = next((c for c in time_cols if c in df.columns), None)
    if not tcol:
        raise SystemExit("No timestamp column found (expected one of: ts_dt / Time (UTC) / ts / timestamp).")

    if tcol == "ts":
        t = pd.to_datetime(df["ts"], unit="s", utc=True, errors="coerce")
    else:
        t = pd.to_datetime(df[tcol], utc=True, errors="coerce")

    df["ts_utc"] = t
    df = df.dropna(subset=["ts_utc"]).sort_values("ts_utc").reset_index(drop=True)

    # --- 2) Side / note fields ---
    side_col = next((c for c in ["side","Side","SIDE"] if c in df.columns), None)
    if side_col:
        df["side_norm"] = df[side_col].astype(str).str.upper().str.strip()
    else:
        df["side_norm"] = ""

    # Prefer rich 'Note', fall back to note/reason variants
    note_col = next((c for c in [
        "Note","note","reason","Reason","reason_short","Reason Short","ReasonShort"
    ] if c in df.columns), None)
    df["note_final"] = df[note_col] if note_col else ""


    # --- 3) Numeric helpers (best-effort) ---
    rename_map = {}
    for cand in ["price","Price","fill_price","Fill Price"]:
        if cand in df.columns: rename_map[cand] = "price"; break
    for cand in ["qty_btc","Qty BTC","qty","quantity","Quantity","size_btc","Size BTC"]:
        if cand in df.columns: rename_map[cand] = "qty_btc"; break
    for cand in ["fee_usd","Fee USD","fee","Fee","fee_usd_after","Commission USD"]:
        if cand in df.columns: rename_map[cand] = "fee_usd"; break
    df = df.rename(columns=rename_map)
    for c in ["price","qty_btc","fee_usd"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")

    # --- 4) Equity derivation ---
    # Always create the column to avoid KeyError later
    df["equity_after"] = np.nan

    # (a) Direct equity columns
    eq_col = next((c for c in [
        "equity_after","Equity After","equity","Equity","equity_usd_after","Equity (USD) After"
    ] if c in df.columns), None)
    if eq_col:
        df["equity_after"] = pd.to_numeric(df[eq_col], errors="coerce")

    # (b) Reconstruct from cash_after + btc_after (+ price)
    cash_col = next((c for c in [
        "cash_after","Cash After","cash","Cash","cash_usd_after","Cash (USD) After"
    ] if c in df.columns), None)
    btc_col = next((c for c in [
        "btc_after","BTC After","btc","BTC","btc_qty_after","BTC Qty After"
    ] if c in df.columns), None)

    if cash_col and btc_col:
        cashf = pd.to_numeric(df[cash_col], errors="coerce")
        btcf  = pd.to_numeric(df[btc_col],  errors="coerce")
        price = df["price"].ffill() if "price" in df.columns else np.nan
        # Only compute where we have at least cash and (btc or price)
        with np.errstate(invalid="ignore"):
            df["equity_after"] = np.where(
                cashf.notna() & btcf.notna() & pd.notna(price),
                cashf + btcf * price,
                df["equity_after"]
            )
        # If price is entirely missing but equity existed earlier, keep that; otherwise NaN remains.

    # Final sanity: if still all NaN, try reconstructing from fills
    if df["equity_after"].notna().sum() == 0:
        df = reconstruct_equity_from_fills(df, path)

    # After reconstruction, insist we have something
    if df["equity_after"].notna().sum() == 0:
        have = ", ".join(df.columns)
        raise SystemExit(
            "Cannot determine equity_after even after reconstruction. "
            "Need one of: [equity_after] or reconstructable columns (Side/price/qty_btc[/fee]). "
            f"Columns present: {have}"
        )

    # Forward-fill occasional gaps
    df["equity_after"] = df["equity_after"].ffill()
    return df


def reconstruct_equity_from_fills(df: pd.DataFrame, trades_path: Path) -> pd.DataFrame:
    """
    Rebuild cash/btc/equity by simulating trades when equity_after & balances are missing.
    Requires columns: ts_utc, side_norm (BUY/SELL), price, qty_btc, fee_usd (fee optional).
    Seed balances are loaded from state/state.json if present, else env, else defaults.
    """
    import json, os

    state_dir = trades_path.parent  # e.g., .../state
    seed_cash = None
    seed_btc  = None

    # Try state/state.json
    for fname in ["state.json", "balances.json"]:
        p = state_dir / fname
        if p.exists():
            try:
                s = json.loads(p.read_text())
                seed_cash = float(s.get("cash_usd", seed_cash if seed_cash is not None else np.nan))
                seed_btc  = float(s.get("btc", seed_btc if seed_btc is not None else np.nan))
            except Exception:
                pass

    # Env overrides
    seed_cash = float(os.getenv("START_CASH", seed_cash if seed_cash is not None and not np.isnan(seed_cash) else 10000.0))
    seed_btc  = float(os.getenv("START_BTC",  seed_btc  if seed_btc  is not None and not np.isnan(seed_btc)  else 0.0))

    df = df.sort_values("ts_utc").copy()
    df["fee_usd"] = pd.to_numeric(df.get("fee_usd", 0.0), errors="coerce").fillna(0.0)
    df["price"]   = pd.to_numeric(df.get("price", np.nan), errors="coerce")
    df["qty_btc"] = pd.to_numeric(df.get("qty_btc", np.nan), errors="coerce")

    if df["price"].isna().any() or df["qty_btc"].isna().any():
        raise SystemExit("Cannot reconstruct equity: price or qty_btc has NaNs. Please fix/ffill those first.")

    cash = seed_cash
    btc  = seed_btc
    cash_list, btc_list, eq_list = [], [], []

    for _, row in df.iterrows():
        side = str(row.get("side_norm","")).upper()
        px   = float(row["price"])
        qty  = float(row["qty_btc"])
        fee  = float(row["fee_usd"])

        if side == "BUY":
            # spend cash, add BTC
            cash -= (px * qty + fee)
            btc  += qty
        elif side == "SELL":
            # receive cash, reduce BTC
            cash += (px * qty - fee)
            btc  -= qty
        else:
            # unknown side: keep balances unchanged
            pass

        equity = cash + btc * px
        cash_list.append(cash)
        btc_list.append(btc)
        eq_list.append(equity)

    df["cash_after_sim"]   = cash_list
    df["btc_after_sim"]    = btc_list
    df["equity_after_sim"] = eq_list

    # If equity_after exists but empty, fill from sim
    if "equity_after" not in df.columns or df["equity_after"].isna().all():
        df["equity_after"] = df["equity_after_sim"]
    else:
        df["equity_after"] = df["equity_after"].fillna(df["equity_after_sim"])

    return df

def monday_close_series(df_equity: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """
    Build Monday-close equity points between [start, end].
    For each Monday 00:00→next Monday 00:00 window, take the last equity sample < Monday 00:00 of that week close.
    """
    # We will take the last known equity strictly before each Monday 00:00 (UTC)
    # Build Mondays
    start = pd.to_datetime(start, utc=True)
    end   = pd.to_datetime(end, utc=True)
    if start.weekday() != 0:
        # shift to the next Monday to start weeks cleanly
        start = (start + pd.offsets.Week(weekday=0))
    mondays = pd.date_range(start=start.normalize(), end=end.normalize()+pd.Timedelta(days=7), freq="W-MON", tz="UTC")
    points = []
    for m in mondays:
        # Monday close = value at that Monday 00:00 (we take the last equity strictly before this moment)
        mask = df_equity["ts_utc"] < m
        if not mask.any():
            # use first known
            eq = df_equity.iloc[0]["equity_after"]
        else:
            eq = df_equity.loc[mask].iloc[-1]["equity_after"]
        points.append({"monday_utc": m, "equity": float(eq)})
    out = pd.DataFrame(points)
    # de-dup if any
    out = out.drop_duplicates(subset=["monday_utc"]).reset_index(drop=True)
    return out

def daily_close_series(df_equity: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    start = pd.to_datetime(start, utc=True)
    end   = pd.to_datetime(end, utc=True)
    s = df_equity.set_index("ts_utc")["equity_after"].sort_index()
    d = s.resample("D").last().dropna()  # end-of-day close
    d = d[(d.index>=start.floor("D")) & (d.index<=end.ceil("D"))]
    return pd.DataFrame({"day_utc": d.index.tz_convert("UTC"), "equity": d.values})

def render_plot_to_base64(x_ts, y_eq, trades_scatter, title="Equity"):
    fig = plt.figure(figsize=(8,4.2), dpi=140)
    plt.plot(x_ts, y_eq, linewidth=1.8)
    # Scatter buys / sells
    if not trades_scatter.empty:
        buys  = trades_scatter[trades_scatter["side_norm"]=="BUY"]
        sells = trades_scatter[trades_scatter["side_norm"]=="SELL"]
        if len(buys):
            plt.scatter(buys["ts_utc"], buys["equity_after"], marker="^", s=26, alpha=0.85, label="Buys")
        if len(sells):
            plt.scatter(sells["ts_utc"], sells["equity_after"], marker="v", s=26, alpha=0.85, label="Sells")
        if len(buys) or len(sells):
            plt.legend(loc="best", fontsize=8)
    
    # Ensure scatter points after the last series point are visible (e.g., 4 Nov)
    try:
        x_min = min(pd.to_datetime(x_ts).min(), pd.to_datetime(trades_scatter["ts_utc"]).min())
        x_max = max(pd.to_datetime(x_ts).max(), pd.to_datetime(trades_scatter["ts_utc"]).max())
        plt.xlim(x_min - pd.Timedelta(days=1), x_max + pd.Timedelta(days=1))
    except Exception:
        pass

    plt.title(title)
    plt.xlabel("Week")
    plt.ylabel("Equity (USD)")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

def equity_benchmarks_daily(dfw, since, until, start_cash=10000.0, dca_daily=500.0):
    """
    Build daily series for your hybrid equity (from fills), Buy&Hold, and DCA($500).
    Returns a DataFrame indexed by daily UTC with columns: hybrid, hold, dca.
    """
    import pandas as pd, numpy as np

    s = dfw.set_index("ts_utc").sort_index()

    # Daily price
    price = pd.to_numeric(s["price"], errors="coerce").resample("D").last().dropna()

    # normalize since/until to UTC-aware (works for str, datetime, Timestamp)
    s_utc = pd.Timestamp(since)
    s_utc = s_utc.tz_localize("UTC") if s_utc.tzinfo is None else s_utc.tz_convert("UTC")
    u_utc = pd.Timestamp(until)
    u_utc = u_utc.tz_localize("UTC") if u_utc.tzinfo is None else u_utc.tz_convert("UTC")

    price = price[(price.index >= s_utc) & (price.index <= u_utc)]
    if price.empty:
        return pd.DataFrame(index=pd.date_range(s_utc, u_utc, tz="UTC", freq="D"))

    # Hybrid = your equity_after resampled to daily last, ffilled to align with price
    if "equity_after" in s.columns:
        hybrid = (pd.to_numeric(s["equity_after"], errors="coerce")
                    .resample("D").last().ffill().reindex(price.index, method="ffill"))
    else:
        hybrid = pd.Series(index=price.index, dtype=float)

    # Buy & Hold: buy all on first day
    first_p = float(price.iloc[0])
    hold_btc = start_cash / first_p
    hold = hold_btc * price  # cash=0

    # DCA: invest fixed USD each day
    dca_vals, dca_btc = [], 0.0
    for p in price:
        dca_btc += dca_daily / p
        dca_vals.append(dca_btc * p)
    dca = pd.Series(dca_vals, index=price.index, name="dca")

    return pd.DataFrame({"hybrid": hybrid, "hold": hold, "dca": dca})


def plot_bench_to_base64(bench_df):
    """
    Render the Hybrid vs Hold vs DCA chart and return a data: URI (base64 PNG).
    """
    import io, base64, matplotlib.pyplot as plt
    fig = plt.figure(figsize=(10, 3.6), dpi=140)
    plt.plot(bench_df.index, bench_df["hybrid"], label="Hybrid (Your Agent)", linewidth=1.8)
    plt.plot(bench_df.index, bench_df["hold"],   label="Buy & Hold",         linewidth=1.8)
    plt.plot(bench_df.index, bench_df["dca"],    label="Daily DCA ($500)",   linewidth=1.8)
    plt.title("Equity Curve — Hybrid vs Hold vs DCA")
    plt.xlabel("Date"); plt.ylabel("Equity (USD)")
    plt.legend(loc="lower right", fontsize=9)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

def html_escape(s):
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))


css = """
<style>
  :root{--fg:#111;--muted:#555;--bd:#e5e7eb;--bg:#fff}
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,'Helvetica Neue',Arial,sans-serif;margin:20px;color:var(--fg);background:var(--bg)}
  h1{margin:0 0 6px 0}
  .muted{color:var(--muted)}
  .kpi{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:10px;margin:14px 0 20px}
  .card{border:1px solid var(--bd);border-radius:12px;padding:10px 12px}
  .label{font-size:12px;color:#666}
  .value{font-weight:600;font-size:14px}
  img{max-width:100%;height:auto;border:1px solid #eee;border-radius:10px}
  .table{border-collapse:collapse;width:100%;font-size:12.5px}
  .table th,.table td{border-bottom:1px solid #eee;padding:6px 8px;text-align:left}
  details{margin-top:10px}
  .sec{margin-top:18px}
  .grid2{display:grid;grid-template-columns:1fr;gap:14px}
  @media (min-width:900px){.grid2{grid-template-columns:2fr 1fr}}
  code{background:#f6f8fa;padding:2px 6px;border-radius:6px}
</style>
"""

def build_html(kpis, img_data_uri, series, recent, recent_n=20, report_mode="weekly"):
    """
    Renders the v2 report HTML.
      - First: KPI cards
      - Second: Benchmarks chart (Hybrid vs Hold vs DCA) if provided via kpis["bench_img_uri"]
      - Third: Grid with (A) main equity chart + series table, (B) recent fills
    Expects globals: css, html_escape
    """
    import pandas as pd

    # small helper for safe KPI lookup + escaping
    def k(key, default="—"):
        return html_escape(kpis.get(key, default))

    # ---- Build Series table (Time/Equity/Cash/BTC if present) ----
    tbl = series.copy()
    tbl["Time (UTC)"] = pd.to_datetime(tbl["Time (UTC)"], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    col_order = [c for c in ["Time (UTC)", "Equity", "Cash", "BTC"] if c in tbl.columns]
    if col_order:
        tbl = tbl[col_order]
    w_html = tbl.to_html(index=False, border=0, classes="table", justify="left",
                         float_format=lambda x: f"{x:,.2f}")

    # ---- Build Recent fills table (with balances + notes if present) ----
    rf = recent.copy()  # <- use the function argument, not 'recent_fills'

    # normalize timestamp
    if "ts_utc" in rf.columns:
        rf["ts_utc"] = pd.to_datetime(rf["ts_utc"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    # normalize side + note
    if "side_norm" in rf.columns:
        rf = rf.rename(columns={"side_norm": "side"})
    if "note_final" in rf.columns:
        rf = rf.rename(columns={"note_final": "note"})

    # prefer real balances; fall back to simulated if present, but LABEL as Cash/BTC
    cash_col = "cash_after" if "cash_after" in rf.columns else ("cash_after_sim" if "cash_after_sim" in rf.columns else None)
    btc_col  = "btc_after"  if "btc_after"  in rf.columns else ("btc_after_sim"  if "btc_after_sim"  in rf.columns else None)

    # Build the display set and rename to pretty headers
    want = ["ts_utc", "side", "price", "qty_btc", "fee_usd", "note"]
    if cash_col: want.append(cash_col)
    if btc_col:  want.append(btc_col)
    if "equity_after" in rf.columns: want.append("equity_after")

    rf = rf[[c for c in want if c in rf.columns]].copy()

    rename_map = {}
    if cash_col: rename_map[cash_col] = "cash"
    if btc_col:  rename_map[btc_col]  = "btc"
    if "equity_after" in rf.columns: rename_map["equity_after"] = "equity_after"  # keep name

    rf = rf.rename(columns=rename_map)

    r_html = rf.to_html(index=False, border=0, classes="table", justify="left",
                        float_format=lambda x: f"{x:,.2f}")


    # ---- Titles & optional second chart ----
    title_mode  = "Weekly" if str(report_mode).lower() == "weekly" else "Daily"
    chart_title = html_escape(kpis.get("chart_title", "Equity"))
    bench_img   = kpis.get("bench_img_uri", "")
    bench_html = (
        f'''
  <div class="sec">
    <h3>Equity Curve — Hybrid vs Hold vs DCA</h3>
    <img src="{html_escape(bench_img)}" alt="Hybrid vs Hold vs DCA" />
  </div>'''
        if bench_img else ""
    )

    # ---- HTML document ----
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>BTC Agent — {title_mode} Report (v2)</title>{css}</head>
<body>
  <h1>BTC Agent — {title_mode} Report <span class="muted">(v2)</span></h1>
  <div class="kpi">
    <div class="card"><div class="label">As of (UTC)</div><div class="value">{k('as_of')}</div></div>
    <div class="card"><div class="label">Trades (7d)</div><div class="value">{k('trades_7d')}</div></div>
    <div class="card"><div class="label">Buys / Sells (7d)</div><div class="value">{k('buys_7d')} / {k('sells_7d')}</div></div>
    <div class="card"><div class="label">Buy Notional (7d)</div><div class="value">${k('buy_notional_7d')}</div></div>
    <div class="card"><div class="label">Sell Notional (7d)</div><div class="value">${k('sell_notional_7d')}</div></div>
    <div class="card"><div class="label">Last Equity</div><div class="value">${k('last_equity')}</div></div>
  </div>

  {bench_html}

  <div class="grid2">
    <div class="sec">
      <h3>Recent Fills (last {recent_n})</h3>
      <img src="{img_data_uri}" alt="Equity with trade markers"/>
      <details><summary>Series table ({chart_title} snapshots)</summary>
        {w_html}
      </details>
    </div>
    <div class="sec">
      <h3>Recent Fills (last {recent_n})</h3>
      {r_html}
    </div>
  </div>

  <details class="sec">
    <summary>Notes</summary>
    <ul>
      <li>Weekly points are sampled at Monday 00:00 UTC. Daily points are end-of-day UTC.</li>
      <li><code>note</code> column is preferred when present; otherwise we fall back to reason/short reason.</li>
    </ul>
  </details>
</body></html>"""
    return html

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--since", type=str, default=None)
    ap.add_argument("--until", type=str, default=None)
    ap.add_argument("--mode", choices=["weekly","daily"], default="weekly")
    args = ap.parse_args()

    df = read_trades(args.trades)

    # date window
    since = pd.to_datetime(args.since, utc=True) if args.since else df["ts_utc"].min()
    until = pd.to_datetime(args.until, utc=True) if args.until else df["ts_utc"].max()

    dfw = df[(df["ts_utc"]>=since) & (df["ts_utc"]<=until)].copy()
    if dfw.empty:
        raise SystemExit("No trades in the requested window.")

    # KPIs (7d = last 7*24h from 'until')
    window_7d_start = until - pd.Timedelta(days=7)
    d7 = df[(df["ts_utc"]>window_7d_start) & (df["ts_utc"]<=until)]
    d7 = d7.copy()
    d7.loc[:, "notional"] = (d7["qty_btc"].abs() * d7["price"]).fillna(0.0)
    trades_7d = len(d7)
    buys_7d  = int((d7["side_norm"]=="BUY").sum())
    sells_7d = int((d7["side_norm"]=="SELL").sum())

    # Notional sums (best effort)
    # if qty & price exist:
    if "qty_btc" in d7.columns and "price" in d7.columns:
        d7["notional"] = (d7["qty_btc"].abs() * d7["price"]).fillna(0.0)
        buy_notional_7d  = float(d7.loc[d7["side_norm"]=="BUY","notional"].sum())
        sell_notional_7d = float(d7.loc[d7["side_norm"]=="SELL","notional"].sum())
    else:
        buy_notional_7d = sell_notional_7d = 0.0

    last_equity = float(dfw.iloc[-1]["equity_after"])
    kpis = dict(
        as_of=until.strftime("%Y-%m-%d %H:%M:%S UTC"),
        trades_7d=trades_7d,
        buys_7d=buys_7d,
        sells_7d=sells_7d,
        buy_notional_7d=buy_notional_7d,
        sell_notional_7d=sell_notional_7d,
        last_equity=last_equity,
    )

    equity_series = dfw[["ts_utc","equity_after"]].copy()
    scatter_trades = dfw[["ts_utc","equity_after","side_norm"]].copy()

    if args.mode == "weekly":
        series = monday_close_series(equity_series, since, until)
        series = series.rename(columns={"monday_utc":"Time (UTC)", "equity":"Equity"})
        chart_title = "Weekly Equity (UTC, Monday close)"
    else:
        series = daily_close_series(equity_series, since, until)
        series = series.rename(columns={"day_utc":"Time (UTC)", "equity":"Equity"})
        chart_title = "Daily Equity (UTC, end-of-day)"

    # --- Auto windowing: default to "live now" ---
    now_utc = pd.Timestamp.now(tz="UTC").floor("min")

    # until -> now if not provided
    until = pd.to_datetime(args.until, utc=True) if args.until else now_utc

    # since -> reasonable trailing window if not provided
    if args.since:
        since = pd.to_datetime(args.since, utc=True)
    else:
        # Weekly view: last 8 weeks; Daily view: last 30 days
        window_days = 56 if args.mode == "weekly" else 30
        since = (until - pd.Timedelta(days=window_days)).normalize()

    # ---- Enrich series with balances if we have them ---- 
    # Prefer real balances; else use simulated ones from reconstruct_equity_from_fills()
    cash_src = "cash_after" if "cash_after" in dfw.columns else ("cash_after_sim" if "cash_after_sim" in dfw.columns else None)
    btc_src  = "btc_after"  if "btc_after"  in dfw.columns else ("btc_after_sim"  if "btc_after_sim"  in dfw.columns else None)

    if cash_src or btc_src:
        idx_target = pd.DatetimeIndex(pd.to_datetime(series["Time (UTC)"], utc=True))
        df_idx = dfw.set_index("ts_utc").sort_index()
        # Ensure unique source index for reindex() – keep the last record per timestamp
        df_idx = df_idx[~df_idx.index.duplicated(keep="last")]

        if cash_src:
            s_cash = pd.to_numeric(df_idx[cash_src], errors="coerce")
            series["Cash"] = s_cash.reindex(idx_target, method="ffill").values

        if btc_src:
            s_btc = pd.to_numeric(df_idx[btc_src], errors="coerce")
            series["BTC"] = s_btc.reindex(idx_target, method="ffill").values

    # Main equity chart image
    img_uri = render_plot_to_base64(series["Time (UTC)"], series["Equity"], scatter_trades, title=chart_title)

    # --- NEW: Benchmarks chart (Hybrid vs Hold vs DCA) ---
    bench = equity_benchmarks_daily(
        dfw, since, until,
        start_cash=float(os.getenv("START_CASH", 10000.0)),
        dca_daily=500.0
    )
    bench_img_uri = plot_bench_to_base64(bench)
    kpis["bench_img_uri"] = bench_img_uri

    # Recent fills
    RECENT_N = int(os.getenv("RECENT_N", 60))
    recent    = dfw.sort_values("ts_utc").tail(RECENT_N)
    html      = build_html(kpis, img_uri, series, recent, recent_n=RECENT_N, report_mode=args.mode)



    # HTML
    kpis["chart_title"] = chart_title

    RECENT_N = int(os.getenv("RECENT_N", 60))  # <- bump table depth so 4-Nov buys show
    html = build_html(
        kpis,
        img_uri,
        series,
        recent,
        recent_n=RECENT_N,
        report_mode=args.mode,   # "weekly" or "daily"
    )
    args.out.write_text(html, encoding="utf-8")
    print(f"✅ Wrote {args.out}")


if __name__ == "__main__":
    main()
