# app/voice_email.py  —  HOTFIX v1.1
# - Builds ts_dt robustly: prefer ts_utc, then existing ts_dt, then ts (epoch s/ms) PER ROW
# - Keeps any extra columns you added
# - Adds running balances (cash_after, btc_after, equity_after, cum_pnl)
# - Writes preview HTML if SMTP creds are missing

import os
import ssl
import smtplib
import json
from pathlib import Path
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

import pandas as pd

# ----------------------------
# Paths / config
# ----------------------------
STATE_DIR = Path(os.getenv("STATE_DIR") or (Path.cwd() / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

LEDGER = STATE_DIR / "trades.csv"
EQUITY = STATE_DIR / "equity_history.csv"
STATE  = STATE_DIR / "portfolio_state.json"

# ----------------------------
# Helpers
# ----------------------------
def _read_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"cash_usd": 0.0, "btc": 0.0}

def _parse_ts_any(x):
    """Accept epoch seconds/ms or ISO/free-form, return UTC Timestamp or NaT."""
    if pd.isna(x) or (isinstance(x, str) and x.strip() == ""):
        return pd.NaT
    try:
        # try numeric epoch
        val = float(str(x).strip())
        if val < 1e12:
            return pd.to_datetime(int(val), unit="s", utc=True)
        else:
            return pd.to_datetime(int(val), unit="ms", utc=True)
    except Exception:
        try:
            return pd.to_datetime(x, utc=True, errors="coerce")
        except Exception:
            return pd.NaT

def _read_ledger() -> pd.DataFrame:
    """Read state/trades.csv; build robust ts_dt; keep extras; drop header-echo rows."""
    path = LEDGER
    if not path.exists():
        return pd.DataFrame()

    # Read as strings first
    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    # Trim whitespace
    df.columns = [c.strip() for c in df.columns]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].map(lambda x: x.strip() if isinstance(x, str) else x)

    # Drop any repeated header rows that got appended into the file
    if not df.empty:
        bad = pd.Series(False, index=df.index)
        for col in ["ts","side","reason","price","qty_btc","fee_usd","note","ts_utc","ts_dt"]:
            if col in df.columns:
                bad = bad | df[col].astype(str).str.lower().eq(col)
        df = df.loc[~bad].copy()

    # Build ts_dt with fallbacks
    parsed = None
    if "ts_dt" in df.columns:
        parsed = pd.to_datetime(df["ts_dt"], utc=True, errors="coerce")
    if parsed is None:
        parsed = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")

    if "ts_utc" in df.columns:
        parsed = parsed.fillna(pd.to_datetime(df["ts_utc"], utc=True, errors="coerce"))
    if "ts" in df.columns:
        parsed = parsed.fillna(df["ts"].map(_parse_ts_any))

    if parsed.isna().any():
        for cand in df.columns:
            lc = cand.lower()
            if "time" in lc or "date" in lc:
                parsed = parsed.fillna(pd.to_datetime(df[cand], utc=True, errors="coerce"))

    df["ts_dt"] = parsed

    # Coerce numerics
    for c in ("price", "qty_btc", "fee_usd"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Compute notional
    if "price" in df.columns and "qty_btc" in df.columns:
        df["notional"] = (df["price"] * df["qty_btc"]).fillna(0.0)
    else:
        df["notional"] = 0.0

    # Normalize side
    if "side" in df.columns:
        df["side"] = df["side"].astype(str).str.upper()

    # Sort with valid timestamps last so the final snapshot is a real trade
    return df.sort_values("ts_dt", na_position="first").reset_index(drop=True)

def _read_equity():
    if not EQUITY.exists():
        return None
    df = pd.read_csv(EQUITY)
    if "ts_utc" in df.columns:
        df["ts_dt"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    elif "ts_dt" in df.columns:
        df["ts_dt"] = pd.to_datetime(df["ts_dt"], utc=True, errors="coerce")
    else:
        df["ts_dt"] = pd.NaT
    for c in ("price","cash_usd","btc","equity"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ts_dt"]).sort_values("ts_dt")
    return df

def _add_running_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Running cash/btc/equity/cum_pnl using each row's price as mark."""
    if df.empty:
        return df

    start_cash = float(os.getenv("STARTING_CASH", "10000") or 10000.0)
    start_btc  = float(os.getenv("STARTING_BTC", "0") or 0.0)
    cash = start_cash
    btc  = start_btc

    cash_after, btc_after, eq_after, pnl_after = [], [], [], []
    baseline = None

    for _, row in df.iterrows():
        price = float(row.get("price") or 0.0)
        qty   = float(row.get("qty_btc") or 0.0)
        fee   = float(row.get("fee_usd") or 0.0)
        side  = str(row.get("side","")).upper()

        if side == "BUY":
            cash -= price * qty + fee
            btc  += qty
        elif side == "SELL":
            cash += price * qty - fee
            btc  -= qty

        equity = cash + btc * price
        if baseline is None:
            baseline = equity
        pnl = equity - baseline

        cash_after.append(cash)
        btc_after.append(btc)
        eq_after.append(equity)
        pnl_after.append(pnl)

    out = df.copy()
    out["cash_after"]   = cash_after
    out["btc_after"]    = btc_after
    out["equity_after"] = eq_after
    out["cum_pnl"]      = pnl_after
    return out

# ----------------------------
# Stats & rendering
# ----------------------------
def build_weekly_stats():
    now = datetime.now(timezone.utc)
    days = int(os.getenv("REPORT_WINDOW_DAYS", "7") or "7")
    cutoff = now - timedelta(days=days)

    st = _read_state()
    ledger = _read_ledger()
    ledger = _add_running_columns(ledger)  # ensures cash_after/btc_after/equity_after/cum_pnl

    # last 7 days window
    led7 = ledger[ledger["ts_dt"] >= cutoff].copy() if not ledger.empty else ledger

    # summary counts
    if not led7.empty:
        u_side = led7.get("side", "").astype(str).str.upper()
        buys  = led7[u_side == "BUY"]
        sells = led7[u_side == "SELL"]
    else:
        buys = led7
        sells = led7

    buy_notional  = float(buys.get("notional", pd.Series(dtype=float)).sum()) if not led7.empty else 0.0
    sell_notional = float(sells.get("notional", pd.Series(dtype=float)).sum()) if not led7.empty else 0.0

    # portfolio snapshot (prefer running balances)
    if not ledger.empty:
        last = ledger.iloc[-1]
        port_cash = float(last.get("cash_after", st.get("cash_usd", 0.0)))
        port_btc  = float(last.get("btc_after",  st.get("btc", 0.0)))
    else:
        port_cash = float(st.get("cash_usd", 0.0))
        port_btc  = float(st.get("btc", 0.0))

    # --- NEW: equity delta from running ledger as a fallback ---
    eq_delta = None
    if "equity_after" in ledger.columns and not ledger.empty:
        eq_last_series = ledger["equity_after"].dropna()
        if not eq_last_series.empty:
            eq_last = float(eq_last_series.iloc[-1])
            past = ledger[ledger["ts_dt"] >= cutoff]
            if not past.empty:
                past_eq = past["equity_after"].dropna()
                if not past_eq.empty:
                    eq_week_ago = float(past_eq.iloc[0])
                    eq_delta = eq_last - eq_week_ago

    # Optional override if equity_history.csv is present
    eq_hist = _read_equity()
    if eq_hist is not None and not eq_hist.empty and "equity" in eq_hist.columns:
        week = eq_hist[eq_hist["ts_dt"] >= cutoff]
        last_vals = eq_hist["equity"].dropna()
        wk_vals   = week["equity"].dropna()
        if not last_vals.empty and not wk_vals.empty:
            eq_delta = float(last_vals.iloc[-1]) - float(wk_vals.iloc[0])

    # choose columns to display (keep extras if present)
    base_cols  = ["ts_dt","side","reason","price","qty_btc","fee_usd","note"]
    extra_cols = [c for c in ["cash_after","btc_after","equity_after","cum_pnl","notional"] if c in led7.columns]
    show_cols  = [c for c in base_cols + extra_cols if c in led7.columns]
    led7 = led7.loc[:, show_cols] if not led7.empty else led7

    return {
        "now_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "trades_total": int(len(led7)),
        "buys": int(len(buys)) if buys is not None else 0,
        "sells": int(len(sells)) if sells is not None else 0,
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "cash_usd": port_cash,
        "btc": port_btc,
        "equity_change": eq_delta,   # will now show even without equity_history.csv
        "trades_df": led7.tail(50),
    }
   

def render_weekly_html(stats):
    def fmt_money(x):
        return f"${x:,.2f}" if x is not None else "n/a"
    eq_delta = stats["equity_change"]
    eq_line = "-" if eq_delta is None else (f"+{eq_delta:,.2f}" if eq_delta >= 0 else f"-{abs(eq_delta):,.2f}")

    # build trades table with formatting
    df = stats["trades_df"].copy()
    if not df.empty:
        # Format a few columns if present
        if "ts_dt" in df.columns:
            df["ts_dt"] = pd.to_datetime(df["ts_dt"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        for c in ("price","fee_usd","notional","equity_after","cum_pnl","cash_after"):
            if c in df.columns:
                df[c] = df[c].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "")
        if "qty_btc" in df.columns:
            df["qty_btc"] = df["qty_btc"].map(lambda v: f"{v:.6f}" if pd.notna(v) else "")
        trades_html = df.to_html(index=False, escape=False)
    else:
        trades_html = "<i>No trades in the last 7 days.</i>"

    html = "\n".join([
        "<h2>BTC Agent - Weekly Summary</h2>",
        f"<p><b>As of:</b> {stats['now_utc']}</p>",
        "<h3>Portfolio</h3>",
        "<ul>",
        f"  <li><b>Cash:</b> {fmt_money(stats['cash_usd'])}</li>",
        f"  <li><b>BTC:</b> {stats['btc']:.6f}</li>",
        f"  <li><b>Equity Δ (7d):</b> {eq_line}</li>",
        "</ul>",
        "<h3>Trades (7d)</h3>",
        "<ul>",
        f"  <li><b>Total:</b> {stats['trades_total']} (buys: {stats['buys']}, sells: {stats['sells']})</li>",
        f"  <li><b>Buy notional:</b> {fmt_money(stats['buy_notional'])}</li>",
        f"  <li><b>Sell notional:</b> {fmt_money(stats['sell_notional'])}</li>",
        "</ul>",
        trades_html,
    ])
    return html

# ----------------------------
# Email (optional)
# ----------------------------
def _send_html_email(subject, html):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT","587") or "587")
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    to   = os.getenv("EMAIL_TO")
    from_addr = os.getenv("EMAIL_FROM", user or "")

    if not (host and user and pwd and to and from_addr):
        return False, "Missing SMTP/EMAIL_* env vars; cannot send."

    try:
        msg = MIMEText(html, "html")
        prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "BTC Agent")
        msg["Subject"] = f"{prefix} — Weekly Summary"
        msg["From"] = from_addr
        msg["To"] = to

        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.sendmail(from_addr, [to], msg.as_string())
        return True, "Email sent."
    except Exception as e:
        return False, f"SMTP error: {e}"

def send_weekly_email(preview_if_missing_creds=True):
    stats = build_weekly_stats()
    html = render_weekly_html(stats)

    ok, msg = _send_html_email("weekly", html)
    if ok:
        return True, "Weekly email sent."

    if preview_if_missing_creds:
        out = STATE_DIR / "weekly_report_preview.html"
        out.write_text(html, encoding="utf-8")
        return False, f"No SMTP creds; wrote preview -> {out}"

    return False, f"Email send failed: {msg}"

if __name__ == "__main__":
    ok, msg = send_weekly_email(preview_if_missing_creds=True)
    print(msg)


