Write-Host "== Smoke Check =="

# 0) Print merged config
@"
from app.config.loader import load
import json
print(json.dumps(load(), indent=2))
"@ | python -

# 1) One tick
python -m app.main --once --symbol BTC-USD --interval-minutes 30

# 2) Telegram ping
@"
from app.notify.telegram import ping
ok, err = ping("Smoke check ✅")
print("telegram:", ok, err or "")
"@ | python -

# 3) Weekly email (send or preview)
python -m app.voice_email --send-now

Write-Host "== Done =="
