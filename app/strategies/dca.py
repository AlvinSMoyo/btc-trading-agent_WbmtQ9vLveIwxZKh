from datetime import datetime, timedelta

def _cooldown_ok(state: dict, min_minutes: int) -> bool:
    last = state.get("last_dca_ts")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return datetime.utcnow() - last_dt >= timedelta(minutes=int(min_minutes))

def _drop_hit(state: dict, price: float, drop_pct: float) -> bool:
    anchor = state.get("last_dca_price") or state.get("last_price") or price
    if not anchor:
        return False
    return ((anchor - price) / anchor * 100.0) >= float(drop_pct)

def dca_actions(state: dict, price: float, cfg: dict):
    """Return a list of trade intents; empty if no DCA this loop."""
    acts = []
    if _drop_hit(state, price, cfg["DCA_DROP_PCT"]) and _cooldown_ok(state, cfg["DCA_MIN_COOLDOWN_MIN"]):
        usd = float(cfg["DCA_LOT_USD"])
        qty = round(usd / price, 8)
        acts.append({"side":"buy","qty":qty,"reason":"dca","meta":{"usd":usd}})
    return acts
