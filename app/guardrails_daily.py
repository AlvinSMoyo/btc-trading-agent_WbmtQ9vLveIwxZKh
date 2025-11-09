from datetime import datetime, timezone
from pathlib import Path
import csv, os

def _parse_dt(s: str):
    s = (s or "").strip()
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def trades_today_count(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    today = datetime.now(timezone.utc).date()
    c = 0
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        has_header = bool(header) and any((h or "").lower().startswith(("time","ts")) for h in header)
        if not has_header:
            for row in r:
                if not row: continue
                dt = _parse_dt(row[0])
                if dt and dt.date() == today:
                    c += 1
        else:
            lower = [(h or "").strip().lower() for h in header]
            time_idx = None
            for key in ("time (utc)","ts","time","timestamp","datetime","date"):
                if key in lower:
                    time_idx = lower.index(key); break
            if time_idx is None:
                return 0
            for row in r:
                if not row: continue
                dt = _parse_dt(row[time_idx])
                if dt and dt.date() == today:
                    c += 1
    return c

def _get_daily_cap() -> int:
    # Prefer MAX_DAILY_TRADES; fallback to MAX_TRADES_PER_DAY
    v = os.getenv("MAX_DAILY_TRADES")
    if not v:
        v = os.getenv("MAX_TRADES_PER_DAY", "")
    try:
        return int(v) if v else 0
    except Exception:
        return 0

def daily_cap_gate(trades_csv: Path) -> tuple[bool, str]:
    cap = _get_daily_cap()
    if cap <= 0:
        return True, "cap disabled"
    n = trades_today_count(trades_csv)
    if n >= cap:
        return False, f"daily_cap reached ({n}/{cap})"
    return True, f"ok ({n}/{cap})"
