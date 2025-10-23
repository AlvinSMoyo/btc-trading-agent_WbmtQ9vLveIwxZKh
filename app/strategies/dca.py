# app/strategies/dca.py
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Iterable

def _parse_iso_aware(s: str | None):
    if not s:
        return None
    try:
        # Python 3.11+ handles Z/offsets with fromisoformat
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _cooldown_ok(state: Dict[str, Any], min_minutes: int) -> bool:
    """
    Return True if enough time has passed since last_dca_ts.
    Works with aware/naive inputs and missing values.
    """
    last = _parse_iso_aware(state.get("last_dca_ts"))
    if last is None:
        return True  # no prior DCA -> allowed

    # make 'now' timezone-aware (UTC)
    now = datetime.now(timezone.utc)

    # if 'last' is naive (shouldn't be, but be safe), assume UTC
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    return now - last >= timedelta(minutes=int(min_minutes))

def _drop_hit(state: Dict[str, Any], price: float, drop_pct: float) -> bool:
    last_price = state.get("last_dca_price")
    if not last_price:
        return True  # first DCA is allowed
    try:
        return (price <= (float(last_price) * (1.0 - float(drop_pct) / 100.0)))
    except Exception:
        return False

def dca_actions(state: Dict[str, Any], price: float, cfg: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    # wrapper uses the robust helpers above
    if _drop_hit(state, price, cfg["DCA_DROP_PCT"]) and _cooldown_ok(state, cfg["DCA_MIN_COOLDOWN_MIN"]):
        lot_usd = float(cfg.get("DCA_LOT_USD", 0))
        if lot_usd > 0:
            qty = lot_usd / float(price)
            yield {"type": "BUY", "qty": qty, "lot_usd": lot_usd}
