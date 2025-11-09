import smtplib
from email.mime.text import MIMEText
from pathlib import Path

# === ONLY EDIT THESE THREE ===
SMTP_USER = "you@gmail.com"       # your email
SMTP_PASS = "YOUR_APP_PASSWORD"   # app-specific password / SMTP password
TO_EMAIL  = "you@gmail.com"       # where to send

# === PROBABLY DON'T TOUCH BELOW THIS LINE ===
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SUBJECT   = "BTC Agent — Weekly Report"

ROOT = Path("/root/btc-trading-agent")
report_path = ROOT / "state" / "reports" / "weekly_email.html"

html = report_path.read_text(encoding="utf-8")

msg = MIMEText(html, "html", "utf-8")
msg["Subject"] = SUBJECT
msg["From"] = SMTP_USER
msg["To"] = TO_EMAIL

with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
    s.starttls()
    s.login(SMTP_USER, SMTP_PASS)
    s.send_message(msg)

print("[OK] Sent weekly report to", TO_EMAIL)
