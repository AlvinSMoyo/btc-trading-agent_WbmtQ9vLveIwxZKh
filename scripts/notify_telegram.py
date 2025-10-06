import os, re, requests, pathlib

STATE = pathlib.Path("state")
PNG   = STATE / "baseline_compare_with_trades.png"
BASE  = STATE / "baseline_summary_with_trades.html"
WKLY  = STATE / "weekly_report_with_balances.html"

token  = os.environ.get("TG_BOT_TOKEN")
chatid = os.environ.get("TG_CHAT_ID")
if not token or not chatid:
    raise SystemExit("Missing TG_BOT_TOKEN or TG_CHAT_ID env vars")

summary_lines = []
if BASE.exists():
    html = BASE.read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r"<td><b>([^<]+)</b></td><td>([^<]+)</td>", html)
    for k, v in rows:
        summary_lines.append(f"{k}: {v}")
else:
    summary_lines.append("Baseline summary: (file not found)")

text = "BTC Agent — Weekly update is ready.\n\n" + "\n".join(summary_lines[:8])

api = f"https://api.telegram.org/bot{token}"

# 1) text
requests.post(f"{api}/sendMessage", data={"chat_id": chatid, "text": text})

# 2) chart image
if PNG.exists():
    with PNG.open("rb") as f:
        requests.post(f"{api}/sendPhoto", data={"chat_id": chatid}, files={"photo": f})

# 3) weekly HTML as document (optional)
if WKLY.exists():
    with WKLY.open("rb") as f:
        requests.post(f"{api}/sendDocument", data={"chat_id": chatid}, files={"document": ("weekly_report.html", f, "text/html")})
print("✅ Telegram update sent.")
