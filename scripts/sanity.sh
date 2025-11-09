#!/usr/bin/env bash
# sanity.sh v2 — absolute paths everywhere
set -euo pipefail

ROOT="/root/btc-trading-agent"
STATE="$ROOT/state"
REPORTS="$STATE/reports"
SVC="btc-agent"

bold(){ printf "\033[1m%s\033[0m\n" "$*"; }
ok(){ printf "✅ %s\n" "$*"; }
warn(){ printf "⚠️  %s\n" "$*" >&2; }
err(){ printf "❌ %s\n" "$*" >&2; }

bold "1) Service status"
systemctl --no-pager status "$SVC" | sed -n '1,20p' || true
systemctl is-active --quiet "$SVC" && ok "Service is active" || { err "Service is not active"; exit 2; }

bold "2) Environment wiring"
systemctl show "$SVC" -p Environment | tr ' ' '\n' | sed 's/^Environment=//g' \
 | egrep -i --color=never --color=never 'LLM|CHOP|DCA|DAILY|MAX_DAILY|TELEGRAM|SYMBOL|INTERVAL|FEED_ORDER|DISABLE|PYTHONUNBUFFERED|FORCE_COLOR' || true
MAINPID=$(systemctl show -p MainPID --value "$SVC" || echo "")
if [[ -n "${MAINPID:-}" && "$MAINPID" != "0" ]]; then
  echo "[proc env]"
  sudo tr '\0' '\n' <"/proc/$MAINPID/environ" \
   | egrep -i --color=never --color=never 'LLM|CHOP|DCA|DAILY|MAX_DAILY|TELEGRAM|SYMBOL|INTERVAL|FEED_ORDER|DISABLE|PYTHONUNBUFFERED|FORCE_COLOR' || true
  ok "Read running process environment"
else
  warn "Could not resolve MAINPID from systemd"
fi

bold "3) Recent logs: ticks, obs, decisions, fills (last 200 lines)"
journalctl -u "$SVC" -n 200 --no-pager -o cat \
 | egrep '^\[tick\]|\[obs\]|\[dec\]|\[fill\]' | tail -n 60 || true

bold "4) Tick cadence & feed health (last ~3h)"
mkdir -p "$STATE"
journalctl -u "$SVC" --since "3 hours ago" --no-pager -o cat \
 | egrep '^\[tick\]|\[obs\]|\[fill\]|\[feed\]' \
 | tail -n 400 > "$STATE/recent.log" || true

python - <<'PY'
import re, statistics as st
from datetime import datetime, timezone
from pathlib import Path
ROOT   = Path("/root/btc-trading-agent")
STATE  = ROOT / "state"
logp   = STATE / "recent.log"
now    = datetime.now(timezone.utc)

if not logp.exists() or logp.stat().st_size == 0:
    print("⚠️  No recent log lines captured"); raise SystemExit(0)

lines = logp.read_text(errors="replace").splitlines()
re_tick   = re.compile(r'^\[tick\]\s+\d+\s+(\d\d:\d\d:\d\d)\s+UTC')
re_obs    = re.compile(r"^\[obs\].*'ts_utc':\s*'([^']+)'")
re_fill   = re.compile(r'^\[fill\]')
re_feedok = re.compile(r'^\[feed\].*\bok\b')

ticks, obs_times = [], []
fills = feedoks = 0

def parse_hhmmss(h, base):
    return datetime.fromisoformat(base.strftime('%Y-%m-%d') + 'T' + h + '+00:00')

for ln in lines:
    m = re_tick.search(ln)
    if m:
        try: ticks.append(parse_hhmmss(m.group(1), now))
        except: pass
    m = re_obs.search(ln)
    if m:
        s = m.group(1)
        # normalize possible offsets without colon
        s = s.replace('+0000','+00:00').replace('+00:00Z','+00:00')
        try: obs_times.append(datetime.fromisoformat(s))
        except: pass
    if re_fill.search(ln):   fills   += 1
    if re_feedok.search(ln): feedoks += 1

def age(dt): return (now - dt).total_seconds()

print(f"[ticks] count={len(ticks)}")
if len(ticks) >= 2:
    gaps = [(ticks[i]-ticks[i-1]).total_seconds() for i in range(1,len(ticks))]
    print(f"[ticks] gap_s median={st.median(gaps):.1f} min={min(gaps):.1f} max={max(gaps):.1f}")
if ticks:
    print(f"[ticks] last_age_s={age(ticks[-1]):.0f}")

print(f"[obs]   count={len(obs_times)}")
if obs_times:
    print(f"[obs]   last={obs_times[-1].isoformat()}  age_s={age(obs_times[-1]):.0f}")

print(f"[fill]  count(last~3h)={fills}")
print(f"[feed]  ok_events(last~3h)={feedoks}")

issues=[]
if ticks and age(ticks[-1]) > 1200: issues.append("stale ticks (>20 min)")
if obs_times and age(obs_times[-1]) > 1200: issues.append("stale obs (>20 min)")
if feedoks == 0: issues.append("no successful feed events in last ~3h")

print("✅ tick + feed cadence looks healthy" if not issues
      else "⚠️  tick/feed issues: " + "; ".join(issues))
PY

bold "5) Fills audit (last 7 days, by day)"
journalctl -u "$SVC" --since "7 days ago" --no-pager -o cat \
 | grep '^\[fill\]' | cut -d" " -f1 | sort | uniq -c || true

bold "6) trades.csv health"
if [[ ! -f "$STATE/trades.csv" ]]; then
  err "Missing $STATE/trades.csv"; exit 3;
fi
python - <<'PY'
import pandas as pd
from pathlib import Path
ROOT  = Path("/root/btc-trading-agent")
STATE = ROOT/"state"
p = STATE/"trades.csv"
df = pd.read_csv(p)

def pick(cols,*c,default=None):
    low={x.lower():x for x in cols}
    for k in c:
        if k.lower() in low: return low[k.lower()]
    return default

c_time = pick(df.columns,"Time (UTC)","ts_dt","timestamp","time","ts")
if c_time == "ts":
    df["ts_dt"]=pd.to_datetime(df[c_time], unit="s", utc=True, errors="coerce")
else:
    df["ts_dt"]=pd.to_datetime(df[c_time], utc=True, errors="coerce")

c_side = pick(df.columns,"Side","side","action","order_side")
c_px   = pick(df.columns,"Price","price","px")
c_qty  = pick(df.columns,"Qty BTC","qty_btc","quantity","size")
c_fee  = pick(df.columns,"Fee","fee","fee_usd","fees")
for c in (c_px,c_qty,c_fee):
    if c: df[c]=pd.to_numeric(df[c], errors="coerce")

total=len(df)
dups = df.duplicated(subset=["ts_dt", c_side, c_px, c_qty], keep="first").sum() if c_side and c_px and c_qty else 0
zeros = int(((df.get(c_px,0)<=0) | (df.get(c_qty,0)<=0)).sum())
nans_time = int(df["ts_dt"].isna().sum())
print(f"[trades] rows={total}  dup_rows={dups}  bad_px_or_qty={zeros}  time_nans={nans_time}")

df=df.dropna(subset=["ts_dt"]).sort_values("ts_dt")
if len(df):
    start,end=df["ts_dt"].iloc[0], df["ts_dt"].iloc[-1]
    print(f"[trades] range={start} .. {end}")
    last7 = df[df["ts_dt"] >= (end - pd.Timedelta(days=7))]
    print(f"[trades] last7d_rows={len(last7)}")
else:
    print("⚠️  trades.csv has no parseable timestamps")
PY

bold "7) Report freshness"
find "$REPORTS" -maxdepth 1 -type f -printf "%TY-%Tm-%Td %TTZ %p\n" 2>/dev/null | sort -r | head -n 6 || true
if [[ -f "$REPORTS/weekly_balance_latest.csv" ]]; then
  echo "[weekly_balance_latest.csv head]"
  sed -n '1,5p' "$REPORTS/weekly_balance_latest.csv" || true
fi

bold "Done."
