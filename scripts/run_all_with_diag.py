import os, sys, re, json, pathlib, subprocess, time

REPO    = pathlib.Path(__file__).resolve().parents[1]
PY      = REPO / ".venv" / "Scripts" / "python.exe"
SCRIPTS = REPO / "scripts"
STATE   = REPO / "state"

BASELINE_OVERLAY = SCRIPTS / "baseline_overlay.py"
BASELINE_QUICK   = SCRIPTS / "baseline_quick.py"
WEEKLY_SCRIPT    = SCRIPTS / "add_trade_balances.py"

PNG_OVERLAY = STATE / "baseline_compare_with_trades.png"
HTML_OVERLAY= STATE / "baseline_summary_with_trades.html"
PNG_FALLBK  = STATE / "baseline_compare.png"
HTML_WEEKLY = STATE / "weekly_report_with_balances.html"

def run(cmd, name):
    print(f"\n? {name}:"," ".join(map(str,cmd)))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout: print(r.stdout.strip())
    if r.stderr: print(r.stderr.strip())
    if r.returncode != 0:
        print(f"? {name} failed with code {r.returncode}")
    return r.returncode

def file_ok(p: pathlib.Path, min_kb=10):
    try:
        return p.exists() and p.stat().st_size >= min_kb*1024
    except: return False

STATE.mkdir(exist_ok=True)

# 1) Baseline with markers (fallback to quick)
rc = run([str(PY), str(BASELINE_OVERLAY)], "baseline_overlay")
if not file_ok(PNG_OVERLAY):
    print("? Overlay PNG missing or too small, falling back to baseline_quick…")
    rc2 = run([str(PY), str(BASELINE_QUICK)], "baseline_quick")
    if rc2 != 0 or not file_ok(PNG_FALLBK):
        print("? Baseline fallback failed; continuing so weekly still runs.")
    else:
        print(f"? Fallback OK: {PNG_FALLBK.name} ({PNG_FALLBK.stat().st_size/1024:.1f} KB)")
else:
    print(f"? Overlay OK: {PNG_OVERLAY.name} ({PNG_OVERLAY.stat().st_size/1024:.1f} KB)")

# 2) Weekly report with balances
rc3 = run([str(PY), str(WEEKLY_SCRIPT)], "weekly_report")
if rc3 == 0 and HTML_WEEKLY.exists():
    print(f"? Weekly HTML: {HTML_WEEKLY.name} ({HTML_WEEKLY.stat().st_size/1024:.1f} KB)")
else:
    print("? Weekly report did not produce the expected HTML.")

# 3) Telegram preflight + send
token  = os.environ.get("TG_BOT_TOKEN")
chatid = os.environ.get("TG_CHAT_ID")

def try_requests():
    try:
        import requests
        return requests
    except ImportError:
        print("… installing requests")
        subprocess.run([str(PY), "-m", "pip", "install", "requests"], check=False)
        import requests
        return requests

def tg_send():
    if not token or not chatid:
        print("? TG_BOT_TOKEN / TG_CHAT_ID not set ? skipping Telegram send.")
        return

    requests = try_requests()
    api = f"https://api.telegram.org/bot{token}"

    # Preflight
    try:
        me = requests.get(f"{api}/getMe", timeout=10)
        ok = me.ok and me.json().get("ok")
        print(f"Telegram getMe ok={ok}")
        if not ok:
            print("? Telegram token rejected. Skipping sends.")
            return
    except Exception as e:
        print(f"? Telegram preflight failed: {e}")
        return

    # Build short text from baseline HTML if present
    txt = "BTC Agent — Weekly update"
    if HTML_OVERLAY.exists():
        html = HTML_OVERLAY.read_text(encoding="utf-8", errors="ignore")
        rows = re.findall(r"<td><b>([^<]+)</b></td><td>([^<]+)</td>", html)
        if rows:
            txt += "\n\n" + "\n".join(f"{k}: {v}" for k,v in rows[:8])

    try:
        r1 = requests.post(f"{api}/sendMessage", data={"chat_id": chatid, "text": txt}, timeout=20)
        print("sendMessage:", r1.status_code, r1.text[:200])
    except Exception as e:
        print("? sendMessage error:", e)

    # Pick whichever chart exists
    chart = PNG_OVERLAY if file_ok(PNG_OVERLAY) else (PNG_FALLBK if file_ok(PNG_FALLBK) else None)
    if chart:
        try:
            with chart.open("rb") as f:
                r2 = requests.post(f"{api}/sendPhoto", data={"chat_id": chatid}, files={"photo": f}, timeout=30)
            print("sendPhoto:", r2.status_code, r2.text[:200])
        except Exception as e:
            print("? sendPhoto error:", e)
    else:
        print("? No chart file to send.")

    if HTML_WEEKLY.exists():
        try:
            with HTML_WEEKLY.open("rb") as f:
                r3 = requests.post(
                    f"{api}/sendDocument",
                    data={"chat_id": chatid},
                    files={"document": ("weekly_report.html", f, "text/html")},
                    timeout=60
                )
            print("sendDocument:", r3.status_code, r3.text[:200])
        except Exception as e:
            print("? sendDocument error:", e)
    else:
        print("? No weekly HTML to send.")

tg_send()
print("\n? Done. See state/ for outputs.")
