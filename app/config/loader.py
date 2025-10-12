# app/config/loader.py
from __future__ import annotations
import csv, io, json, os, time
from typing import Dict, Any, Tuple
import requests  # lightweight; already a dep via yfinance
from .schema import DEFAULTS, coerce_key

"""
Two ways to read config:

1) Public/Published Sheet (no auth):
   - Set GOOGLE_SHEET_CSV_URL to a 'Publish to web' CSV link, OR
   - Set GOOGLE_SHEET_ID + GOOGLE_SHEET_TAB (we'll build a gviz CSV URL)

2) Offline fallback:
   - We always keep a JSON cache (config.cache.json by default)
   - If fetch fails or TTL not met, we load from cache; if no cache, use DEFAULTS

Env knobs:
  CONFIG_CACHE_PATH   (default: config.cache.json)
  CONFIG_TTL_SEC      (default: 3600)
  GOOGLE_SHEET_CSV_URL
  GOOGLE_SHEET_ID
  GOOGLE_SHEET_TAB
"""

def _cache_path() -> str:
    return os.getenv("CONFIG_CACHE_PATH", "config.cache.json")

def _ttl_sec() -> int:
    try:
        return int(os.getenv("CONFIG_TTL_SEC", "3600"))
    except Exception:
        return 3600

def _build_csv_url() -> str | None:
    url = os.getenv("GOOGLE_SHEET_CSV_URL")
    if url:
        return url
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    tab = os.getenv("GOOGLE_SHEET_TAB")
    if sheet_id and tab:
        # Works for sheets published to web or when “anyone with link can view”
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}"
    return None

def _fetch_csv(url: str) -> Dict[str, Any]:
    # Expect 2-column CSV: key,value (case-insensitive)
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    text = r.text
    reader = csv.reader(io.StringIO(text))
    out: Dict[str, Any] = {}
    for row in reader:
        if not row or len(row) < 2:
            continue
        k = str(row[0]).strip()
        v = str(row[1]).strip()
        if not k:
            continue
        out[k] = v
    return out

def _load_cache() -> Tuple[Dict[str, Any] | None, float]:
    path = _cache_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mtime = os.path.getmtime(path)
        return data, mtime
    except Exception:
        return None, 0.0

def _save_cache(data: Dict[str, Any]) -> None:
    path = _cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _merge_and_coerce(raw: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    for k, v in raw.items():
        key = k.strip()
        if not key:
            continue
        if key in DEFAULTS:
            merged[key] = coerce_key(key, str(v))
        else:
            # allow unknown keys to pass through as strings
            merged[key] = v
    return merged

def load() -> Dict[str, Any]:
    """
    Returns a dict of config values merged as:
      DEFAULTS <- (cache or sheet)  (env is still read elsewhere if code uses os.getenv)
    """
    cache, mtime = _load_cache()
    url = _build_csv_url()
    now = time.time()
    ttl_ok = (now - mtime) <= _ttl_sec()

    # If we have a fresh-enough cache and a URL is not set, just use cache
    if cache and (ttl_ok or not url):
        return _merge_and_coerce(cache)

    # Try fetch if URL present; otherwise fall back to cache/DEFAULTS
    if url:
        try:
            raw = _fetch_csv(url)
            if raw:
                _save_cache(raw)
                return _merge_and_coerce(raw)
        except Exception:
            # fall through to cache
            pass

    # cache fallback or defaults
    if cache:
        return _merge_and_coerce(cache)
    return dict(DEFAULTS)
