import os, ssl, smtplib, pandas as pd
from pathlib import Path
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
import json

STATE_DIR = Path(os.getenv("STATE_DIR", "/content/drive/MyDrive/btc-trading-agent/state"))
LEDGER    = STATE_DIR / "trades.csv"
EQUITY    = STATE_DIR / "equity_history.csv"
STATE     = STATE_DIR / "portfolio_state.json"

def _read_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"cash_usd": 0.0, "btc": 0.0}

def _read_ledger():
    if not LEDGER.exists():
        return pd.DataFrame(columns=["ts","side","reason","price","qty_btc","fee_usd","note"])
    df = pd.read_csv(LEDGER)
    # robust timestamp parse (supports unix seconds or ISO)
    def _parse_ts(x):
        try:
            # ints/floats → unix seconds
            return pd.to_datetime(float(x), unit="s", utc=True)
        except Exception:
            try:
                return pd.to_datetime(x, utc=True)
            except Exception:
                return pd.NaT
    df["ts_dt"] = df["ts"].apply(_parse_ts)
    df = df.dropna(subset=["ts_dt"]).sort_values("ts_dt")
    for c in ("price","qty_btc","fee_usd"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "price" in df.columns and "qty_btc" in df.columns:
        df["notional"] = df["price"] * df["qty_btc"]
    else:
        df["notional"] = 0.0
    return df

def _read_equity():
    if not EQUITY.exists():
        return None
    df = pd.read_csv(EQUITY)
    if "ts_utc" in df.columns:
        df["ts_dt"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    else:
        df["ts_dt"] = pd.NaT
    for c in ("price","cash_usd","btc","equity"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["ts_dt"]).sort_values("ts_dt")

def build_weekly_stats():
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    st = _read_state()
    ledger = _read_ledger()
    eq = _read_equity()

    # filter to last 7 days
    led7 = ledger[ledger["ts_dt"] >= week_ago].copy()

    buys  = led7[(led7["side"].str.upper()=="BUY")]
    sells = led7[(led7["side"].str.upper()=="SELL")]

    stats = {
        "now_utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "trades_total": int(len(led7)),
        "buys": int(len(buys)),
        "sells": int(len(sells)),
        "buy_notional": float(buys["notional"].sum()) if "notional" in buys else 0.0,
        "sell_notional": float(sells["notional"].sum()) if "notional" in sells else 0.0,
        "cash_usd": float(st.get("cash_usd", 0.0)),
        "btc": float(st.get("btc", 0.0)),
        "equity_change": None,  # fill from equity history if available
        "equity_last": None,
        "equity_week_ago": None,
        "trades_df": led7.tail(30),  # show up to last 30 trades in email
    }

    if eq is not None and not eq.empty and "equity" in eq.columns:
        eq7 = eq[eq["ts_dt"] >= week_ago]
        if not eq.empty:
            stats["equity_last"] = float(eq["equity"].dropna().iloc[-1])
        if not eq7.empty:
            stats["equity_week_ago"] = float(eq7["equity"].dropna().iloc[0])
        if stats["equity_last"] is not None and stats["equity_week_ago"] is not None:
            stats["equity_change"] = stats["equity_last"] - stats["equity_week_ago"]

    return stats

def render_weekly_html(stats):
    def fmt_money(x):
        return f"${x:,.2f}" if x is not None else "n/a"
    eq_delta = stats["equity_change"]
    eq_line = "-"
    if eq_delta is not None:
        sign = "+" if eq_delta >= 0 else "-"
        eq_line = f"{sign}{abs(eq_delta):,.2f}"

    # trades table
    df = stats["trades_df"].copy()
    if not df.empty:
        df = df[["ts_dt","side","reason","price","qty_btc","fee_usd","note"]].copy()
        df["ts_dt"] = df["ts_dt"].dt.strftime("%Y-%m-%d %H:%M")
        df["price"] = df["price"].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "")
        df["qty_btc"] = df["qty_btc"].map(lambda v: f"{v:.6f}" if pd.notna(v) else "")
        df["fee_usd"] = df["fee_usd"].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "")
        trades_html = df.to_html(index=False, escape=False)
    else:
        trades_html = "<i>No trades in the last 7 days.</i>"

    # Construct HTML string piecewise to avoid parsing issues
    html_lines = [
        "<h2>BTC Agent - Weekly Summary</h2>",
        f"<p><b>As of:</b> {stats['now_utc']}</p>",
        "<h3>Portfolio</h3>",
        "<ul>",
        f"  <li><b>Cash:</b> {fmt_money(stats['cash_usd'])}</li>",
        f"  <li><b>BTC:</b> {stats['btc']:.6f}</li>", # Directly format here
        f"  <li><b>Equity Δ (7d):</b> {eq_line}</li>",
        "</ul>",
        "<h3>Trades (7d)</h3>",
        "<ul>",
        f"  <li><b>Total:</b> {stats['trades_total']} (buys: {stats['buys']}, sells: {stats['sells']})</li>",
        f"  <li><b>Buy notional:</b> {fmt_money(stats['buy_notional'])}</li>",
        f"  <li><b>Sell notional:</b> {fmt_money(stats['sell_notional'])}</li>",
        "</ul>",
        trades_html,
    ]

    html = "\n".join(html_lines) # Join with newline characters

    return html

def _send_html_email(subject, html):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT","587") or "587")
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    to   = os.getenv("EMAIL_TO")
    from_addr = os.getenv("EMAIL_FROM", user or "")

    if not (host and user and pwd and to and from_addr):
        return False, "Missing SMTP/EMAIL_* env vars; cannot send."

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

def send_weekly_email(preview_if_missing_creds=True):
    stats = build_weekly_stats()
    html = render_weekly_html(stats)

    ok, msg = _send_html_email("weekly", html)
    if ok:
        return True, "✅ Weekly email sent."

    if preview_if_missing_creds:
        # write a preview HTML so you can open it from Drive/Colab
        out = STATE_DIR / "weekly_report_preview.html"
        out.write_text(html, encoding="utf-8")
        return False, f"✍️ No SMTP creds; wrote preview → {out}"
    return False, f"❌ {msg}"
