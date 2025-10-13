# scripts/baseline_overlay.py
# Render equity curve + Buy/Hold + weekly DCA with trade markers.
# Uses ALL history by default. Set OVERLAY_DAYS to limit (e.g. "30").
# Set OVERLAY_DAYS=all (or unset) to disable any cutoff.

import os
import json
from typing import Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


STATE_DIR   = os.getenv("STATE_DIR", "state")
EQ_CSV      = os.path.join(STATE_DIR, "equity_history.csv")
TRADES_CSV  = os.path.join(STATE_DIR, "trades.csv")
DCA_LOT     = float(os.getenv("DCA_USD", "300"))

# OVERLAY_DAYS:
# - "all" (default) -> no time filter, use full history
# - positive int     -> last N days only
_OVERLAY_RAW = os.getenv("OVERLAY_DAYS", "all").strip().lower()
if _OVERLAY_RAW == "" or _OVERLAY_RAW == "none":
    _OVERLAY_RAW = "all"


# ------------------------
# Helpers
# ------------------------

def _utc(s: pd.Series) -> pd.Series:
    """Parse to UTC timestamps, coercing errors to NaT."""
    return pd.to_datetime(s, utc=True, errors="coerce")


def _num(s: pd.Series, nd: int | None = None) -> pd.Series:
    """Coerce to numeric floats, optionally round."""
    x = pd.to_numeric(s, errors="coerce")
    if nd is not None:
        x = x.round(nd)
    return x


def _apply_optional_cutoff(df: pd.DataFrame, ts_col: str = "ts") -> pd.DataFrame:
    """Apply recency cutoff if OVERLAY_DAYS is an integer; otherwise keep all."""
    if _OVERLAY_RAW == "all":
        return df.copy()

    try:
        days = int(_OVERLAY_RAW)
        if days <= 0:
            return df.copy()
    except ValueError:
        # Any non-int -> treat as 'all'
        return df.copy()

    cutoff = pd.Timestamp.utcnow().tz_localize("UTC") - pd.Timedelta(days=days)
    out = df[df[ts_col] >= cutoff].copy()
    return out


def _equity_at(tr_ts: pd.Timestamp, eq: pd.DataFrame) -> float:
    """Equity value at or just before trade time."""
    idx = int(eq["ts"].searchsorted(tr_ts, side="right")) - 1
    if idx < 0:
        return np.nan
    return float(eq["equity"].iloc[idx])


# ------------------------
# Loading
# ------------------------

def load_equity() -> pd.DataFrame:
    if not os.path.exists(EQ_CSV):
        raise SystemExit(f"[overlay] Missing equity CSV: {EQ_CSV}")

    eq = pd.read_csv(EQ_CSV)

    # Flexible schema: accept ts_utc (preferred). If missing, try ts or ts_dt.
    ts_key = None
    for cand in ("ts_utc", "ts", "ts_dt"):
        if cand in eq.columns:
            ts_key = cand
            break
    if not ts_key:
        raise SystemExit("[overlay] equity_history.csv has no timestamp column (ts_utc/ts/ts_dt)")

    eq["ts"]      = _utc(eq[ts_key])
    eq["price"]   = _num(eq.get("price", np.nan), 2)
    eq["cash_usd"]= _num(eq.get("cash_usd", np.nan), 2)
    eq["btc"]     = _num(eq.get("btc", np.nan), 8)
    eq["equity"]  = _num(eq.get("equity", np.nan), 2)

    # Drop bad rows; sort; dedupe identical timestamps (keep last).
    eq = eq.dropna(subset=["ts"]).sort_values("ts")
    eq = eq.drop_duplicates(subset=["ts"], keep="last").reset_index(drop=True)

    total_before = len(eq)
    eq_recent = _apply_optional_cutoff(eq, "ts")

    print(f"[overlay] equity path: {os.path.abspath(EQ_CSV)}")
    print(f"[overlay] rows(total/recent): {total_before} / {len(eq_recent)}")
    if len(eq_recent):
        print(f"[overlay] ts window: {eq_recent['ts'].min()} → {eq_recent['ts'].max()}")

    return eq_recent


def load_trades(eq_min_ts: pd.Timestamp) -> pd.DataFrame:
    if not os.path.exists(TRADES_CSV):
        print(f"[overlay] no trades file at {TRADES_CSV}")
        return pd.DataFrame(columns=["ts", "side", "price"])

    tr = pd.read_csv(TRADES_CSV)

    # Pick a timestamp column
    ts_key = None
    for cand in ("ts_utc", "ts_dt", "ts"):
        if cand in tr.columns:
            ts_key = cand
            break
    tr["ts"] = _utc(tr[ts_key]) if ts_key else pd.NaT

    # Normalize cols
    if "side" not in tr.columns:
        tr["side"] = ""
    else:
        tr["side"] = tr["side"].astype(str).str.lower()

    if "price" not in tr.columns:
        tr["price"] = np.nan
    else:
        tr["price"] = _num(tr["price"], 2)

    tr = tr.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    tr = tr[tr["ts"] >= eq_min_ts].reset_index(drop=True)

    # Optionally apply same cutoff as equity
    tr = _apply_optional_cutoff(tr, "ts")

    print(f"[overlay] trades path: {os.path.abspath(TRADES_CSV)} | rows in window: {len(tr)}")
    return tr


# ------------------------
# Benchmarks
# ------------------------

def build_benchmarks(eq: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """
    Buy & Hold: buy qty = first_equity / first_price, then value with price.
    Weekly DCA: buy DCA_LOT every 7 days.
    """
    if eq.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    e0 = float(eq["equity"].iloc[0])
    p0 = float(eq["price"].iloc[0]) if float(eq["price"].iloc[0]) > 0 else np.nan
    qty = e0 / p0 if p0 and np.isfinite(p0) and p0 > 0 else 0.0
    hold = (qty * eq["price"]).rename("hold_equity")

    dca_btc = 0.0
    last_buy_ts = None
    dca_vals = []

    for ts, price in zip(eq["ts"], eq["price"]):
        if (last_buy_ts is None) or ((ts - last_buy_ts) >= pd.Timedelta(days=7)):
            if price and np.isfinite(price) and price > 0:
                dca_btc += DCA_LOT / float(price)
            last_buy_ts = ts
        dca_vals.append(dca_btc * float(price if np.isfinite(price) else np.nan))

    dca = pd.Series(dca_vals, index=eq.index, name="dca_equity")
    return hold, dca


# ------------------------
# Plot & HTML
# ------------------------

def plot(eq: pd.DataFrame, trades: pd.DataFrame, hold: pd.Series, dca: pd.Series, out_png: str) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(eq["ts"], eq["equity"], label="Hybrid (Your Agent)")
    ax.plot(eq["ts"], hold,        label="Buy & Hold")
    ax.plot(eq["ts"], dca,         label=f"Weekly DCA (${int(DCA_LOT)})")

    # Trade markers aligned to equity timeline
    if not trades.empty:
        for side, marker, label in (("buy", "^", "Buy"), ("sell", "v", "Sell")):
            df = trades[trades["side"] == side]
            if df.empty:
                continue
            xs, ys = [], []
            for _, r in df.iterrows():
                y = _equity_at(r["ts"], eq)
                if np.isfinite(y):
                    xs.append(r["ts"])
                    ys.append(y)
            if xs:
                ax.scatter(xs, ys, marker=marker, s=120, label=label)

    ax.set_title("Equity Curve — Hybrid vs Hold vs DCA (with trade markers)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity (USD)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    print(f"[overlay] wrote PNG: {out_png}")


def write_html(out_png: str, eq: pd.DataFrame) -> None:
    out_html = os.path.join(STATE_DIR, "baseline_summary_with_trades.html")
    latest = eq.iloc[-1]
    now = pd.Timestamp.utcnow().isoformat()

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Baseline Overlay</title></head>
<body>
<h2>Baseline Overlay (generated {now}Z)</h2>
<p><b>Latest equity:</b> ${float(latest['equity']):,.2f}
&nbsp; <b>Price:</b> ${float(latest['price']):,.2f}
&nbsp; <b>BTC:</b> {float(latest['btc']):.8f}</p>
<img src="{os.path.basename(out_png)}" style="max-width:100%;height:auto;" />
</body></html>"""

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[overlay] wrote HTML: {out_html}")


# ------------------------
# Main
# ------------------------

def main() -> None:
    eq = load_equity()
    if eq.empty:
        raise SystemExit("[overlay] No equity rows in selected window. "
                         "Run the bot or set OVERLAY_DAYS=all to include full history.")
    trades = load_trades(eq["ts"].min())
    hold, dca = build_benchmarks(eq)

    stamp  = pd.Timestamp.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_png = os.path.join(STATE_DIR, f"baseline_overlay_{stamp}.png")

    plot(eq, trades, hold, dca, out_png)
    write_html(out_png, eq)


if __name__ == "__main__":
    main()
