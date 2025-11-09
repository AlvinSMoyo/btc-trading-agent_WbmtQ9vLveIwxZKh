#!/usr/bin/env bash
set -euo pipefail
[ -t 1 ] && { R=$'\033[31m'; Y=$'\033[33m'; G=$'\033[32m'; Z=$'\033[0m'; } || { R=""; Y=""; G=""; Z=""; }
grn(){ printf "%b%s%b\n" "$G" "$*" "$Z"; }; ylw(){ printf "%b%s%b\n" "$Y" "$*" "$Z"; }; err(){ printf "%b%s%b\n" "$R" "$*" "$Z"; }
LOG="${LOG:-$(journalctl -u btc-agent --since '6 hours ago' -o cat)}"
LAST_OBS="$(printf '%s\n' "$LOG" | grep -a '^\[obs\]' | tail -n1)"
OBS_INT="$(printf '%s\n' "$LAST_OBS" | sed -n "s/.*interval_min['\"]*[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p")"
YF_ROWS="$(printf '%s\n' "$LOG" | awk '/\[feed\] ok fetch_yfinance/ {if (match($0,/rows=([0-9]+)/,a)) r=a[1]} END{if(r) print r}')"
BZ_ROWS="$(printf '%s\n' "$LOG" | awk '/\[feed\] exit  _binance_ohlcv/ {if (match($0,/rows=([0-9]+)/,a)) r=a[1]} END{if(r) print r}')"
[ -n "$YF_ROWS" ] && [ "$YF_ROWS" -gt 0 ] && grn "✅ yfinance rows: $YF_ROWS" || ylw "⚠️ yfinance rows: ${YF_ROWS:-n/a}"
[ -n "$BZ_ROWS" ] && [ "$BZ_ROWS" -gt 0 ] && grn "✅ binance rows: $BZ_ROWS" || ylw "⚠️ binance rows: ${BZ_ROWS:-n/a}"
[ -n "$OBS_INT" ] && ylw "Note: last observed candle timeframe interval_min=$OBS_INT. Loop cadence is set by the service --interval-minutes." \
                 || ylw "Note: could not parse interval_min from last [obs]. Consider logging: [obs_interval] interval_min=<n>"
if { [ -z "${YF_ROWS:-}" ] || [ "${YF_ROWS:-0}" -eq 0 ]; } && { [ -z "${BZ_ROWS:-}" ] || [ "${BZ_ROWS:-0}" -eq 0 ]; }; then err "❌ No usable rows detected."; exit 1; fi
