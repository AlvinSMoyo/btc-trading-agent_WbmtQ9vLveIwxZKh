import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

# Read config from environment (matching your existing SendGrid setup)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "2525"))
SMTP_USER = os.environ.get("SMTP_USER", "apikey")
SMTP_PASS = os.environ.get("SMTP_PASS")

EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_FROM)
EMAIL_SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", "BTC Agent")

if not SMTP_PASS:
    raise SystemExit("❌ SMTP_PASS not set in environment (SendGrid API key).")

if not EMAIL_FROM or not EMAIL_TO:
    raise SystemExit("❌ EMAIL_FROM / EMAIL_TO not set in environment.")

subject = f"{EMAIL_SUBJECT_PREFIX} — Weekly Report"

ROOT = Path("/root/btc-trading-agent")
report_path = ROOT / "state" / "reports" / "weekly_email.html"

if not report_path.exists():
    raise SystemExit(f"❌ Report not found: {report_path}")

html = report_path.read_text(encoding="utf-8")

msg = MIMEText(html, "html", "utf-8")
msg["Subject"] = subject
msg["From"] = EMAIL_FROM
msg["To"] = EMAIL_TO

print(f"[INFO] Using SMTP {SMTP_HOST}:{SMTP_PORT} as {SMTP_USER}")
print(f"[INFO] Sending weekly report {report_path} to {EMAIL_TO}")

with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
    s.starttls()
    s.login(SMTP_USER, SMTP_PASS)
    s.send_message(msg)

print("[OK] Sent weekly report via SendGrid to", EMAIL_TO)
