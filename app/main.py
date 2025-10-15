# app/main.py
import argparse
import os
import sys
import inspect
import logging

from app.logs.setup import init_logging
init_logging()
log = logging.getLogger(__name__)

# Prefer package-relative imports (python -m app.main).
# Fall back to direct-path execution (python app/main.py).
try:
    from .runner import run_once, run_loop
    try:
        from .voice_email import send_weekly_email
    except Exception:
        send_weekly_email = None
    # NEW: import executor
    try:
        from .engine import try_execute_trade
    except Exception:
        try_execute_trade = None
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from runner import run_once, run_loop  # type: ignore
    try:
        from voice_email import send_weekly_email  # type: ignore
    except Exception:
        send_weekly_email = None
    try:
        from engine import try_execute_trade  # type: ignore
    except Exception:
        try_execute_trade = None


def advisor_model() -> str:
    """Tiny shim: read advisor model from env; default to mock."""
    return os.getenv("ADVISOR_MODEL", "mock")


# ---- NEW: shim the executor the runner can call ----
def _has_executor_param(fn) -> bool:
    try:
        sig = inspect.signature(fn)
        return any(p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name == "executor"
                   for p in sig.parameters.values())
    except Exception:
        return False

def _executor_shim(dec: dict, obs: dict):
    """
    Expected keys (based on your logs):
      dec: {'action','confidence','reason_short', ...}
      obs: {'price','atr14','rsi14', ...}

    Returns (ok, info_or_reason)
    """
    if try_execute_trade is None:
        return False, "try_execute_trade not available"

    side = str(dec.get("action", "")).lower()
    if side not in ("buy", "sell"):
        return False, f"action={side} (no trade)"

    conf   = float(dec.get("confidence", 0.0) or 0.0)
    reason = (dec.get("reason_short") or "LLM").strip()
    price  = float(obs.get("price", 0.0) or 0.0)
    atr    = obs.get("atr14", None)
    rsi    = obs.get("rsi14", None)
    spread_bps = obs.get("spread_bps", None)  # ok if None

    ok, info = try_execute_trade(
        side=side,
        conf=conf,
        reason=reason,
        price=price,
        atr=atr,
        spread_bps=spread_bps,
        rsi=rsi,
        note=dec.get("reason_short", "")
    )

    # Log for traceability
    if ok:
        log.info(f"[exec] {info}")
    else:
        log.info(f"[gate] skip -> {info}")
    return ok, info
# ----------------------------------------------------


def main():
    p = argparse.ArgumentParser(description="BTC paper-trading agent")
    p.add_argument("--once", action="store_true", help="run a single tick")
    p.add_argument("--loop", action="store_true", help="run forever")
    p.add_argument("--email-now", action="store_true", help="send the weekly email now (test)")
    p.add_argument("--symbol", default=os.getenv("SYMBOL", "BTC-USD"))
    p.add_argument("--interval-minutes", type=int, default=int(os.getenv("INTERVAL_MINUTES", "30")))
    p.add_argument("--max-ticks", type=int, default=None)
    args = p.parse_args()

    print("Advisor model:", advisor_model())

    if args.email_now:
        if send_weekly_email is None:
            print("voice_email module not available.")
            return
        ok, msg = send_weekly_email(preview_if_missing_creds=True)
        print(msg)
        return

    # If runner supports executor=, pass it; otherwise, call as before.
    supports_once_exec = _has_executor_param(run_once)
    supports_loop_exec = _has_executor_param(run_loop)

    if args.once:
        if supports_once_exec and try_execute_trade is not None:
            run_once(args.symbol, args.interval_minutes, executor=_executor_shim)
        else:
            run_once(args.symbol, args.interval_minutes)
        return

    if args.loop:
        if supports_loop_exec and try_execute_trade is not None:
            run_loop(args.symbol, args.interval_minutes, args.max_ticks, executor=_executor_shim)
        else:
            run_loop(args.symbol, args.interval_minutes, args.max_ticks)
        return

    p.print_help()


if __name__ == "__main__":
    main()
