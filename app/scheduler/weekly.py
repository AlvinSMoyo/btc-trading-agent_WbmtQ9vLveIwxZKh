# app/scheduler/weekly.py
from __future__ import annotations
import os, sys, subprocess, argparse
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # Py<3.9 fallback not expected here

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ensure .env loads for this submodule too
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from app.voice_email import send_weekly_email

TZ = os.getenv("REPORT_TZ", "Asia/Riyadh")

def _run_overlay():
    """Rebuild the overlay artifacts before sending."""
    try:
        # run the script via a subprocess to avoid import path issues
        return subprocess.run(
            [sys.executable, "scripts/baseline_overlay.py"],
            check=False
        ).returncode == 0
    except Exception:
        return False

def _weekly_job():
    ok_overlay = _run_overlay()
    ok_email, msg = send_weekly_email(preview_if_missing_creds=False)
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[weekly] {stamp} overlay_ok={ok_overlay} email_ok={ok_email} msg={msg}")

def start_scheduler():
    tz = ZoneInfo(TZ) if ZoneInfo else None
    sched = BackgroundScheduler(timezone=tz)
    # Monday 09:00 local (TZ). If tz None, scheduler runs naive (system time).
    trig = CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=tz)
    sched.add_job(_weekly_job, trig, id="weekly_report", replace_existing=True)
    sched.start()
    print(f"[sched] started for Monday 09:00 in {TZ}")
    return sched

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--send-now", action="store_true")
    p.add_argument("--start", action="store_true")
    args = p.parse_args()

    if args.send_now:
        _run_overlay()
        ok, msg = send_weekly_email(preview_if_missing_creds=False)
        print(msg); sys.exit(0)

    if args.start:
        start_scheduler()
        # Keep the process alive
        try:
            while True:
                pass
        except KeyboardInterrupt:
            pass
