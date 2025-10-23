import os, json

def env(name, default=None, cast=str):
    val = os.getenv(name, default)
    if val is None: return None
    try: return cast(val)
    except Exception: return val

def load_local_fallback(path="config.local.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"app_config":{"symbol":"BTC-USD","interval_minutes":30,"atr_window":14,"rsi_window":14}}

def state_dir():
    return env("STATE_DIR", "/content/drive/MyDrive/btc-trading-agent/state")

def service_json_path():
    return env("SERVICE_JSON_PATH", "/content/drive/MyDrive/btc-trading-agent/service_account.json")

def sheet_id():
    return env("SHEET_ID", "")

def advisor_model():
    return env("ADVISOR_MODEL", "mock")

