# app/notify/telegram.py
from __future__ import annotations
import os, requests

_TRUE = {"1","true","yes","on","y","t"}

def _creds():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    return token, chat_id

def _flag_enabled() -> bool:
    # Prefer env; if missing, fall back to sheet config
    v = os.getenv("TELEGRAM_ENABLED")
    if v is not None:
        return str(v).strip().lower() in _TRUE
    try:
        from app.config.loader import load
        cfg = load()
        v = cfg.get("TELEGRAM_ENABLED")
        return str(v).strip().lower() in _TRUE
    except Exception:
        return False

def ping(text: str):
    token, chat_id = _creds()
    if not (token and chat_id):
        return False, "missing TELEGRAM_BOT_TOKEN/CHAT_ID"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text}, timeout=10
        )
        return r.ok, None if r.ok else r.text
    except Exception as e:
        return False, str(e)

def send_trade_alert(tx):
    if not _flag_enabled():
        return False, "disabled"
    token, chat_id = _creds()
    if not (token and chat_id):
        return False, "missing creds"

    # normalize tx to dict
    if isinstance(tx, tuple) and len(tx) >= 2 and isinstance(tx[1], dict):
        t = tx[1]
    elif isinstance(tx, dict):
        t = tx
    else:
        t = {}

    side   = str(t.get("side","?")).upper()
    reason = str(t.get("reason") or "trade")
    price  = t.get("price")
    qty    = t.get("qty_btc") or t.get("qty")
    note   = t.get("note") or ""

    msg = f"#{reason} {side}\nQty: {qty}\nPrice: {price}\n{note}".strip()
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg}, timeout=10
        )
        return r.ok, None if r.ok else r.text
    except Exception as e:
        return False, str(e)
