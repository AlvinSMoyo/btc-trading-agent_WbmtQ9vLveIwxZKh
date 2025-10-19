import os
import ccxt

def _make_exchange():
    name = os.getenv("EXCHANGE", "kraken").lower()
    sandbox = str(os.getenv("EXCHANGE_SANDBOX", "true")).lower() == "true"
    key = os.getenv("EXCHANGE_KEY") or ""
    secret = os.getenv("EXCHANGE_SECRET") or ""
    password = os.getenv("EXCHANGE_PASSWORD") or None

    klass = getattr(ccxt, name)
    ex = klass({
        "apiKey": key,
        "secret": secret,
        "password": password,
        "enableRateLimit": True,
        "options": {"adjustForTimeDifference": True},
    })
    if sandbox and hasattr(ex, "set_sandbox_mode"):
        ex.set_sandbox_mode(True)
    return ex

def fetch_price(symbol="BTC-USD"):
    # Map ai symbol â†’ exchange symbol
    sym = symbol.replace("-", "/")
    ex = _make_exchange()
    t = ex.fetch_ticker(sym)
    return float(t["last"])

def fetch_balances():
    ex = _make_exchange()
    try:
        bal = ex.fetch_balance()
        return {k: v for k, v in bal.get("total", {}).items() if v}
    except Exception as e:
        return {"error": type(e).__name__}

def exchange_diagnostics():
    print("exchange:", os.getenv("EXCHANGE", "kraken"))
    print("sandbox:", os.getenv("EXCHANGE_SANDBOX", "true"))
    print("price:", fetch_price("BTC-USD"))
    print("balances:", fetch_balances())

