import os, requests, datetime as dt
url = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/sendMessage"
payload = {
    "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
    "text": f"✅ BTC agent test ping @ {dt.datetime.utcnow().isoformat()}Z"
}
r = requests.post(url, json=payload, timeout=10)
print("status:", r.status_code, "ok:", r.ok, "resp:", r.text[:200])
