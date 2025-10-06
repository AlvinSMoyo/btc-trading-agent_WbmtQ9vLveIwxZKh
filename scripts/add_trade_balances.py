import os, sys
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_datetime64_any_dtype

STATE_DIR = os.path.join(".", "state")
TRADES_CSV = os.path.join(STATE_DIR, "trades.csv")
EQUITY_CSV = os.path.join(STATE_DIR, "equity_history.csv")
OUT_CSV    = os.path.join(STATE_DIR, "trades_with_balances.csv")
OUT_HTML   = os.path.join(STATE_DIR, "weekly_report_with_balances.html")

TS_TRADES = ["ts_dt","ts_utc","timestamp","date","datetime","ts","time","created_at"]
TS_EQUITY = ["ts_utc","ts_dt","date","timestamp","datetime","ts"]

def pick_first(df, names):
    for n in names:
        if n in df.columns: return n
    return None

def to_utc_naive_from_strings(s):
    s = pd.to_datetime(s, utc=True, errors="coerce")
    return s.dt.tz_convert("UTC").dt.tz_localize(None)

def parse_epoch_numeric(vals: pd.Series) -> pd.Series:
    v = pd.to_numeric(vals, errors="coerce")
    vmax = v.max()

    if vmax > 1e17:   unit, _v = "ns", v
    elif vmax > 1e14: unit, _v = "us", v
    elif vmax > 1e11: unit, _v = "ms", v
    elif vmax > 1e8:  unit, _v = "s",  v
    else:
        if 1 < vmax < 10:   # seconds divided by 1e9 (odd case)
            unit, _v = "s", (v * 1_000_000_000.0)
        else:
            return to_utc_naive_from_strings(vals.astype(str))

    dt = pd.to_datetime(_v, unit=unit, utc=True, errors="coerce")
    return dt.dt.tz_convert("UTC").dt.tz_localize(None)

def parse_maybe_epoch(series: pd.Series) -> pd.Series:
    if is_numeric_dtype(series):
        return parse_epoch_numeric(series)
    else:
        return to_utc_naive_from_strings(series)

def load_trades(path):
    if not os.path.exists(path):
        sys.exit(f"❌ Missing {path}")
    t = pd.read_csv(path)
    t.columns = [c.strip().lower() for c in t.columns]

    raw_ts = pick_first(t, TS_TRADES)
    if raw_ts is None:
        t["ts_dt"] = parse_maybe_epoch(pd.Series(np.arange(len(t)), dtype="int64"))
    else:
        t["ts_dt"] = parse_maybe_epoch(t[raw_ts])

    t = t.dropna(subset=["ts_dt"]).sort_values("ts_dt").reset_index(drop=True)

    price_col = pick_first(t, ["price","fill_price","avg_price","executed_price"]) or "price"
    qty_col   = pick_first(t, ["qty_btc","size_btc","amount_btc","qty","size","amount"]) or "qty_btc"
    fee_col   = pick_first(t, ["fee_usd","fee","commission","fee_quote"]) or "fee_usd"

    for col, default in [(price_col,0.0),(qty_col,0.0),(fee_col,0.0)]:
        t[col] = pd.to_numeric(t.get(col, default), errors="coerce").fillna(default)

    if "side" not in t.columns:
        t["side"] = np.where(t[qty_col] >= 0, "buy", "sell")
    else:
        t["side"] = t["side"].astype(str).str.lower().fillna("")

    for c in TS_TRADES:
        if c in t.columns and c != "ts_dt":
            t.drop(columns=[c], inplace=True)
    t = t[["ts_dt"] + [c for c in t.columns if c != "ts_dt"]]

    return t, "ts_dt", price_col, qty_col, fee_col

def load_equity(path):
    if not os.path.exists(path):
        sys.exit(f"❌ Missing {path}")
    eh = pd.read_csv(path)
    eh.columns = [c.strip().lower() for c in eh.columns]

    raw = pick_first(eh, TS_EQUITY)
    if raw is None:
        eh["ts_dt"] = parse_maybe_epoch(pd.Series(np.arange(len(eh)), dtype="int64"))
    else:
        eh["ts_dt"] = parse_maybe_epoch(eh[raw])

    eh = eh.dropna(subset=["ts_dt"]).sort_values("ts_dt").reset_index(drop=True)
    return eh, "ts_dt"

def starting_balances(eh, dt_col, first_trade_ts, have_trade_time):
    if have_trade_time and is_datetime64_any_dtype(eh[dt_col]):
        pre = eh[eh[dt_col] <= first_trade_ts]
        base = pre.iloc[-1] if not pre.empty else eh.iloc[0]
    else:
        base = eh.iloc[0]

    def pf(cols, names):
        for n in names:
            if n in cols: return n
        return None

    cash_name = pf(eh.columns, ["cash_usd","cash","usd_cash","quote_balance"])
    btc_name  = pf(eh.columns, ["btc","base_balance","qty_btc"])

    cash = float(base[cash_name]) if cash_name else np.nan
    btc  = float(base[btc_name])  if btc_name  else np.nan

    if (pd.isna(cash) or pd.isna(btc)):
        price_col = pf(eh.columns, ["price","close","btc_price","btc_close"])
        if price_col is not None and "equity" in eh.columns:
            cash = float(base["equity"])
            btc  = 0.0
        else:
            sys.exit("❌ Need starting cash/btc or (equity & price) to simulate balances.")
    return cash, btc

def fmt_money(x, nd=2): 
    try: return f"{x:,.{nd}f}"
    except: return x

def fmt_btc(x, nd=6):
    try: return f"{x:,.{nd}f}"
    except: return x

def main():
    t, ts_col, price_col, qty_col, fee_col = load_trades(TRADES_CSV)
    eh, dt_col = load_equity(EQUITY_CSV)

    have_trade_time = is_datetime64_any_dtype(t[ts_col]) if len(t) else False
    first_trade_ts = t[ts_col].iloc[0] if have_trade_time else None

    # Build balances through trades
    cash0, btc0 = starting_balances(eh, dt_col, first_trade_ts, have_trade_time) if len(t) else (np.nan, np.nan)
    cash, btc = cash0, btc0
    cash_bal, btc_bal = [], []
    for _, r in t.iterrows():
        side = (r.get("side","") or "").lower()
        price = float(r[price_col]); qty = float(r[qty_col]); fee = float(r[fee_col])
        if side == "buy":
            cash -= price * qty + fee
            btc  += qty
        elif side == "sell":
            cash += price * qty - fee
            btc  -= qty
        cash_bal.append(cash); btc_bal.append(btc)

    insert_at = t.columns.get_loc(fee_col) + 1 if fee_col in t.columns else len(t.columns)
    if len(t):
        t.insert(insert_at, "cash_balance_usd", cash_bal)
        t.insert(insert_at + 1, "btc_balance", btc_bal)

    os.makedirs(STATE_DIR, exist_ok=True)
    t.to_csv(OUT_CSV, index=False)

    # ===== Weekly header summary =====
    as_of_ts = max(
        [x for x in [
            eh[dt_col].max() if len(eh) else None,
            t[ts_col].max() if len(t) else None
        ] if x is not None]
    )
    seven_days_ago = as_of_ts - pd.Timedelta(days=7)

    # Portfolio snapshot (prefer equity file)
    latest_eh = eh.iloc[-1] if len(eh) else None
    cash_now = float(latest_eh.get("cash_usd", np.nan)) if latest_eh is not None else (cash_bal[-1] if cash_bal else np.nan)
    btc_now  = float(latest_eh.get("btc", np.nan))      if latest_eh is not None else (btc_bal[-1] if btc_bal else np.nan)

    # Equity delta 7d (prefer equity column; else compute if price available)
    def equity_from_row(row):
        if row is None: return np.nan
        if "equity" in row.index: return float(row["equity"])
        price_col_eh = "price" if "price" in row.index else ("close" if "close" in row.index else None)
        if price_col_eh and ("cash_usd" in row.index or "cash" in row.index) and "btc" in row.index:
            cashv = float(row.get("cash_usd", row.get("cash", np.nan)))
            return cashv + float(row["btc"]) * float(row[price_col_eh])
        return np.nan

    now_e = equity_from_row(latest_eh)
    prev_idx = eh[eh[dt_col] <= seven_days_ago].index
    prev_row = eh.loc[prev_idx[-1]] if len(prev_idx) else (eh.iloc[0] if len(eh) else None)
    prev_e = equity_from_row(prev_row)
    eq_delta_7d = now_e - prev_e if (pd.notna(now_e) and pd.notna(prev_e)) else np.nan

    # Trades (7d)
    t7 = t[t[ts_col] >= seven_days_ago] if len(t) else t
    buys  = (t7["side"] == "buy").sum()  if "side" in t7.columns else 0
    sells = (t7["side"] == "sell").sum() if "side" in t7.columns else 0
    total = len(t7)
    buy_notional  = float((t7.loc[t7["side"]=="buy",  price_col] * t7.loc[t7["side"]=="buy",  qty_col]).sum()) if len(t7) else 0.0
    sell_notional = float((t7.loc[t7["side"]=="sell", price_col] * t7.loc[t7["side"]=="sell", qty_col]).sum()) if len(t7) else 0.0

    # ===== HTML =====
    view = t.copy()
    if len(view):
        view["ts_dt"] = view["ts_dt"].dt.strftime("%Y-%m-%d %H:%M")
        if price_col in view:            view[price_col] = view[price_col].map(lambda v: fmt_money(v, 2))
        if qty_col in view:              view[qty_col] = view[qty_col].map(lambda v: fmt_money(v, 6))
        if fee_col in view:              view[fee_col] = view[fee_col].map(lambda v: fmt_money(v, 2))
        if "cash_balance_usd" in view:   view["cash_balance_usd"] = view["cash_balance_usd"].map(lambda v: fmt_money(v, 2))
        if "btc_balance" in view:        view["btc_balance"] = view["btc_balance"].map(lambda v: fmt_money(v, 6))

    parts = []
    parts.append("<style>body{font-family:Segoe UI,Arial,sans-serif} table{border-collapse:collapse} th,td{border:1px solid #ddd;padding:6px} th{background:#f4f6f8} h2{margin-bottom:4px}</style>")
    parts.append("<h2>BTC Agent - Weekly Summary</h2>")
    parts.append(f"<p><b>As of:</b> {as_of_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC</p>")

    # Portfolio block
    parts.append("<h3>Portfolio</h3>")
    parts.append("<ul>")
    parts.append(f"<li>Cash: ${fmt_money(cash_now,2) if pd.notna(cash_now) else 'n/a'}</li>")
    parts.append(f"<li>BTC: {fmt_btc(btc_now,6) if pd.notna(btc_now) else 'n/a'}</li>")
    parts.append(f"<li>Equity Δ (7d): {fmt_money(eq_delta_7d,2) if pd.notna(eq_delta_7d) else 'n/a'}</li>")
    parts.append("</ul>")

    # Trades (7d)
    parts.append("<h3>Trades (7d)</h3>")
    parts.append("<ul>")
    parts.append(f"<li>Total: {total} (buys: {buys}, sells: {sells})</li>")
    parts.append(f"<li>Buy notional: ${fmt_money(buy_notional,2)}</li>")
    parts.append(f"<li>Sell notional: ${fmt_money(sell_notional,2)}</li>")
    parts.append("</ul>")

    # Full table (all trades) with balances
    parts.append("<h3>Trades with Post-Trade Balances</h3>")
    parts.append("<p>Balances reflect holdings immediately <b>after</b> each execution.</p>")
    parts.append(view.to_html(index=False, escape=False))

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    print("OK wrote:", OUT_CSV)
    print("OK wrote:", OUT_HTML)

if __name__ == "__main__":
    main()

