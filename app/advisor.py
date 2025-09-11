
import os, json
from jsonschema import validate, ValidationError

DECISION_SCHEMA = {
    "type": "object",
    "required": ["state", "action", "confidence"],
    "properties": {
        "state": {"enum": ["peak","dip","consolidation"]},
        "action": {"enum": ["buy","sell","hold"]},
        "confidence": {"type":"number","minimum":0,"maximum":1},
        "size_usd": {"type":"number"},
        "stop_atr_k": {"type":["number","null"]},
        "reason_short": {"type":"string"},
        "risk_flags": {"type":"array","items":{"type":"string"}}
    },
    "additionalProperties": False
}

def validate_decision(dec):
    try:
        validate(instance=dec, schema=DECISION_SCHEMA)
        return True, ""
    except ValidationError as e:
        return False, str(e)

def coerce_to_schema(dec: dict, strat: dict, obs: dict):
    if not isinstance(dec, dict):
        return None
    out = {}
    state = dec.get("state") or dec.get("regime")
    if state in ("peak","dip","consolidation"):
        out["state"] = state
    else:
        rsi = float(obs.get("rsi14", 50.0))
        out["state"] = "dip" if rsi < 32 else ("peak" if rsi > 70 else "consolidation")
    action = dec.get("action")
    if action not in ("buy","sell","hold"):
        action = "buy" if out["state"]=="dip" else ("sell" if out["state"]=="peak" else "hold")
    out["action"] = action
    conf = dec.get("confidence")
    if not isinstance(conf, (int,float)):
        conf = 0.66 if out["state"] in ("peak","dip") else 0.55
    out["confidence"] = max(0.0, min(1.0, float(conf)))
    out["size_usd"]   = float(dec.get("size_usd", strat.get("llm_size_usd", 300.0)))
    sak               = dec.get("stop_atr_k", strat.get("llm_stop_atr_k_default", 1.3))
    out["stop_atr_k"] = float(sak) if sak is not None else None
    out["reason_short"] = dec.get("reason_short", f"{out['state']} → {out['action']}")
    out["risk_flags"]   = dec.get("risk_flags", [out["state"]])
    return out

def ask_mock(obs: dict, strat: dict):
    rsi = float(obs.get("rsi14", 50.0))
    if rsi < 32:
        return {"state":"dip","action":"buy","confidence":0.72,
                "size_usd":float(strat.get("llm_size_usd",300.0)),
                "stop_atr_k":float(strat.get("llm_stop_atr_k_default",1.3)),
                "reason_short":"RSI oversold","risk_flags":["dip"]}
    if rsi > 70:
        return {"state":"peak","action":"sell","confidence":0.68,
                "size_usd":0.0,"stop_atr_k":None,
                "reason_short":"RSI overbought","risk_flags":["peak"]}
    return {"state":"consolidation","action":"hold","confidence":0.55,
            "size_usd":0.0,"stop_atr_k":None,
            "reason_short":"Neutral","risk_flags":["chop"]}

def ask_gpt4o(obs: dict, strat: dict):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    system = ("You are a cautious crypto analyst. "
              "Return ONLY a function call to 'decision' with fields that EXACTLY match the schema. "
              "Do not predict price; classify CURRENT regime.")
    tools = [{
        "type": "function",
        "function": {
            "name": "decision",
            "description": "Return a trading decision in strict schema.",
            "parameters": {
                "type": "object",
                "required": ["state","action","confidence"],
                "properties": {
                    "state": {"type":"string","enum":["peak","dip","consolidation"]},
                    "action": {"type":"string","enum":["buy","sell","hold"]},
                    "confidence": {"type":"number","minimum":0,"maximum":1},
                    "size_usd": {"type":"number"},
                    "stop_atr_k": {"type":["number","null"]},
                    "reason_short": {"type":"string"},
                    "risk_flags": {"type":"array","items":{"type":"string"}}
                },
                "additionalProperties": False
            }
        }
    }]
    import json as _json
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role":"system","content":system},
                  {"role":"user","content":f"Observation: {_json.dumps(obs)}"}],
        tools=tools,
        tool_choice={"type":"function","function":{"name":"decision"}},
        temperature=0.2,
    )
    msg = resp.choices[0].message
    if msg.tool_calls:
        args = msg.tool_calls[0].function.arguments
        if isinstance(args, str):
            args = _json.loads(args)
        return args
    try:
        return _json.loads(msg.content or "{}")
    except Exception:
        return {"state":"consolidation","action":"hold","confidence":0.5}

def ask_model(obs: dict, strat: dict):
    model = (os.getenv("ADVISOR_MODEL","mock") or "mock").strip().lower()
    if model in ("gpt-4o","gpt4o","openai"):
        return ask_gpt4o(obs, strat)
    return ask_mock(obs, strat)
