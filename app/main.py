# app/main.py
import argparse, os, sys
from app.indicators.atr import atr
from app.strategies.dca import dca_actions
from app.strategies.swing_atr import swing_entry   # ok even if you keep SWING_ENABLED=false
from app.risk.stop_watch import check_stops

# allow both "python -m app.main" and "python app/main.py"
try:
    from .config import advisor_model
    from .runner import run_once, run_loop
    try:
        from .voice_email import send_weekly_email
    except Exception:
        send_weekly_email = None
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from config import advisor_model
    from runner import run_once, run_loop
    try:
        from voice_email import send_weekly_email
    except Exception:
        send_weekly_email = None

def main():
    p = argparse.ArgumentParser(description="BTC paper-trading agent")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--email-now", action="store_true")
    p.add_argument("--symbol", default=os.getenv("SYMBOL","BTC-USD"))
    p.add_argument("--interval-minutes", type=int, default=int(os.getenv("INTERVAL_MINUTES","30")))
    p.add_argument("--max-ticks", type=int, default=None)
    args = p.parse_args()

    print("Advisor model:", advisor_model())

    if args.email_now:
        if send_weekly_email is None:
            print("voice_email module not available.")
            return
        ok, msg = send_weekly_email(preview_if_missing_creds=True)
        print(msg); return

    if args.once:
        run_once(args.symbol, args.interval_minutes); return

    if args.loop:
        run_loop(args.symbol, args.interval_minutes, args.max_ticks); return

    p.print_help()

if __name__ == "__main__":
    main()