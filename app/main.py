import argparse, os
from .config import advisor_model
from .runner import run_once, run_loop
try:
    from .voice_email import send_weekly_email
except Exception:
    send_weekly_email = None

def main():
    p = argparse.ArgumentParser(description="BTC paper-trading agent")
    p.add_argument("--once", action="store_true", help="Run one advisory tick")
    p.add_argument("--loop", action="store_true", help="Run forever (or until max ticks)")
    p.add_argument("--email-now", action="store_true", help="Send weekly summary now (or write preview if SMTP missing)")
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
