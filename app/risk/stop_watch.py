def check_stops(open_positions, price: float):
    """
    open_positions: iterable of dicts with at least {'id','qty','stop'}
    returns list of exit intents when price <= stop
    """
    exits = []
    for pos in open_positions or []:
        stop = pos.get("stop")
        if stop is not None and price <= float(stop):
            exits.append({"side":"sell","qty":pos["qty"],"reason":"stop","ref_trade_id":pos["id"]})
    return exits
