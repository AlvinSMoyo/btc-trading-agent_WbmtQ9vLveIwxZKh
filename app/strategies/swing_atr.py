def swing_entry(price: float, atr_value: float, cfg: dict):
    """
    Compute an ATR-based stop for an opportunistic entry.
    The *decision* to enter is up to your regime/heuristics.
    """
    if atr_value is None:
        return None
    k = float(cfg["ATR_K"])
    stop = round(price - k * atr_value, 2)
    return {"side":"buy","qty":0.0,"reason":"opportunistic","meta":{"stop":stop}}
