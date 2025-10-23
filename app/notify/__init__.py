# app/notify/__init__.py
from .telegram import ping, send_trade_alert
__all__ = ["ping", "send_trade_alert"]
