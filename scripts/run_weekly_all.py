import os, re, sys, subprocess, pathlib

REPO  = pathlib.Path(__file__).resolve().parents[1]
PY    = REPO / ".venv" / "Scripts" / "python.exe"
STATE = REPO / "state"
SCRIPTS = REPO / "scripts"

def run(cmd, **kw):
    print(">"," ".join(map(str,cmd)))
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.stdout: print(r.stdout.strip())
    if r.stderr: print(r.stderr.strip())
    if r.returncode != 0:
        print(f"? Command failed: {cmd}")
        sys.exit(r.returncode)

STATE.mkdir(exist_ok=True)

# 1) Baseline plot with trade markers
run([str(PY), str(SCRIPTS / "baseline_overlay.py")])

# 2) Weekly report with post-trade balances
run([str(PY), str(SCRIPTS / "add_trade_balances.py")])

# Build a short text summary from the baseline HTML
summary_path = STATE / "baseline_summary_with_trades.html"
summary_text = "Baseline summary unavailable."
if summary_path.exists():
    html = summary_path.read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r"<td><b>([^<]+)</b></td><td>([^<]+)</td>", html)
    summary_text = "\n".join(f"{k}: {v}" for k,v in rows[:8])

# 3) Telegram notify (only if env vars are present)
token  = os.environ.get("TG_BOT_TOKEN")
chatid = os.environ.get("TG_CHAT_ID")

if token and chatid:
    try:
        import requests
    except ImportError:
        run([str(PY), "-m", "pip", "install", "requests"])
        import requests

    api = f"https://api.telegram.org/bot{token}"
    text = "BTC Agent — weekly update\n\n" + summary_text

    # Text
    requests.post(f"{api}/sendMessage", data={"chat_id": chatid, "text": text})

    # Chart
    png = STATE / "baseline_compare_with_trades.png"
    if png.exists():
        with png.open("rb") as f:
            requests.post(f"{api}/sendPhoto", data={"chat_id": chatid}, files={"photo": f})

    # Weekly HTML
    wk = STATE / "weekly_report_with_balances.html"
    if wk.exists():
        with wk.open("rb") as f:
            requests.post(
                f"{api}/sendDocument",
                data={"chat_id": chatid},
                files={"document": ("weekly_report.html", f, "text/html")}
            )
    print("? Telegram update sent.")
else:
    print("?? TG_BOT_TOKEN / TG_CHAT_ID not set — skipping Telegram send.")

print("? Done. Outputs in: state\\")
