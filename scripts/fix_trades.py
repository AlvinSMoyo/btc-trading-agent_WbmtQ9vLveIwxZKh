import os
from pathlib import Path
import pandas as pd

PATH = os.path.join('state','trades.csv')
REQUIRED = ['ts','side','source','reason','price','qty_btc','fee_usd','note','confidence']

if not Path(PATH).exists():
    raise SystemExit('No trades.csv at ' + PATH)

df = pd.read_csv(PATH, dtype=str)

# Lowercase/trim column names
df.columns = [c.strip().lower() for c in df.columns]

# Normalize timestamp column name
if 'ts' not in df.columns and 'ts_utc' in df.columns:
    df['ts'] = df['ts_utc']

# Ensure columns exist (fill with empty strings)
for c in REQUIRED:
    if c not in df.columns:
        df[c] = ''

# If qty_btc empty, try to pull from 'qty' if present
if df['qty_btc'].eq('').all() and 'qty' in df.columns:
    df['qty_btc'] = df['qty']

# Fill source from reason when missing
df['source'] = df['source'].where(df['source'].astype(str).str.len() > 0, df['reason'])

# Coerce numeric-friendly columns (keep strings if not parseable)
for c in ['price','qty_btc','fee_usd','confidence']:
    df[c] = pd.to_numeric(df[c], errors='coerce')

# Parse timestamps tz-aware (some rows may be ISO, some epoch)
ts_raw = df['ts'].astype(str).str.strip()
ts_num = pd.to_numeric(ts_raw, errors='coerce')
ts = pd.to_datetime(ts_num, unit='s', utc=True, errors='coerce')
bad = ts.isna()
ts.loc[bad] = pd.to_datetime(ts_raw[bad], utc=True, errors='coerce')
df['ts'] = ts

# Build a safe subset for exact de-duplication
df['__qty'] = df['qty_btc']
subset_cols = [c for c in ['ts','side','source','reason','price','__qty'] if c in df.columns]
dedup = (df.sort_values('ts')
           .dropna(subset=['ts'])
           .drop_duplicates(subset=subset_cols, keep='last')
           .drop(columns='__qty', errors='ignore'))

# Re-order columns and write back
for c in REQUIRED:
    if c not in dedup.columns:
        dedup[c] = ''
out = dedup[REQUIRED].copy()

out.to_csv(PATH, index=False)
print('wrote', PATH, 'rows:', len(out))
