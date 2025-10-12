# app/main.py
import argparse
import os
import sys

# Prefer package-relative imports (python -m app.main).
# Fall back to direct-path execution (python app/main.py).
try:
    from .runner import run_once, run_loop
    try:
        from .voice_email import send_weekly_email
    except Exception:
        send_weekly_email = None
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from runner import run_once, run_loop  # type: ignore
    try:
        from voice_email import send_weekly_email  # type: ignore
    except Exception:
        send_weekly_email = None

def advisor_model() -> str:
    """Tiny shim: read advisor model from env; default to mock."""
    return os.getenv("ADVISOR_MODEL", "mock")

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

    if args.once:
        run_once(args.symbol, args.interval_minutes)
        return

    if args.loop:
        run_loop(args.symbol, args.interval_minutes, args.max_ticks)
        return

    p.print_help()

if __name__ == "__main__":
    main()

