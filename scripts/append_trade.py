# scripts/append_trade.py
import os, csv, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

STATE_DIR = os.getenv("STATE_DIR", "state")
TRADES_CSV = os.path.join(STATE_DIR, "trades.csv")

COLUMNS = ["ts","side","source","reason","price","qty_btc","fee_usd","note","confidence"]

def _ensure_header():
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    if not Path(TRADES_CSV).exists() or os.path.getsize(TRADES_CSV) == 0:
        with open(TRADES_CSV, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLUMNS)

def _now_utc_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def append_trade(*, side, source="", reason="", price=0.0, qty_btc=0.0, fee_usd=0.0, note="", confidence=None, ts_utc=None):
    _ensure_header()
    ts = ts_utc or _now_utc_iso()

    # load and check for near-duplicate (±5s)
    try:
        df = pd.read_csv(TRADES_CSV, dtype=str)
    except Exception:
        df = pd.DataFrame(columns=COLUMNS)
    if not df.empty:
        df.columns = [c.strip().lower() for c in df.columns]
        if "ts" not in df.columns and "ts_utc" in df.columns:
            df["ts"] = df["ts_utc"]
        df["_ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        t_new = pd.to_datetime(ts, utc=True, errors="coerce")
        window = (df["_ts"] >= t_new - timedelta(seconds=5)) & (df["_ts"] <= t_new + timedelta(seconds=5))
        same = (
            window
            & (df.get("side","").str.lower().fillna("") == str(side).lower())
            & (df.get("source","").fillna("") == str(source))
            & (df.get("reason","").fillna("") == str(reason))
            & (pd.to_numeric(df.get("price"), errors="coerce").fillna(-1) == float(price))
            & (pd.to_numeric(df.get("qty_btc", df.get("qty")), errors="coerce").fillna(-1) == float(qty_btc))
        )
        if same.any():
            print("[append_trade] skipped: near-duplicate within ±5s")
            return

    row = {
        "ts": ts,
        "side": str(side).lower(),
        "source": source,
        "reason": reason,
        "price": float(price),
        "qty_btc": float(qty_btc),
        "fee_usd": float(fee_usd),
        "note": note,
        "confidence": ("" if confidence is None else float(confidence)),
    }
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writerow(row)
    print("[append_trade] appended:", row)

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Append one trade row to state/trades.csv")
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--source", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--qty-btc", type=float, required=True, dest="qty_btc")
    p.add_argument("--fee-usd", type=float, default=0.0, dest="fee_usd")
    p.add_argument("--note", default="")
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--ts-utc", default=None, dest="ts_utc",
                  help="Optional ISO timestamp (UTC). Defaults to now.")
    args = p.parse_args()

    append_trade(**vars(args))
