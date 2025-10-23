import json, os, time
from pathlib import Path

def trace(event: str, payload: dict):
    """
    Append a small JSON line for post-hoc analysis into state/decision_trace.jsonl
    """
    state = Path(os.getenv("STATE_DIR", "state"))
    state.mkdir(parents=True, exist_ok=True)
    path = state / "decision_trace.jsonl"
    payload = {"t": time.time(), "event": event, **(payload or {})}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

def dump_effective_config(effective_config: dict, filename: str = "config.effective.json") -> None:
    """
    Write the fully-merged runtime config to state/<filename>.
    Call this once after your config is assembled (post-merge).
    """
    try:
        from pathlib import Path
        import os, json
        st = Path(os.getenv("STATE_DIR", "state"))
        st.mkdir(parents=True, exist_ok=True)
        (st / filename).write_text(json.dumps(effective_config, indent=2), encoding="utf-8")
    except Exception:
        # keep silent in production; this is debug-only
        pass

